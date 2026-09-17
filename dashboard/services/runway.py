"""
Runway — deterministic cash-flow forecast ("how long will your money last?").

This is Baseera's shift from *analysing the past* to *warning about the future*:
given a business's dated transactions, it computes the average monthly net
(burn or surplus) and — when the money is running out — the date the cash is
projected to hit zero. The headline the dashboard shows is:

    "سيولتك تكفيك حتى 7 نوفمبر"  /  "you have ~3.2 months of runway"

Everything here is summed straight from the user's own rows (no LLM, no
invented numbers), in keeping with the «لُبّ» principle: the engine computes,
the model only explains. It is careful never to guess:

* It needs an amount column, an explicit direction (income/expense) column,
  AND a date column — without a date there is no honest way to derive a
  monthly rate, so it returns status "insufficient".
* Current cash is read from a balance/رصيد column when the file has one;
  otherwise it is *estimated* as the cumulative net of the ledger (assuming
  the file starts from zero) and clearly flagged with cash_estimated=True, so
  the UI can label it an estimate rather than pass it off as a real balance.
"""
import logging
from datetime import timedelta

from dashboard.services.waste_analyzer import _find_col, _to_num
from dashboard.services.first_win_insights import (
    _find_amount_col, _find_type_col, _DEBIT_LABELS,
    _INCOME_LABELS, _EXPENSE_EXTRA, _INCOME_EXTRA,
)

logger = logging.getLogger(__name__)

_DAYS_PER_MONTH = 30.44  # average calendar month, for date projection


def _find_balance_col(columns):
    for c in columns:
        low = str(c).lower()
        if any(h in low for h in ("balance", "رصيد", "الرصيد", "running")):
            return c
    return None


def _is_expense(t):
    t = str(t or "").strip().lower()
    return (t in _DEBIT_LABELS) or any(k in t for k in _EXPENSE_EXTRA)


def _is_income(t):
    t = str(t or "").strip().lower()
    return (t in _INCOME_LABELS) or any(k in t for k in _INCOME_EXTRA)


def compute_runway(rows, today=None):
    """
    Returns one of:

      {"status": "insufficient", "reason": "<ar>"}

      {"status": "surplus"|"burning"|"critical",
       "total_income", "total_expense", "net",
       "months_observed", "avg_monthly_net", "monthly_burn",
       "current_cash", "cash_estimated": bool,
       "runway_months": float|None, "runway_date": "YYYY-MM-DD"|None}
    """
    import datetime as _dt
    import pandas as pd

    today = today or _dt.date.today()
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return {"status": "insufficient", "reason": "لا توجد بيانات كافية بعد."}

    columns = list(rows[0].keys())
    col_amount = _find_amount_col(columns)
    col_type = _find_type_col(columns)
    col_date = _find_col(columns, "date") or _find_col(columns, "تاريخ")
    col_balance = _find_balance_col(columns)

    if not col_amount or not col_type:
        return {"status": "insufficient",
                "reason": "نحتاج عمود المبلغ وعمود النوع (دخل/مصروف) لحساب التدفق."}
    if not col_date:
        return {"status": "insufficient",
                "reason": "نحتاج عمود التاريخ لتوقّع مدى كفاية السيولة."}

    # Parse dates once (aligned with the rows we keep).
    raw_dates = pd.to_datetime([r.get(col_date) for r in rows], errors="coerce")

    income = expense = 0.0
    monthly = {}                 # "YYYY-MM" -> net
    balances = []                # (date, balance) when a balance column exists
    for row, dt in zip(rows, raw_dates):
        amount = _to_num(row.get(col_amount))
        if amount is None or pd.isna(dt):
            continue
        amount = abs(amount)
        signed = 0.0
        if _is_expense(row.get(col_type)):
            expense += amount
            signed = -amount
        elif _is_income(row.get(col_type)):
            income += amount
            signed = amount
        else:
            continue
        key = f"{dt.year}-{dt.month:02d}"
        monthly[key] = monthly.get(key, 0.0) + signed
        if col_balance is not None:
            bal = _to_num(row.get(col_balance))
            if bal is not None:
                balances.append((dt, bal))

    if not monthly:
        return {"status": "insufficient",
                "reason": "لم نتعرّف على حركات دخل/مصروف مؤرّخة في الملف."}

    months_observed = len(monthly)
    avg_monthly_net = sum(monthly.values()) / months_observed

    # Current cash: prefer a real balance column (latest by date), else estimate
    # from the cumulative net of the whole ledger.
    cash_estimated = True
    if balances:
        balances.sort(key=lambda x: x[0])
        current_cash = balances[-1][1]
        cash_estimated = False
    else:
        current_cash = income - expense

    result = {
        "total_income": round(income, 2),
        "total_expense": round(expense, 2),
        "net": round(income - expense, 2),
        "months_observed": months_observed,
        "avg_monthly_net": round(avg_monthly_net, 2),
        "monthly_burn": round(abs(avg_monthly_net), 2) if avg_monthly_net < 0 else 0.0,
        "current_cash": round(current_cash, 2),
        "cash_estimated": cash_estimated,
        "runway_months": None,
        "runway_date": None,
    }

    if avg_monthly_net >= 0:
        result["status"] = "surplus"
        return result

    # Burning cash.
    if current_cash <= 0:
        result["status"] = "critical"
        return result

    burn = abs(avg_monthly_net)
    runway_months = current_cash / burn
    runway_date = today + timedelta(days=runway_months * _DAYS_PER_MONTH)
    result["status"] = "burning"
    result["runway_months"] = round(runway_months, 1)
    result["runway_date"] = runway_date.isoformat()
    return result
