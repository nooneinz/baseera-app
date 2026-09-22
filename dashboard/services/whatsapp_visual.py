"""
On-demand financial visuals for WhatsApp.

When a user asks for a "صورة / رسم / تقرير" after their data is in, Baseera
renders a REAL analysis card from their own numbers (never an AI-hallucinated
picture). Every figure comes from the same deterministic engines the dashboard
uses — compute_transaction_signal, compute_waste_signals, compute_glance — so
the image agrees with the site exactly and always shows the basis of the math
("محسوب من N حالة فعلية"), never invented numbers.

The card is built top-down onto a tall canvas and cropped to fit, so sections
(KPIs, the waste/leak block, the category split) appear only when the user's
data actually supports them.

Rendered with Pillow (already a dependency) rather than matplotlib, to stay
light on the memory-constrained web instance.

Arabic text: when Pillow is built with libraqm we pass RAW logical Arabic and
let raqm do shaping + bidi (direction="rtl"). Manually reshaping first and then
handing it to raqm double-processes the string and renders it disconnected —
that was the old bug. We only fall back to arabic_reshaper/bidi without raqm.

The card carries Baseera's identity: the eye logo and the brand's Nile/Glow
palette (green/red stay reserved for the semantic income/expense/waste meaning).
"""
import io
import os
import re
import logging
from datetime import date

logger = logging.getLogger(__name__)

_AR_FONT = os.path.join(os.path.dirname(__file__), "fonts", "Amiri.ttf")
_LOGO = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),  # dashboard/
    "static", "dashboard", "img", "logo.png",
)

_AR_RE = re.compile(r"[؀-ۿ]")


def _has_raqm():
    try:
        from PIL import features
        return features.check("raqm")
    except Exception:
        return False


_HAS_RAQM = _has_raqm()


def _shape(text):
    """With raqm, return raw logical text (raqm shapes + orders it). Without
    raqm, reshape + bidi-order manually so Arabic still renders connected RTL."""
    if _HAS_RAQM:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


# Keyword gate: only build+send an image when the user actually asks for one.
_VISUAL_TERMS = (
    "صورة", "صوره", "رسم", "رسمة", "رسمه", "مخطط", "شارت", "بياني", "انفوجرافيك",
    "chart", "graph", "image", "picture", "infographic", "visual",
    # "تقرير/report" here means the visual analysis card
    "تقرير", "ملخص", "ملخّص", "report", "summary",
)

# Palette — Baseera's identity (Nile / Glow); green/red reserved for the
# semantic income / expense / waste meaning.
_NILE = (43, 36, 112)       # #2b2470 brand primary
_GLOW = (124, 108, 240)     # #7c6cf0 brand accent
_GLOW_SOFT = (238, 235, 251)
_GREEN = (15, 157, 107)
_RED = (200, 68, 68)
_RED_SOFT = (253, 236, 236)
_INK = (30, 27, 75)         # #1e1b4b
_MUTED = (91, 87, 118)      # #5b5776
_GROUND = (246, 245, 251)
_CARD = (255, 255, 255)
_LINE = (236, 233, 248)


# A user asking specifically for a PDF gets the full multi-section report as a
# PDF document; any other visual ask gets the concise summary image.
_PDF_TERMS = ("pdf", "بي دي اف", "بيديإف", "بيدياف", "ملف pdf", "تقرير pdf", "بي دي إف")


def wants_visual(text):
    t = (text or "").lower()
    return any(term in t for term in _VISUAL_TERMS)


def wants_pdf(text):
    t = (text or "").lower()
    return any(term in t for term in _PDF_TERMS)


def _font(size, bold=False):
    from PIL import ImageFont
    candidates = [_AR_FONT] + (["DejaVuSans-Bold.ttf"] if bold else []) + ["DejaVuSans.ttf"]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _text(d, xy, text, font, fill, anchor=None):
    """Draw text, letting raqm handle Arabic shaping + RTL when the string
    contains Arabic (numbers/dates stay left-to-right)."""
    kwargs = {}
    if _HAS_RAQM and _AR_RE.search(text or ""):
        kwargs["direction"] = "rtl"
    try:
        d.text(xy, _shape(text), font=font, fill=fill, anchor=anchor, **kwargs)
    except Exception:
        d.text(xy, _shape(text), font=font, fill=fill, anchor=anchor)


def _fmt(n):
    try:
        v = float(n)
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{round(v):,}"
    except (TypeError, ValueError):
        return "0"


def _paste_logo(img, x, y, box):
    try:
        from PIL import Image
        logo = Image.open(_LOGO).convert("RGBA")
        logo.thumbnail((box, box), Image.LANCZOS)
        img.paste(logo, (int(x), int(y)), logo)
    except Exception as e:
        logger.info("logo paste skipped: %s", e)


def _cols_count(rows):
    for r in rows or []:
        if isinstance(r, dict):
            return len(r.keys())
    return 0


