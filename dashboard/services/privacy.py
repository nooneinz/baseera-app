"""
privacy.py — Redact identifying / sensitive fields before any business data
leaves Baseera's own compute for an external model (Gemini).

Baseera's promise to customers is: *"we never send your raw financial data to a
third party — only an anonymized summary, so the AI can explain it."* This
module is what makes that literally true. Every place that builds a prompt from
a user's uploaded rows routes the rows through :func:`redact_rows_for_ai` first,
so a customer name, phone number, bank-account/IBAN, national ID, tax/CR number,
email or address is masked *before* it is serialized into a prompt.

What is kept: the numbers, categories, dates and descriptions the agent needs to
ground its explanation. What is removed: anything that identifies a person, an
account, or contact details.

Important: the financial *math* is always done in Python on the full,
un-redacted rows (waste_analyzer / first_win_insights / …). Redaction applies
ONLY to the text handed to the model — never to a computation.
"""
import re

# Column headers (AR + EN, matched case-insensitively as substrings) whose
# whole value is identifying and must be masked.
_SENSITIVE_HEADERS = (
    # names / parties
    "name", "customer", "client", "supplier", "vendor", "employee", "staff",
    "اسم", "العميل", "الزبون", "المورد", "المورّد", "الموظف", "موظف", "الزبائن",
    # contact
    "phone", "mobile", "tel", "whatsapp", "email", "mail", "fax",
    "جوال", "هاتف", "موبايل", "واتساب", "واتس", "بريد", "ايميل", "إيميل", "فاكس",
    # identity / registration
    "national", "civil", "passport", "iban", "vat", "tax",
    "هوية", "الهوية", "بطاقة", "مدني", "جواز", "سجل تجاري", "السجل", "ضريبي",
    "الرقم الضريبي", "آيبان", "ايبان",
    # account / card / address
    "account", "card", "address", "location",
    "حساب", "الحساب", "رقم الحساب", "ائتمان", "عنوان", "العنوان", "موقع", "الحي", "شارع",
)

MASK = "[محجوب]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# IBAN-like: 2 letters + 2 digits + up to 30 alphanumerics.
_IBAN_RE = re.compile(r"\b[A-Za-z]{2}\d{2}[A-Za-z0-9]{10,30}\b")
# A run of 9+ digits (possibly space/dash grouped) = account / card / phone.
_LONGNUM_RE = re.compile(r"\b(?:\d[ \-]?){9,}\b")


def _header_is_sensitive(key):
    k = str(key).strip().lower()
    return any(token in k for token in _SENSITIVE_HEADERS)


def _mask_value_text(value):
    """Mask identifiers that appear *inside* a value, regardless of its column
    (defense in depth): emails, IBANs, and long digit runs. Short numbers such
    as amounts (< 9 digits) are left untouched so the agent can still reason
    about them."""
    if not isinstance(value, str):
        return value
    masked = _EMAIL_RE.sub(MASK, value)
    masked = _IBAN_RE.sub(MASK, masked)
    masked = _LONGNUM_RE.sub(MASK, masked)
    return masked


def redact_row(row):
    """Return a redacted copy of a single row dict."""
    if not isinstance(row, dict):
        return row
    out = {}
    for key, value in row.items():
        if _header_is_sensitive(key):
            out[key] = MASK
        else:
            out[key] = _mask_value_text(value)
    return out


def redact_rows_for_ai(rows):
    """Redact a list of row dicts before it is sent to an external model."""
    return [redact_row(r) for r in (rows or [])]


def redacted_json(rows, cap=100, max_chars=None):
    """Convenience: redact up to ``cap`` rows and return a JSON string, capped
    to ``max_chars`` if given. Returns "" on empty input or any failure — the
    caller then simply grounds the reply in less context, never in raw data."""
    import json

    rows = list(rows or [])[:cap]
    if not rows:
        return ""
    try:
        text = json.dumps(redact_rows_for_ai(rows), ensure_ascii=False)
    except Exception:
        return ""
    if max_chars:
        return text[:max_chars]
    return text
