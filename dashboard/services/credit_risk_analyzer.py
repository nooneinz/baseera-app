"""
Early warning before selling on credit — "الإنذار المبكر لمخاطرة البيع بالآجل".

Warn the owner BEFORE they extend credit to a customer who carries real risk
indicators in their own data — before the loss, not after (the same idea as a
credit bureau, but computed only from what the user uploaded).

Same two-stage discipline as waste_analyzer:

1. compute_credit_risk_signals() — a deterministic pass over the user's real
   rows. Every signal carries the exact numbers and how many rows back it.
2. phrase_credit_alert() — the model may only *word* an alert from those
   signals. It never sees raw rows, is told not to add facts about the
   customer, and any number it writes that is not in the signals makes us
   discard its text and fall back to a template built from the signals.

There is no external credit source and no foreign analysis: the fifth signal
is a watchlist the user maintains themselves (CreditWatchlistEntry).
"""
import json
import logging
import re
import statistics

from dashboard.services.waste_analyzer import _to_num

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds — PENDING REVIEW WITH THE TECHNICAL/FINANCE CONSULTANT
# (roadmap principle 4: agree the concentration % and delay days before
# locking them). Every rule below reads its limit from this one table.
# ---------------------------------------------------------------------------
CREDIT_RISK_THRESHOLDS = {
    # 1) Revenue concentration: one customer above this share of all sales.
    "concentration_share": 0.25,
    # Concentration is only meaningful with at least this many customers.
    "concentration_min_customers": 3,
    # 2) Late-payment trend: the latest payment is at least this many days
    #    past the agreed terms AND lateness has been rising.
    "late_days_over_terms": 15,
    "late_trend_min_invoices": 3,
    "late_trend_min_increase_days": 5,
    # 3) Sudden order spike from a customer without a long history:
    #    "new" = at most this many earlier orders, or first seen within
    #    this many days of their latest order.
    "new_customer_max_prior_orders": 3,
    "new_customer_max_days": 90,
    #    ...and the latest order is at least this multiple of their own
    #    earlier average (or of the file's median order when they have none).
    "spike_multiplier": 3.0,
    # 4) Unusual payment terms: longer than the user's own typical terms by
    #    both this multiple and this many days. (No external sector feed —
    #    the baseline is the business's own normal terms.)
    "terms_multiplier": 1.5,
    "terms_min_extra_days": 30,
}

# Column-name hints, most specific first. Ranked matching (see _pick) so a
# generic hint like "تاريخ" never steals "تاريخ الاستحقاق".
COL_HINTS = {
    "customer": ["اسم العميل", "العميل", "عميل", "الزبون", "زبون", "المشتري", "customer_name", "customer", "client", "buyer"],
    "due": ["تاريخ الاستحقاق", "الاستحقاق", "due_date", "due date", "due"],
    "paid": ["تاريخ السداد", "تاريخ الدفع", "تاريخ التحصيل", "payment_date", "paid_date", "paid date", "date paid", "paid"],
    "late": ["أيام التأخير", "ايام التأخير", "التأخير", "days_late", "days late", "overdue_days", "overdue", "delay"],
    "terms": ["مدة الآجل", "أيام الآجل", "ايام الآجل", "شروط الدفع", "مدة السداد", "الآجل", "payment_terms", "credit_days", "terms", "net_days"],
    "amount": ["قيمة الفاتورة", "مبلغ الفاتورة", "المبلغ", "إجمالي الفاتورة", "الإجمالي", "إجمالي", "القيمة", "مبيعات", "invoice_amount", "amount", "total", "sales", "revenue", "value"],
    "date": ["تاريخ الفاتورة", "تاريخ البيع", "تاريخ الطلب", "التاريخ", "تاريخ", "invoice_date", "order_date", "date"],
}


def _pick(columns, kind, taken):
    for hint in COL_HINTS[kind]:
        h = hint.lower()
        for col in columns:
            if col in taken:
                continue
            if h in str(col).lower():
                return col
    return None


