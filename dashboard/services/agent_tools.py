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

logger = logging.getLogger(__name__)

MAX_REACT_ITERATIONS = 3

# The hard constraint, enforced at the code layer rather than trusted to
# prompt wording: these are the ONLY names ever executed, no matter what
# the model requests.
#
# SECURITY (F-01): "run_python_code" was removed. It exec()'d model-chosen
# code with full builtins inside the Django process, which any registered
# user could drive via /api/insights/chat -> remote code execution / full
# server + multi-tenant compromise. There is no server-side Python-exec
# tool anymore; arithmetic the model needs is done by the model itself.
_TOOL_NAMES = {
    "create_notification", "save_memory",
    # READ-ONLY query tools (compute/read & return, never mutate):
    "get_runway", "get_cashflow", "get_benchmark",
    "get_waste_summary", "get_recent_files", "search_documents",
    "draft_negotiation_message",
    # Safe natural-language data exploration (parameters only, no exec):
    "describe_dataset", "count_where",
}

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
    # Financial read-only tools:
    "سيولة", "الرصيد", "تكفي", "تخلص فلوس", "متى ينفد", "runway", "burn", "cash",
    "دخل", "مصروف", "صافي", "وين تروح", "أكبر مصروف", "income", "expense", "net",
    # Financial read-only tools -- explicit phrases only, so an ordinary
    # analytical "why is my waste high?" question does NOT pay the pre-loop.
    "متى تخلص", "متى ينفد", "كم يكفيني", "كم يكفي", "الرصيد يكفي", "توقع السيولة",
    "runway", "burn rate", "how long will my", "when will i run out",
    "كم دخلي", "كم مصروفي", "وين تروح فلوسي", "my cash flow",
    "قارني", "قارنّي", "مقارنتي", "مقارنة القطاع", "compare me", "vs my sector",
    "ملفاتي", "بياناتي المرفوعة", "وش رفعت", "my uploaded files",
    "ابحث في", "دوّر لي", "search my", "find in my",
    "رسالة تفاوض", "صيغ لي رسالة", "تفاوض مع", "negotiation message", "negotiate with",
    "كم عدد", "كم من", "عدد المعاملات", "كم معاملة", "كم عملية", "متوسط", "توزيع",
    "how many", "count of", "average of", "distribution of", "describe the data",
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


def _rows_for(user_id, cap=10000):
    from dashboard.models import DynamicRecord
    if not user_id:
        return []
    return list(
        DynamicRecord.objects.filter(user_id=user_id)
        .values_list("row_data", flat=True)[:cap]
    )


def _get_runway_tool(user_id):
    """READ-ONLY: the user's cash-flow runway, computed deterministically."""
    try:
        from dashboard.services.runway import compute_runway
        return json.dumps(compute_runway(_rows_for(user_id)), ensure_ascii=False)
    except Exception as e:
        logger.info("get_runway tool failed: %s", e)
        return "Could not compute runway."


def _get_cashflow_tool(user_id):
    """READ-ONLY: income / expense / net and the biggest expense groups."""
    try:
        from dashboard.services.first_win_insights import compute_transaction_signal
        cf = compute_transaction_signal(_rows_for(user_id))
        return json.dumps(cf, ensure_ascii=False) if cf else "No transaction-shaped data available."
    except Exception as e:
        logger.info("get_cashflow tool failed: %s", e)
        return "Could not compute cash flow."


def _get_benchmark_tool(user_id):
    """READ-ONLY: anonymized sector benchmark for this user."""
    try:
        from django.contrib.auth.models import User
        from dashboard.services.sector_benchmark import sector_benchmark_for
        if not user_id:
            return "No active user session."
        return json.dumps(sector_benchmark_for(User.objects.get(id=user_id)), ensure_ascii=False)
    except Exception as e:
        logger.info("get_benchmark tool failed: %s", e)
        return "Could not compute sector benchmark."


def _get_waste_summary_tool(user_id):
    """READ-ONLY: the biggest quantified money-leak sources."""
    try:
        from dashboard.services.waste_analyzer import compute_waste_signals
        w = compute_waste_signals(_rows_for(user_id)) or {}
        top = sorted(
            (w.get("signals") or []),
            key=lambda s: s.get("currency_amount", 0) or 0, reverse=True,
        )[:3]
        return json.dumps({
            "total_waste": w.get("total_waste", 0),
            "top_sources": [
                {"title": s.get("title"), "amount": s.get("currency_amount"),
                 "evidence_count": s.get("evidence_count")} for s in top
            ],
        }, ensure_ascii=False)
    except Exception as e:
        logger.info("get_waste_summary tool failed: %s", e)
        return "Could not compute waste summary."


