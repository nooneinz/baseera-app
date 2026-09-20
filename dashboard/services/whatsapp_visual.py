"""
On-demand financial visuals for WhatsApp.

When a user asks for a "صورة / رسم / تقرير" after their data is in, Baseera
renders a REAL summary card from their own numbers (never an AI-hallucinated
picture) and sends it back as a WhatsApp image. Numbers come from the same
deterministic engine the dashboard uses (compute_transaction_signal), so the
image agrees with the site exactly.

Rendered with Pillow (already a dependency) rather than matplotlib, to stay
light on the memory-constrained web instance. Labels are English + numerals
for now (Arabic-in-image needs a bundled Arabic font; that's a follow-up);
the numbers are the point and read the same in any language.
"""
import io
import os
import logging
from datetime import date

logger = logging.getLogger(__name__)

_AR_FONT = os.path.join(os.path.dirname(__file__), "fonts", "Amiri.ttf")


def _shape(text):
    """Reshape + bidi-order Arabic so it renders connected and right-to-left in
    a raster image. Returns the text unchanged if the libraries aren't present."""
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

# Palette (kept in sync with Baseera's teal identity).
_TEAL = (14, 90, 83)
_TEAL_LIGHT = (18, 133, 122)
_GREEN = (22, 143, 108)
_RED = (200, 68, 68)
_INK = (18, 33, 30)
_MUTED = (90, 107, 101)
_GROUND = (244, 246, 245)
_CARD = (255, 255, 255)


def wants_visual(text):
    t = (text or "").lower()
    return any(term in t for term in _VISUAL_TERMS)


def _font(size, bold=False):
    from PIL import ImageFont
    # Prefer the bundled Cairo face (has Arabic + Latin); fall back to DejaVu,
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


def _fmt(n):
    try:
        return f"{round(float(n)):,}"
    except (TypeError, ValueError):
        return "0"


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

        W, H = 900, 620
        img = Image.new("RGB", (W, H), _GROUND)
        d = ImageDraw.Draw(img)

        cur = "ر.ع"

        # Header band
        d.rectangle([0, 0, W, 96], fill=_TEAL)
        d.text((40, 24), _shape("بصيرة"), font=_font(40, True), fill=(255, 255, 255))
        d.text((285, 38), _shape("الملخّص المالي"), font=_font(26), fill=(220, 240, 236))
        d.text((W - 190, 40), date.today().isoformat(), font=_font(20), fill=(200, 226, 221))

        # Three stat tiles (right-aligned label + value for a natural RTL read)
        tiles = [
            ("الدخل", income, _GREEN),
            ("المصروف", expense, _RED),
            ("الصافي", net, _GREEN if net >= 0 else _RED),
        ]
        tw, th, gap, x0, y0 = 260, 120, 20, 40, 128
        for i, (label, val, color) in enumerate(tiles):
            x = x0 + i * (tw + gap)
            d.rounded_rectangle([x, y0, x + tw, y0 + th], radius=18, fill=_CARD)
            d.rectangle([x + tw - 8, y0, x + tw, y0 + th], fill=color)  # accent on the right (RTL)
            d.text((x + tw - 26, y0 + 20), _shape(label), font=_font(22, True), fill=_MUTED, anchor="ra")
            d.text((x + tw - 26, y0 + 52), _shape(f"{_fmt(val)} {cur}"), font=_font(32, True), fill=color, anchor="ra")

        # Income vs Expense bar chart
        cx0, cy0, cx1, cy1 = 40, 300, W - 40, 560
        d.rounded_rectangle([cx0, cy0, cx1, cy1], radius=18, fill=_CARD)
        d.text((cx1 - 26, cy0 + 18), _shape("الدخل مقابل المصروف"), font=_font(24, True), fill=_INK, anchor="ra")
        base_y = cy1 - 60
        top_y = cy0 + 115
        d.line([cx0 + 40, base_y, cx1 - 40, base_y], fill=(210, 220, 217), width=2)
        peak = max(income, expense, 1)
        bars = [("الدخل", income, _GREEN), ("المصروف", expense, _RED)]
        bar_w = 150
        centers = [cx0 + 200, cx1 - 200]
        for (label, val, color), cx in zip(bars, centers):
            h = int((val / peak) * (base_y - top_y))
            d.rounded_rectangle([cx - bar_w // 2, base_y - h, cx + bar_w // 2, base_y],
                                radius=10, fill=color)
            d.text((cx, base_y - h - 28), _shape(f"{_fmt(val)}"), font=_font(22, True),
                   fill=color, anchor="mm")
            d.text((cx, base_y + 22), _shape(label), font=_font(22), fill=_MUTED, anchor="mm")

        # Footer note (biggest expense, if any) -- right-aligned RTL
        if top and top.get("name"):
            note = _shape(f"أكبر مصروف: {top['name']} = {_fmt(top.get('total'))} {cur}")
            d.text((W - 40, 578), note, font=_font(20), fill=_MUTED, anchor="ra")
        d.text((40, 592), _shape("أُنشئ بواسطة بصيرة"), font=_font(16), fill=(150, 165, 160))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.info("render_summary_image failed: %s", e)
        return None
