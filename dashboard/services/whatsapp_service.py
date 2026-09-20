"""
WhatsApp inbound handler — the single entry point n8n calls.

Flow (see docs/whatsapp/): Meta WhatsApp Cloud API -> n8n workflow ->
POST /api/integrations/whatsapp/inbound/ (this handler) -> n8n sends the
returned `reply` text back to the user via Meta.

n8n owns the transport (webhook verification, sending replies). Media can
arrive either already base64-encoded, or as a WhatsApp media id that Baseera
downloads itself (download_whatsapp_media) so the n8n workflow stays trivial.
Baseera owns the intelligence: it resolves the sender by phone,
runs whatever they sent (a photo of a ledger/receipt, a bank-statement
CSV, an Excel sheet) through the SAME validation + processing + insight
engines the web app uses, and returns one short, WhatsApp-ready reply --
the biggest money leak or the cash-flow snapshot, computed from their real
rows, never invented.
"""
import os
import re
import json
import logging

logger = logging.getLogger(__name__)

DASHBOARD_URL = os.environ.get("BASEERA_PUBLIC_URL", "https://baseera.it.com").rstrip("/") + "/dashboard/"

_MIME_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "application/pdf": ".pdf", "text/csv": ".csv", "text/plain": ".txt",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}


def _fmt(n):
    try:
        return f"{round(float(n)):,}"
    except (TypeError, ValueError):
        return str(n)


def _record_whatsapp_failure(user, phone, status, detail=""):
    """
    Make WhatsApp processing/reply failures visible instead of silent: write a
    SystemLog row (shown in admin logs) and report to Sentry if configured.
    Best-effort -- monitoring must never itself break the reply path.
    """
    try:
        from dashboard.models import SystemLog
        SystemLog.objects.create(
            user=user if getattr(user, "id", None) else None,
            action_type="واتساب / WhatsApp Failure",
            details=f"فشل معالجة رسالة واتساب ({status}) من {normalize_phone(phone)}: {detail}"[:500],
        )
    except Exception:
        pass
    try:
        import sentry_sdk
        sentry_sdk.capture_message(f"WhatsApp failure [{status}] from {normalize_phone(phone)}: {detail}", level="warning")
    except Exception:
        pass
    logger.warning("WhatsApp failure [%s] from %s: %s", status, normalize_phone(phone), detail)


def normalize_phone(phone):
    """Digits only, so numbers stored with/without country code or spaces still match."""
    return re.sub(r"\D", "", str(phone or ""))


