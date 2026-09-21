"""
Agent activity — the engine behind the live "Agent Activity" screen.

An agent that wants to be watched creates an AgentRun, then pushes short,
human-readable steps as it works. The frontend polls the run and renders the
steps as a live terminal-style log. Everything is scoped to the owning user;
every helper is fail-soft and never raises into the caller's flow.

The steps here are DETERMINISTIC and grounded: run_analysis_agent() replays
Baseera's real analysis pipeline (the same build_analysis_steps used across
the app) as a tracked, cancellable background task, so the activity screen
shows real work, not a canned animation.
"""
import time
import logging

logger = logging.getLogger(__name__)


def start_run(user, label, title):
    """Create a queued AgentRun for `user`. Returns the run, or None on error."""
    try:
        from dashboard.models import AgentRun
        return AgentRun.objects.create(
            user=user, label=(label or "")[:120], title=(title or "")[:200],
            status="queued",
        )
    except Exception as e:
        logger.info("agent_activity.start_run failed: %s", e)
        return None


def push_step(run, message, status="done"):
    """Append a timestamped step to `run` and update its current-state line."""
    try:
        from dashboard.models import AgentRun, AgentRunStep
        seq = run.steps.count() + 1
        AgentRunStep.objects.create(run=run, seq=seq, message=(message or "")[:300], status=status)
        AgentRun.objects.filter(pk=run.pk).update(
            progress_state=(message or "")[:160], status="running",
        )
    except Exception as e:
        logger.info("agent_activity.push_step failed: %s", e)


def complete_run(run, summary="", status="done"):
    """Mark the run finished (done / error / cancelled) with a result summary."""
    try:
        from dashboard.models import AgentRun
        AgentRun.objects.filter(pk=run.pk).update(
            status=status, result_summary=(summary or "")[:4000], progress_state="",
        )
    except Exception as e:
        logger.info("agent_activity.complete_run failed: %s", e)


def is_cancelled(run_id):
    """Re-read the cancel flag from the DB (set by the cancel endpoint)."""
    try:
        from dashboard.models import AgentRun
        return AgentRun.objects.filter(pk=run_id, cancel_requested=True).exists()
    except Exception:
        return False


def serialize(run):
    """Shape an AgentRun (+ steps) for the activity API. Never raises."""
    try:
        steps = [
            {
                "seq": s.seq,
                "message": s.message,
                "status": s.status,
                "at": s.created_at.isoformat() if s.created_at else "",
            }
            for s in run.steps.all()
        ]
        return {
            "id": run.id,
            "label": run.label,
            "title": run.title,
            "status": run.status,
            "progress_state": run.progress_state,
            "result_summary": run.result_summary,
            "cancel_requested": run.cancel_requested,
            "steps": steps,
            "created_at": run.created_at.isoformat() if run.created_at else "",
            "updated_at": run.updated_at.isoformat() if run.updated_at else "",
        }
    except Exception as e:
        logger.info("agent_activity.serialize failed: %s", e)
        return {"id": getattr(run, "id", None), "status": "error", "steps": []}


# --- A real, grounded agent to watch ------------------------------------------

def run_analysis_agent(user_id, run_id):
    """
    Background worker: replay the deterministic analysis pipeline as a tracked,
    cancellable AgentRun. Pushes one grounded step at a time (with a small,
    human-perceptible pause) so the activity screen shows real work. Never
    raises; always leaves the run in a terminal state.
    """
    from django.contrib.auth.models import User
    from dashboard.models import AgentRun, DynamicRecord
    from dashboard.services.analysis_theater import build_analysis_steps

    try:
        run = AgentRun.objects.filter(pk=run_id, user_id=user_id).first()
        if not run:
            return
    except Exception as e:
        logger.info("run_analysis_agent: cannot load run: %s", e)
        return

    try:
        user = User.objects.filter(pk=user_id).first()
        push_step(run, "بدء المهمة وتحميل بياناتك")
        rows = list(
            DynamicRecord.objects.filter(user=user).values_list("row_data", flat=True)[:10000]
        )
        steps = build_analysis_steps(rows)

        verdict_line = ""
        for st in steps:
            if is_cancelled(run_id):
                complete_run(run, "أُلغيت المهمة بناءً على طلبك.", status="cancelled")
                return
            # Turn a rich analysis step into one concise, grounded activity
            # line: the step title plus its most specific data point (the first
            # detail line, e.g. the real supplier name) and its result.
            title = st.get("title") or "خطوة تحليل"
            lines = st.get("lines") or []
            result = st.get("result")
            bits = [title]
            if lines:
                bits.append(str(lines[0]))
            if result:
                bits.append(str(result))
            msg = " — ".join(bits)
            push_step(run, msg)
            if st.get("phase") == "verdict":
                verdict_line = " / ".join(st.get("lines") or []) or msg
            time.sleep(0.7)

        if is_cancelled(run_id):
            complete_run(run, "أُلغيت المهمة بناءً على طلبك.", status="cancelled")
            return
        complete_run(run, verdict_line or "اكتمل التحليل.", status="done")
    except Exception as e:
        logger.info("run_analysis_agent failed: %s", e)
        complete_run(run, "تعذّر إكمال المهمة.", status="error")
