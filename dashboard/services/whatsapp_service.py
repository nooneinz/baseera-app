"""
WhatsApp inbound handler — the single entry point n8n calls.

Flow (see docs/whatsapp/): Meta WhatsApp Cloud API -> n8n workflow ->
POST /api/integrations/whatsapp/inbound/ (this handler) -> n8n sends the
returned `reply` text back to the user via Meta.

n8n owns the transport (webhook verification, media download, sending
replies). Baseera owns the intelligence: it resolves the sender by phone,
runs whatever they sent (a photo of a ledger/receipt, a bank-statement
CSV, an Excel sheet) through the SAME validation + processing + insight
engines the web app uses, and returns one short, WhatsApp-ready reply --
the biggest money leak or the cash-flow snapshot, computed from their real
rows, never invented.
"""
import os
import re
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


def normalize_phone(phone):
    """Digits only, so numbers stored with/without country code or spaces still match."""
    return re.sub(r"\D", "", str(phone or ""))


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

    safe_msg = sanitize_cell_for_prompt(message or "", max_len=1200)
    file_context = _recent_file_context(user)
    try:
        meta = ai.get_agent_meta("general", user_id=user.id, lang=lang)
        persona = meta["system_prompt_ar"] if lang == "ar" else meta["system_prompt_en"]
    except Exception:
        persona = "أنت بصيرة، المحلل المالي الذكي." if lang == "ar" else "You are Baseera, the smart financial analyst."

    if lang == "ar":
        wa_rules = (
            "\n\nأنت الآن ترد عبر واتساب. قواعد إلزامية:\n"
            "- اجعل الرد قصيراً جداً (٢-٤ أسطر كحد أقصى)، بلا جداول ولا عناوين Markdown ولا أكواد.\n"
            "- استند حصراً إلى بيانات المستخدم أدناه؛ لا تختلق أي رقم غير موجود فيها.\n"
            "- إن لم تكفِ البيانات للإجابة، اطلب منه رفع ملف/صورة بإيجاز.\n"
        )
        tail = f"\n\nبيانات المستخدم (عينة JSON):\n{file_context or 'لا توجد بيانات مرفوعة بعد.'}\n\nسؤال المستخدم: {safe_msg}\n\nردك المختصر:"
    else:
        wa_rules = (
            "\n\nYou are replying over WhatsApp. Mandatory rules:\n"
            "- Keep it very short (max 2-4 lines), no tables, no Markdown headings, no code.\n"
            "- Rely ONLY on the user's data below; never invent a number.\n"
            "- If the data is insufficient, briefly ask them to upload a file/photo.\n"
        )
        tail = f"\n\nUser data (JSON sample):\n{file_context or 'No data uploaded yet.'}\n\nUser question: {safe_msg}\n\nYour short reply:"

    prompt = persona + wa_rules + tail
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
            return {"status": "empty", "reply": "أرسل *صورة* كشف حساب أو فاتورة، أو ملف Excel، وسأكشف لك أين تخسر فلوسك 📊"}

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
                "أهلاً! 👋 أنا بصيرة، محللك المالي.\n"
                "أرسل *صورة* كشف حساب أو فاتورة أو دفترك، أو ملف Excel/CSV، "
                "وسأكشف لك أكبر تسريب مالي في ثوانٍ.\n\n"
                f"لوحتك: {DASHBOARD_URL}"
            ),
        }
    except Exception as exc:
        logger.exception("WhatsApp inbound failed: %s", exc)
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
        return {"status": "error", "reply": "تعذّرت معالجة الملف. تأكد من وضوح البيانات وحاول مجدداً."}

    try:
        from dashboard.services.retrieval_service import index_accepted_sheets
        index_accepted_sheets(project_file, validation.get("accepted_sheets"))
    except Exception as idx_err:
        logger.info("WhatsApp retrieval indexing skipped: %s", idx_err)

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
