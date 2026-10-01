"""
Sales Leads agent -- finds businesses that could buy the owner's products.

Pipeline (same boundary as the rest of Baseera: numbers are computed here, the
LLM only words things):
  1. pick_products_to_push(rows)  -- deterministic choice of which products to
     sell, from the waste engine's own evidence (dead stock first).
  2. search_places(...)           -- Google Places API (New) text search for
     buyer types in a city ("مطاعم في مسقط"). Only a buyer type and a city
     leave the platform: no product names, prices, costs or any other user
     data are sent to Google.
  3. score_place(...)            -- deterministic 0-100 lead score.
  4. draft_outreach(...)         -- a short message the owner reviews and sends
     themselves. Nothing is ever sent automatically, and the draft never
     invents prices, discounts or claims the owner did not supply.
"""
import json
import logging
import math
import re
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress",
    "places.internationalPhoneNumber", "places.nationalPhoneNumber",
    "places.websiteUri", "places.googleMapsUri", "places.rating",
    "places.userRatingCount", "places.businessStatus",
])
MAX_RESULTS = 20
TIMEOUT_SECONDS = 10

BUYER_PRESETS = {
    "restaurants": {"ar": "مطاعم", "en": "restaurants"},
    "cafes": {"ar": "مقاهي", "en": "cafes"},
    "supermarkets": {"ar": "سوبرماركت", "en": "supermarkets"},
    "retail": {"ar": "محلات تجزئة", "en": "retail shops"},
    "hotels": {"ar": "فنادق", "en": "hotels"},
    "wholesale": {"ar": "تجار جملة", "en": "wholesalers"},
}


class LeadSearchError(Exception):
    """Raised with a user-safe message when the Places lookup cannot run."""


def get_api_key():
    return getattr(settings, "GOOGLE_MAPS_API_KEY", "") or ""


def pick_products_to_push(rows, limit=5):
    """
    Which products the owner should push to new buyers, from real rows only.

    Stuck (dead) stock comes first -- it is money sitting on a shelf -- then
    the remaining products by unit margin when price and cost columns exist.
    Returns [{"name", "reason", "value"}] (reason: dead_stock | margin).
    """
    from .waste_analyzer import compute_waste_signals, _find_col, _to_num

    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return []
    picked, seen = [], set()

    signals = compute_waste_signals(rows)
    for sig in signals.get("signals", []):
        if sig.get("type") != "dead_stock":
            continue
        for ex in sig.get("examples", []):
            name = str(ex.get("name") or "").strip()
            if name and name != "—" and name not in seen:
                seen.add(name)
                picked.append({"name": name, "reason": "dead_stock", "value": ex.get("value")})

    columns = list(rows[0].keys())
    col_product, col_price, col_cost = (_find_col(columns, k) for k in ("product", "price", "cost"))
    if col_product and col_price and col_cost:
        margins = {}
        for r in rows:
            name = str(r.get(col_product) or "").strip()
            price, cost = _to_num(r.get(col_price)), _to_num(r.get(col_cost))
            if name and price is not None and cost is not None and price > cost:
                margins[name] = max(margins.get(name, 0), round(price - cost, 3))
        for name, m in sorted(margins.items(), key=lambda kv: -kv[1]):
            if name not in seen:
                seen.add(name)
                picked.append({"name": name, "reason": "margin", "value": m})
    return picked[:limit]


def build_query(buyer_type, city, lang="ar"):
    preset = BUYER_PRESETS.get(buyer_type)
    label = preset[lang if lang in ("ar", "en") else "ar"] if preset else str(buyer_type or "").strip()
    city = str(city or "").strip()
    if not label:
        raise LeadSearchError("اختر نوع المشتري أولاً." if lang == "ar" else "Choose a buyer type first.")
    return f"{label} في {city}" if lang == "ar" and city else (f"{label} in {city}" if city else label)


