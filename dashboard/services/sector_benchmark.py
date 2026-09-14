"""
Sector benchmarking — Baseera's data network-effect moat.

Compares a business's key ratios against the *anonymized median* of other
businesses in the same sector: "your expenses are 30% above similar
restaurants." The comparison only appears once there are enough peers with a
comparable metric (MIN_PEERS) -- until then it honestly reports that the
benchmark is still building, so nothing is ever invented. Accuracy grows with
the customer base, which is exactly why it's hard for a competitor to copy.

Every per-user metric is computed deterministically from that user's own
rows via the existing engines (waste_analyzer / first_win_insights).
"""
import logging
import statistics

logger = logging.getLogger(__name__)

MIN_PEERS = 3          # need at least this many OTHER businesses to benchmark a metric
_ROW_CAP = 10000
_MAX_PEERS_SCANNED = 200

# Each metric: how to read it, and whether higher is better (for phrasing).
_METRIC_META = {
    "margin_pct":    {"label": "هامش الربح", "unit": "%", "higher_is_better": True},
    "expense_ratio": {"label": "نسبة المصروف إلى الدخل", "unit": "%", "higher_is_better": False},
    "waste_ratio":   {"label": "نسبة الهدر", "unit": "%", "higher_is_better": False},
}


def _rows_for(user):
    from dashboard.models import DynamicRecord
    return list(
        DynamicRecord.objects.filter(user=user).values_list("row_data", flat=True)[:_ROW_CAP]
    )


def compute_user_metrics(rows):
    """
    A small bundle of comparable ratios from a user's rows. Any metric that
    can't be computed from this particular file is simply omitted (never zero-
    filled), so it won't distort a sector median.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return {}

    from dashboard.services.waste_analyzer import _find_col, _to_num, compute_waste_signals
    from dashboard.services.first_win_insights import compute_transaction_signal

    metrics = {}
    columns = list(rows[0].keys())

    # 1) Profit margin % (needs per-item price + cost).
    col_price, col_cost = _find_col(columns, "price"), _find_col(columns, "cost")
    if col_price and col_cost:
        margins = []
        for row in rows:
            price, cost = _to_num(row.get(col_price)), _to_num(row.get(col_cost))
            if price and price > 0 and cost is not None and cost >= 0:
                margins.append((price - cost) / price * 100)
        if margins:
            metrics["margin_pct"] = round(statistics.mean(margins), 1)

    # 2) Expense-to-income % (transaction / bank-statement files).
    cf = compute_transaction_signal(rows)
    if cf and cf.get("total_income", 0) > 0:
        metrics["expense_ratio"] = round(cf["total_expense"] / cf["total_income"] * 100, 1)
        # 3) Waste as a share of income, when both are known.
        waste = (compute_waste_signals(rows) or {}).get("total_waste", 0) or 0
        if waste > 0:
            metrics["waste_ratio"] = round(waste / cf["total_income"] * 100, 1)

    return metrics


def _peer_users(user):
    """Other active users in the same sector (Profile.project_type)."""
    from dashboard.models import Profile
    from django.contrib.auth.models import User

    profile = Profile.objects.filter(user=user).first()
    sector = getattr(profile, "project_type", None) or "other"
    peer_ids = list(
        Profile.objects.filter(project_type=sector, user__is_active=True)
        .exclude(user=user)
        .values_list("user_id", flat=True)[:_MAX_PEERS_SCANNED]
    )
    users = list(User.objects.filter(id__in=peer_ids))
    sector_label = profile.get_project_type_display() if profile else "قطاعك"
    return users, sector_label


def sector_benchmark_for(user, min_peers=MIN_PEERS):
    """
    Returns:
      {
        "status": "ready" | "building",
        "sector": "<label>",
        "peer_count": int,           # peers that had ANY comparable metric
        "comparisons": [ {metric, label, unit, user_value, sector_value,
                          delta_pct, direction, is_good} ],
        "message": "<when building>"
      }
    """
    my_metrics = compute_user_metrics(_rows_for(user))
    peers, sector_label = _peer_users(user)

    # Collect peer values per metric.
    peer_values = {k: [] for k in _METRIC_META}
    contributing_peers = 0
    for peer in peers:
        pm = compute_user_metrics(_rows_for(peer))
        if pm:
            contributing_peers += 1
        for k, v in pm.items():
            if k in peer_values:
                peer_values[k].append(v)

    comparisons = []
    for metric, meta in _METRIC_META.items():
        mine = my_metrics.get(metric)
        vals = peer_values.get(metric, [])
        if mine is None or len(vals) < min_peers:
            continue
        median = round(statistics.median(vals), 1)
        if median == 0:
            continue
        delta_pct = round((mine - median) / abs(median) * 100)
        above = mine > median
        # "good" = above when higher-is-better, else below.
        is_good = above if meta["higher_is_better"] else (not above)
        comparisons.append({
            "metric": metric,
            "label": meta["label"],
            "unit": meta["unit"],
            "user_value": mine,
            "sector_value": median,
            "delta_pct": delta_pct,
            "direction": "above" if above else "below",
            "is_good": is_good,
        })

    if comparisons:
        return {
            "status": "ready",
            "sector": sector_label,
            "peer_count": contributing_peers,
            "comparisons": comparisons,
        }

    return {
        "status": "building",
        "sector": sector_label,
        "peer_count": contributing_peers,
        "comparisons": [],
        "message": (
            "المقارنة المعيارية لقطاعك تنضج مع نمو عدد المنشآت المشابهة على بصيرة. "
            "ارفع بياناتك بانتظام، وكل ما زاد عدد المشتركين في قطاعك، دقّت المقارنة."
        ),
    }
