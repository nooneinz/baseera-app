"""
Analysis theater — turn Baseera's DETERMINISTIC analysis into a live,
step-by-step "show your work" screen the user watches unfold.

Baseera's whole value is that the numbers are computed in Python, not guessed
by an LLM. This module exposes that: build_analysis_steps(rows) replays the
exact deterministic pipeline the dashboard already runs, but as an ordered
list of visible steps -- each carrying the real formula and the real result
from the user's own data. The frontend reveals them one by one so the user
literally sees the math being done in front of them.

Every number here comes from the same trusted functions the rest of the app
uses (compute_transaction_signal, compute_runway, compute_waste_signals,
_detect_finding). Nothing is invented; a step that the data can't support is
simply skipped. Never raises -- returns whatever steps it could build.
"""
import logging

logger = logging.getLogger(__name__)


def _fmt(n):
    """Human number: thousands separators, no trailing .0 noise."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.2f}"


def _columns(rows):
    for r in rows:
        if isinstance(r, dict) and r:
            return list(r.keys())
    return []


def build_analysis_steps(rows):
    """
    Return an ordered list of grounded analysis steps for these rows:

      { "phase", "icon", "title", "lines": [..], "formula"?, "result"?, "tone" }

    tone in {info, good, warn, bad, muted}. Never raises.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    steps = []

    # --- Step 1: read the file --------------------------------------------
    cols = _columns(rows)
    steps.append({
        "phase": "read",
        "icon": "",
        "title": "أقرأ ملفك",
        "lines": [
            f"عدد العمليات: {_fmt(len(rows))}",
            ("الأعمدة: " + "، ".join(cols[:6])) if cols else "لم أتعرّف على أعمدة بعد",
        ],
        "tone": "info",
    })

    if not rows:
        steps.append({
            "phase": "verdict", "icon": "", "title": "بانتظار بياناتك",
            "lines": ["ارفع كشف حساب أو ملف مبيعات وأبدأ التحليل فوراً."],
            "tone": "muted",
        })
        return steps

    # --- Step 2: split income vs expense ----------------------------------
    sig = None
    try:
        from dashboard.services.first_win_insights import compute_transaction_signal
        sig = compute_transaction_signal(rows)
    except Exception as e:
        logger.info("theater: signal failed: %s", e)

    if sig:
        inc = sig.get("total_income") or 0
        exp = sig.get("total_expense") or 0
        net = sig.get("net") or 0
        steps.append({
            "phase": "split",
            "icon": "",
            "title": "أفصل الدخل عن المصروف",
            "lines": [
                f"إجمالي الدخل: {_fmt(inc)} ر.ع",
                f"إجمالي المصروف: {_fmt(exp)} ر.ع",
            ],
            "formula": "الصافي = الدخل − المصروف",
            "result": f"{_fmt(inc)} − {_fmt(exp)} = {_fmt(net)} ر.ع",
            "tone": "good" if net >= 0 else "bad",
        })

    # --- Step 3: biggest recurring expense (renegotiation target) ---------
    try:
        from dashboard.services.agent_actions import _detect_finding
        finding = _detect_finding(rows)
        if finding:
            steps.append({
                "phase": "recurring",
                "icon": "",
                "title": "أبحث عن أكبر بند متكرر",
                "lines": [
                    f"البند: «{finding['name']}»",
                    f"عدد العمليات: {_fmt(finding.get('count', 1))}",
                ],
                "formula": "إجمالي البند = مجموع عملياته",
                "result": f"= {_fmt(finding['total'])} ر.ع — فرصة تفاوض وتوفير",
                "tone": "warn",
            })
    except Exception as e:
        logger.info("theater: finding failed: %s", e)

    # --- Step 4: liquidity / runway ---------------------------------------
    try:
        from dashboard.services.runway import compute_runway
        rw = compute_runway(rows)
        st = rw.get("status")
        if st in ("surplus", "burning", "critical"):
            if st == "surplus":
                lines = ["تدفقك النقدي موجب — وضعك مستقر"]
                result = f"متوسط صافي شهري ≈ +{_fmt(rw.get('avg_monthly_net'))} ر.ع"
                tone = "good"
            elif st == "critical":
                lines = ["نقدك التقديري صفر أو أقل — تحذير حرج"]
                result = "السيولة تحتاج مراجعة فورية"
                tone = "bad"
            else:  # burning
                months = rw.get("runway_months")
                lines = [f"وتيرة الحرق الشهري ≈ {_fmt(rw.get('monthly_burn'))} ر.ع"]
                result = (f"السيولة تكفي ≈ {_fmt(months)} شهر" if months is not None
                          else "السيولة محدودة — راقب المصاريف")
                tone = "warn"
            steps.append({
                "phase": "runway",
                "icon": "",
                "title": "أحسب كفاية السيولة (Runway)",
                "lines": lines,
                "formula": "الأشهر المتبقية = النقد الحالي ÷ الحرق الشهري",
                "result": result,
                "tone": tone,
            })
    except Exception as e:
        logger.info("theater: runway failed: %s", e)

    # --- Step 5: waste (below-cost / leakage) -----------------------------
    try:
        from dashboard.services.waste_analyzer import compute_waste_signals
        w = compute_waste_signals(rows)
        if w and w.get("analyzable"):
            total = w.get("total_waste", 0) or 0
            if total > 0 and w.get("signals"):
                name = (w["signals"][0].get("title") or "").strip()
                steps.append({
                    "phase": "waste",
                    "icon": "",
                    "title": "أفتّش عن الهدر والتسريب",
                    "lines": [f"المصدر: {name}" if name else "أصناف تُباع بأقل من تكلفتها"],
                    "formula": "الهدر = مجموع (التكلفة − السعر) × الكمية",
                    "result": f"≈ {_fmt(total)} ر.ع قابلة للاسترجاع",
                    "tone": "bad",
                })
            else:
                steps.append({
                    "phase": "waste",
                    "icon": "",
                    "title": "أفتّش عن الهدر والتسريب",
                    "lines": ["لا يوجد بيع بأقل من التكلفة في هذا الملف"],
                    "tone": "good",
                })
    except Exception as e:
        logger.info("theater: waste failed: %s", e)

    # --- Step 6: verdict ---------------------------------------------------
    verdict = _verdict(sig)
    steps.append(verdict)
    return steps


def _verdict(sig):
    """A short grounded closing line built only from the computed signal."""
    if not sig:
        return {
            "phase": "verdict", "icon": "", "title": "الخلاصة",
            "lines": ["حلّلت ما أمكن من ملفك. أضف عمود النوع (دخل/مصروف) لتحليل أعمق."],
            "tone": "muted",
        }
    net = sig.get("net") or 0
    if net >= 0:
        lines = [f"مشروعك رابح بصافي {_fmt(net)} ر.ع في هذه الفترة.",
                 "التالي: تفاوض على أكبر بند متكرر لرفع الصافي أكثر."]
        tone = "good"
    else:
        lines = [f"مشروعك يخسر {_fmt(abs(net))} ر.ع في هذه الفترة.",
                 "التالي: راجع أكبر بند متكرر والسيولة فوراً."]
        tone = "bad"
    return {"phase": "verdict", "icon": "", "title": "الخلاصة",
            "lines": lines, "tone": tone}