def search_places(query, api_key=None, region="OM", language="ar", max_results=MAX_RESULTS, opener=None):
    """One Places API (New) text search. Returns the raw `places` list."""
    key = api_key or get_api_key()
    if not key:
        raise LeadSearchError("خدمة خرائط Google غير مفعّلة بعد (مفتاح GOOGLE_MAPS_API_KEY غير مضبوط).")
    body = json.dumps({
        "textQuery": query, "regionCode": region, "languageCode": language,
        "pageSize": max(1, min(int(max_results), MAX_RESULTS)),
    }).encode("utf-8")
    req = urllib.request.Request(PLACES_URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK,
    })
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        logger.warning("Places search failed: HTTP %s", exc.code)
        raise LeadSearchError("تعذّر البحث في خرائط Google الآن. حاول لاحقاً.") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("Places search failed: %s", type(exc).__name__)
        raise LeadSearchError("تعذّر الاتصال بخرائط Google الآن. حاول لاحقاً.") from exc
    return payload.get("places", []) or []


def score_place(place):
    """Deterministic 0-100 lead score: reachability and proof of a live business."""
    if place.get("businessStatus") not in (None, "OPERATIONAL"):
        return 0
    rating = place.get("rating") or 0
    reviews = place.get("userRatingCount") or 0
    score = 0.0
    if rating:
        score += max(0.0, min((rating - 3.0) / 2.0, 1.0)) * 35      # 3.0 -> 0, 5.0 -> 35
    score += min(math.log10(reviews + 1) / 3.0, 1.0) * 25            # ~1000 reviews -> 25
    if place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber"):
        score += 30
    if place.get("websiteUri"):
        score += 10
    return int(round(score))


def normalize_place(place):
    name = (place.get("displayName") or {}).get("text", "") if isinstance(place.get("displayName"), dict) else ""
    return {
        "place_id": place.get("id", ""),
        "business_name": name.strip(),
        "address": place.get("formattedAddress", "") or "",
        "phone": place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber") or "",
        "website": place.get("websiteUri", "") or "",
        "maps_url": place.get("googleMapsUri", "") or "",
        "rating": place.get("rating"),
        "reviews_count": int(place.get("userRatingCount") or 0),
        "score": score_place(place),
    }


def find_leads(product, buyer_type, city, lang="ar", api_key=None, opener=None):
    """Search + normalise + rank. Dropped: unnamed, closed, or score-zero places."""
    query = build_query(buyer_type, city, lang)
    places = search_places(query, api_key=api_key, language=lang if lang in ("ar", "en") else "ar", opener=opener)
    leads = [normalize_place(p) for p in places]
    leads = [l for l in leads if l["place_id"] and l["business_name"] and l["score"] > 0]
    leads.sort(key=lambda l: -l["score"])
    return {"query": query, "product": product, "leads": leads}


def whatsapp_link(phone, text=""):
    """wa.me click-to-chat link from a public listing phone; '' if unusable."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 8:                      # local Omani number
        digits = "968" + digits
    if not 10 <= len(digits) <= 15:
        return ""
    from urllib.parse import quote
    return f"https://wa.me/{digits}" + (f"?text={quote(text)}" if text else "")


def draft_outreach(product, business_name, seller_name, lang="ar", offer=""):
    """
    Plain template draft. Deliberately no LLM and no invented numbers: the only
    commercial terms in it are the `offer` text the owner typed themselves.
    """
    offer = (offer or "").strip()
    if lang == "ar":
        lines = [f"السلام عليكم، فريق {business_name} المحترم.",
                 f"معكم {seller_name}. نوفّر {product} ونودّ عرضه عليكم للتعاون التجاري."]
        if offer:
            lines.append(offer)
        lines.append("هل يناسبكم أن نرسل لكم التفاصيل والأسعار؟ شكراً لوقتكم.")
    else:
        lines = [f"Hello {business_name} team,",
                 f"This is {seller_name}. We supply {product} and would like to offer it to you."]
        if offer:
            lines.append(offer)
        lines.append("May we send you the details and pricing? Thank you for your time.")
    return "\n".join(lines)
