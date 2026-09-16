"""
Weekly "Business Pulse" — proactive weekly digest for every active business.

Two things happen per run:
1. Each active user with uploaded data gets a fresh WeeklyDigest generated
   (the same "نبض الأعمال" the dashboard shows), so the in-app card is always
   current -- this works today, with or without WhatsApp.
2. If an outbound WhatsApp webhook is configured (WHATSAPP_OUTBOUND_WEBHOOK_URL,
   an n8n webhook that sends via the connected provider), a short pulse
   message is pushed to each user's WhatsApp number. Until that's wired, this
   step is simply skipped -- no error.

Triggered weekly by n8n's Schedule node hitting /api/cron/weekly-pulse/
(secret-protected), or by `manage.py send_weekly_pulse`.
"""
import os
import json
import logging

logger = logging.getLogger(__name__)


def _sample_records(user, cap=100):
    from dashboard.models import DynamicRecord
    from dashboard.services.privacy import redacted_json
    rows = list(
        DynamicRecord.objects.filter(user=user).values_list("row_data", flat=True)[:cap]
    )
    # Redact identifying/sensitive fields before this leaves for Gemini.
    return redacted_json(rows, cap=cap)


def build_pulse_message(digest, dashboard_url):
    """A short, WhatsApp-ready weekly pulse built from a WeeklyDigest."""
    lines = [f"📊 نبض أعمالك الأسبوعي"]
    summary = (getattr(digest, "summary_text", "") or "").strip()
    if summary:
        lines.append("")
        lines.append(summary)
    risks = getattr(digest, "top_risks", None) or []
    if risks:
        lines.append(f"\n⚠️ أهم خطر: {str(risks[0])}")
    actions = getattr(digest, "action_plan", None) or []
    if actions:
        lines.append(f"✅ خطوتك هذا الأسبوع: {str(actions[0])}")
    lines.append(f"\nلوحتك الكاملة 👇\n{dashboard_url}")
    return "\n".join(lines)


def _push_to_whatsapp(phone, message, webhook_url):
    """Best-effort POST to the configured n8n outbound webhook. Never raises."""
    import urllib.request

    try:
        data = json.dumps({"phone": phone, "message": message}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=data, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception as e:
        logger.info("Weekly pulse push failed for %s: %s", phone, e)
        return False


def run_weekly_pulse(push=True, limit=None):
    """
    Generate (and optionally push) the weekly pulse for every active user with
    data. Returns a summary dict; never raises for a single user's failure.
    """
    from django.contrib.auth.models import User
    from dashboard.models import ProjectFile, Profile
    from dashboard.services.ai_service import GeminiAIService
    from dashboard.services.whatsapp_service import DASHBOARD_URL, normalize_phone

    ai = GeminiAIService()
    webhook = os.environ.get("WHATSAPP_OUTBOUND_WEBHOOK_URL", "").strip()

    processed = generated = pushed = 0
    users = User.objects.filter(is_active=True).order_by("id")
    if limit:
        users = users[:limit]

    for user in users.iterator():
        if not ProjectFile.objects.filter(user=user).exists():
            continue
        processed += 1
        sample = _sample_records(user)
        if not sample:
            continue
        try:
            digest = ai.generate_weekly_digest_for_user(sample, user)
        except Exception as e:
            logger.info("Weekly digest generation failed for user %s: %s", user.id, e)
            continue
        if not digest:
            continue
        generated += 1

        if push and webhook:
            profile = Profile.objects.filter(user=user).first()
            phone = normalize_phone(profile.phone_number) if (profile and profile.phone_number) else ""
            if phone:
                message = build_pulse_message(digest, DASHBOARD_URL)
                if _push_to_whatsapp(phone, message, webhook):
                    pushed += 1

    result = {"processed": processed, "generated": generated, "pushed": pushed}
    logger.info("Weekly pulse run: %s", result)
    return result
