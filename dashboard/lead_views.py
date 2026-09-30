"""
Sales Leads agent -- page + JSON endpoints.

Every endpoint is login-only and every query is scoped to request.user, so one
tenant can never read or change another tenant's leads. CSRF stays ON for all
state-changing calls. The agent never contacts anyone: it finds public
business listings and drafts a message; the owner decides whether and when to
send it (status changes are the owner's own pipeline notes).
"""
import json
import logging
from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .models import DynamicRecord, SalesLead
from .security import rate_limit, safe_error_message
from .services import lead_finder

logger = logging.getLogger(__name__)

ROW_CAP = 10000
DAILY_SEARCH_CAP = 20          # Places calls are billed per request: cap per user per day
VALID_STATUSES = {c[0] for c in SalesLead.STATUS_CHOICES}


def _lang(request):
    return "en" if (request.session.get("lang") or request.COOKIES.get("lang", "ar")) == "en" else "ar"


def _rows(user):
    return list(DynamicRecord.objects.filter(user=user).values_list("row_data", flat=True)[:ROW_CAP])


def _seller_name(user):
    profile = getattr(user, "profile", None)
    return (getattr(profile, "company_name", "") or user.get_username()).strip()


def _lead_dict(lead):
    return {
        "id": lead.id, "product": lead.product, "buyer_type": lead.buyer_type, "city": lead.city,
        "business_name": lead.business_name, "address": lead.address, "phone": lead.phone,
        "website": lead.website, "maps_url": lead.maps_url, "rating": lead.rating,
        "reviews_count": lead.reviews_count, "score": lead.score, "status": lead.status,
        "notes": lead.notes, "draft_message": lead.draft_message,
        "whatsapp_url": lead_finder.whatsapp_link(lead.phone, lead.draft_message),
    }


def _quota_key(user):
    return f"sales_leads_searches:{user.id}:{date.today().isoformat()}"


@login_required
def sales_leads_page(request):
    user = request.user
    leads = [_lead_dict(l) for l in SalesLead.objects.filter(user=user)[:200]]
    lang = _lang(request)
    return render(request, "dashboard/sales_leads.html", {
        "products": lead_finder.pick_products_to_push(_rows(user)),
        "buyer_presets": [{"key": k, "label": v[lang]} for k, v in lead_finder.BUYER_PRESETS.items()],
        "leads": leads,
        "statuses": [{"key": k, "label": label} for k, label in SalesLead.STATUS_CHOICES],
        "maps_enabled": bool(lead_finder.get_api_key()),
    })


@login_required
@require_http_methods(["POST"])
@rate_limit(requests_per_minute=10, key_prefix="leads_search", per_user=True)
def api_leads_search(request):
    """{product, buyer_type, city} -> ranked buyers from Google Maps, saved to the user's pipeline."""
    lang = _lang(request)
    try:
        data = json.loads(request.body or "{}")
        product = str(data.get("product", "")).strip()[:200]
        buyer_type = str(data.get("buyer_type", "")).strip()[:120]
        city = str(data.get("city", "")).strip()[:120]
        if not product:
            return JsonResponse({"status": "error", "message": "اختر المنتج أولاً."}, status=400)
        if not lead_finder.get_api_key():
            return JsonResponse({"status": "error", "code": "maps_not_configured",
                                 "message": "خدمة خرائط Google غير مفعّلة بعد. يضيف مسؤول النظام مفتاح GOOGLE_MAPS_API_KEY."}, status=503)
        key = _quota_key(request.user)
        used = cache.get(key, 0)
        if used >= DAILY_SEARCH_CAP:
            return JsonResponse({"status": "error", "message": "بلغت حد البحث اليومي. جرّب غداً."}, status=429)

        result = lead_finder.find_leads(product, buyer_type, city, lang=lang)
        cache.set(key, used + 1, 60 * 60 * 26)

        saved = []
        with transaction.atomic():
            for l in result["leads"]:
                lead, created = SalesLead.objects.get_or_create(
                    user=request.user, place_id=l["place_id"], product=product,
                    defaults={"buyer_type": buyer_type, "city": city, **{k: l[k] for k in (
                        "business_name", "address", "phone", "website", "maps_url", "rating", "reviews_count", "score")}},
                )
                saved.append({**_lead_dict(lead), "is_new": created})
        return JsonResponse({"status": "success", "query": result["query"], "count": len(saved), "leads": saved})
    except lead_finder.LeadSearchError as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=502)
    except Exception as e:
        logger.info("lead search failed: %s", e)
        return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)


def _owned_lead(request, lead_id):
    return SalesLead.objects.filter(user=request.user, id=lead_id).first()


@login_required
@require_http_methods(["POST"])
def api_lead_update(request, lead_id):
    """{status?, notes?} -> the owner's own pipeline state for one lead."""
    lead = _owned_lead(request, lead_id)
    if not lead:
        return JsonResponse({"status": "error", "message": "غير موجود."}, status=404)
    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        data = {}
    if "status" in data:
        if data["status"] not in VALID_STATUSES:
            return JsonResponse({"status": "error", "message": "حالة غير صحيحة."}, status=400)
        lead.status = data["status"]
    if "notes" in data:
        lead.notes = str(data["notes"]).strip()[:1000]
    lead.save(update_fields=["status", "notes", "updated_at"])
    return JsonResponse({"status": "success", "lead": _lead_dict(lead)})


@login_required
@require_http_methods(["POST"])
def api_lead_draft(request, lead_id):
    """{offer?} -> an outreach draft (template, no invented numbers) + a click-to-chat link."""
    lead = _owned_lead(request, lead_id)
    if not lead:
        return JsonResponse({"status": "error", "message": "غير موجود."}, status=404)
    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        data = {}
    lead.draft_message = lead_finder.draft_outreach(
        lead.product, lead.business_name, _seller_name(request.user),
        lang=_lang(request), offer=str(data.get("offer", ""))[:400])
    lead.save(update_fields=["draft_message", "updated_at"])
    return JsonResponse({"status": "success", "lead": _lead_dict(lead)})


@login_required
@require_http_methods(["POST"])
def api_lead_delete(request, lead_id):
    deleted, _ = SalesLead.objects.filter(user=request.user, id=lead_id).delete()
    if not deleted:
        return JsonResponse({"status": "error", "message": "غير موجود."}, status=404)
    return JsonResponse({"status": "success"})
