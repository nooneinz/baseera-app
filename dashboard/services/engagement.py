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


def _radar_alert(rows):
    """
    The single highest-priority grounded signal for these rows, as
    (kind, message_core), or None. Priority: liquidity (runway) > waste >
    big recurring expense. Every message is built only from the user's own
    numbers/items. Never raises.
    """
    # 1) Liquidity / runway -- the most urgent.
    try:
        from dashboard.services.runway import compute_runway
        rw = compute_runway(rows)
        st = rw.get("status")
        if st == "critical":
            return ("runway", "⚠️ تنبيه سيولة حرج: مشروعك يحرق نقداً ورصيدك التقديري صفر أو أقل — راجع مصاريفك فوراً.")
        if st == "burning" and rw.get("runway_months") is not None and rw["runway_months"] <= 2:
            return ("runway", f"⚠️ تنبيه سيولة: بالوتيرة الحالية سيولتك تكفي تقريباً {rw['runway_months']} شهر فقط — وقت المراجعة الآن.")
    except Exception:
        pass
    # 2) Waste (items sold below cost).
    try:
        from dashboard.services.waste_analyzer import compute_waste_signals
        w = compute_waste_signals(rows)
        if w and w.get("total_waste", 0) > 0 and w.get("signals"):
            total = f"{round(w['total_waste']):,} ر.ع"
            name = (w["signals"][0].get("title") or "").strip()
            extra = f" — {name}" if name else ""
            return ("waste", f"👀 رادار بصيرة: عندك هدر تقريباً {total} من أصناف تُباع بأقل من تكلفتها{extra}. راجعها.")
    except Exception:
        pass
    # 3) Big recurring expense (a savings / negotiation opportunity).
    try:
        from dashboard.services.agent_actions import _detect_finding
        f = _detect_finding(rows)
        if f:
            total = f'{f["total"]:,.0f} ر.ع'
            return ("recurring", f"👀 رادار بصيرة: بند «{f['name']}» بلغ {total} عبر {f['count']} عمليات — فرصة تفاوض وتوفير.")
    except Exception:
        pass
    return None


def run_daily_radar(push=True, limit=None, throttle_hours=20):
    """
    Scan every active user's data once and push the single highest-priority
    grounded alert (liquidity > waste > recurring expense). Throttled to at
    most once/day per user. Returns a summary dict; never raises.
    """
    from django.contrib.auth.models import User
    from dashboard.models import ProjectFile, DynamicRecord, Profile
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
        alert = _radar_alert(rows)
        if not alert:
            continue
        kind, core = alert
        flagged += 1
        msg = core + "\n\nافتح لوحتك 👇\n" + DASHBOARD_URL
        _log(user, RADAR_ACTION, f"radar[{kind}]")
        if push:
            profile = Profile.objects.filter(user=user).exclude(phone_number="").first()
            if profile:
                phone = normalize_phone(profile.phone_number)
                if phone and _outbound(phone, msg):
                    pushed += 1

    result = {"processed": processed, "flagged": flagged, "pushed": pushed}
    logger.info("Daily radar run: %s", result)
    return result


MONTH_ACTION = "بصيرة / تقرير شهري"


def run_month_end_report(push=True, limit=None, force=False):
    """
    Once a month (on the 1st, unless force=True), push each active user a short
    grounded month summary (income / expense / net) from their own data.
    Throttled so nobody gets more than one per calendar month. Never raises.
    """
    from django.utils import timezone
    if not force and timezone.now().day != 1:
        return {"skipped": True, "reason": "not first of month", "pushed": 0}

    from django.contrib.auth.models import User
    from dashboard.models import ProjectFile, DynamicRecord, Profile
    from dashboard.services.first_win_insights import compute_transaction_signal
    from dashboard.services.whatsapp_service import normalize_phone, DASHBOARD_URL

    processed = pushed = 0
    users = User.objects.filter(is_active=True).order_by("id")
    if limit:
        users = users[:limit]

    for user in users.iterator():
        if not ProjectFile.objects.filter(user=user).exists():
            continue
        if _logged_since(user, MONTH_ACTION, 24 * 25):  # already sent this month
            continue
        rows = list(
            DynamicRecord.objects.filter(user=user).values_list("row_data", flat=True)[:10000]
        )
        sig = compute_transaction_signal(rows)
        if not sig:
            continue
        processed += 1
        inc = f"{round(sig.get('total_income') or 0):,}"
        exp = f"{round(sig.get('total_expense') or 0):,}"
        net = f"{round(sig.get('net') or 0):,}"
        msg = (
            "📅 تقريرك الشهري من بصيرة:\n"
            f"الدخل: {inc} ر.ع\nالمصروف: {exp} ر.ع\nالصافي: {net} ر.ع\n\n"
            "التفاصيل الكاملة في لوحتك 👇\n" + DASHBOARD_URL
        )
        _log(user, MONTH_ACTION, "monthly report")
        if push:
            profile = Profile.objects.filter(user=user).exclude(phone_number="").first()
            if profile:
                phone = normalize_phone(profile.phone_number)
                if phone and _outbound(phone, msg):
                    pushed += 1

    result = {"processed": processed, "pushed": pushed}
    logger.info("Month-end report run: %s", result)
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
