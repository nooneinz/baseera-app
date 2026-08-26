"""
AI-driven Financial Waste Detection — "كشف الهدر المالي".

The platform's premise is that Baseera replaces a dedicated data analyst:
the user uploads whatever messy files they have, and the AI figures out
where money is leaking. That means waste must be *inferred from the data*,
not read off a column literally named "هدر".

This module does that in two stages:

1. A deterministic pre-pass (compute_waste_signals) that scans the real
   uploaded rows for concrete, arithmetic evidence of leakage — negative or
   near-zero margins, items sold below cost, price inconsistency for the
   same item, explicit waste/damaged quantities, and dead stock. Every
   signal it returns carries the actual numbers and the rows it came from.

2. An LLM pass (analyze_waste) that receives ONLY those verified signals
   (never raw invented figures) and turns them into an executive-readable
   diagnosis: what the leak is, why it is happening, and what to do.

If the LLM is unavailable, the deterministic signals are still returned and
rendered on their own — the numbers never depend on the model being up. The
model's job is interpretation, not arithmetic. It is explicitly forbidden
from inventing figures, and any total it reports is recomputed here.
"""
import json
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

# Column-name hints. These only *locate* candidate columns; whether a column
# actually holds usable numbers is decided by parsing its values.
COL_HINTS = {
    "price": ["سعر البيع", "سعر الوحدة", "سعر", "price", "unit_price", "selling"],
    "cost": ["تكلفة الوحدة", "تكلفة", "cost", "unit_cost", "cogs", "شراء"],
    "qty": ["الكمية", "كمية", "عدد", "qty", "quantity", "sold", "units"],
    "revenue": ["إجمالي المبيعات", "الإجمالي", "إجمالي", "مبيعات", "إيراد", "revenue", "sales", "total", "amount"],
    "waste_qty": ["كمية مهدرة", "هدر", "تالف", "منتهي", "waste", "damaged", "expired", "spoiled", "loss"],
    "product": ["الصنف", "المنتج", "اسم", "صنف", "منتج", "بند", "product", "item", "name", "description"],
    "category": ["التصنيف", "الفئة", "فئة", "تصنيف", "قسم", "category", "type", "department"],
    "date": ["التاريخ", "تاريخ", "يوم", "شهر", "date", "day", "month", "period"],
}


def _find_col(columns, kind):
    """Finds the best-matching column name for a semantic kind, or None."""
    hints = COL_HINTS.get(kind, [])
    lowered = {str(c): str(c).lower() for c in columns}
    for hint in hints:
        for original, low in lowered.items():
            if hint.lower() in low:
                return original
    return None