def _resolve_columns(columns):
    taken = set()
    found = {}
    # Specific columns first, so generic "date"/"amount" can't claim them.
    for kind in ("customer", "due", "paid", "late", "terms", "amount", "date"):
        col = _pick(columns, kind, taken)
        found[kind] = col
        if col:
            taken.add(col)
    return found


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize_name(name):
    """Loose Arabic/Latin name key so 'مؤسسة النور' matches 'موسسة النور '."""
    s = str(name or "").translate(_AR_DIGITS).strip().lower()
    s = re.sub(r"[ً-ْـ]", "", s)          # harakat + tatweel
    s = re.sub(r"[إأآا]", "ا", s).replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_date(value):
    if value in (None, ""):
        return None
    try:
        import pandas as pd
        text = str(value).translate(_AR_DIGITS).strip()
        # ISO first; otherwise day-first (the Omani convention: 23/05/2025).
        d = pd.to_datetime(text, errors="coerce", dayfirst=not re.match(r"^\d{4}-", text))
        return None if pd.isna(d) else d.to_pydatetime()
    except Exception:
        return None


def _num(value):
    if isinstance(value, str):
        value = value.translate(_AR_DIGITS)
    return _to_num(value)


def _collect(rows, cols):
    """Group the rows into per-customer order histories."""
    customers = {}
    for idx, row in enumerate(rows):
        raw_name = row.get(cols["customer"])
        if raw_name in (None, "") or str(raw_name).strip().lower() in ("nan", "none", "-"):
            continue
        key = normalize_name(raw_name)
        if not key:
            continue
        c = customers.setdefault(key, {"name": str(raw_name).strip(), "orders": []})
        amount = _num(row.get(cols["amount"])) if cols["amount"] else None
        date = _parse_date(row.get(cols["date"])) if cols["date"] else None
        terms = _num(row.get(cols["terms"])) if cols["terms"] else None
        late = None
        if cols["late"]:
            late = _num(row.get(cols["late"]))
        elif cols["paid"]:
            paid = _parse_date(row.get(cols["paid"]))
            due = _parse_date(row.get(cols["due"])) if cols["due"] else None
            if due is None and date is not None and terms is not None:
                import datetime as _dt
                due = date + _dt.timedelta(days=terms)
            if paid is not None and due is not None:
                late = (paid - due).days
        c["orders"].append({"i": idx, "amount": amount, "date": date, "terms": terms, "late": late})
    for c in customers.values():
        # Chronological when dates exist; otherwise keep file order.
        c["orders"].sort(key=lambda o: (o["date"] is None, o["date"] or 0, o["i"]))
        c["sales"] = round(sum(o["amount"] for o in c["orders"] if o["amount"]), 2)
    return customers


def _fmt(n):
    try:
        n = float(n)
        return f"{n:,.0f}" if abs(n) >= 100 or n == int(n) else f"{n:,.2f}"
    except (TypeError, ValueError):
        return str(n)


