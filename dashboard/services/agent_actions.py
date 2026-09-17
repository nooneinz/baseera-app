"""
Proactive agent action — Baseera's proof of "Agentic", not "chatbot".

Instead of waiting to be asked, the agent:
  1. DETECTS a concrete, deterministic signal from the user's own rows
     (the biggest recurring expense destination — a real renegotiation
     opportunity), computed in Python, never invented.
  2. ACTS: creates a real Notification for the user (a visible side effect on
     the world, not just text) — the same create_notification capability the
     ReAct loop exposes.
  3. DRAFTS the concrete next step: a ready-to-send supplier renegotiation
     message, filled with the real numbers.

The finding and its numbers are always deterministic. The draft message is a
grounded template; if a Gemini client is available it may be polished, but the
template stands on its own so a live demo never depends on the network.
"""
import logging

logger = logging.getLogger(__name__)

_ROW_CAP = 10000
_CURRENCY = "ر.ع"


def _rows_for(user):
    from dashboard.models import DynamicRecord
    return list(
        DynamicRecord.objects.filter(user=user).values_list("row_data", flat=True)[:_ROW_CAP]
    )


def _detect_finding(rows):
    """The most actionable deterministic signal: the biggest recurring expense
    destination (best renegotiation target), else the single biggest expense."""
    from dashboard.services.first_win_insights import compute_transaction_signal

    signal = compute_transaction_signal(rows)
    if not signal:
        return None
    g = signal.get("top_recurring") or (signal.get("top_groups") or [None])[0]
    if not g or not g.get("total"):
        return None
    return {
        "name": g["name"],
        "total": round(g["total"], 2),
        "count": g.get("count", 1),
        "recurring": bool(signal.get("top_recurring")),
    }


def _draft_negotiation(finding):
    """A ready-to-send, grounded renegotiation message (deterministic)."""
    name = finding["name"]
    total = f'{finding["total"]:,.0f} {_CURRENCY}'
    count = finding["count"]
    return (
        f"السلام عليكم،\n\n"
        f"نتعامل معكم بشكل متكرر في بند «{name}»، وقد بلغ إجمالي تعاملنا معكم "
        f"{total} عبر {count} عمليات. نقدّر هذه الشراكة، ونطلب مراجعة السعر أو "
        f"منْحنا خصمّاً على الكمية نظراً لتكرار التعامل واستمراريته.\n\n"
        f"هل يمكن ترتيب عرض محدّث؟ شاكرين تعاونكم."
    )


def run_proactive_action(user):
    """
    Returns:
      {"status": "acted", "finding": {...}, "notification_id": int,
       "notification_title": str, "notification_message": str,
       "draft_message": str}
      or {"status": "no_signal", "message": "<ar>"}
    """
    from dashboard.models import Notification

    rows = _rows_for(user)
    finding = _detect_finding(rows)
    if not finding:
        return {
            "status": "no_signal",
            "message": "لم أجد بعد بنداً متكرراً واضحاً للتفاوض. ارفع كشفاً أو ملف مصروفات أكبر وسأتصرّف.",
        }

    total = f'{finding["total"]:,.0f} {_CURRENCY}'
    label = "بند متكرر" if finding["recurring"] else "أكبر مصروف"
    title = f"الوكيل رصد فرصة توفير في «{finding['name']}»"
    message = (
        f"لاحظت أن {label} «{finding['name']}» بلغ {total} عبر {finding['count']} عمليات. "
        f"جهّزت لك رسالة تفاوض جاهزة لتخفيضه."
    )

    notif = Notification.objects.create(
        user=user, title=title, message=message, type="warning",
    )

    return {
        "status": "acted",
        "finding": finding,
        "notification_id": notif.id,
        "notification_title": title,
        "notification_message": message,
        "draft_message": _draft_negotiation(finding),
    }