def push_whatsapp_message(phone, message):
    """
    Best-effort OUTBOUND WhatsApp message via the configured n8n outbound
    webhook -- the same channel the weekly pulse uses -- so the agent can
    reach the user on WhatsApp without being asked (proactive alerts).
    Returns True on success, False otherwise. Never raises; a missing webhook
    or phone is just a no-op.
    """
    import urllib.request

    url = os.environ.get("WHATSAPP_OUTBOUND_WEBHOOK_URL", "").strip()
    ph = normalize_phone(phone)
    if not url or not ph or not (message or "").strip():
        return False
    try:
        data = json.dumps({"phone": ph, "message": message}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception as e:
        logger.info("WhatsApp outbound push failed for %s: %s", phone, e)
        return False


def send_whatsapp_reply(phone, message):
    """
    Send a text message to a user via the Meta WhatsApp Cloud API directly --
    this is what lets Baseera reply without n8n. Needs WHATSAPP_GRAPH_TOKEN and
    WHATSAPP_PHONE_NUMBER_ID in the env. Returns True on success. Never raises.
    """
    import urllib.request

    token = os.environ.get("WHATSAPP_GRAPH_TOKEN", "").strip()
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    ph = normalize_phone(phone)
    if not token or not phone_id or not ph or not (message or "").strip():
        return False
    version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v20.0").strip() or "v20.0"
    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": ph,
        "type": "text",
        "text": {"body": message[:4096]},
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception as e:
        logger.info("WhatsApp send reply failed for %s: %s", phone, e)
        return False


def send_whatsapp_image(phone, image_bytes, caption=""):
    """
    Send an image to a user over the Meta WhatsApp Cloud API: first upload the
    bytes to the /media endpoint (multipart), then send an image message by the
    returned media id. Needs WHATSAPP_GRAPH_TOKEN + WHATSAPP_PHONE_NUMBER_ID.
    Returns True on success. Never raises.
    """
    import urllib.request
    import uuid

    token = os.environ.get("WHATSAPP_GRAPH_TOKEN", "").strip()
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    ph = normalize_phone(phone)
    if not token or not phone_id or not ph or not image_bytes:
        return False
    version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v20.0").strip() or "v20.0"
    try:
        # 1) upload media (multipart/form-data)
        boundary = "----baseera" + uuid.uuid4().hex
        parts = []
        for k, v in (("messaging_product", "whatsapp"), ("type", "image/png")):
            parts.append(("--" + boundary).encode())
            parts.append(('Content-Disposition: form-data; name="%s"' % k).encode())
            parts.append(b"")
            parts.append(str(v).encode())
        parts.append(("--" + boundary).encode())
        parts.append(b'Content-Disposition: form-data; name="file"; filename="summary.png"')
        parts.append(b"Content-Type: image/png")
        parts.append(b"")
        parts.append(image_bytes)
        parts.append(("--" + boundary + "--").encode())
        parts.append(b"")
        body = b"\r\n".join(parts)
        up = urllib.request.Request(
            f"https://graph.facebook.com/{version}/{phone_id}/media",
            data=body, method="POST",
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "multipart/form-data; boundary=" + boundary,
            },
        )
        with urllib.request.urlopen(up, timeout=30) as resp:
            media = json.loads(resp.read().decode("utf-8") or "{}")
        media_id = media.get("id")
        if not media_id:
            return False

        # 2) send the image message
        payload = {
            "messaging_product": "whatsapp",
            "to": ph,
            "type": "image",
            "image": {"id": media_id, "caption": (caption or "")[:1024]},
        }
        send = urllib.request.Request(
            f"https://graph.facebook.com/{version}/{phone_id}/messages",
            data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(send, timeout=20) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception as e:
        logger.info("WhatsApp send image failed for %s: %s", phone, e)
        return False


def transcribe_audio(audio_bytes, mime="audio/ogg"):
    """
    Transcribe a WhatsApp voice note to Arabic text with Gemini, so a spoken
    question is handled exactly like a typed one. Returns the transcript, or
    None. Never raises.
    """
    if not audio_bytes:
        return None
    try:
        from dashboard.services.ai_service import GeminiAIService, GEMINI_MODEL
        from google.genai import types
    except Exception:
        return None
    ai = GeminiAIService()
    if not getattr(ai, "client", None):
        return None
    try:
        clean_mime = (mime or "audio/ogg").split(";")[0].strip() or "audio/ogg"
        part = types.Part.from_bytes(data=audio_bytes, mime_type=clean_mime)
        prompt = (
            "حوّل هذه الرسالة الصوتية إلى نص عربي حرفي فقط، بدون أي مقدمة أو تعليق "
            "أو علامات اقتباس. إن لم يكن فيها كلام واضح، أعد نصاً فارغاً."
        )
        resp = ai.client.models.generate_content(model=GEMINI_MODEL, contents=[prompt, part])
        text = (getattr(resp, "text", "") or "").strip()
        return text or None
    except Exception as e:
        logger.info("audio transcription failed: %s", e)
        return None


def parse_meta_messages(payload):
    """
    Pull the user messages out of a raw Meta WhatsApp Cloud API webhook body.
    Returns a list of {phone, text, media_id, mtype} -- media_id is set for a
    photo/document/audio/video, text for a text message, and mtype carries the
    original message type so audio can be routed to transcription. Ignores
    delivery/read status callbacks (which carry no `messages`). Never raises.
    """
    out = []
    try:
        for entry in (payload.get("entry") or []):
            for change in (entry.get("changes") or []):
                value = change.get("value") or {}
                for msg in (value.get("messages") or []):
                    phone = msg.get("from")
                    if not phone:
                        continue
                    mtype = msg.get("type")
                    text = None
                    media_id = None
                    if mtype == "text":
                        text = ((msg.get("text") or {}).get("body")) or None
                    elif mtype in ("image", "document", "audio", "voice", "video"):
                        media_id = ((msg.get(mtype) or {}).get("id")) or None
                    out.append({"phone": phone, "text": text, "media_id": media_id, "mtype": mtype})
    except Exception as e:
        logger.info("parse_meta_messages failed: %s", e)
    return out


def process_and_reply(phone, text=None, media_id=None, media_type=None):
    """
    The full inbound pipeline for one message arriving straight from Meta (no
    n8n): download the media if a media_id was given, run the shared
    handle_inbound engine, then send the reply back to the user. A voice note
    is transcribed first and handled as a spoken question. Never raises.
    """
    try:
        media_bytes = None
        media_mime = None
        if media_id:
            media_bytes, media_mime = download_whatsapp_media(media_id)
            if media_type in ("audio", "voice") and media_bytes:
                # Spoken question -> transcribe, then treat as text (don't run
                # audio through the file/financial pipeline).
                transcript = transcribe_audio(media_bytes, media_mime)
                if transcript:
                    text = (text + "\n" + transcript) if text else transcript
                media_bytes = None
                media_mime = None
        result = handle_inbound(phone, text=text, media_bytes=media_bytes, media_mime=media_mime)
        reply = (result or {}).get("reply")
        if reply:
            send_whatsapp_reply(phone, reply)

        # Generative visual: if the user asked for a chart/report/image and has
        # data, render a real summary card from their own numbers and send it
        # as an image -- the same figures the dashboard shows, not a fabricated
        # picture. Best-effort; never blocks the text reply.
        try:
            from dashboard.services.whatsapp_visual import wants_visual, render_summary_image
            if text and wants_visual(text):
                user = resolve_user_by_phone(phone)
                if user:
                    from dashboard.models import DynamicRecord
                    rows = list(
                        DynamicRecord.objects.filter(user=user)
                        .values_list("row_data", flat=True)[:10000]
                    )
                    png = render_summary_image(rows)
                    if png:
                        send_whatsapp_image(phone, png, caption="ملخّصك المالي 📊 (بيانات حقيقية من ملفاتك)")
        except Exception as viz_err:
            logger.info("WhatsApp visual generation skipped: %s", viz_err)

        return result
    except Exception as e:
        logger.exception("process_and_reply failed: %s", e)
        return {"status": "error"}


def resolve_user_by_phone(phone):
    """
    Match a WhatsApp sender to a Baseera user by the phone on their Profile.
    Compares the last 8 digits (an Omani local number) so a country-code
    prefix (968...) on one side but not the other still resolves.
    """
    from dashboard.models import Profile

    norm = normalize_phone(phone)
    if len(norm) < 8:
        return None
    suffix = norm[-8:]
    for profile in Profile.objects.exclude(phone_number="").select_related("user").iterator():
        stored = normalize_phone(profile.phone_number)
        if stored and stored[-8:] == suffix:
            return profile.user
    return None


def _ext_from_mime(media_mime):
    return _MIME_EXT.get((media_mime or "").split(";")[0].strip().lower(), ".jpg")


def download_whatsapp_media(media_id):
    """
    Fetch the actual bytes of a WhatsApp media object by its id, so a photo
    of a receipt/ledger sent over WhatsApp reaches the SAME processing engines
    the web upload uses -- instead of n8n having to download + base64 it.

    Two-step Meta Cloud API dance: GET /{media_id} returns a short-lived,
    authenticated URL + mime type; a second GET on that URL (same Bearer
    token) returns the binary. Requires WHATSAPP_GRAPH_TOKEN in the env (the
    same permanent token n8n uses to send replies); WHATSAPP_GRAPH_VERSION is
    optional (defaults to a current Graph version).

    Returns (bytes, mime_type) on success, or (None, None) on any failure --
    never raises, so a missing token or a Meta hiccup just means "no media"
    and the caller falls back to asking the user to resend.
    """
    import urllib.request

    token = os.environ.get("WHATSAPP_GRAPH_TOKEN", "").strip()
    if not token or not media_id:
        return None, None
    version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v20.0").strip() or "v20.0"
    auth = {"Authorization": "Bearer " + token}
    try:
        meta_req = urllib.request.Request(
            f"https://graph.facebook.com/{version}/{media_id}", headers=auth, method="GET",
        )
        with urllib.request.urlopen(meta_req, timeout=15) as resp:
            meta = json.loads(resp.read().decode("utf-8") or "{}")
        url = meta.get("url")
        mime = meta.get("mime_type")
        if not url:
            return None, None
        # Meta requires the Bearer token on the media URL fetch too.
        bin_req = urllib.request.Request(url, headers=auth, method="GET")
        with urllib.request.urlopen(bin_req, timeout=30) as resp:
            data = resp.read()
        if not data or len(data) > 20 * 1024 * 1024:
            return None, None
        return data, mime
    except Exception as e:
        logger.info("WhatsApp media download failed for %s: %s", media_id, e)
        return None, None


def build_reply_from_rows(rows, currency="ر.ع"):
    """One short WhatsApp reply: waste -> cash-flow -> glance, all from real rows."""
    from dashboard.services.waste_analyzer import compute_waste_signals
    from dashboard.services.first_win_insights import compute_transaction_signal, compute_glance

    computed = compute_waste_signals(rows)
    signals = sorted(
        computed.get("signals", []) or [],
        key=lambda s: s.get("currency_amount", 0) or 0, reverse=True,
    )
    money = [s for s in signals if (s.get("currency_amount") or 0) > 0]
    if money:
        top = money[0]
        return (
            f"⚠️ اكتشفنا هدراً في بياناتك!\n\n"
            f"*{_fmt(computed['total_waste'])} {currency}* — أكبر مصدر: {top['title']}.\n"
            f"محسوب من {top.get('evidence_count', 0)} حالة فعلية في ملفك — لا تخمين.\n\n"
            f"شوف التفاصيل والخطة 👇\n{DASHBOARD_URL}"
        )

    cf = compute_transaction_signal(rows)
    if cf:
        top = (cf.get("top_groups") or [None])[0]
        detail = (
            f"أكبر مصروف: {top['name']} — *{_fmt(top['total'])} {currency}*"
            if top else f"إجمالي المصروف: *{_fmt(cf['total_expense'])} {currency}*"
        )
        return (
            f"💡 نظرة على تدفّقك النقدي:\n\n"
            f"الدخل: {_fmt(cf['total_income'])} {currency}\n"
            f"المصروف: {_fmt(cf['total_expense'])} {currency}\n"
            f"الصافي: {_fmt(cf['net'])} {currency}\n"
            f"{detail}\n\n"
            f"لوحتك الكاملة 👇\n{DASHBOARD_URL}"
        )

    glance = compute_glance(rows)
    if glance:
        return (
            f"✅ استلمنا بياناتك ({_fmt(glance['row_count'])} عملية).\n"
            f"لكشف الهدر أو التدفّق النقدي تلقائياً، تأكد أن الملف فيه *السعر والتكلفة*، "
            f"أو *المبلغ والنوع* (دخل/مصروف).\n\n{DASHBOARD_URL}"
        )

    return f"استلمنا رسالتك، لكن تعذّر استخراج بيانات مالية واضحة منها. جرّب صورة أوضح أو ملف Excel.\n{DASHBOARD_URL}"


def _recent_file_context(user, max_rows=60):
    """A compact sample of the user's most recent uploaded rows, to ground
    the agent's WhatsApp reply in real data (never invented)."""
    from dashboard.models import ProjectFile, DynamicRecord

    latest = ProjectFile.objects.filter(user=user).order_by("-uploaded_at").first()
    if not latest:
        return ""
    rows = list(
        DynamicRecord.objects.filter(user=user, project_file=latest)
        .values_list("row_data", flat=True)[:max_rows]
    )
    if not rows:
        return ""
    # Redact identifying/sensitive fields before this leaves for Gemini.
    from dashboard.services.privacy import redacted_json
    return redacted_json(rows, cap=max_rows, max_chars=6000)


def generate_agent_reply(user, message, lang="ar"):
    """
    A real, grounded agent answer for a WhatsApp text message -- the same
    Baseera agent persona the web "اسأل بصيرة" chat uses, but shaped for
    WhatsApp (short, plain text). Returns None when the AI client is
    unavailable (no GEMINI_API_KEY) so the caller can fall back gracefully.
    """
    try:
        from dashboard.services.ai_service import GeminiAIService, GEMINI_MODEL
        from dashboard.security import sanitize_cell_for_prompt
    except Exception:
        return None

    ai = GeminiAIService()
    if not getattr(ai, "client", None):
        return None

    # Cost guardrail: cap Gemini-backed replies per user/day and globally.
    # Over the cap we return None so the caller falls back to the static help
    # message instead of making an (uncapped) paid call.
    try:
        from dashboard.services.ai_quota import check_gemini_quota
        if not check_gemini_quota(user_id=getattr(user, "id", None)):
            return None
    except Exception:
        pass

    safe_msg = sanitize_cell_for_prompt(message or "", max_len=1200)
    file_context = _recent_file_context(user)
    try:
        meta = ai.get_agent_meta("general", user_id=user.id, lang=lang)
        persona = meta["system_prompt_ar"] if lang == "ar" else meta["system_prompt_en"]
    except Exception:
        persona = "أنت بصيرة، المحلل المالي الذكي." if lang == "ar" else "You are Baseera, the smart financial analyst."

    if lang == "ar":
        wa_rules = (
            "\n\nأنت الآن تتحدث مع صاحب المنشأة عبر واتساب، مثل صديق ومستشار عُماني — مو موظف رسمي. قواعد إلزامية:\n"
            "- تكلّم باللهجة العُمانية الدارجة الطبيعية، بأسلوب ودود ودافئ وبسيط (مثل: هلا والله، تمام، أبشر، على راسي، وش أقدر أساعدك، لا هنت).\n"
            "- خلّ ردك قصير (سطر إلى ٣ أسطر)، بلا جداول ولا رموز Markdown ولا أكواد ولا لغة رسمية جافة.\n"
            "- إذا سلّم عليك أو سولف كلام عام (سلام، كيف الحال، شكراً)، ردّ عليه بترحيب طبيعي وسولف معه بلطف — ولا تقحم أرقاماً مالية إلا إذا سأل عن ماليته أو رفع ملفاً.\n"
            "- لا تختلق أي رقم أبداً؛ استخدم بيانات المستخدم أدناه فقط، وفقط لو كان سؤاله متعلقاً بماليته.\n"
            "- إذا ذكر أنه أرسل صورة أو ملفاً ولا توجد بيانات جديدة عندك، لا تدّعِ أبداً أنك حلّلته أو تعطيه أرقاماً — قل له بصدق ولطف إن الملف ما وصلك واطلب منه يعيد إرساله.\n"
            "- إذا ما كفت البيانات، اطلب منه بلطف يرفع ملف أو صورة، بدون ما تفبرك.\n"
        )
        tail = f"\n\nبيانات المستخدم المخزّنة (استخدمها فقط إذا كان سؤاله عن ماليته، وإلا تجاهلها وسولف معه طبيعي):\n{file_context or 'لا توجد بيانات مرفوعة بعد.'}\n\nرسالة المستخدم: {safe_msg}\n\nردك القصير باللهجة العُمانية:"
    else:
        wa_rules = (
            "\n\nYou are replying over WhatsApp. Mandatory rules:\n"
            "- Keep it very short (max 2-4 lines), no tables, no Markdown headings, no code.\n"
            "- Rely ONLY on the user's data below; never invent a number.\n"
            "- If the data is insufficient, briefly ask them to upload a file/photo.\n"
        )
        tail = f"\n\nUser data (JSON sample):\n{file_context or 'No data uploaded yet.'}\n\nUser question: {safe_msg}\n\nYour short reply:"

    prompt = persona + wa_rules + tail

    # Agent parity with the web "اسأل بصيرة" chat: when the question plausibly
    # needs a real tool (compute runway/cashflow, count rows, save a memory,
    # raise a reminder...), run the SAME bounded ReAct pre-loop the website
    # uses -- so a WhatsApp answer is grounded in the same agents/tools and
    # gives an identical result to the dashboard, not a lighter separate
    # reply path. Gated by should_attempt_react() and never raises, so an
    # ordinary chat/greeting skips it with zero added latency or cost.
    try:
        from dashboard.services.agent_tools import should_attempt_react, run_react_preloop
        if should_attempt_react(message or ""):
            prompt = run_react_preloop(
                ai, prompt, getattr(user, "id", None), GEMINI_MODEL, lang=lang,
            )
    except Exception as e:
        logger.info("WhatsApp ReAct pre-loop skipped: %s", e)

    try:
        resp = ai.client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (getattr(resp, "text", "") or "").strip()
        return text or None
    except Exception as e:
        logger.info("WhatsApp agent reply generation failed: %s", e)
        return None


def handle_inbound(phone, text=None, media_bytes=None, media_mime=None):
    """
    Returns {"status": <str>, "reply": <str>} for n8n to send back.
    Never raises: any failure becomes a friendly reply.
    """
    try:
        user = resolve_user_by_phone(phone)
        if not user:
            return {
                "status": "unregistered",
                "reply": (
                    "مرحباً بك في بصيرة 👋\n"
                    "رقمك غير مسجّل بعد. سجّل مؤسستك على baseera.it.com بنفس رقم واتساب هذا، "
                    "ثم أرسل صورة كشف حسابك أو مبيعاتك هنا وسأحللها فوراً."
                ),
            }

        if media_bytes:
            return _handle_media(user, phone, media_bytes, media_mime)

        t = (text or "").strip()
        if not t:
            return {"status": "empty", "reply": "هلا والله 👋 إذا تبي أساعدك، طرّش لي صورة كشف حسابك أو فاتورة أو ملف Excel، وأكشف لك وين تروح فلوسك."}

        # Let the real agent answer the customer's question, grounded in their
        # own uploaded data -- the same persona as the web "اسأل بصيرة" chat.
        agent_reply = generate_agent_reply(user, t, lang="ar")
        if agent_reply:
            try:
                from dashboard.models import SystemLog
                SystemLog.objects.create(
                    user=user, action_type="واتساب / WhatsApp Chat",
                    details=f"رد الوكيل على رسالة واتساب من {normalize_phone(phone)}.",
                )
            except Exception:
                pass
            return {"status": "agent", "reply": agent_reply}

        # Fallback when the AI client is unavailable (no GEMINI_API_KEY):
        return {
            "status": "text",
            "reply": (
                "هلا والله 👋 أنا بصيرة، مستشارك المالي.\n"
                "طرّش لي صورة كشف حسابك أو فاتورة أو ملف Excel، وأكشف لك أكبر تسريب في فلوسك بثواني.\n\n"
                f"لوحتك: {DASHBOARD_URL}"
            ),
        }
    except Exception as exc:
        logger.exception("WhatsApp inbound failed: %s", exc)
        _record_whatsapp_failure(locals().get("user"), phone, "error", str(exc))
        return {"status": "error", "reply": "حدث خطأ مؤقت أثناء المعالجة. حاول مرة أخرى بعد قليل."}


def _handle_media(user, phone, media_bytes, media_mime):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from dashboard.security import build_safe_filename
    from dashboard.services.validation_service import validate_financial_file
    from dashboard.views import process_excel_to_db
    from dashboard.models import ProjectFile, DynamicRecord, SystemLog

    if len(media_bytes) > 20 * 1024 * 1024:
        return {"status": "too_large", "reply": "الملف كبير جداً (الحد 20 ميجابايت). أرسل نسخة أصغر."}

    fname = build_safe_filename("whatsapp_upload" + _ext_from_mime(media_mime))
    upload = SimpleUploadedFile(fname, media_bytes, content_type=(media_mime or "application/octet-stream"))

    validation = validate_financial_file(upload)
    if not validation.get("is_valid"):
        _record_whatsapp_failure(user, phone, "invalid", validation.get("reason", "validation failed"))
        return {
            "status": "invalid",
            "reply": "تعذّر قراءة ما أرسلته كبيانات مالية. جرّب صورة أوضح لكشف الحساب/الفاتورة، أو أرسل ملف Excel/CSV.",
        }

    upload.seek(0)
    upload.name = fname
    project_file = ProjectFile.objects.create(
        user=user, excel_file=upload, document_type=validation.get("document_type"),
    )
    ok, err = process_excel_to_db(
        project_file, user, validation.get("accepted_sheets"),
        extracted_rows=validation.get("extracted_rows"),
    )
    if not ok:
        project_file.delete()
        _record_whatsapp_failure(user, phone, "process_error", str(err))
        return {"status": "error", "reply": "تعذّرت معالجة الملف. تأكد من وضوح البيانات وحاول مجدداً."}

    try:
        from dashboard.services.retrieval_service import index_accepted_sheets
        index_accepted_sheets(project_file, validation.get("accepted_sheets"))
    except Exception as idx_err:
        logger.info("WhatsApp retrieval indexing skipped: %s", idx_err)

    # Archive the incoming document: fingerprint it for integrity and open its
    # lifecycle (a photographed invoice/receipt is AI-read, so it lands in
    # needs_review pending a human confirm; a structured file is 'received').
    try:
        from dashboard.services.archiving import stamp_document
        stamp_document(project_file, raw_bytes=media_bytes, user=user)
    except Exception as arch_err:
        logger.info("WhatsApp archiving stamp skipped: %s", arch_err)

    rows = list(
        DynamicRecord.objects.filter(user=user, project_file=project_file)
        .values_list("row_data", flat=True)[:10000]
    )
    try:
        SystemLog.objects.create(
            user=user, action_type="واتساب / WhatsApp",
            details=f"عولجت رسالة واتساب من {normalize_phone(phone)} (ملف #{project_file.id}).",
        )
    except Exception:
        pass

    return {"status": "success", "reply": build_reply_from_rows(rows)}