def compute_credit_risk_signals(rows, watchlist=None, thresholds=None):
    """
    Deterministic credit-risk evidence from the user's real rows.

    watchlist: iterable of {"customer_name": str, "reason": str} the user keeps.
    Returns:
      {
        "analyzable": bool,        # a customer column + sales amounts exist
        "columns_used": {...},
        "customers_count": int,
        "signals": [ {type, customer, title, detail, severity, evidence_count, values} ],
        "customers": { key: {name, sales, share, orders, risk_level, signal_types} },
        "thresholds": {...},
      }
    """
    T = dict(CREDIT_RISK_THRESHOLDS, **(thresholds or {}))
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    result = {"analyzable": False, "columns_used": {}, "customers_count": 0,
              "signals": [], "customers": {}, "thresholds": T}
    # No data (or no customer column) still leaves the user's own watchlist.
    cols = _resolve_columns(list(rows[0].keys())) if rows else {k: None for k in COL_HINTS}
    result["columns_used"] = cols
    customers = _collect(rows, cols) if cols["customer"] else {}
    result["customers_count"] = len(customers)
    result["analyzable"] = bool(customers) and bool(cols["amount"])
    signals = []

    total_sales = sum(c["sales"] for c in customers.values() if c["sales"] > 0)
    all_amounts = [o["amount"] for c in customers.values() for o in c["orders"] if o["amount"] and o["amount"] > 0]
    median_order = statistics.median(all_amounts) if all_amounts else None
    all_terms = [o["terms"] for c in customers.values() for o in c["orders"] if o["terms"] is not None and o["terms"] >= 0]
    median_terms = statistics.median(all_terms) if all_terms else None

    for key, c in customers.items():
        name, orders = c["name"], c["orders"]

        # 1) Revenue concentration -------------------------------------------
        if total_sales > 0 and len(customers) >= T["concentration_min_customers"]:
            share = c["sales"] / total_sales
            c["share"] = round(share, 4)
            if share > T["concentration_share"]:
                signals.append({
                    "type": "revenue_concentration", "customer": name,
                    "title": "تركّز الإيراد",
                    "detail": f"{name} وحده يمثّل {share*100:.0f}٪ من مبيعاتك ({_fmt(c['sales'])} من {_fmt(total_sales)} ر.ع)",
                    "severity": "high" if share >= T["concentration_share"] * 1.6 else "medium",
                    "evidence_count": len(orders),
                    "values": {"share_pct": round(share * 100), "customer_sales": c["sales"], "total_sales": round(total_sales, 2)},
                })

        # 2) Late-payment trend ----------------------------------------------
        lates = [o["late"] for o in orders if o["late"] is not None]
        if len(lates) >= T["late_trend_min_invoices"]:
            half = len(lates) // 2
            early = statistics.mean(lates[:half]) if half else lates[0]
            recent = statistics.mean(lates[-half:]) if half else lates[-1]
            if lates[-1] >= T["late_days_over_terms"] and recent - early >= T["late_trend_min_increase_days"]:
                seq = " ← ".join(str(int(round(x))) for x in lates[-5:])
                signals.append({
                    "type": "late_payment_trend", "customer": name,
                    "title": "نمط تأخر السداد يتزايد",
                    "detail": f"تأخير {name} عن الموعد المتفق يتصاعد (بالأيام: {seq})؛ آخر سداد تأخّر {int(round(lates[-1]))} يوماً",
                    "severity": "high" if lates[-1] >= T["late_days_over_terms"] * 2 else "medium",
                    "evidence_count": len(lates),
                    "values": {"latest_late_days": int(round(lates[-1])), "early_avg_late": round(early, 1),
                               "recent_avg_late": round(recent, 1), "sequence": [int(round(x)) for x in lates[-5:]]},
                })

        # 3) Sudden order spike from a customer without a long history -------
        amt_orders = [o for o in orders if o["amount"] and o["amount"] > 0]
        if amt_orders:
            latest = amt_orders[-1]
            prior = amt_orders[:-1]
            span_days = None
            if latest["date"] and amt_orders[0]["date"]:
                span_days = (latest["date"] - amt_orders[0]["date"]).days
            is_new = len(prior) <= T["new_customer_max_prior_orders"] or (
                span_days is not None and span_days <= T["new_customer_max_days"])
            baseline = statistics.mean(o["amount"] for o in prior) if prior else median_order
            basis = "متوسط طلباته السابقة" if prior else "الطلب المعتاد لديك"
            if is_new and baseline and len(all_amounts) >= 3 and latest["amount"] >= T["spike_multiplier"] * baseline:
                mult = latest["amount"] / baseline
                signals.append({
                    "type": "order_spike", "customer": name,
                    "title": "قفزة طلبات مفاجئة من عميل جديد",
                    "detail": f"آخر طلب لـ{name} بقيمة {_fmt(latest['amount'])} ر.ع يعادل {mult:.1f}× {basis} ({_fmt(baseline)} ر.ع)، وسجلّه معك قصير ({len(prior)} طلب سابق)",
                    "severity": "high" if mult >= T["spike_multiplier"] * 2 else "medium",
                    "evidence_count": len(amt_orders),
                    "values": {"latest_order": round(latest["amount"], 2), "baseline": round(baseline, 2),
                               "multiple": round(mult, 1), "prior_orders": len(prior)},
                })

        # 4) Unusual payment terms -------------------------------------------
        terms = [o["terms"] for o in orders if o["terms"] is not None]
        if terms and median_terms is not None and len(all_terms) >= 3:
            longest = max(terms)
            limit = max(median_terms * T["terms_multiplier"], median_terms + T["terms_min_extra_days"])
            if longest >= limit:
                signals.append({
                    "type": "unusual_terms", "customer": name,
                    "title": "شروط دفع غير معتادة",
                    "detail": f"{name} حصل على آجل {int(longest)} يوماً بينما المعتاد لديك {int(median_terms)} يوماً",
                    "severity": "medium",
                    "evidence_count": len(terms),
                    "values": {"terms_days": int(longest), "typical_terms_days": int(median_terms)},
                })

    # 5) The user's own watchlist (no external source) ------------------------
    for entry in (watchlist or []):
        wname = entry.get("customer_name") if isinstance(entry, dict) else getattr(entry, "customer_name", "")
        reason = entry.get("reason", "") if isinstance(entry, dict) else getattr(entry, "reason", "")
        key = normalize_name(wname)
        if not key:
            continue
        in_data = customers.get(key)
        signals.append({
            "type": "user_watchlist", "customer": (in_data or {}).get("name") or str(wname).strip(),
            "title": "في قائمة المخاطر التي سجّلتها",
            "detail": f"سجّلتَ هذا العميل بنفسك في قائمة المخاطر" + (f": {reason}" if reason else ""),
            "severity": "high", "evidence_count": 1,
            "values": {"reason": reason or ""},
        })
        if in_data is None:
            customers[key] = {"name": str(wname).strip(), "orders": [], "sales": 0.0}

    # Roll up a per-customer level.
    by_customer = {}
    for s in signals:
        by_customer.setdefault(normalize_name(s["customer"]), []).append(s)
    for key, c in customers.items():
        own = by_customer.get(key, [])
        level = "low"
        if own:
            level = "high" if (len(own) >= 2 or any(s["severity"] == "high" for s in own)) else "medium"
        result["customers"][key] = {
            "name": c["name"], "sales": c.get("sales", 0.0), "share": c.get("share"),
            "orders": len(c.get("orders", [])), "risk_level": level,
            "signal_types": [s["type"] for s in own],
        }

    order = {"high": 0, "medium": 1}
    signals.sort(key=lambda s: (order.get(s["severity"], 2), s["customer"]))
    result["signals"] = signals
    return result


