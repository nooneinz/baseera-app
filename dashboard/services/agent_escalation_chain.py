"""
Escalating Multi-Agent Collaboration Chain.

This is the architecture spec's collaboration-chain example: Audit detects a
real anomaly in the uploaded data -> Financial quantifies its cash-flow
impact -> (only if the anomaly is inventory/purchasing-flavored) Supply
Chain proposes a grounded procurement fix -> (only if the cash-flow impact
is material) Pricing proposes a grounded liquidity offer.

This is deliberately different from orchestrator.select_committee_agents()
(the chat-driven "committee": triggered by a user's own strategic question,
always some fixed 2-3 agents chosen by keyword). This chain is PROACTIVE and
CONDITIONAL:

  * It starts from deterministic evidence found in the user's own uploaded
    rows -- not from anything the user typed.
  * Each stage after Audit only runs when the PREVIOUS stage's real finding
    actually warrants it. A stage that isn't triggered never runs: no LLM
    call, no entry pretending it "would have" said something.
  * If Audit finds nothing real, the whole chain reports "not triggered"
    rather than manufacturing a narrative to fill four stages.

Every number any stage reports is computed here in plain Python before any
LLM ever sees it (same two-stage discipline as waste_analyzer.py and
vision_ocr_service.py); the LLM's only job at each stage is turning
verified numbers into a short executive narrative in that agent's voice. If
the LLM is unavailable, every stage still has a real, data-derived fallback
narrative -- the chain's conclusions never depend on the model being up.
"""
import json
import logging

from dashboard.services.ai_service import GEMINI_MODEL
from dashboard.services.waste_analyzer import compute_waste_signals, _find_col, _to_num
from dashboard.security import sanitize_for_prompt

logger = logging.getLogger(__name__)

# Waste-signal types that are specifically about purchasing / inventory /
# procurement (as opposed to a purely commercial pricing issue) -- these are
# what pull Supply Chain into the chain.
_PROCUREMENT_SIGNAL_TYPES = {"dead_stock", "price_inconsistency", "below_cost_sales", "explicit_waste"}

# Once the flagged monetary amount crosses this share of total recorded
# revenue, the financial impact is treated as material enough to also pull
# in Pricing for an immediate-liquidity recommendation.
MATERIALITY_REVENUE_SHARE = 0.03  # 3%

_DEBIT_LABELS = {"debit", "withdrawal", "سحب", "مدين", "out", "expense", "مصروف"}


