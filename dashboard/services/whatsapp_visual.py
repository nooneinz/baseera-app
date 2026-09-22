"""
On-demand financial visuals for WhatsApp.

When a user asks for a "صورة / رسم / تقرير" after their data is in, Baseera
renders a REAL summary card from their own numbers (never an AI-hallucinated
picture) and sends it back as a WhatsApp image. Numbers come from the same
deterministic engine the dashboard uses (compute_transaction_signal), so the
image agrees with the site exactly.

Rendered with Pillow (already a dependency) rather than matplotlib, to stay
light on the memory-constrained web instance.

Arabic text: when Pillow is built with libraqm we pass RAW logical Arabic and
let raqm do shaping + bidi (direction="rtl"). Manually reshaping first (with
arabic_reshaper/bidi) and THEN handing it to raqm double-processes the string
and renders it disconnected and reversed — that was the old bug. We only fall
back to arabic_reshaper/bidi when raqm is unavailable.

The card carries Baseera's identity: the eye logo and the brand's Nile/Glow
palette (green/red stay reserved for income/expense, which are semantic).
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
    """True when Pillow can shape complex text (Arabic) itself."""
    try:
        from PIL import features
        return features.check("raqm")
    except Exception:
        return False


_HAS_RAQM = _has_raqm()


def _shape(text):
    """With raqm we return the raw logical text (raqm shapes + orders it). Only
    when raqm is missing do we reshape + bidi-order manually so Arabic still
    renders connected and right-to-left."""
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
    # "تقرير/report" here means the visual summary card
    "تقرير", "ملخص", "ملخّص", "report", "summary",
)

# Palette — Baseera's identity (Nile / Glow), with green/red reserved for the
# semantic income/expense meaning.
_NILE = (43, 36, 112)      # #2b2470 brand primary (header)
_GLOW = (124, 108, 240)    # #7c6cf0 brand accent
_GREEN = (15, 157, 107)
_RED = (200, 68, 68)
_INK = (30, 27, 75)        # #1e1b4b
_MUTED = (91, 87, 118)     # #5b5776
_GROUND = (246, 245, 251)  # soft lavender ground
_CARD = (255, 255, 255)


def wants_visual(text):
    t = (text or "").lower()
    return any(term in t for term in _VISUAL_TERMS)


def _font(size, bold=False):
    from PIL import ImageFont
    # Prefer the bundled Amiri face (has Arabic + Latin); fall back to DejaVu,
    # then Pillow's default, so rendering never crashes on a bare environment.
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
    """Draw text, letting raqm handle Arabic shaping + RTL ordering when the
    string actually contains Arabic (numbers/dates stay left-to-right)."""
    kwargs = {}
    if _HAS_RAQM and _AR_RE.search(text or ""):
        kwargs["direction"] = "rtl"
    try:
        d.text(xy, _shape(text), font=font, fill=fill, anchor=anchor, **kwargs)
    except Exception:
        # Some Pillow builds reject direction+anchor combos; retry plainly.
        d.text(xy, _shape(text), font=font, fill=fill, anchor=anchor)


def _fmt(n):
    try:
        return f"{round(float(n)):,}"
    except (TypeError, ValueError):
        return "0"


def _paste_logo(img, x, y, box):
    """Paste the Baseera eye logo (fit inside box×box) with its alpha; no-op if
    the asset is missing."""
    try:
        from PIL import Image
        logo = Image.open(_LOGO).convert("RGBA")
        logo.thumbnail((box, box), Image.LANCZOS)
        img.paste(logo, (int(x), int(y)), logo)
    except Exception as e:
        logger.info("logo paste skipped: %s", e)


def render_summary_image(rows):
    """
    Return PNG bytes of a financial summary card for these rows, or None when
    there is nothing meaningful to chart. Never raises.
    """
    try:
        from PIL import Image, ImageDraw
        from dashboard.services.first_win_insights import compute_transaction_signal

        sig = compute_transaction_signal(rows or [])
        if not sig:
            return None
        income = float(sig.get("total_income") or 0)
        expense = float(sig.get("total_expense") or 0)
        net = float(sig.get("net") or (income - expense))
        if income <= 0 and expense <= 0:
            return None
        top = (sig.get("top_groups") or [None])[0]

        W, H = 900, 640
        img = Image.new("RGB", (W, H), _GROUND)
        d = ImageDraw.Draw(img)

        cur = "ر.ع"

        # Header band — Baseera Nile, with the eye logo + brand name on the RTL
        # (right) side and the date on the left.
        HH = 110
        d.rectangle([0, 0, W, HH], fill=_NILE)
        _paste_logo(img, W - 40 - 64, (HH - 64) // 2, 64)
        _text(d, (W - 40 - 64 - 18, 22), "بصيرة", _font(38, True), (255, 255, 255), anchor="ra")
        _text(d, (W - 40 - 64 - 18, 66), "الملخّص المالي", _font(22), (206, 198, 244), anchor="ra")
        _text(d, (40, 44), date.today().isoformat(), _font(20), (206, 198, 244))

        # Three stat tiles (right-aligned label + value for a natural RTL read)
        tiles = [
            ("الدخل", income, _GREEN),
            ("المصروف", expense, _RED),
            ("الصافي", net, _GREEN if net >= 0 else _RED),
        ]
        tw, th, gap, x0, y0 = 260, 120, 20, 40, 142
        for i, (label, val, color) in enumerate(tiles):
            x = x0 + i * (tw + gap)
            d.rounded_rectangle([x, y0, x + tw, y0 + th], radius=18, fill=_CARD)
            d.rectangle([x + tw - 8, y0, x + tw, y0 + th], fill=color)  # accent on the right (RTL)
            _text(d, (x + tw - 26, y0 + 20), label, _font(22, True), _MUTED, anchor="ra")
            _text(d, (x + tw - 26, y0 + 52), f"{_fmt(val)} {cur}", _font(32, True), color, anchor="ra")

        # Income vs Expense bar chart
        cx0, cy0, cx1, cy1 = 40, 314, W - 40, 574
        d.rounded_rectangle([cx0, cy0, cx1, cy1], radius=18, fill=_CARD)
        _text(d, (cx1 - 26, cy0 + 18), "الدخل مقابل المصروف", _font(24, True), _INK, anchor="ra")
        base_y = cy1 - 60
        top_y = cy0 + 115
        d.line([cx0 + 40, base_y, cx1 - 40, base_y], fill=(224, 220, 240), width=2)
        peak = max(income, expense, 1)
        bars = [("الدخل", income, _GREEN), ("المصروف", expense, _RED)]
        bar_w = 150
        centers = [cx0 + 200, cx1 - 200]
        for (label, val, color), cx in zip(bars, centers):
            h = int((val / peak) * (base_y - top_y))
            d.rounded_rectangle([cx - bar_w // 2, base_y - h, cx + bar_w // 2, base_y],
                                radius=10, fill=color)
            _text(d, (cx, base_y - h - 28), f"{_fmt(val)}", _font(22, True), color, anchor="mm")
            _text(d, (cx, base_y + 22), label, _font(22), _MUTED, anchor="mm")

        # Footer note (biggest expense, if any) -- right-aligned RTL
        if top and top.get("name"):
            _text(d, (W - 40, 592), f"أكبر مصروف: {top['name']} = {_fmt(top.get('total'))} {cur}",
                  _font(20), _MUTED, anchor="ra")
        _text(d, (40, 606), "أُنشئ بواسطة بصيرة", _font(16), _GLOW)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.info("render_summary_image failed: %s", e)
        return None