def check_customer(rows, customer_name, watchlist=None, proposed_amount=None, proposed_terms_days=None, thresholds=None):
    """
    The pre-sale check: everything the data says about ONE customer, plus
    how a proposed credit sale (amount / credit days) compares to their own
    history and to the business's normal terms.
    """
    T = dict(CREDIT_RISK_THRESHOLDS, **(thresholds or {}))
    computed = compute_credit_risk_signals(rows, watchlist=watchlist, thresholds=T)
    key = normalize_name(customer_name)
    cust = computed["customers"].get(key)
    signals = [s for s in computed["signals"] if normalize_name(s["customer"]) == key]

    rows = [r for r in (rows or []) if isinstance(r, dict)]
    cols = computed["columns_used"] or {}
    customers = _collect(rows, cols) if rows and cols.get("customer") else {}
    history = customers.get(key, {"orders": []})
    amounts = [o["amount"] for o in history["orders"] if o["amount"] and o["amount"] > 0]
    all_terms = [o["terms"] for c in customers.values() for o in c["orders"] if o["terms"] is not None and o["terms"] >= 0]

    # Checks on the sale being considered right now.
    amt = _num(proposed_amount) if proposed_amount not in (None, "") else None
    if amt and amt > 0:
        all_amounts = [o["amount"] for c in customers.values() for o in c["orders"] if o["amount"] and o["amount"] > 0]
        baseline = statistics.mean(amounts) if amounts else (statistics.median(all_amounts) if all_amounts else None)
        new_customer = len(amounts) <= T["new_customer_max_prior_orders"]
        if baseline and new_customer and amt >= T["spike_multiplier"] * baseline:
            signals.append({
                "type": "proposed_spike", "customer": (cust or {}).get("name") or customer_name,
                "title": "الطلب الحالي أكبر بكثير من المعتاد",
                "detail": f"البيع المقترح {_fmt(amt)} ر.ع يعادل {amt / baseline:.1f}× "
                          + ("متوسط طلباته" if amounts else "الطلب المعتاد لديك") + f" ({_fmt(baseline)} ر.ع)",
                "severity": "high" if amt >= T["spike_multiplier"] * 2 * baseline else "medium",
                "evidence_count": max(len(amounts), 1),
                "values": {"proposed_amount": round(amt, 2), "baseline": round(baseline, 2), "multiple": round(amt / baseline, 1)},
            })
    days = _num(proposed_terms_days) if proposed_terms_days not in (None, "") else None
    if days and days > 0 and len(all_terms) >= 3:
        typical = statistics.median(all_terms)
        if days >= max(typical * T["terms_multiplier"], typical + T["terms_min_extra_days"]):
            signals.append({
                "type": "proposed_terms", "customer": (cust or {}).get("name") or customer_name,
                "title": "مدة الآجل المقترحة أطول من المعتاد",
                "detail": f"آجل {int(days)} يوماً مقابل المعتاد لديك {int(typical)} يوماً",
                "severity": "medium", "evidence_count": len(all_terms),
                "values": {"proposed_terms_days": int(days), "typical_terms_days": int(typical)},
            })

    level = "low"
    if signals:
        level = "high" if (len(signals) >= 2 or any(s["severity"] == "high" for s in signals)) else "medium"
    return {
        "customer": (cust or {}).get("name") or str(customer_name).strip(),
        "found_in_data": bool(history["orders"]),
        "orders_in_data": len(history["orders"]),
        "sales_in_data": round(sum(amounts), 2) if amounts else 0.0,
        "risk_level": level,
        "signals": signals,
        "analyzable": computed["analyzable"],
        "thresholds": T,
    }


