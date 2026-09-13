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
