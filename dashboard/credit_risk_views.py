"""
Early warning before selling on credit — page + JSON endpoints.

Every endpoint is login-only and scoped to request.user: the rows come from
the user's own DynamicRecords and the watchlist from their own entries, so
one tenant can never see another's customers. CSRF protection stays ON for
the state-changing endpoints (watchlist add/remove).
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .models import CreditWatchlistEntry, DynamicRecord
from .security import rate_limit, safe_error_message
from .services.credit_risk_analyzer import (
    CREDIT_RISK_THRESHOLDS,
    check_customer,
    compute_credit_risk_signals,
    normalize_name,
    phrase_credit_alert,
)

logger = logging.getLogger(__name__)

ROW_CAP = 10000


def _rows(user):
    return list(DynamicRecord.objects.filter(user=user).values_list("row_data", flat=True)[:ROW_CAP])


def _watchlist(user):
    return list(CreditWatchlistEntry.objects.filter(user=user).values("id", "customer_name", "reason", "created_at"))


def _overview(user):
    computed = compute_credit_risk_signals(_rows(user), watchlist=_watchlist(user))
    rank = {"high": 0, "medium": 1, "low": 2}
    at_risk = sorted(
        (c for c in computed["customers"].values() if c["risk_level"] != "low"),
        key=lambda c: (rank[c["risk_level"]], -(c["sales"] or 0)),
    )
    by_customer = {}
    for s in computed["signals"]:
        by_customer.setdefault(normalize_name(s["customer"]), []).append(
            {"type": s["type"], "title": s["title"], "detail": s["detail"], "severity": s["severity"]})
    for c in at_risk:
        c["signals"] = by_customer.get(normalize_name(c["name"]), [])
    return {
        "analyzable": computed["analyzable"],
        "customer_column": computed["columns_used"].get("customer"),
        "customers_count": computed["customers_count"],
        "at_risk": at_risk,
        "customer_names": sorted(c["name"] for c in computed["customers"].values() if c["orders"]),
    }


def _ai_service_for(user):
    """The Gemini client, only if configured and within the user's quota."""
    try:
        from .services.ai_quota import check_gemini_quota
        from .services.ai_service import GeminiAIService
        if not check_gemini_quota(user_id=user.id):
            return None
        svc = GeminiAIService()
        return svc if getattr(svc, "client", None) else None
    except Exception:
        return None


@login_required
def credit_risk_page(request):
    return render(request, "dashboard/credit_risk.html", {
        "overview": _overview(request.user),
        "watchlist": _watchlist(request.user),
        "thresholds": CREDIT_RISK_THRESHOLDS,
    })


@login_required
@require_http_methods(["POST"])
@rate_limit(requests_per_minute=30, key_prefix="credit_check", per_user=True)
def api_credit_risk_check(request):
    """Pre-sale check: {customer, amount?, terms_days?} -> signals + worded alert."""
    try:
        data = json.loads(request.body or "{}")
        customer = str(data.get("customer", "")).strip()[:200]
        if not customer:
            return JsonResponse({"status": "error", "message": "اكتب اسم العميل أولاً."}, status=400)
        check = check_customer(
            _rows(request.user), customer, watchlist=_watchlist(request.user),
            proposed_amount=data.get("amount"), proposed_terms_days=data.get("terms_days"),
        )
        alert = phrase_credit_alert(check, ai_service=_ai_service_for(request.user) if check["signals"] else None)
        return JsonResponse({
            "status": "success",
            "customer": check["customer"],
            "found_in_data": check["found_in_data"],
            "orders_in_data": check["orders_in_data"],
            "risk_level": check["risk_level"],
            "signals": [{"type": s["type"], "title": s["title"], "detail": s["detail"], "severity": s["severity"],
                         "evidence_count": s["evidence_count"]} for s in check["signals"]],
            "alert": alert["text"],
            "ai_used": alert["ai_used"],
        })
    except Exception as e:
        logger.info("credit check failed: %s", e)
        return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)


@login_required
@require_http_methods(["GET"])
def api_credit_risk_overview(request):
    return JsonResponse({"status": "success", **_overview(request.user)})


@login_required
@require_http_methods(["GET", "POST"])
def api_credit_watchlist(request):
    """GET: the user's watchlist. POST {customer_name, reason?}: add one."""
    if request.method == "GET":
        return JsonResponse({"status": "success", "watchlist": [
            {"id": w["id"], "customer_name": w["customer_name"], "reason": w["reason"]} for w in _watchlist(request.user)]})
    try:
        data = json.loads(request.body or "{}")
        name = str(data.get("customer_name", "")).strip()[:200]
        reason = str(data.get("reason", "")).strip()[:300]
        if not normalize_name(name):
            return JsonResponse({"status": "error", "message": "اكتب اسم العميل."}, status=400)
        with transaction.atomic():
            entry = CreditWatchlistEntry.objects.create(user=request.user, customer_name=name, reason=reason)
        return JsonResponse({"status": "success", "entry": {"id": entry.id, "customer_name": entry.customer_name, "reason": entry.reason}})
    except IntegrityError:
        return JsonResponse({"status": "error", "message": "هذا العميل موجود مسبقاً في قائمتك."}, status=400)
    except Exception as e:
        return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)


@login_required
@require_http_methods(["POST"])
def api_credit_watchlist_delete(request, entry_id):
    deleted, _ = CreditWatchlistEntry.objects.filter(user=request.user, id=entry_id).delete()
    if not deleted:
        return JsonResponse({"status": "error", "message": "not found"}, status=404)
    return JsonResponse({"status": "success"})