def _build_card(rows, full=False):
    """
    Build and return the analysis card as a cropped PIL image, or None when
    there is nothing meaningful to show. Sections adapt to the data: a KPI row,
    a waste/leak block (with its evidence and math), and a top split. When
    `full` is True every waste signal and the income/expense split are included
    (used for the downloadable PDF report); otherwise only the headline block is
    drawn (the concise WhatsApp image).
    """
    from PIL import Image, ImageDraw
    from dashboard.services.first_win_insights import compute_transaction_signal, compute_glance
    from dashboard.services.waste_analyzer import compute_waste_signals

    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return None

    sig = compute_transaction_signal(rows) or {}
    glance = compute_glance(rows) or {}
    waste = compute_waste_signals(rows) or {}

    income = float(sig.get("total_income") or 0)
    expense = float(sig.get("total_expense") or 0)
    net = float(sig.get("net") or (income - expense))
    top_groups = sig.get("top_groups") or []
    total_waste = float(waste.get("total_waste") or 0)
    waste_signals = [s for s in (waste.get("signals") or []) if s]

    # Nothing at all to say?
    if income <= 0 and expense <= 0 and not glance.get("total") and total_waste <= 0:
        return None

    cur = "ر.ع"
    W = 900
    PAD = 40
    img = Image.new("RGB", (W, 1500), _GROUND)
    d = ImageDraw.Draw(img)

    # ---- Header ----------------------------------------------------------
    HH = 110
    d.rectangle([0, 0, W, HH], fill=_NILE)
    _paste_logo(img, W - 40 - 64, (HH - 64) // 2, 64)
    _text(d, (W - 40 - 64 - 18, 22), "بصيرة", _font(38, True), (255, 255, 255), anchor="ra")
    _text(d, (W - 40 - 64 - 18, 66), "تحليل مالي من بياناتك", _font(20), (206, 198, 244), anchor="ra")
    _text(d, (40, 44), date.today().isoformat(), _font(20), (206, 198, 244))
    y = HH + 24

    # ---- KPI row (adaptive) ---------------------------------------------
    # Prefer income/expense/net when the file reads as transactions;
    # otherwise fall back to a sales glance. Always show record/column count
    # and the total waste when we found any.
    tiles = []
    if income > 0 or expense > 0:
        tiles.append(("الدخل", f"{_fmt(income)} {cur}", _GREEN, None))
        tiles.append(("المصروف", f"{_fmt(expense)} {cur}", _RED, None))
        tiles.append(("الصافي", f"{_fmt(net)} {cur}", _GREEN if net >= 0 else _RED,
                      "= الدخل − المصروف"))
    elif glance.get("total") is not None:
        total = float(glance.get("total") or 0)
        rc = int(glance.get("row_count") or len(rows))
        avg = (total / rc) if rc else 0
        label = glance.get("amount_label") or "الإجمالي"
        tiles.append((f"إجمالي {label}", f"{_fmt(total)} {cur}", _NILE, None))
        tiles.append(("المتوسط", f"{_fmt(avg)} {cur}", _GLOW, "= الإجمالي ÷ السجلات"))
    # records / columns tile
    tiles.append(("السجلات", f"{len(rows):,}", _INK, f"{_cols_count(rows)} أعمدة"))
    if total_waste > 0:
        tiles.append(("إجمالي الهدر", f"{_fmt(total_waste)} {cur}", _RED, "محسوب من عمود الهدر"))

    tiles = tiles[:4]
    n = len(tiles)
    gap = 20
    tw = (W - 2 * PAD - (n - 1) * gap) // n
    th = 118
    for i, (label, val, color, sub) in enumerate(tiles):
        x = PAD + i * (tw + gap)
        d.rounded_rectangle([x, y, x + tw, y + th], radius=18, fill=_CARD)
        d.rectangle([x + tw - 8, y, x + tw, y + th], fill=color)  # RTL accent
        _text(d, (x + tw - 22, y + 16), label, _font(18, True), _MUTED, anchor="ra")
        _text(d, (x + tw - 22, y + 46), val, _font(26, True), color, anchor="ra")
        if sub:
            _text(d, (x + tw - 22, y + 86), sub, _font(14), _MUTED, anchor="ra")
    y += th + 24

    # ---- Waste / leak block (the headline insight) ----------------------
    lead = waste_signals[0] if waste_signals else None
    if lead:
        examples = [e for e in (lead.get("examples") or []) if e.get("name")]
        block_h = 150 + (len(examples[:3]) * 46) + 46
        d.rounded_rectangle([PAD, y, W - PAD, y + block_h], radius=18, fill=_CARD)
        d.rounded_rectangle([PAD, y, W - PAD, y + 6], radius=3, fill=_RED)  # top accent
        iy = y + 26
        # big amount + label
        amt = lead.get("currency_amount") or total_waste
        _text(d, (W - PAD - 24, iy), f"{_fmt(amt)} {cur}", _font(44, True), _RED, anchor="ra")
        _text(d, (W - PAD - 24, iy + 58), "أكبر مصدر هدر قابل للإيقاف", _font(20, True), _INK, anchor="ra")
        _text(d, (W - PAD - 24, iy + 90), str(lead.get("title") or ""), _font(16), _MUTED, anchor="ra")
        # evidence line
        ev = int(lead.get("evidence_count") or 0)
        by = iy + 128
        _text(d, (W - PAD - 24, by), "الدليل من بياناتك — لا تخمين", _font(16, True), _GREEN, anchor="ra")
        # item bars
        by += 34
        peak = max([e.get("value") or 0 for e in examples[:3]] or [1]) or 1
        bx0, bx1 = PAD + 40, W - PAD - 220
        for e in examples[:3]:
            v = float(e.get("value") or 0)
            bar_w = int((v / peak) * (bx1 - bx0))
            d.rounded_rectangle([bx1 - bar_w, by, bx1, by + 26], radius=8, fill=_RED)
            _text(d, (bx1 - bar_w - 12, by + 13), f"{_fmt(v)} {cur}", _font(16, True), _RED, anchor="rm")
            _text(d, (W - PAD - 24, by + 13), str(e.get("name") or "—"), _font(16), _INK, anchor="rm")
            by += 46
        if ev:
            _text(d, (W - PAD - 24, by + 6),
                  f"محسوب من {ev} حالة فعلية — بصيرة تعرض مصدر الرقم، لا تختلقه",
                  _font(15), _MUTED, anchor="ra")
        y += block_h + 24

    # ---- Extra waste sources (full report only) -------------------------
    if full:
        extra = (waste_signals[1:] if lead else waste_signals)[:4]
        if extra:
            block_h = 60 + len(extra) * 40
            d.rounded_rectangle([PAD, y, W - PAD, y + block_h], radius=18, fill=_CARD)
            _text(d, (W - PAD - 24, y + 20), "مصادر هدر إضافية", _font(20, True), _INK, anchor="ra")
            by = y + 58
            for s in extra:
                amt = s.get("currency_amount") or 0
                ev = int(s.get("evidence_count") or 0)
                _text(d, (W - PAD - 24, by), str(s.get("title") or ""), _font(15), _INK, anchor="ra")
                _text(d, (PAD + 24, by), (f"{_fmt(amt)} {cur}" if amt else f"{ev} حالة"),
                      _font(15, True), _RED, anchor="la")
                by += 40
            y += block_h + 24

    # ---- Top income/expense split (shown when there's no waste headline,
    #      or always in the full report) ---------------------------------
    if top_groups and (full or not lead):
        rows_g = top_groups[:3]
        block_h = 90 + len(rows_g) * 46
        d.rounded_rectangle([PAD, y, W - PAD, y + block_h], radius=18, fill=_CARD)
        _text(d, (W - PAD - 24, y + 22), "أكبر وجهات الصرف", _font(22, True), _INK, anchor="ra")
        by = y + 74
        peak = max([g.get("total") or 0 for g in rows_g] or [1]) or 1
        bx0, bx1 = PAD + 40, W - PAD - 240
        for g in rows_g:
            v = float(g.get("total") or 0)
            bar_w = int((v / peak) * (bx1 - bx0))
            d.rounded_rectangle([bx1 - bar_w, by, bx1, by + 26], radius=8, fill=_GLOW)
            _text(d, (bx1 - bar_w - 12, by + 13), f"{_fmt(v)} {cur}", _font(16, True), _NILE, anchor="rm")
            _text(d, (W - PAD - 24, by + 13), str(g.get("name") or "—"), _font(16), _INK, anchor="rm")
            by += 46
        y += block_h + 24

    # ---- Footer ----------------------------------------------------------
    _text(d, (W - PAD, y + 4), "أُنشئ بواسطة بصيرة — أرقام حقيقية من ملفاتك", _font(16), _GLOW, anchor="ra")
    y += 40

    return img.crop((0, 0, W, min(y, 1500)))


def render_summary_image(rows):
    """Return PNG bytes of the concise analysis card, or None. Never raises."""
    try:
        img = _build_card(rows, full=False)
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.info("render_summary_image failed: %s", e)
        return None


def render_report_pdf(rows):
    """Return PDF bytes of the full multi-section report (every waste source +
    the income/expense split), or None. Never raises."""
    try:
        img = _build_card(rows, full=True)
        if img is None:
            return None
        buf = io.BytesIO()
        # white background flattens cleanly into a single-page PDF
        img.convert("RGB").save(buf, format="PDF", resolution=150.0)
        return buf.getvalue()
    except Exception as e:
        logger.info("render_report_pdf failed: %s", e)
        return None
