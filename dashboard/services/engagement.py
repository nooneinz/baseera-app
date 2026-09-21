"""
Engagement engine — Baseera reaches OUT, it doesn't wait to be opened.

Two automated jobs, both meant to run on a daily schedule (a cron hitting
/api/cron/engagement/):

1) run_daily_radar(): the "financial radar". For every active user with data,
   it re-runs the deterministic proactive detection and, if it finds a real,
   grounded signal (a big recurring expense), pushes a short WhatsApp alert --
   without the user asking. Throttled so nobody gets pinged more than once a
   day, and only ever mentions figures/items from the user's own data.

2) run_reengagement(): a gentle nudge for users who've gone quiet. If a user
   hasn't interacted over WhatsApp for a while, it sends one catchy hook
   ("ما فكّرت تحسب كم هدرت اليوم؟") to pull them back. Rotated and throttled so
   it never becomes spam.

IMPORTANT (WhatsApp policy): free-form messages only deliver inside the 24h
customer-service window. Reaching a user who's been silent longer than that
requires a Meta-APPROVED message template; until templates are set up, those
sends simply fail-soft (the engine still records who it would nudge). The
engine never raises.
"""
import os
import random
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

RADAR_ACTION = "بصيرة / رادار مالي"
NUDGE_ACTION = "بصيرة / تذكير تفاعل"

# Catchy Omani-dialect hooks for quiet users. Rotated at random.
_NUDGES = [
    "ما فكّرت تحسب كم هدرت اليوم؟ طرّش لي ملف مبيعاتك وأكشفها لك 👀",
    "صار لك فترة ما شفنا أرقامك — تبي نظرة سريعة على وين تروح فلوسك؟ 💡",
    "٩٠ ثانية تكفيني أوريك أكبر تسريب في مصاريفك. جرّب، أرسل ملفك 📊",
    "وضعك المالي هالأسبوع… تبي أكشفه لك؟ أرسل كشف حسابك وأنا أتكفّل 🔎",
    "أغلب المطاعم تخسر من بند واحد وهي ما تدري. تبي أشوف بندك؟ طرّش ملفك 👇",
]


def _outbound(phone, message):
    """Send via the direct Meta path, falling back to the outbound webhook.
    Returns True only if something actually went out. Never raises."""
    try:
        from dashboard.services.whatsapp_service import send_whatsapp_reply, push_whatsapp_message
        if send_whatsapp_reply(phone, message):
            return True
        return push_whatsapp_message(phone, message)
    except Exception as e:
        logger.info("engagement outbound failed for %s: %s", phone, e)
        return False


def _logged_since(user, action_type, hours):
    from django.utils import timezone
    from dashboard.models import SystemLog
    since = timezone.now() - timedelta(hours=hours)
    return SystemLog.objects.filter(user=user, action_type=action_type, timestamp__gte=since).exists()


def _log(user, action_type, detail=""):
    try:
        from dashboard.models import SystemLog
        SystemLog.objects.create(user=user, action_type=action_type, details=(detail or "")[:500])
    except Exception:
        pass


def run_daily_radar(push=True, limit=None, throttle_hours=20):
    """
    Scan every active user's data once; push a grounded alert when a real
    recurring-expense signal is found. Returns a summary dict; never raises.
    """
    from django.contrib.auth.models import User
    from dashboard.models import ProjectFile, DynamicRecord, Profile
    from dashboard.services.agent_actions import _detect_finding
    from dashboard.services.whatsapp_service import normalize_phone, DASHBOARD_URL

    processed = flagged = pushed = 0
    users = User.objects.filter(is_active=True).order_by("id")
    if limit:
        users = users[:limit]

    for user in users.iterator():
        if not ProjectFile.objects.filter(user=user).exists():
            continue
        processed += 1
        if _logged_since(user, RADAR_ACTION, throttle_hours):
            continue  # already alerted recently -- don't spam
        rows = list(
            DynamicRecord.objects.filter(user=user).values_list("row_data", flat=True)[:10000]
        )
        finding = _detect_finding(rows)
        if not finding:
            continue
        flagged += 1
        total = f'{finding["total"]:,.0f} ر.ع'
        msg = (
            "👀 رادار بصيرة رصد شي في أرقامك:\n"
            f"بند «{finding['name']}» بلغ {total} عبر {finding['count']} عمليات — يستاهل مراجعة.\n\n"
            "تبي أجهّز لك رسالة تفاوض؟ افتح لوحتك 👇\n" + DASHBOARD_URL
        )
        _log(user, RADAR_ACTION, f"radar: {finding['name']} = {total}")
        if push:
            profile = Profile.objects.filter(user=user).exclude(phone_number="").first()
            if profile:
                phone = normalize_phone(profile.phone_number)
                if phone and _outbound(phone, msg):
                    pushed += 1

    result = {"processed": processed, "flagged": flagged, "pushed": pushed}
    logger.info("Daily radar run: %s", result)
    return result


def run_reengagement(push=True, inactive_hours=48, throttle_hours=72, limit=None):
    """
    Nudge users who've gone quiet (no WhatsApp interaction within inactive_hours)
    with a single catchy hook. Throttled by throttle_hours so it never spams.
    Returns a summary dict; never raises.
    """
    from django.utils import timezone
    from django.contrib.auth.models import User
    from dashboard.models import Profile, SystemLog
    from dashboard.services.whatsapp_service import normalize_phone

    candidates = nudged = 0
    cutoff = timezone.now() - timedelta(hours=inactive_hours)
    users = User.objects.filter(is_active=True).order_by("id")
    if limit:
        users = users[:limit]

    for user in users.iterator():
        profile = Profile.objects.filter(user=user).exclude(phone_number="").first()
        if not profile:
            continue
        # Skip users who interacted over WhatsApp recently (they're active).
        if SystemLog.objects.filter(user=user, action_type__icontains="واتساب",
                                    timestamp__gte=cutoff).exists():
            continue
        # Skip users already nudged within the throttle window.
        if _logged_since(user, NUDGE_ACTION, throttle_hours):
            continue
        candidates += 1
        msg = random.choice(_NUDGES)
        _log(user, NUDGE_ACTION, "nudge")
        if push:
            phone = normalize_phone(profile.phone_number)
            if phone and _outbound(phone, msg):
                nudged += 1

    result = {"candidates": candidates, "nudged": nudged}
    logger.info("Re-engagement run: %s", result)
    return result