def _to_num(value):
    """Parses a possibly messy cell (currency symbols, separators) into a float, or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            f = float(value)
            return None if f != f else f  # filter NaN
        except (TypeError, ValueError):
            return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    import re
    match = re.search(r"-?\d+\.?\d*", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def compute_waste_signals(rows):
    """
    Deterministic waste evidence extracted from the user's real rows.

    Returns:
      {
        "signals": [ {type, title, amount, currency_amount, detail, evidence_count, examples[]} ],
        "total_waste": float,       # sum of quantified monetary leakage
        "columns_used": {...},
        "analyzable": bool,         # False when the file lacks the columns needed to reason about waste
      }
    """
    result = {
        "signals": [],
        "total_waste": 0.0,
        "columns_used": {},
        "analyzable": False,
    }

    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return result

    columns = list(rows[0].keys())
    col_price = _find_col(columns, "price")
    col_cost = _find_col(columns, "cost")
    col_qty = _find_col(columns, "qty")
    col_revenue = _find_col(columns, "revenue")
    col_waste_qty = _find_col(columns, "waste_qty")
    col_product = _find_col(columns, "product")
    col_category = _find_col(columns, "category")

    # A revenue-ish column can be mistaken for a price column; if both resolve
    # to the same header, prefer keeping it as revenue and drop the price role.
    if col_price and col_price == col_revenue:
        col_price = None

    result["columns_used"] = {
        "price": col_price, "cost": col_cost, "qty": col_qty,
        "revenue": col_revenue, "waste_qty": col_waste_qty,
        "product": col_product, "category": col_category,
    }

    total_waste = 0.0

    # --- Signal 1: explicit wasted/damaged/expired quantity -----------------
    if col_waste_qty:
        wasted_value = 0.0
        wasted_units = 0.0
        per_item = {}
        for row in rows:
            wq = _to_num(row.get(col_waste_qty))
            if not wq or wq <= 0:
                continue
            unit_cost = _to_num(row.get(col_cost)) if col_cost else None
            if unit_cost is None and col_price:
                unit_cost = _to_num(row.get(col_price))
            wasted_units += wq
            if unit_cost:
                value = wq * unit_cost
                wasted_value += value
                key = str(row.get(col_product) or row.get(col_category) or "—")
                per_item[key] = per_item.get(key, 0.0) + value
        if wasted_units > 0:
            top = sorted(per_item.items(), key=lambda kv: kv[1], reverse=True)[:3]
            total_waste += wasted_value
            result["signals"].append({
                "type": "explicit_waste",
                "title": "هدر مباشر مسجّل في الملف (تالف/منتهي الصلاحية)",
                "amount": round(wasted_units, 2),
                "currency_amount": round(wasted_value, 2),
                "detail": f"إجمالي {wasted_units:,.0f} وحدة مهدرة"
                          + (f" بقيمة تقديرية {wasted_value:,.2f}" if wasted_value else ""),
                "evidence_count": sum(1 for r in rows if (_to_num(r.get(col_waste_qty)) or 0) > 0),
                "examples": [{"name": k, "value": round(v, 2)} for k, v in top],
            })

    # --- Signal 2: items sold at or below cost (negative margin) -----------
    if col_price and col_cost:
        loss_value = 0.0
        per_item = {}
        below_cost_rows = 0
        for row in rows:
            price = _to_num(row.get(col_price))
            cost = _to_num(row.get(col_cost))
            if price is None or cost is None or cost <= 0:
                continue
            if price < cost:
                qty = _to_num(row.get(col_qty)) if col_qty else 1
                qty = qty if (qty and qty > 0) else 1
                loss = (cost - price) * qty
                loss_value += loss
                below_cost_rows += 1
                key = str(row.get(col_product) or row.get(col_category) or "—")
                per_item[key] = per_item.get(key, 0.0) + loss
        if below_cost_rows:
            top = sorted(per_item.items(), key=lambda kv: kv[1], reverse=True)[:3]
            total_waste += loss_value
            result["signals"].append({
                "type": "below_cost_sales",
                "title": "بيع بأقل من التكلفة (هامش سالب)",
                "amount": below_cost_rows,
                "currency_amount": round(loss_value, 2),
                "detail": f"{below_cost_rows} عملية بيع تمت بسعر أقل من تكلفة الشراء، بخسارة تراكمية {loss_value:,.2f}",
                "evidence_count": below_cost_rows,
                "examples": [{"name": k, "value": round(v, 2)} for k, v in top],
            })

    # --- Signal 3: dangerously thin margins (< 5%) -------------------------
    if col_price and col_cost:
        thin = {}
        thin_rows = 0
        for row in rows:
            price = _to_num(row.get(col_price))
            cost = _to_num(row.get(col_cost))
            if not price or price <= 0 or cost is None or cost <= 0:
                continue
            margin = (price - cost) / price
            if 0 <= margin < 0.05:
                thin_rows += 1
                key = str(row.get(col_product) or row.get(col_category) or "—")
                thin[key] = thin.get(key, 0.0) + 1
        if thin_rows:
            top = sorted(thin.items(), key=lambda kv: kv[1], reverse=True)[:3]
            result["signals"].append({
                "type": "thin_margin",
                "title": "هوامش ربح شديدة الانخفاض (أقل من 5%)",
                "amount": thin_rows,
                "currency_amount": 0.0,
                "detail": f"{thin_rows} عملية بهامش ربح أقل من 5% — أي تقلب بسيط في التكلفة يحوّلها إلى خسارة",
                "evidence_count": thin_rows,
                "examples": [{"name": k, "value": int(v)} for k, v in top],
            })

    # --- Signal 4: same product sold at inconsistent prices ----------------
    if col_product and col_price:
        by_product = {}
        for row in rows:
            name = row.get(col_product)
            price = _to_num(row.get(col_price))
            if name is None or price is None or price <= 0:
                continue
            by_product.setdefault(str(name), []).append(price)

        leakage = 0.0
        offenders = []
        for name, prices in by_product.items():
            if len(prices) < 3:
                continue
            hi, lo = max(prices), min(prices)
            if lo <= 0 or hi <= lo:
                continue
            spread = (hi - lo) / hi
            if spread > 0.25:  # >25% variation on the identical product
                # Money left on the table: every unit not sold at the top price.
                lost = sum(hi - p for p in prices)
                leakage += lost
                offenders.append((name, lost, lo, hi))
        if offenders:
            offenders.sort(key=lambda t: t[1], reverse=True)
            total_waste += leakage
            result["signals"].append({
                "type": "price_inconsistency",
                "title": "تذبذب غير منضبط في تسعير نفس المنتج",
                "amount": len(offenders),
                "currency_amount": round(leakage, 2),
                "detail": f"{len(offenders)} منتجاً بيع بأسعار متفاوتة بأكثر من 25% للوحدة نفسها، بفارق تراكمي {leakage:,.2f}",
                "evidence_count": len(offenders),
                "examples": [
                    {"name": n, "value": round(v, 2), "range": f"{lo:,.2f} - {hi:,.2f}"}
                    for n, v, lo, hi in offenders[:3]
                ],
            })

    # --- Signal 5: dead stock (bought/held but never sold) ------------------
    if col_product and col_qty and col_revenue:
        movement = {}
        for row in rows:
            name = str(row.get(col_product) or "—")
            qty = _to_num(row.get(col_qty)) or 0
            rev = _to_num(row.get(col_revenue)) or 0
            agg = movement.setdefault(name, {"qty": 0.0, "rev": 0.0})
            agg["qty"] += qty
            agg["rev"] += rev
        dead = [(n, v["qty"]) for n, v in movement.items() if v["qty"] > 0 and v["rev"] <= 0]
        if dead:
            dead.sort(key=lambda t: t[1], reverse=True)
            result["signals"].append({
                "type": "dead_stock",
                "title": "مخزون راكد بلا إيراد مقابل",
                "amount": len(dead),
                "currency_amount": 0.0,
                "detail": f"{len(dead)} صنفاً سُجلت له كميات دون أي إيراد مقابل — رأس مال مجمّد",
                "evidence_count": len(dead),
                "examples": [{"name": n, "value": round(q, 2)} for n, q in dead[:3]],
            })

    result["total_waste"] = round(total_waste, 2)
    # "Analyzable" means we had enough structure to reason about waste at all,
    # even if the healthy conclusion is that no leakage was found.
    result["analyzable"] = bool(
        (col_price and col_cost) or col_waste_qty or (col_product and col_price) or (col_product and col_qty and col_revenue)
    )
    return result


def analyze_waste(rows, ai_service=None, lang="ar", company_profile=None):
    """
    Full waste analysis: deterministic signals + AI interpretation.

    Returns:
      {
        "analyzable": bool,
        "total_waste": float,
        "signals": [...],           # verified, arithmetic evidence
        "diagnosis": str,           # AI narrative (or a data-derived fallback)
        "recommendations": [str],
        "ai_used": bool,
      }
    """
    computed = compute_waste_signals(rows)

    output = {
        "analyzable": computed["analyzable"],
        "total_waste": computed["total_waste"],
        "signals": computed["signals"],
        "diagnosis": "",
        "recommendations": [],
        "ai_used": False,
    }

    if not computed["analyzable"]:
        output["diagnosis"] = (
            "لا يحتوي الملف المرفوع على أعمدة كافية (مثل السعر والتكلفة أو الكميات) "
            "لتحليل الهدر المالي. أضف عمود تكلفة أو كمية لتفعيل هذا التحليل."
        )
        return output

    if not computed["signals"]:
        output["diagnosis"] = (
            "تم فحص بيانات الملف بالكامل بحثاً عن مؤشرات الهدر (هوامش سالبة، بيع تحت التكلفة، "
            "تذبذب التسعير، المخزون الراكد) ولم تُرصد أي حالات هدر واضحة في هذه البيانات."
        )
        return output

    # --- AI interpretation layer -----------------------------------------
    if ai_service is not None and getattr(ai_service, "client", None):
        profile_context = ""
        if company_profile is not None:
            try:
                profile_context = (
                    f"\nالملف الاستراتيجي للشركة: القطاع={company_profile.sector}، "
                    f"المرحلة={company_profile.growth_stage}، "
                    f"مستوى المخاطرة={company_profile.risk_tolerance}، "
                    f"ترتيب الأولويات={', '.join(company_profile.strategic_priorities_ranking)}. "
                    "اربط توصياتك بهذه الأولويات."
                )
            except Exception:
                profile_context = ""

        prompt = f"""أنت وكيل التدقيق المالي في منصة "بصيرة"، وتحل محل محلل بيانات مختص.

