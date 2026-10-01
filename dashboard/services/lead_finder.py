"""
Sales Leads agent -- finds businesses that could buy the owner's products.

Pipeline (same boundary as the rest of Baseera: numbers are computed here, the
LLM only words things):
  1. pick_products_to_push(rows)  -- deterministic choice of which products to
     sell, from the waste engine's own evidence (dead stock first).
  2. search_leads(...)            -- Google Maps search for buyer types in a
     city ("مطاعم في مسقط"), through SerpApi (preferred, one key) or the
     Google Places API (New). Only a buyer type, a city and its coordinates
     leave the platform: no product names, prices, costs or any other user
     data are sent to either provider.
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
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
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


# Centre points (lat, lng) for SerpApi's `ll` parameter, so results stay local.
# A city not listed here is still searched by name only ("... في صحار").
CITY_COORDS = {
    "مسقط": (23.5880, 58.3829), "muscat": (23.5880, 58.3829),
    "السيب": (23.6703, 58.1891), "seeb": (23.6703, 58.1891),
    "بوشر": (23.5859, 58.4059), "bawshar": (23.5859, 58.4059),
    "مطرح": (23.6139, 58.5922), "muttrah": (23.6139, 58.5922),
    "صلالة": (17.0151, 54.0924), "salalah": (17.0151, 54.0924),
    "صحار": (24.3476, 56.7093), "sohar": (24.3476, 56.7093),
    "نزوى": (22.9333, 57.5333), "nizwa": (22.9333, 57.5333),
    "صور": (22.5667, 59.5289), "sur": (22.5667, 59.5289),
    "بركاء": (23.6793, 57.8890), "barka": (23.6793, 57.8890),
    "الرستاق": (23.3908, 57.4244), "rustaq": (23.3908, 57.4244),
    "عبري": (23.2254, 56.5156), "ibri": (23.2254, 56.5156),
    "الخابورة": (23.9667, 57.0833), "khaburah": (23.9667, 57.0833),
    "بهلاء": (22.9667, 57.3000), "bahla": (22.9667, 57.3000),
}
CITY_ZOOM = 12


class LeadSearchError(Exception):
    """Raised with a user-safe message when the Places lookup cannot run."""


def get_api_key():
    return getattr(settings, "GOOGLE_MAPS_API_KEY", "") or ""


def get_serpapi_key():
    return getattr(settings, "SERPAPI_API_KEY", "") or ""


def get_provider():
    """'serpapi' when its key is set (preferred), else 'google', else ''."""
    if get_serpapi_key():
        return "serpapi"
    if get_api_key():
        return "google"
    return ""


def is_enabled():
    return bool(get_provider())


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


def city_ll(city):
    """SerpApi `ll` value ("@lat,lng,zoomz") for a known city, else ''."""
    coords = CITY_COORDS.get(str(city or "").strip().lower())
    return f"@{coords[0]},{coords[1]},{CITY_ZOOM}z" if coords else ""


def search_serpapi(query, city="", api_key=None, language="ar", opener=None):
    """
    One SerpApi `google_maps` search. Returns SerpApi's raw `local_results`.
    Sent to SerpApi: the query text, the city's coordinates and the language.
    """
    key = api_key or get_serpapi_key()
    if not key:
        raise LeadSearchError("خدمة البحث في الخرائط غير مفعّلة بعد (مفتاح SERPAPI_API_KEY غير مضبوط).")
    params = {"engine": "google_maps", "type": "search", "q": query, "hl": language, "api_key": key}
    ll = city_ll(city)
    if ll:
        params["ll"] = ll
    url = SERPAPI_URL + "?" + urllib.parse.urlencode(params, safe="@,")
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        logger.warning("SerpApi search failed: HTTP %s", exc.code)    # never log the URL: it carries the key
        raise LeadSearchError("تعذّر البحث في الخرائط الآن. حاول لاحقاً.") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("SerpApi search failed: %s", type(exc).__name__)
        raise LeadSearchError("تعذّر الاتصال بخدمة الخرائط الآن. حاول لاحقاً.") from exc
    error = payload.get("error")
    if error:
        if "hasn't returned any results" in str(error):
            return []
        logger.warning("SerpApi returned an error")
        raise LeadSearchError("تعذّر البحث في الخرائط الآن. حاول لاحقاً.")
    results = payload.get("local_results")
    if results is None and isinstance(payload.get("place_results"), dict):
        results = [payload["place_results"]]
    return results or []


def normalize_serp_result(r):
    """SerpApi local result -> the same lead dict the Google path produces."""
    pid = r.get("place_id") or r.get("data_id") or ""
    phone = r.get("phone") or ""
    website = r.get("website") or ""
    rating = r.get("rating")
    reviews = int(r.get("reviews") or 0)
    status = "CLOSED_PERMANENTLY" if r.get("permanently_closed") else (
        "CLOSED_TEMPORARILY" if r.get("temporarily_closed") else "OPERATIONAL")
    score = score_place({
        "businessStatus": status, "rating": rating, "userRatingCount": reviews,
        "internationalPhoneNumber": phone, "websiteUri": website,
    })
    return {
        "place_id": pid,
        "business_name": str(r.get("title") or "").strip(),
        "address": r.get("address", "") or "",
        "phone": phone,
        "website": website,
        "maps_url": f"https://www.google.com/maps/place/?q=place_id:{pid}" if str(pid).startswith("ChI") else "",
        "rating": rating,
        "reviews_count": reviews,
        "score": score,
    }


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


def find_leads(product, buyer_type, city, lang="ar", api_key=None, opener=None, provider=None):
    """Search + normalise + rank. Dropped: unnamed, closed, or score-zero places."""
    query = build_query(buyer_type, city, lang)
    language = lang if lang in ("ar", "en") else "ar"
    provider = provider or get_provider()
    if provider == "serpapi":
        raw = search_serpapi(query, city=city, api_key=api_key, language=language, opener=opener)
        leads = [normalize_serp_result(r) for r in raw]
    else:
        places = search_places(query, api_key=api_key, language=language, opener=opener)
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
