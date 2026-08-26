"""
Reconciliation Layer — section 6 of the platform architecture spec.

Runs immediately BEFORE any downloadable report (Excel/PDF) is generated:
recomputes the report's headline aggregated number (grand total) two ways —
(1) exactly as report_generator.normalize_report_items + the report's own
SUM formula would produce it, and (2) an independent ground-truth checksum
computed directly from the untouched source rows — and refuses to hand back
a file unless the two agree within a small floating-point tolerance.

If they disagree: one self-correction retry is attempted (rebuild items
straight from the source rows, bypassing report_generator's own
"is this a unit price or an already-multiplied total?" heuristic — the exact
kind of silent transformation that can make a report drift from its
source). If the retry still disagrees, generation is aborted and the
standard user-facing message is raised instead of ever handing back a
mismatched file.
"""
import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

RECONCILIATION_TOLERANCE = Decimal("0.01")

STANDARD_MISMATCH_MESSAGE_AR = (
    "حدث خطأ في عملية مطابقة الأرقام أثناء تجهيز التقرير للحفاظ على دقة "
    "بياناتك، يرجى إعادة المحاولة بأمر أكثر تحديداً."
)
STANDARD_MISMATCH_MESSAGE_EN = (
    "A number-reconciliation error occurred while preparing your report to "
    "protect the accuracy of your data. Please try again with a more "
    "specific request."
)


class ReconciliationError(Exception):
    """
    Raised when a report's aggregated numbers cannot be reconciled against
    the source data, even after one self-correction retry. Carries the
    exact user-facing message required by spec section 6.
    """
    def __init__(self, lang="ar"):
        self.message = STANDARD_MISMATCH_MESSAGE_AR if lang == "ar" else STANDARD_MISMATCH_MESSAGE_EN
        super().__init__(self.message)


def _to_decimal(value):
    try:
        if value is None:
            return Decimal("0")
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def compute_grand_total(items):
    """
    Recomputes the report's 'إجمالي المبيعات' the same way its Excel
    formula does: SUM(qty_sold * unit_price) across every normalized row.
    """
    total = Decimal("0")
    for item in items or []:
        total += _to_decimal(item.get("qty_sold", 0)) * _to_decimal(item.get("unit_price", 0))
    return total


def _find_num_or_none(row, keywords):
    """Like report_generator.find_num_val, but returns None (not a default)
    when no keyword-matching column holds a parseable number — needed to
    tell "column absent" apart from "column present and legitimately 0"."""
    for key, val in row.items():
        key_str = str(key).lower().strip()
        if any(kw.lower() in key_str for kw in keywords):
            try:
                num_str = str(val).replace(',', '').strip()
                num = float(num_str)
                if num == num:  # filters out NaN
                    return num
            except (ValueError, TypeError):
                continue
    return None


def compute_source_checksum(raw_rows, quantity_keywords=None, price_keywords=None):
    """
    Ground-truth total computed directly from the untouched source rows
    (e.g. DynamicRecord.row_data), independent of the report generator's own
    item-normalization heuristics.

    Prefers an explicit per-row total column already present in the source
    (e.g. a "الإجمالي"/"Total Sales" column) when every row has one — the
    most trustworthy ground truth, since it requires no qty*price
    assumption at all. Falls back to qty * price otherwise.
    """
    from dashboard.report_generator import find_num_val, REPORT_FIELD_KEYWORDS

    qty_kw = quantity_keywords or REPORT_FIELD_KEYWORDS["qty_sold"]
    price_kw = price_keywords or REPORT_FIELD_KEYWORDS["unit_price"]
    total_kw = REPORT_FIELD_KEYWORDS["row_total"]

    rows = [r for r in (raw_rows or []) if isinstance(r, dict)]
    if not rows:
        return Decimal("0")

    explicit_totals = [_find_num_or_none(row, total_kw) for row in rows]
    if all(t is not None for t in explicit_totals):
        return sum((_to_decimal(t) for t in explicit_totals), Decimal("0"))

    total = Decimal("0")
    for row in rows:
        qty = find_num_val(row, qty_kw, 1.0)
        price = find_num_val(row, price_kw, 0.0)
        total += _to_decimal(qty) * _to_decimal(price)
    return total


def reconcile_report_items(raw_rows, lang="ar"):
    """
    Main entry point for section 6. Given the raw source rows a report is
    about to be built from, returns the verified, safe-to-render normalized
    items list.

    Raises ReconciliationError (never generates/returns a mismatched file)
    if the grand total cannot be reconciled with the source data even after
    one self-correction retry.
    """
    from dashboard.report_generator import normalize_report_items

    normalize_lang = "AR" if lang == "ar" else "EN"
    source_total = compute_source_checksum(raw_rows)

    items = normalize_report_items(raw_rows, lang=normalize_lang)
    report_total = compute_grand_total(items)

    if abs(report_total - source_total) <= RECONCILIATION_TOLERANCE:
        return items

    logger.warning(
        "Reconciliation mismatch (attempt 1): report_total=%s source_total=%s — "
        "retrying with self-correction", report_total, source_total,
    )

    # Self-correction: rebuild items straight from the raw source rows using
    # only the plain qty * price extraction, bypassing normalize_report_items'
    # "unit_price looks like an already-multiplied total, divide it" guess —
    # the most likely single point of drift from the source data.
    from dashboard.report_generator import find_str_val, find_num_val, REPORT_FIELD_KEYWORDS

    corrected_items = []
    for idx, row in enumerate(raw_rows or []):
        if not isinstance(row, dict):
            continue
        corrected_items.append({
            "code": find_str_val(row, REPORT_FIELD_KEYWORDS["code"], f"SKU-{idx+1:02d}"),
            "name": find_str_val(row, REPORT_FIELD_KEYWORDS["name"], f"بند {idx+1}"),
            "category": find_str_val(row, REPORT_FIELD_KEYWORDS["category"], "عام"),
            "qty_sold": find_num_val(row, REPORT_FIELD_KEYWORDS["qty_sold"], 1.0),
            "unit_price": find_num_val(row, REPORT_FIELD_KEYWORDS["unit_price"], 0.0),
            "unit_cost": find_num_val(row, REPORT_FIELD_KEYWORDS["unit_cost"], 0.0),
            "qty_wasted": find_num_val(row, REPORT_FIELD_KEYWORDS["qty_wasted"], 0.0),
        })

    corrected_total = compute_grand_total(corrected_items)

    if abs(corrected_total - source_total) <= RECONCILIATION_TOLERANCE:
        logger.info("Reconciliation self-correction succeeded: total=%s", corrected_total)
        return corrected_items

    logger.error(
        "Reconciliation failed after self-correction: corrected_total=%s source_total=%s — "
        "aborting report generation", corrected_total, source_total,
    )
    raise ReconciliationError(lang=lang)
