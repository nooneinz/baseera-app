"""
ai_quota — a lightweight daily cap on generative-model (Gemini) calls.

Protects against a surprise bill: bounds how many Gemini-backed replies a
single user, and the whole platform, can trigger per day. Enforced at the
cost-exposed entry points (notably the *public* WhatsApp inbound, where anyone
who can message the number could otherwise drive unbounded calls).

Backed by the Django cache (Redis in production) with a key that expires ~26h
after first use, so counters reset daily on their own. Fail-open by design: if
the cache is unavailable we never block a legitimate reply over an infra
hiccup -- the cap is a cost guardrail, not a security control.

Limits are env-configurable:
  GEMINI_DAILY_LIMIT_PER_USER  (default 50)
  GEMINI_DAILY_LIMIT_GLOBAL    (default 3000)
"""
import os
import datetime
import logging

logger = logging.getLogger(__name__)

_TTL_SECONDS = 26 * 60 * 60  # a bit over a day, so a day's counter self-expires


def _limit(env_name, default):
    try:
        return int(os.environ.get(env_name, default))
    except (TypeError, ValueError):
        return default


def within_daily_quota(scope, limit):
    """
    Atomically count one call against today's counter for ``scope`` and report
    whether it is still within ``limit``. Returns (allowed: bool, used: int).
    Fail-open (returns allowed=True) on any cache error.
    """
    from django.core.cache import cache

    day = datetime.date.today().isoformat()
    key = f"gemini_quota:{scope}:{day}"
    try:
        cache.add(key, 0, timeout=_TTL_SECONDS)  # no-op if it already exists
        used = cache.incr(key)
    except Exception as exc:
        logger.info("ai_quota check skipped (cache error): %s", exc)
        return True, 0
    return used <= limit, used


def check_gemini_quota(user_id=None):
    """
    Convenience gate for a Gemini-backed action: enforces both the per-user and
    the global daily caps. Returns True if the call is allowed to proceed.
    """
    ok_global, _ = within_daily_quota("global", _limit("GEMINI_DAILY_LIMIT_GLOBAL", 3000))
    if not ok_global:
        logger.warning("Gemini global daily quota reached.")
        return False
    if user_id is not None:
        ok_user, _ = within_daily_quota(f"user:{user_id}", _limit("GEMINI_DAILY_LIMIT_PER_USER", 50))
        if not ok_user:
            logger.info("Gemini per-user daily quota reached for user %s.", user_id)
            return False
    return True
