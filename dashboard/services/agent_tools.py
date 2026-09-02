"""
Task 2 (scoped, per explicit product decision): real Gemini Function
Declarations for Baseera's non-financial agent tools, plus a small,
bounded autonomous ReAct (Thought -> Action -> Observation -> Reflection)
loop that uses them.

Scope is deliberately narrow. Only tools with NO financial or
decision-metric consequence are ever exposed here: running a short Python
snippet, saving a note to long-term memory, and raising a notification.
Every action that touches a financial number or a decision metric
(UPDATE_DECISION_METRIC, RESOLVE_RISK, RESOLVE_LEAK) has NO function-
calling tool at all -- it can only ever be emitted as the existing
[[ACTION:...]] text tag inside the model's final answer, which the
frontend renders as a button a human must click to apply. That split is
the hard constraint this module exists to enforce: the model may
autonomously decide to run code or leave itself a note, but it can never
autonomously touch a financial figure. See ai_service.py's existing
[[ACTION:...]] handling, which this module does not change or replace.

The loop itself:
  Thought      -- the model's own reasoning (hidden, handled by Gemini).
  Action       -- a REAL Gemini function call (never a regex-parsed text
                  tag) against one of the three tools below.
  Observation  -- the tool's real return value, fed back verbatim.
  Reflection   -- the model deciding whether it needs another tool call
                  or is ready to answer; run_react_preloop keeps calling
                  the model until it stops requesting tools or a bounded
                  iteration cap is hit.

If anything here fails (model unavailable, a malformed/disallowed call,
a tool erroring) the loop simply ends early and hands back the prompt
unchanged from that point -- a problem in this module can never block
the user from getting a final answer, it just means no tool ran.
"""
import json
import logging

from google.genai import types

from dashboard.services.sandbox_client import run_python_code as _sandbox_run_python_code

logger = logging.getLogger(__name__)

MAX_REACT_ITERATIONS = 3

# The hard constraint, enforced at the code layer rather than trusted to
# prompt wording: these are the ONLY three names ever executed, no matter
# what the model requests.
_TOOL_NAMES = {"run_python_code", "create_notification", "save_memory"}

# Latency gate: the pre-loop costs at least one extra live round trip
# before the final answer even starts streaming, so it's only worth
# paying for when the message plausibly needs one of the three tools --
# an ordinary analytical question doesn't need Python executed on its
# behalf; the file context/deterministic signals it needs are already in
# the prompt. Deliberately narrow, matching the same keyword-gate
# convention already used in orchestrator.py, so most turns pay zero
# added latency and never touch this module at all.
_REACT_TRIGGER_TERMS = [
    "احسب", "احسبي", "احسبلي", "احسبها", "نفذ كود", "شغل كود", "شغّل كود",
    "calculate", "compute", "run this code", "run code", "run python",
    "احفظ", "تذكر هذا", "تذكري هذا", "لاحظ هذا",
    "remember this", "save this note", "note this down",
    "ذكرني", "ذكريني", "نبهني", "نبهيني",
    "remind me", "notify me", "alert me",
]


def should_attempt_react(message_text):
    """Cheap, deterministic gate deciding whether the ReAct pre-loop is
    worth its extra round trip for this message at all."""
    text = (message_text or "").lower()
    return any(term in text for term in _REACT_TRIGGER_TERMS)


def _finalize(working_prompt, tool_was_used, lang):
    """
    The tool trace above was appended mid-completion (right after the
    prompt's own "model: " cue), so without this the model would often
    just continue completing in that same bracketed/technical shape --
    echoing the raw code or tool call back to the user instead of
    switching into its normal final answer. This explicitly tells it the
    trace was internal, and re-opens a clean "model: " turn so it answers
    the same way it always does when no tool was involved at all.
    """
    if not tool_was_used:
        return working_prompt
    if lang == "ar":
        closing = (
            "\n\n[ملاحظة نظام: عمليات استدعاء الأداة والنتائج (Observation) أعلاه "
            "جرت بشكل داخلي وصامت تماماً -- المستخدم لم ير أي كود أو JSON أو اسم "
            "أداة، ويجب ألا يراها أبداً. باستخدام الأرقام/النتائج الحقيقية الواردة "
            "أعلاه فقط، اكتب الآن ردك النهائي المعتاد بنفس الشخصية والنبرة وقواعد "
            "التنسيق المعروفة لديك. يُمنع تماماً كتابة أي كود أو تكرار استدعاء "
            "الأداة أو الـ JSON الخام في ردك.]\n\nmodel: "
        )
    else:
        closing = (
            "\n\n[System note: the tool call(s) and Observation(s) above "
            "happened silently in the background -- the user has not seen "
            "any code, JSON, or tool name, and must never see them. Using "
            "only the real numbers/results from the Observations above, "
            "write your normal final answer now, in your usual persona, "
            "tone, and formatting rules. Never include a code block or "
            "repeat the raw tool call/output in your answer.]\n\nmodel: "
        )
    return working_prompt + closing


def _run_python_tool(code):
    """
    Same entry point as the [[ACTION:RUN_PYTHON|...]] text-tag path in
    ai_service.py -- both now go through sandbox_client.run_python_code,
    which uses the isolated sandbox service when one is configured and
    falls back to a restricted in-process exec() otherwise (see that
    module's docstring). Kept as a thin wrapper rather than inlined so a
    future change to this tool's calling convention doesn't have to touch
    the shared client.
    """
    return _sandbox_run_python_code(code)