def _get_recent_files_tool(user_id):
    """READ-ONLY: what data the user has, so the agent knows what it can use."""
    try:
        from dashboard.models import ProjectFile, DynamicRecord
        if not user_id:
            return "No active user session."
        files = ProjectFile.objects.filter(user_id=user_id).order_by("-uploaded_at")[:5]
        out = []
        for f in files:
            out.append({
                "name": (f.excel_file.name.split("/")[-1] if f.excel_file else f"file-{f.id}"),
                "rows": DynamicRecord.objects.filter(project_file=f).count(),
                "uploaded": f.uploaded_at.strftime("%Y-%m-%d") if f.uploaded_at else None,
            })
        return json.dumps(out, ensure_ascii=False) if out else "No files uploaded yet."
    except Exception as e:
        logger.info("get_recent_files tool failed: %s", e)
        return "Could not list recent files."


def _search_documents_tool(user_id, query):
    """READ-ONLY: hybrid RAG search over the user's own uploaded sheets."""
    try:
        from dashboard.services.retrieval_service import search_relevant_sheets
        if not user_id:
            return "No active user session."
        hits = search_relevant_sheets(user_id, (query or "").strip(), top_k=5) or []
        return json.dumps(hits, ensure_ascii=False) if hits else "No relevant documents found."
    except Exception as e:
        logger.info("search_documents tool failed: %s", e)
        return "Could not search documents."


def _draft_negotiation_tool(user_id):
    """READ-ONLY: a ready-to-send supplier renegotiation message for the
    biggest recurring expense, grounded in the real numbers."""
    try:
        from dashboard.services.agent_actions import _detect_finding, _draft_negotiation
        finding = _detect_finding(_rows_for(user_id))
        if not finding:
            return "No clear recurring expense to negotiate yet."
        return _draft_negotiation(finding)
    except Exception as e:
        logger.info("draft_negotiation tool failed: %s", e)
        return "Could not draft a negotiation message."


# Safe, whitelisted comparison operators for count_where. Note: there is NO
# code-execution here -- the model supplies only a column name, an operator
# from this set, and a value; trusted pandas code does the comparison. This is
# the SAFE equivalent of a "pandas agent" that avoids the F-01 RCE that comes
# from letting a model generate and exec() arbitrary code on a DataFrame.
_ALLOWED_QUERY_OPS = {"==", "!=", ">", ">=", "<", "<=", "contains"}


def _describe_dataset_tool(user_id):
    """READ-ONLY: a safe profile of the user's data (shape, numeric stats, top
    categorical values), computed by trusted pandas -- never generated code."""
    try:
        import pandas as pd
        rows = _rows_for(user_id)
        if not rows:
            return "No data uploaded yet."
        df = pd.DataFrame(rows)
        out = {"rows": int(len(df)), "columns": [str(c) for c in list(df.columns)[:50]]}
        num = df.apply(pd.to_numeric, errors="coerce")
        num = num.dropna(axis=1, how="all")
        if not num.empty:
            out["numeric"] = {
                str(c): {
                    "min": round(float(num[c].min()), 2),
                    "max": round(float(num[c].max()), 2),
                    "mean": round(float(num[c].mean()), 2),
                    "sum": round(float(num[c].sum()), 2),
                } for c in list(num.columns)[:15]
            }
        top = {}
        for c in df.columns:
            if str(c) in out.get("numeric", {}):
                continue
            vc = df[c].astype(str).value_counts().head(5)
            if len(vc):
                top[str(c)] = {str(k): int(v) for k, v in vc.items()}
        if top:
            out["top_values"] = {k: top[k] for k in list(top)[:10]}
        return json.dumps(out, ensure_ascii=False)[:6000]
    except Exception as e:
        logger.info("describe_dataset tool failed: %s", e)
        return "Could not describe the dataset."


