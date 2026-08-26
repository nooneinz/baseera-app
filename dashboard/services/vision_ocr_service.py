"""
Financial Vision OCR — reads photographed/handwritten financial documents
(bank statement photos, receipts, checks, handwritten ledger pages) and
rejects anything that is not genuinely financial (a random personal photo,
a landscape, a screenshot of something unrelated).

This extends the Validation Layer (dashboard/services/validation_service)
to image uploads. Excel/CSV/PDF validation there is deterministic
(pandas/pdfplumber); an image has no structured cells to parse, so the
only way to tell "a real financial document" apart from "an arbitrary
photo" is to actually look at it — hence a vision-model pass here.

Nothing about this pass is allowed to invent numbers: it either reads a
real value off the page, or it leaves that field out. The extracted rows
feed the same DynamicRecord pipeline as a spreadsheet row, so every later
layer (Retrieval, Reconciliation, dashboard charts) treats them exactly
the same as data typed into a real Excel file.
"""
import json
import logging

logger = logging.getLogger(__name__)

VISION_MODEL = "gemini-2.5-flash"

REJECT_NOT_FINANCIAL_AR = (
    "هذه الصورة لا تبدو مستنداً مالياً (فاتورة، إيصال، كشف حساب، أو دفتر محاسبي "
    "يدوي). يرجى رفع صورة واضحة لمستند مالي فقط."
)
REJECT_NOT_FINANCIAL_EN = (
    "This image does not appear to be a financial document (invoice, receipt, "
    "bank statement, or a handwritten ledger page). Please upload a clear photo "
    "of a financial document only."
)
REJECT_UNREADABLE_AR = (
    "تعذّر قراءة أي بيانات واضحة من هذه الصورة (قد تكون ضبابية أو الخط غير مقروء). "
    "حاول التقاط صورة أوضح."
)
REJECT_UNREADABLE_EN = (
    "No clear data could be read from this image (it may be blurry or the "
    "handwriting illegible). Try a clearer photo."
)
SERVICE_UNAVAILABLE_AR = (
    "خدمة تحليل الصور بالذكاء الاصطناعي غير متاحة حالياً. يرجى المحاولة لاحقاً "
    "أو رفع الملف بصيغة Excel أو CSV أو PDF بدلاً من الصورة."
)
SERVICE_UNAVAILABLE_EN = (
    "The AI image-analysis service is currently unavailable. Please try again "
    "later, or upload the file as Excel, CSV, or PDF instead of an image."
)

_VISION_PROMPT = """You are a meticulous financial document OCR engine. You will be shown ONE image.

First decide: is this image genuinely a financial document? That includes a
printed or handwritten bank statement, receipt, invoice, check, or a
handwritten ledger/notebook page recording transactions, amounts, or
balances (any language, any handwriting quality). It does NOT include a
selfie, a random object, a landscape, a screenshot of an app/website with no
financial figures, or anything without real numeric financial content.

If it is NOT a financial document, or the content is too blurry/illegible to
extract anything reliably, respond with exactly:
{"is_financial": false, "readable": <true if a document but illegible, else false>, "rows": [], "reason": "<one short sentence in Arabic explaining what you actually see>"}

If it IS a financial document you can read, extract every transaction/line
item you can actually see into structured rows. NEVER invent a value you
cannot read — omit that field for that row instead of guessing. Respond with
exactly this JSON shape (no markdown fences, no extra text):
{
  "is_financial": true,
  "readable": true,
  "document_type": "receipt|invoice|bank_statement|check|handwritten_ledger|other",
  "rows": [
    {"date": "YYYY-MM-DD or null if unreadable", "description": "string or null", "amount": <number or null>, "type": "credit|debit|null"}
  ],
  "reason": ""
}
"""


def ocr_extract_financial_rows(file_obj, ai_service=None):
    """
    Runs the vision pass on an uploaded image file.

    Args:
        file_obj: a Django UploadedFile (or any object with .read()/.seek())
                   positioned at a real image.
        ai_service: a GeminiAIService instance (or any object exposing
                    .client.models.generate_content), or None.

    Returns a dict:
      {
        "status": "accept" | "warning" | "reject",
        "message": str,
        "is_financial": bool | None,   # None only when the service was unavailable
        "document_type": str,
        "rows": [ {date, description, amount, type}, ... ],
      }
    """
    result = {
        "status": "reject",
        "message": "",
        "is_financial": None,
        "document_type": "other",
        "rows": [],
    }

    client = getattr(ai_service, "client", None) if ai_service is not None else None
    if not client:
        result["message"] = SERVICE_UNAVAILABLE_AR
        return result

    try:
        import PIL.Image
        file_obj.seek(0)
        img = PIL.Image.open(file_obj)
        img.load()  # force-read now, while the file handle is still open
    except Exception:
        logger.exception("Could not open uploaded image for OCR")
        result["message"] = REJECT_UNREADABLE_AR
        return result
    finally:
        try:
            file_obj.seek(0)
        except Exception:
            pass

    try:
        response = client.models.generate_content(
            model=VISION_MODEL,
            contents=[img, _VISION_PROMPT],
        )
        text = (response.text or "").strip()
        if text.startswith("```json"):
            text = text.replace("```json", "", 1)
        if text.startswith("```"):
            text = text.replace("```", "", 1)
        if text.endswith("```"):
            text = text[:-3]
        data = json.loads(text.strip())
    except Exception as e:
        logger.info("Vision OCR call failed: %s", e)
        result["message"] = SERVICE_UNAVAILABLE_AR
        return result

    is_financial = bool(data.get("is_financial"))
    readable = bool(data.get("readable"))
    raw_rows = data.get("rows") or []
    reason = str(data.get("reason") or "").strip()

    result["is_financial"] = is_financial
    result["document_type"] = str(data.get("document_type") or "other")

    if not is_financial:
        result["status"] = "reject"
        result["message"] = REJECT_NOT_FINANCIAL_AR + (f" ({reason})" if reason else "")
        return result

    if not readable or not raw_rows:
        result["status"] = "reject"
        result["message"] = REJECT_UNREADABLE_AR + (f" ({reason})" if reason else "")
        return result

    # Normalize rows: keep only ones with at least a real value, drop the
    # model's own placeholder nulls rather than passing them downstream as
    # if they were confirmed empty fields.
    clean_rows = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        clean = {k: v for k, v in row.items() if v not in (None, "", "null")}
        if clean:
            clean_rows.append(clean)

    if not clean_rows:
        result["status"] = "reject"
        result["message"] = REJECT_UNREADABLE_AR
        return result

    result["rows"] = clean_rows

    unreadable_field_count = sum(
        1 for row in raw_rows if isinstance(row, dict) and (row.get("amount") in (None, "") or row.get("date") in (None, ""))
    )
    if unreadable_field_count > len(raw_rows) * 0.5:
        result["status"] = "warning"
        result["message"] = (
            f"تم قبول الصورة، لكن بعض القيم لم تكن واضحة بما يكفي للقراءة "
            f"({unreadable_field_count} من {len(raw_rows)} سطراً). يُفضّل مراجعة البيانات يدوياً."
        )
    else:
        result["status"] = "accept"
        result["message"] = f"تم استخراج {len(clean_rows)} حركة مالية من الصورة بنجاح."

    return result