def _create_notification_tool(user_id, title, message, notif_type="info"):
    try:
        from dashboard.models import Notification
        from django.contrib.auth.models import User

        if not user_id:
            return "No active user session -- notification not created."
        user = User.objects.get(id=user_id)
        Notification.objects.create(
            user=user,
            title=(title or "").strip(),
            message=(message or "").strip(),
            type=(notif_type or "info").strip(),
        )
        return "Notification created."
    except Exception as e:
        logger.info("create_notification tool failed: %s", e)
        return f"Could not create notification: {e}"


def _save_memory_tool(ai_service, user_id, content):
    try:
        from dashboard.models import AgentMemory
        from django.contrib.auth.models import User

        if not user_id:
            return "No active user session -- memory not saved."
        content = (content or "").strip()
        if not content:
            return "Nothing to save."
        user = User.objects.get(id=user_id)
        embedding = []  # AgentMemory.embedding has no default/null -- an
        # unembedded memory (model unavailable) is still saved as text,
        # just without a vector for similarity search yet.
        if ai_service is not None and getattr(ai_service, "client", None):
            try:
                emb_res = ai_service.client.models.embed_content(
                    model="text-embedding-004", contents=content,
                )
                embedding = emb_res.embeddings[0].values
            except Exception as e:
                logger.info("save_memory embedding skipped: %s", e)
        AgentMemory.objects.create(user=user, content=content, embedding=embedding)
        return "Memory saved."
    except Exception as e:
        logger.info("save_memory tool failed: %s", e)
        return f"Could not save memory: {e}"


def build_agent_tools():
    """
    The ONLY function-calling surface the agent is ever given -- see
    module docstring for why financial/decision actions are deliberately
    absent from this list.
    """
    run_python_fd = types.FunctionDeclaration(
        name="run_python_code",
        description=(
            "Executes a short, self-contained Python snippet to perform a "
            "calculation or data transformation and returns whatever it "
            "prints. Use this only for arithmetic/data-shape work needed "
            "to answer the user -- never to touch financial records."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The Python code to execute."},
            },
            "required": ["code"],
        },
    )
    create_notification_fd = types.FunctionDeclaration(
        name="create_notification",
        description=(
            "Raises a proactive notification for the user, e.g. a "
            "reminder or an alert worth surfacing outside the chat."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "message": {"type": "string"},
                "notif_type": {"type": "string", "description": "One of: info, warning, success."},
            },
            "required": ["title", "message"],
        },
    )
    save_memory_fd = types.FunctionDeclaration(
        name="save_memory",
        description=(
            "Saves a short strategic fact or preference to long-term "
            "memory so it can inform future conversations."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
            },
            "required": ["content"],
        },
    )
    return types.Tool(function_declarations=[run_python_fd, create_notification_fd, save_memory_fd])


def run_react_preloop(ai_service, prompt, user_id, model, lang="ar", on_state=None,
                       max_iterations=MAX_REACT_ITERATIONS):
    """
    Runs the bounded ReAct loop and returns the prompt augmented with
    every Thought/Action/Observation exchange that happened, ready to be
    handed to the existing streaming call for the actual user-facing
    answer. Never raises.

    `on_state(text)` is an optional callback used to surface the same
    kind of short progress notice the pipeline already shows elsewhere
    (e.g. "AGENT_LOG: ..."), so this doesn't need any new UI surface.
    """
    if ai_service is None or not getattr(ai_service, "client", None):
        return prompt

    def note(text):
        if on_state:
            try:
                on_state(text)
            except Exception:
                pass

    tool = build_agent_tools()
    working_prompt = prompt
    tool_was_used = False

    for _ in range(max_iterations):
        try:
            response = ai_service.client.models.generate_content(
                model=model,
                contents=working_prompt,
                config=types.GenerateContentConfig(tools=[tool]),
            )
        except Exception as e:
            logger.info("ReAct pre-loop call failed, proceeding without tools: %s", e)
            return _finalize(working_prompt, tool_was_used, lang)

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return _finalize(working_prompt, tool_was_used, lang)
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []

        function_call_part = None
        for part in parts:
            if getattr(part, "function_call", None) is not None:
                function_call_part = part.function_call
                break

        if function_call_part is None:
            # Reflection: the model didn't ask for a tool this turn --
            # nothing more to do, hand back to the normal streaming call.
            return _finalize(working_prompt, tool_was_used, lang)

        name = function_call_part.name
        args = dict(function_call_part.args or {})
        if name not in _TOOL_NAMES:
            # Hard constraint, not a soft check: only these three tools
            # are ever executed, no matter what the model asks for.
            logger.warning("ReAct pre-loop: model requested unknown/disallowed tool %s -- ignored", name)
            return _finalize(working_prompt, tool_was_used, lang)

        note(
            f"AGENT_LOG: {'الوكيل ينفذ إجراءً مستقلاً (' + name + ')...' if lang == 'ar' else 'Agent is autonomously running a tool (' + name + ')...'}"
        )

        if name == "run_python_code":
            observation = _run_python_tool(args.get("code", ""))
        elif name == "create_notification":
            observation = _create_notification_tool(
                user_id, args.get("title", ""), args.get("message", ""), args.get("notif_type", "info"),
            )
        elif name == "save_memory":
            observation = _save_memory_tool(ai_service, user_id, args.get("content", ""))
        else:
            observation = "Tool not available."

        tool_was_used = True
        working_prompt = (
            working_prompt
            + "\n\n[Internal tool call -- not shown to the user]"
            + f"\nAction: {name}({json.dumps(args, ensure_ascii=False)})"
            + f"\nObservation: {observation}\n"
        )

    return _finalize(working_prompt, tool_was_used, lang)
