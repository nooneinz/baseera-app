"""
Live report builder — the grounded content behind the "watch your report being
written" screen.

build_live_report(user, rows, ...) assembles a structured executive report
straight from Baseera's DETERMINISTIC analysis (the same trusted compute
functions used across the app): sections with a heading, grounded paragraphs
and optional stat lines. The frontend then reveals it section by section,
typing it out in front of the user. Nothing here is invented; a section the
data can't support is simply omitted. Never raises — returns whatever it could
build, plus a plain-text rendering for persistence/download.
"""
import datetime
import logging

logger = logging.getLogger(__name__)


def _num(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"
    return f"{int(n):,}" if n == int(n) else f"{n:,.2f}"


def build_live_report(user, rows, company_name="", lang="ar"):
    """
    Return a structured, grounded report:

      {
        "title", "meta": {company, date, records},
        "sections": [ {heading, paragraphs:[...], stats:[{label,value}]?} ],
        "plain": "<full text rendering>",
      }

    Never raises.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    company = company_name or getattr(user, "username", "") or "منشأتك"
    title = "تقرير بصيرة للذكاء المالي"

    sections = []

    # Compute the trusted signals once.
    sig = runway = waste = finding = None
    try:
        from dashboard.services.first_win_insights import compute_transaction_signal
        sig = compute_transaction_signal(rows)
    except Exception as e:
        logger.info("report_live: signal failed: %s", e)
    try:
        from dashboard.services.runway import compute_runway
        runway = compute_runway(rows)
    except Exception:
        pass
    try:
        from dashboard.services.waste_analyzer import compute_waste_signals
        waste = compute_waste_signals(rows)
    except Exception:
        pass
    try:
        from dashboard.services.agent_actions import _detect_finding
        finding = _detect_finding(rows)
    except Exception:
        pass

    # --- Executive summary ------------------------------------------------
    if sig:
        net = sig.get("net") or 0
        verdict = (f"خلال هذه الفترة حقّقت منشأتك صافياً موجباً قدره {_num(net)} ر.ع."
                   if net >= 0 else
                   f"خلال هذه الفترة سجّلت منشأتك عجزاً قدره {_num(abs(net))} ر.ع يستوجب المراجعة.")
        sections.append({
            "heading": "الملخّص التنفيذي",
            "paragraphs": [
                f"أُعدّ هذا التقرير آلياً بالاعتماد على {_num(len(rows))} عملية من بياناتك الفعلية.",
                verdict,
            ],
            "stats": [
                {"label": "إجمالي الدخل", "value": f"{_num(sig.get('total_income') or 0)} ر.ع"},
                {"label": "إجمالي المصروف", "value": f"{_num(sig.get('total_expense') or 0)} ر.ع"},
                {"label": "الصافي", "value": f"{_num(net)} ر.ع"},
            ],
        })
    else:
        sections.append({
            "heading": "الملخّص التنفيذي",
            "paragraphs": [
                f"أُعدّ هذا التقرير بالاعتماد على {_num(len(rows))} سجلاً من بياناتك.",
                "لإضافة تحليل الدخل والمصروف، تأكّد من وجود عمود للنوع (دخل/مصروف) وعمود للمبلغ.",
            ],
        })

    # --- Biggest recurring expense ---------------------------------------
    if finding:
        sections.append({
            "heading": "أكبر بند متكرّر",
            "paragraphs": [
                f"تبيّن أن البند «{finding['name']}» هو أكبر وجهة إنفاق متكرّرة، إذ بلغ إجماليه "
                f"{_num(finding['total'])} ر.ع عبر {_num(finding.get('count', 1))} عملية.",
                "نوصي بالتفاوض على هذا البند أو مراجعة بدائله، لأن أي تخفيض فيه ينعكس مباشرة على الصافي.",
            ],
            "stats": [
                {"label": "البند", "value": finding["name"]},
                {"label": "الإجمالي", "value": f"{_num(finding['total'])} ر.ع"},
                {"label": "عدد العمليات", "value": _num(finding.get("count", 1))},
            ],
        })

    # --- Liquidity / runway ----------------------------------------------
    if runway and runway.get("status") in ("surplus", "burning", "critical"):
        st = runway["status"]
        if st == "surplus":
            para = ("تدفقك النقدي موجب، ومتوسط الصافي الشهري "
                    f"{_num(runway.get('avg_monthly_net'))} ر.ع، ما يشير إلى وضع مستقر.")
        elif st == "critical":
            para = ("رصيدك النقدي التقديري عند الصفر أو أقل مع استمرار حرق النقد؛ "
                    "هذا وضع حرج يستوجب إجراءً فورياً على المصاريف.")
        else:
            months = runway.get("runway_months")
            para = (f"بوتيرة الحرق الحالية ({_num(runway.get('monthly_burn'))} ر.ع شهرياً)، "
                    + (f"تكفي سيولتك لنحو {_num(months)} شهر." if months is not None
                       else "تحتاج سيولتك إلى مراقبة دقيقة."))
        sections.append({"heading": "كفاية السيولة (Runway)", "paragraphs": [para]})

    # --- Waste ------------------------------------------------------------
    if waste and waste.get("analyzable") and (waste.get("total_waste") or 0) > 0 and waste.get("signals"):
        name = (waste["signals"][0].get("title") or "").strip()
        sections.append({
            "heading": "الهدر والتسريب",
            "paragraphs": [
                f"رصد التحليل هدراً تقديرياً بقيمة {_num(waste['total_waste'])} ر.ع"
                + (f" مصدره: {name}." if name else "."),
                "معالجة هذا البند تعني استرجاعاً مباشراً لقيمة كانت تُفقد دون مقابل.",
            ],
        })

    # --- Recommendations --------------------------------------------------
    recs = []
    if finding:
        recs.append(f"ابدأ بالتفاوض على بند «{finding['name']}» — أكبر فرصة توفير حالياً.")
    if runway and runway.get("status") in ("burning", "critical"):
        recs.append("ضع سقفاً أسبوعياً للمصاريف غير الأساسية حتى تتحسّن السيولة.")
    if waste and (waste.get("total_waste") or 0) > 0:
        recs.append("راجع الأصناف التي تُباع بأقل من تكلفتها وأعد تسعيرها.")
    if not recs:
        recs.append("واصل رفع بياناتك بانتظام ليصبح التحليل أدقّ وأعمق مع الوقت.")
    sections.append({"heading": "التوصيات", "paragraphs": recs})

    plain = _render_plain(title, company, now, len(rows), sections)
    return {
        "title": title,
        "meta": {"company": company, "date": now, "records": len(rows)},
        "sections": sections,
        "plain": plain,
    }


def _render_plain(title, company, now, n, sections):
    lines = ["=" * 60, title, "=" * 60,
             f"المنشأة: {company}", f"التاريخ: {now}", f"عدد العمليات: {n:,}", ""]
    for s in sections:
        lines.append("## " + s["heading"])
        for p in s.get("paragraphs", []):
            lines.append(p)
        for st in s.get("stats", []):
            lines.append(f"- {st['label']}: {st['value']}")
        lines.append("")
    lines.append("— أُعدّ آلياً بواسطة منصة بصيرة، وكل رقم فيه محسوب من بياناتك.")
    return "\n".join(lines)