def detect_recurring_outflows(rows, min_occurrences=3):
    """
    Audit-domain signal distinct from compute_waste_signals: repeated
    same-description debit/withdrawal transactions -- the literal "سحوبات
    متكررة" example from the architecture spec, most relevant to
    bank-statement-style uploads (typed or OCR'd) rather than retail sales
    rows.

    Deliberately conservative: this ONLY fires when the rows carry an
    explicit direction column (type/نوع/direction) that says debit/
    withdrawal. Without that column there is no honest way to tell an
    outflow from a recurring sale, so it returns nothing rather than guess.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return []

    columns = list(rows[0].keys())
    lowered = {str(c): str(c).lower() for c in columns}

    col_type = None
    for c, low in lowered.items():
        if any(h in low for h in ("type", "نوع", "direction", "الاتجاه")):
            col_type = c
            break
    if not col_type:
        return []

    col_desc = None
    for c, low in lowered.items():
        if any(h in low for h in ("description", "الوصف", "بيان", "detail", "التفاصيل")):
            col_desc = c
            break
    if not col_desc:
        col_desc = _find_col(columns, "product")
    if not col_desc:
        return []

    col_amount = _find_col(columns, "revenue") or _find_col(columns, "price")
    if not col_amount:
        # Bank/transaction-style uploads commonly label the amount column
        # "المبلغ" (amount) rather than any of waste_analyzer's retail-price
        # hints -- checked separately here since it's transaction vocabulary,
        # not a retail price/revenue term.
        for c, low in lowered.items():
            if any(h in low for h in ("المبلغ", "amount", "value", "قيمة")):
                col_amount = c
                break

    groups = {}
    for row in rows:
        type_val = str(row.get(col_type) or "").strip().lower()
        if type_val not in _DEBIT_LABELS:
            continue
        desc = row.get(col_desc)
        if not desc:
            continue
        amount = _to_num(row.get(col_amount)) if col_amount else None
        key = str(desc).strip()
        g = groups.setdefault(key, {"count": 0, "total": 0.0})
        g["count"] += 1
        if amount:
            g["total"] += amount

    findings = [
        {"description": desc, "count": g["count"], "total_amount": round(g["total"], 2)}
        for desc, g in groups.items() if g["count"] >= min_occurrences
    ]
    findings.sort(key=lambda f: (f["count"], f["total_amount"]), reverse=True)
    return findings


def _compute_total_inflow(rows):
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return None
    columns = list(rows[0].keys())
    col_revenue = _find_col(columns, "revenue")
    if not col_revenue:
        return None
    total = sum((_to_num(r.get(col_revenue)) or 0) for r in rows)
    return total if total > 0 else None


def _narrate(agent_id, role_prompt, data, ai_service, lang):
    """Real-data-only LLM narration. Returns (text_or_None, ai_used)."""
    if ai_service is None or not getattr(ai_service, "client", None):
        return None, False
    # Task 5 hardening: `data` carries text pulled straight from the user's
    # uploaded rows (transaction descriptions, product names, ...) -- never
    # embed it in a prompt unsanitized.
    safe_data = sanitize_for_prompt(data)
    if lang == "ar":
        prompt = (
            f"{role_prompt}\n\nالبيانات الحقيقية المرصودة (JSON):\n"
            f"{json.dumps(safe_data, ensure_ascii=False, default=str)}\n\n"
            "قاعدة صارمة: ممنوع اختراع أي رقم غير موجود أعلاه. أعد نصاً تنفيذياً موجزاً "
            "(2-3 جمل) فقط، بدون JSON وبدون أي تنسيق markdown."
        )
    else:
        prompt = (
            f"{role_prompt}\n\nReal observed data (JSON):\n"
            f"{json.dumps(safe_data, ensure_ascii=False, default=str)}\n\n"
            "STRICT RULE: never invent a number not present above. Return a short "
            "(2-3 sentence) executive narrative only, no JSON, no markdown formatting."
        )
    try:
        response = ai_service.client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or "").strip()
        return (text or None), bool(text)
    except Exception as e:
        logger.info("%s stage narrative unavailable, using computed fallback: %s", agent_id, e)
        return None, False


def _skipped_stage(agent_id, agent_name, skip_reason):
    return {
        "agent_id": agent_id, "agent_name": agent_name, "triggered": False,
        "skip_reason": skip_reason, "finding": None, "narrative": "", "ai_used": False,
    }


def _run_audit_stage(audit_finding, ai_service, lang):
    agent_name = "وكيل التدقيق ومكافحة الهدر" if lang == "ar" else "Forensic Audit Agent"
    role_prompt = (
        "أنت وكيل التدقيق الجنائي في منصة بصيرة. فيما يلي شذوذ حقيقي مستخرج حسابياً من بيانات "
        "المستخدم الفعلية (وليس افتراضاً). صف الاكتشاف بدقة تنفيذية موجزة دون اختراع أي رقم غير معطى."
        if lang == "ar" else
        "You are the Forensic Audit agent for Baseera. Below is a REAL anomaly computed "
        "arithmetically from the user's actual data. Describe the finding precisely and "
        "briefly, without inventing any number not given."
    )
    narrative, ai_used = _narrate("audit", role_prompt, audit_finding, ai_service, lang)
    if not narrative:
        parts = []
        if audit_finding["waste_signals"]:
            top = max(audit_finding["waste_signals"], key=lambda s: s.get("currency_amount", 0) or 0)
            parts.append(f"{top['title']}: {top['detail']}")
        if audit_finding["recurring_debits"]:
            d = audit_finding["recurring_debits"][0]
            parts.append(
                f"عملية سحب متكررة \"{d['description']}\" تكررت {d['count']} مرات "
                f"بإجمالي {d['total_amount']:,.2f}"
                if lang == "ar" else
                f"Recurring withdrawal \"{d['description']}\" occurred {d['count']} times "
                f"totaling {d['total_amount']:,.2f}"
            )
        narrative = " ".join(parts)
    return {
        "agent_id": "audit", "agent_name": agent_name, "triggered": True,
        "skip_reason": None, "finding": audit_finding, "narrative": narrative, "ai_used": ai_used,
    }


def _run_financial_stage(audit_finding, waste, rows, ai_service, lang):
    agent_name = "الوكيل المالي (CFO)" if lang == "ar" else "Financial Analyst (CFO)"
    total_inflow = _compute_total_inflow(rows)
    flagged_amount = (waste.get("total_waste") or 0) + sum(
        d["total_amount"] for d in audit_finding["recurring_debits"]
    )
    impact_ratio = (flagged_amount / total_inflow) if (total_inflow and flagged_amount) else None

    finding = {
        "flagged_amount": round(flagged_amount, 2),
        "total_inflow": round(total_inflow, 2) if total_inflow else None,
        "impact_ratio_percent": round(impact_ratio * 100, 2) if impact_ratio is not None else None,
    }
    role_prompt = (
        "أنت الوكيل المالي (CFO) في بصيرة. وكيل التدقيق رصد المبالغ المشبوهة أدناه. اشرح أثرها "
        "على التدفق النقدي بجملتين فقط، مستنداً حصراً إلى الأرقام المعطاة."
        if lang == "ar" else
        "You are the Financial (CFO) agent. The Audit agent flagged the amounts below. Explain "
        "their cash-flow impact in two sentences, grounded strictly in the numbers given."
    )
    narrative, ai_used = _narrate("financial", role_prompt, finding, ai_service, lang)
    if not narrative:
        if finding["impact_ratio_percent"] is not None:
            narrative = (
                f"الأثر النقدي المقدر للمبالغ المرصودة يبلغ {finding['flagged_amount']:,.2f} "
                f"أي ما نسبته {finding['impact_ratio_percent']:.1f}% من إجمالي الإيرادات المسجلة."
                if lang == "ar" else
                f"The estimated cash impact of the flagged amounts is {finding['flagged_amount']:,.2f}, "
                f"{finding['impact_ratio_percent']:.1f}% of total recorded revenue."
            )
        else:
            narrative = (
                f"الأثر النقدي المقدر للمبالغ المرصودة يبلغ {finding['flagged_amount']:,.2f} "
                "(تعذّر حساب نسبته من الإيرادات لعدم وجود عمود إيراد واضح في هذا الملف)."
                if lang == "ar" else
                f"The estimated cash impact of the flagged amounts is {finding['flagged_amount']:,.2f} "
                "(revenue ratio unavailable -- no clear revenue column in this file)."
            )
    stage = {
        "agent_id": "financial", "agent_name": agent_name, "triggered": True,
        "skip_reason": None, "finding": finding, "narrative": narrative, "ai_used": ai_used,
    }
    return stage, impact_ratio


def _run_supply_chain_stage(waste, ai_service, lang):
    agent_name = "وكيل سلاسل الإمداد (COO)" if lang == "ar" else "Supply Chain (COO)"
    procurement_signals = [s for s in waste["signals"] if s["type"] in _PROCUREMENT_SIGNAL_TYPES]
    finding = {"procurement_signals": procurement_signals}
    role_prompt = (
        "أنت وكيل سلاسل الإمداد في بصيرة. وكيل التدقيق رصد مؤشرات الهدر المتعلقة بالمشتريات/المخزون "
        "أدناه. اقترح إجراءً تشغيلياً واحداً محدداً وقابلاً للتنفيذ فوراً لتقليل تكلفة الشراء أو تصريف "
        "المخزون الراكد، مستنداً حصراً إلى الأصناف والأرقام المذكورة."
        if lang == "ar" else
        "You are the Supply Chain agent. The Audit agent flagged the procurement/inventory signals "
        "below. Propose ONE concrete, immediately actionable step to cut purchase cost or clear dead "
        "stock, grounded strictly in the items/numbers given."
    )
    narrative, ai_used = _narrate("supply_chain", role_prompt, finding, ai_service, lang)
    if not narrative:
        top = max(procurement_signals, key=lambda s: s.get("currency_amount", 0) or 0)
        names = ", ".join(e["name"] for e in top.get("examples", [])[:3])
        narrative = (
            f"استناداً لاكتشاف وكيل التدقيق ({top['title']})، يوصى بمراجعة اتفاقيات الشراء أو تصريف "
            f"المخزون المرتبط بـ: {names or 'الأصناف المتأثرة'}."
            if lang == "ar" else
            f"Based on the Audit finding ({top['title']}), review purchasing terms or clear the "
            f"affected stock: {names or 'the affected items'}."
        )
    return {
        "agent_id": "supply_chain", "agent_name": agent_name, "triggered": True,
        "skip_reason": None, "finding": finding, "narrative": narrative, "ai_used": ai_used,
    }


def _run_pricing_stage(rows, cash_impact_ratio, ai_service, lang):
    agent_name = "أخصائي التسعير وهوامش الربح" if lang == "ar" else "Pricing Strategist"

    columns = list(rows[0].keys()) if rows else []
    col_price = _find_col(columns, "price")
    col_cost = _find_col(columns, "cost")
    col_product = _find_col(columns, "product")
    healthy_margin_products = []
    if col_price and col_cost and col_product:
        margins = {}
        for row in rows:
            price = _to_num(row.get(col_price))
            cost = _to_num(row.get(col_cost))
            name = row.get(col_product)
            if not name or price is None or cost is None or price <= 0:
                continue
            margins.setdefault(str(name), []).append((price - cost) / price)
        avg_margins = sorted(
            ((n, sum(v) / len(v)) for n, v in margins.items()), key=lambda t: t[1], reverse=True
        )
        healthy_margin_products = [
            {"name": n, "margin_percent": round(m * 100, 1)} for n, m in avg_margins[:3] if m > 0
        ]

    finding = {
        "cash_impact_ratio_percent": round(cash_impact_ratio * 100, 2),
        "healthy_margin_products": healthy_margin_products,
    }
    role_prompt = (
        "أنت أخصائي التسعير في بصيرة. الأثر النقدي المكتشف في التصعيد السابق يشكل نسبة مؤثرة من "
        "الإيرادات. اقترح عرضاً تسعيرياً واحداً محدداً (خصم أو حزمة) لتوفير سيولة فورية، معتمداً حصراً "
        "على الأصناف ذات الهامش الصحي المذكورة إن وجدت، دون اختراع أي منتج أو رقم."
        if lang == "ar" else
        "You are the Pricing Strategist. The prior stage found a material cash impact as a share of "
        "revenue. Propose ONE concrete pricing offer (discount or bundle) for immediate liquidity, "
        "grounded strictly in the healthy-margin items listed if any, never inventing a product or number."
    )
    narrative, ai_used = _narrate("pricing", role_prompt, finding, ai_service, lang)
    if not narrative:
        if healthy_margin_products:
            names = "، ".join(p["name"] for p in healthy_margin_products)
            narrative = (
                f"نظراً لأن الأثر النقدي يشكل {finding['cash_impact_ratio_percent']:.1f}% من الإيرادات، "
                f"يوصى بعرض ترويجي محدود المدة على الأصناف ذات الهامش الصحي ({names}) لتوليد سيولة فورية "
                "دون المساس بالربحية الإجمالية."
                if lang == "ar" else
                f"Since the cash impact is {finding['cash_impact_ratio_percent']:.1f}% of revenue, a "
                f"time-limited promotion on the healthy-margin items ({names}) is recommended for "
                "immediate liquidity without hurting overall profitability."
            )
        else:
            narrative = (
                f"الأثر النقدي يشكل {finding['cash_impact_ratio_percent']:.1f}% من الإيرادات، لكن الملف "
                "لا يحتوي أعمدة سعر/تكلفة/منتج كافية لتحديد أصناف صحية الهامش لبناء عرض محدد."
                if lang == "ar" else
                f"The cash impact is {finding['cash_impact_ratio_percent']:.1f}% of revenue, but the "
                "file lacks sufficient price/cost/product columns to identify healthy-margin items for "
                "a specific offer."
            )
    return {
        "agent_id": "pricing", "agent_name": agent_name, "triggered": True,
        "skip_reason": None, "finding": finding, "narrative": narrative, "ai_used": ai_used,
    }


def run_escalation_chain(rows, ai_service=None, lang="ar"):
    """
    Runs the full proactive, conditional escalation chain over a user's real
    uploaded rows.

    Returns:
      {
        "triggered": bool,                 # False when Audit found nothing real
        "reason_not_triggered": str|None,
        "stages": [
          {
            "agent_id": "audit"|"financial"|"supply_chain"|"pricing",
            "agent_name": str,
            "triggered": bool,
            "skip_reason": str|None,       # set only when triggered is False
            "finding": {...real numbers...} | None,
            "narrative": str,
            "ai_used": bool,
          }, ...
        ],
      }
    """
    waste = compute_waste_signals(rows)
    recurring_debits = detect_recurring_outflows(rows)

    if not waste["signals"] and not recurring_debits:
        return {
            "triggered": False,
            "reason_not_triggered": (
                "لم يرصد وكيل التدقيق أي شذوذ حقيقي في البيانات المرفوعة (لا هدر، لا عمليات "
                "متكررة مشبوهة) — لا داعي لتصعيد السلسلة."
                if lang == "ar" else
                "The Audit agent found no genuine anomaly in the uploaded data (no waste, no "
                "suspicious recurring transactions) -- no reason to escalate the chain."
            ),
            "stages": [],
        }

    audit_finding = {
        "waste_signals": waste["signals"],
        "total_waste": waste["total_waste"],
        "recurring_debits": recurring_debits,
    }

    stages = [_run_audit_stage(audit_finding, ai_service, lang)]

    financial_stage, cash_impact_ratio = _run_financial_stage(audit_finding, waste, rows, ai_service, lang)
    stages.append(financial_stage)

    procurement_related = any(s["type"] in _PROCUREMENT_SIGNAL_TYPES for s in waste["signals"])
    if procurement_related:
        stages.append(_run_supply_chain_stage(waste, ai_service, lang))
    else:
        stages.append(_skipped_stage(
            "supply_chain", "وكيل سلاسل الإمداد (COO)" if lang == "ar" else "Supply Chain (COO)",
            "لا توجد مؤشرات هدر متعلقة بالمشتريات أو المخزون في هذا الملف." if lang == "ar" else
            "No procurement/inventory-related waste signals in this file.",
        ))

    if cash_impact_ratio is not None and cash_impact_ratio >= MATERIALITY_REVENUE_SHARE:
        stages.append(_run_pricing_stage(rows, cash_impact_ratio, ai_service, lang))
    else:
        stages.append(_skipped_stage(
            "pricing", "أخصائي التسعير وهوامش الربح" if lang == "ar" else "Pricing Strategist",
            "الأثر المالي المكتشف لا يتجاوز النسبة التي تستدعي تدخلاً تسعيرياً فورياً." if lang == "ar" else
            "The detected financial impact does not cross the threshold that would warrant an "
            "immediate pricing intervention.",
        ))

    return {"triggered": True, "reason_not_triggered": None, "stages": stages}