# ---------------------------------------------------------------------------
# Wording the alert — from the signals only.
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers_in(text):
    out = set()
    for m in _NUM_RE.findall(str(text or "").translate(_AR_DIGITS)):
        try:
            out.add(round(float(m.replace(",", "")), 1))
        except ValueError:
            pass
    return out


def _allowed_numbers(check):
    allowed = set()
    for s in check.get("signals", []):
        allowed |= _numbers_in(s.get("detail", ""))
        allowed |= _numbers_in(json.dumps(s.get("values", {}), ensure_ascii=False))
    allowed |= _numbers_in(check.get("orders_in_data"))
    allowed |= _numbers_in(check.get("sales_in_data"))
    allowed |= {0.0, 1.0, 2.0, 3.0}  # ordinal words that models write as digits
    return allowed


def _template_alert(check, lang="ar"):
    name, level, sigs = check["customer"], check["risk_level"], check["signals"]
    if not sigs:
        if not check.get("found_in_data"):
            return (f"لا يوجد لـ{name} سجل في بياناتك الحالية ولا هو في قائمة المخاطر. "
                    "لا توجد مؤشرات تُبنى عليها، فابدأ بمبلغ صغير أو دفعة مقدّمة حتى يتكوّن له سجل.")
        return (f"لم تُرصد مؤشرات مخاطرة على {name} في بياناتك الحالية "
                f"({check['orders_in_data']} عملية). هذا لا يعني ضماناً، لكنه لا يحمل إشارات إنذار.")
    head = "تنبيه عالي الخطورة" if level == "high" else "تنبيه"
    lines = [f"{head} قبل البيع بالآجل لـ{name}:"] + [f"• {s['title']}: {s['detail']}." for s in sigs]
    lines.append("المقترح: اطلب دفعة مقدّمة أو قصّر مدة الآجل، ولا توسّع الائتمان قبل تسوية المتأخر."
                 if level == "high" else "المقترح: قلّل المبلغ الآجل أو اطلب دفعة مقدّمة جزئية.")
    return "\n".join(lines)


def phrase_credit_alert(check, ai_service=None, lang="ar"):
    """
    Turn a check_customer() result into a short alert. The model sees only
    the computed signals; any number it writes that isn't in them voids its
    text (we keep the deterministic template instead).
    Returns {"text": str, "ai_used": bool}.
    """
    fallback = _template_alert(check, lang)
    if not check.get("signals") or ai_service is None or not getattr(ai_service, "client", None):
        return {"text": fallback, "ai_used": False}
    try:
        from dashboard.services.ai_service import GEMINI_MODEL
        from dashboard.security import sanitize_for_prompt
        payload = {"customer": check["customer"], "risk_level": check["risk_level"],
                   "signals": [{"title": s["title"], "detail": s["detail"], "severity": s["severity"]} for s in check["signals"]]}
        prompt = f"""أنت وكيل المخاطر المالية في منصة «بصيرة». صاحب المنشأة على وشك البيع بالآجل لعميل.
هذه إشارات مخاطرة **محسوبة من بياناته فقط**:

{json.dumps(sanitize_for_prompt(payload), ensure_ascii=False, indent=2)}

اكتب تنبيهاً قصيراً (٢–٣ جمل) بالعربية الواضحة:
- اذكر أهم الإشارات بأرقامها كما هي حرفياً.
- اختم بإجراء واحد عملي (دفعة مقدّمة، تقصير الآجل، تقليل المبلغ، أو التسوية أولاً).
قواعد صارمة: لا تضف أي رقم غير موجود أعلاه، ولا أي معلومة عن العميل من خارج هذه الإشارات، ولا تحكم على العميل بصفات شخصية.
أعد النص فقط بدون عناوين أو رموز."""
        resp = ai_service.client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            return {"text": fallback, "ai_used": False}
        invented = _numbers_in(text) - _allowed_numbers(check)
        if invented:
            logger.info("credit alert: model wrote numbers not in the signals %s — using template", invented)
            return {"text": fallback, "ai_used": False}
        return {"text": text, "ai_used": True}
    except Exception as e:
        logger.info("credit alert phrasing unavailable, using template: %s", e)
        return {"text": fallback, "ai_used": False}