فيما يلي مؤشرات هدر مالي **مستخرجة حسابياً وموثقة** من ملف بيانات رفعه صاحب المنشأة:

{json.dumps(computed['signals'], ensure_ascii=False, indent=2)}

إجمالي الهدر المحسوب: {computed['total_waste']:,.2f}
{profile_context}

قواعد صارمة:
1. ممنوع منعاً باتاً اختراع أي رقم غير موجود أعلاه. استخدم الأرقام المعطاة فقط.
2. اشرح **سبب** الهدر وأثره التشغيلي، لا تكرر الأرقام فقط.
3. التوصيات يجب أن تكون قابلة للتنفيذ فوراً ومحددة بالأصناف/الفئات المذكورة أعلاه.

أعد **فقط** كائن JSON صالح بهذه المفاتيح:
{{
  "diagnosis": "تشخيص تنفيذي من 2-4 جمل يشرح أين يتسرب المال ولماذا",
  "recommendations": ["توصية تنفيذية محددة", "توصية ثانية", "توصية ثالثة"]
}}"""

        try:
            response = ai_service.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = (response.text or "").strip()
            if text.startswith("```json"):
                text = text.replace("```json", "", 1)
            if text.startswith("```"):
                text = text.replace("```", "", 1)
            if text.endswith("```"):
                text = text[:-3]
            data = json.loads(text.strip())
            diagnosis = str(data.get("diagnosis", "")).strip()
            recs = [str(r).strip() for r in (data.get("recommendations") or []) if str(r).strip()]
            if diagnosis:
                output["diagnosis"] = diagnosis
                output["recommendations"] = recs[:5]
                output["ai_used"] = True
        except Exception as e:
            logger.info("Waste AI interpretation unavailable, using computed fallback: %s", e)

    # Data-derived fallback narrative (no LLM, no invented numbers).
    if not output["diagnosis"]:
        top = max(computed["signals"], key=lambda s: s.get("currency_amount", 0) or 0)
        parts = [f"{s['title']} ({s['detail']})" for s in computed["signals"][:3]]
        output["diagnosis"] = (
            f"رصد التحليل {len(computed['signals'])} مؤشر هدر في بياناتك. "
            f"أبرزها: {top['title']}. التفاصيل: " + "؛ ".join(parts) + "."
        )
        output["recommendations"] = [
            f"راجع فوراً: {s['title']}" for s in computed["signals"][:3]
        ]

    return output
