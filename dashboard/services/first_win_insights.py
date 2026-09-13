"""
First-Win insight helpers for transaction / bank-statement style uploads.

waste_analyzer.compute_waste_signals is built for per-item SALES data (it
needs price + cost, or product + price, to reason about leakage). A very
common Omani SME upload is the opposite shape: a bank statement or a cash
ledger with one row per transaction -- Date, Description, Amount, and a
direction (Income / Expense, دخل / مصروف, credit / debit). That file has no
per-item cost, so the waste engine correctly finds nothing and the user
lands on the "add price/cost columns" state feeling like "nothing happened".

This module gives that file its own honest First Win, computed
deterministically (no LLM, no invented numbers):

* compute_transaction_signal -> income vs expense, net cash position, and the
  biggest / most recurring expense destination.
* compute_glance -> a last-resort "quick glance" (row count, total of the
  main amount column, date range) so the fallback screen is never empty.

Every number returned here is summed straight from the user's own rows.
"""
import logging

from dashboard.services.waste_analyzer import _find_col, _to_num
from dashboard.services.agent_escalation_chain import _DEBIT_LABELS

logger = logging.getLogger(__name__)

# Direction words that mark a row as money coming IN. Kept separate from
# waste_analyzer's retail vocabulary because this is transaction/statement
# language (a bank "credit" is an inflow, not a retail term).
_INCOME_LABELS = {
    "income", "إيراد", "ايراد", "دخل", "deposit", "إيداع", "ايداع",
    "credit", "دائن", "in", "sales", "مبيعات", "revenue", "وارد",
}
_EXPENSE_EXTRA = ("expense", "مصروف", "مصاريف", "سحب", "debit", "withdraw", "out", "منصرف")
_INCOME_EXTRA = ("income", "إيراد", "ايراد", "دخل", "deposit", "credit", "دائن", "sales", "مبيعات", "وارد")


def _lowered(columns):
    return {str(c): str(c).lower() for c in columns}


def _find_amount_col(columns):
    col = _find_col(columns, "revenue") or _find_col(columns, "price")
    if col:
        return col
    for c, low in _lowered(columns).items():
        if any(h in low for h in ("المبلغ", "مبلغ", "amount", "value", "قيمة")):
            return c
    return None


def _find_type_col(columns):
    for c, low in _lowered(columns).items():
        if any(h in low for h in ("type", "نوع", "direction", "الاتجاه", "حركة")):
            return c
    return None


def _find_desc_col(columns):
    for c, low in _lowered(columns).items():
        if any(h in low for h in ("description", "الوصف", "البيان", "بيان", "detail", "التفاصيل")):
            return c
    return _find_col(columns, "product")


def compute_transaction_signal(rows):
    """
    Income/expense analysis for transaction-shaped rows.

    Returns None unless the rows carry BOTH an amount column and an explicit
    direction (type) column -- without a direction there is no honest way to
    tell an outflow from an inflow, so we refuse to guess. Otherwise returns:

      {
        total_income, total_expense, net, expense_count, row_count,
        top_groups: [ {name, count, total, pct} ],   # biggest expense destinations
        top_recurring: {name, count, total, pct} | None,
      }
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return None

    columns = list(rows[0].keys())
    col_amount = _find_amount_col(columns)
    col_type = _find_type_col(columns)
    col_desc = _find_desc_col(columns)
    if not col_amount or not col_type:
        return None

    income = 0.0
    expense = 0.0
    expense_count = 0
    groups = {}
    for row in rows:
        amount = _to_num(row.get(col_amount))
        if amount is None:
            continue
        amount = abs(amount)
        t = str(row.get(col_type) or "").strip().lower()
        is_expense = (t in _DEBIT_LABELS) or any(k in t for k in _EXPENSE_EXTRA)
        is_income = (t in _INCOME_LABELS) or any(k in t for k in _INCOME_EXTRA)
        if is_expense:
            expense += amount
            expense_count += 1
            key = str(row.get(col_desc) or "—").strip() if col_desc else "—"
            g = groups.setdefault(key, {"count": 0, "total": 0.0})
            g["count"] += 1
            g["total"] += amount
        elif is_income:
            income += amount

    if expense_count == 0 and income == 0:
        return None

    top_groups = sorted(
        ({"name": k, "count": v["count"], "total": round(v["total"], 2)} for k, v in groups.items()),
        key=lambda x: x["total"], reverse=True,
    )
    max_val = max([g["total"] for g in top_groups] or [0]) or 1
    for g in top_groups:
        g["pct"] = int(round(g["total"] / max_val * 100)) if max_val else 0

    recurring = [g for g in top_groups if g["count"] >= 2]

    return {
        "total_income": round(income, 2),
        "total_expense": round(expense, 2),
        "net": round(income - expense, 2),
        "expense_count": expense_count,
        "row_count": len(rows),
        "top_groups": top_groups[:3],
        "top_recurring": recurring[0] if recurring else None,
    }


def compute_glance(rows):
    """
    Last-resort "quick glance" so the fallback screen is never empty: the
    number of records, the sum of the most amount-like column, and the date
    range if a date column is present. All read straight from the rows.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return None

    columns = list(rows[0].keys())
    col_amount = _find_amount_col(columns)
    col_date = _find_col(columns, "date")

    total = None
    if col_amount:
        vals = [_to_num(r.get(col_amount)) for r in rows]
        vals = [abs(v) for v in vals if v is not None]
        if vals:
            total = round(sum(vals), 2)

    date_start = date_end = None
    if col_date:
        import pandas as pd
        parsed = pd.to_datetime([r.get(col_date) for r in rows], errors="coerce")
        valid = parsed[~parsed.isna()]
        if len(valid):
            date_start = valid.min().date().isoformat()
            date_end = valid.max().date().isoformat()

    return {
        "row_count": len(rows),
        "total": total,
        "amount_label": str(col_amount) if col_amount else None,
        "date_start": date_start,
        "date_end": date_end,
    }