def _count_where_tool(user_id, column, op, value):
    """READ-ONLY: count rows where <column> <op> <value>. Parameters only --
    no generated code, only a whitelisted operator applied by trusted pandas."""
    try:
        import pandas as pd
        op = (op or "").strip()
        if op not in _ALLOWED_QUERY_OPS:
            return f"Unsupported operator. Use one of: {sorted(_ALLOWED_QUERY_OPS)}"
        rows = _rows_for(user_id)
        if not rows:
            return "No data uploaded yet."
        df = pd.DataFrame(rows)
        if column not in df.columns:
            return f"Column '{column}' not found. Available: {[str(c) for c in list(df.columns)[:20]]}"
        col = df[column]
        if op == "contains":
            mask = col.astype(str).str.contains(str(value), case=False, na=False)
        else:
            num = pd.to_numeric(col, errors="coerce")
            try:
                v = float(value)
            except (TypeError, ValueError):
                v = None
            if v is not None and num.notna().any() and op in {">", ">=", "<", "<=", "==", "!="}:
                mask = {">": num > v, ">=": num >= v, "<": num < v, "<=": num <= v,
                        "==": num == v, "!=": num != v}[op]
            else:
                s = col.astype(str)
                mask = (s == str(value)) if op == "==" else (s != str(value))
        return json.dumps(
            {"column": str(column), "op": op, "value": value, "count": int(mask.sum())},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.info("count_where tool failed: %s", e)
        return "Could not run the count."


def build_agent_tools():
    """
    The ONLY function-calling surface the agent is ever given -- see
    module docstring for why financial/decision *mutations* are deliberately
    absent. The get_*/search_*/draft_*/describe_*/count_* tools below are
    READ-ONLY: they compute and return values from the user's own rows (via
    the deterministic engines and trusted pandas) but never change a financial
    figure, and never execute model-generated code.
    """
    # SECURITY (F-01): a "run_python_code" tool was removed here -- see the
    # note on _TOOL_NAMES. The model is never handed a server-side code
    # execution tool.
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
    get_runway_fd = types.FunctionDeclaration(
        name="get_runway",
        description=(
            "READ-ONLY. Returns the business's cash-flow runway (average "
            "monthly net, monthly burn, current cash, and the projected date "
            "the money runs out) computed deterministically from the user's "
            "own transactions. Use it when the user asks how long their money "
            "lasts, about burn rate, or when they will run out of cash."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    )
    get_cashflow_fd = types.FunctionDeclaration(
        name="get_cashflow",
        description=(
            "READ-ONLY. Returns total income, total expense, net, and the "
            "biggest/most recurring expense destinations from the user's own "
            "transactions. Use it when the user asks about income vs expense, "
            "their net, or where their money goes."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    )
    get_benchmark_fd = types.FunctionDeclaration(
        name="get_benchmark",
        description=(
            "READ-ONLY. Returns an anonymized comparison of the user's key "
            "ratios against the median of peer businesses in the same sector. "
            "Use it when the user asks how they compare to similar businesses "
            "or to their sector."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    )
    get_waste_summary_fd = types.FunctionDeclaration(
        name="get_waste_summary",
        description=(
            "READ-ONLY. Returns the biggest quantified money-leak sources and "
            "total waste from the user's own data. Use it when the user asks "
            "where they lose money or about waste/leakage."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    )
    get_recent_files_fd = types.FunctionDeclaration(
        name="get_recent_files",
        description=(
            "READ-ONLY. Lists the user's most recent uploaded files with row "
            "counts and dates, so you know what data is available. Use it when "
            "you need to know what the user has uploaded, or they ask what data "
            "you have."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    )
    search_documents_fd = types.FunctionDeclaration(
        name="search_documents",
        description=(
            "READ-ONLY. Searches the user's own uploaded sheets/documents for "
            "the given query and returns the most relevant ones. Use it to "
            "find a specific file or detail the user refers to."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to search for."}},
            "required": ["query"],
        },
    )
    draft_negotiation_fd = types.FunctionDeclaration(
        name="draft_negotiation_message",
        description=(
            "READ-ONLY. Returns a ready-to-send supplier renegotiation message "
            "for the user's biggest recurring expense, grounded in the real "
            "numbers. Use it when the user wants to negotiate or reduce a "
            "recurring cost."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    )
    describe_dataset_fd = types.FunctionDeclaration(
        name="describe_dataset",
        description=(
            "READ-ONLY. Returns a safe profile of the user's uploaded data: "
            "row count, columns, numeric stats (min/max/mean/sum per numeric "
            "column) and the top values of categorical columns. Use it to "
            "explore the data, answer 'what's the average / distribution of X', "
            "or understand the columns before answering."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    )
    count_where_fd = types.FunctionDeclaration(
        name="count_where",
        description=(
            "READ-ONLY. Counts how many rows in the user's data match a simple "
            "condition on one column. Use it for 'how many transactions above "
            "1000', 'how many expense rows', etc."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "column": {"type": "string", "description": "Exact column name to filter on."},
                "op": {"type": "string", "description": "One of: ==, !=, >, >=, <, <=, contains"},
                "value": {"type": "string", "description": "The value to compare against."},
            },
            "required": ["column", "op", "value"],
        },
    )
    return types.Tool(function_declarations=[
        create_notification_fd, save_memory_fd,
        get_runway_fd, get_cashflow_fd, get_benchmark_fd,
        get_waste_summary_fd, get_recent_files_fd, search_documents_fd, draft_negotiation_fd,
        describe_dataset_fd, count_where_fd,
    ])


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

        if name == "create_notification":
            observation = _create_notification_tool(
                user_id, args.get("title", ""), args.get("message", ""), args.get("notif_type", "info"),
            )
        elif name == "save_memory":
            observation = _save_memory_tool(ai_service, user_id, args.get("content", ""))
        elif name == "get_runway":
            observation = _get_runway_tool(user_id)
        elif name == "get_cashflow":
            observation = _get_cashflow_tool(user_id)
        elif name == "get_benchmark":
            observation = _get_benchmark_tool(user_id)
        elif name == "get_waste_summary":
            observation = _get_waste_summary_tool(user_id)
        elif name == "get_recent_files":
            observation = _get_recent_files_tool(user_id)
        elif name == "search_documents":
            observation = _search_documents_tool(user_id, args.get("query", ""))
        elif name == "draft_negotiation_message":
            observation = _draft_negotiation_tool(user_id)
        elif name == "describe_dataset":
            observation = _describe_dataset_tool(user_id)
        elif name == "count_where":
            observation = _count_where_tool(user_id, args.get("column", ""), args.get("op", ""), args.get("value", ""))
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
