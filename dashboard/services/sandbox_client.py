"""
Single entry point for running a short Python snippet on behalf of the AI
agent (the ReAct tool in agent_tools.py, and the legacy
[[ACTION:RUN_PYTHON|...]] text-tag path in ai_service.py both call
run_python_code() here instead of exec()-ing the snippet in-process).

Baseera runs in two different shapes, and the isolation available differs
between them:

  - Self-hosted (docker-compose.yml): a dedicated `sandbox` container runs
    the snippet as a separate OS process, on a Docker network with no
    outbound internet access (`baseera-private` is `internal: true`) and
    none of the app's secrets (it does not receive GEMINI_API_KEY_FILE).
    That combination -- separate process, no network egress, no secrets
    to read -- is the real security boundary. Reachable at SANDBOX_URL.

  - Render (render.yaml): a single web dyno, no sandbox sidecar at all.
    SANDBOX_URL is unset there, so this falls back to a restricted
    in-process exec(). That fallback is defense-in-depth, not a real
    sandbox: it runs in the same process as the rest of the app, with no
    timeout and no filesystem/network isolation. It only strips the
    handful of builtins an AI-generated snippet has no legitimate need
    for and that are the actual exfiltration/tampering surface (file I/O,
    dynamic import, re-entrant eval/exec, process control), so an obvious
    one-liner like `open('/etc/passwd').read()` fails immediately instead
    of silently succeeding. Treat it as reducing the blast radius, not
    eliminating it.

Either path always returns a plain string and never raises -- a failed or
blocked tool call is meant to read as "no tool ran" to the agent loop, not
as a reason to withhold the final answer (see agent_tools.py's module
docstring for that same "never blocks the final answer" contract).
"""
import builtins
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

# Builtins an AI-generated snippet has no legitimate need for and that are
# the actual exfiltration/tampering surface, blocked in the local fallback
# path. Not exhaustive/bulletproof -- see module docstring.
_BLOCKED_BUILTINS = {
    "open", "__import__", "eval", "exec", "compile",
    "input", "exit", "quit", "breakpoint", "help",
}


def _restricted_globals():
    safe_builtins = {
        name: obj for name, obj in vars(builtins).items()
        if name not in _BLOCKED_BUILTINS
    }
    return {"__builtins__": safe_builtins}


def _run_local_restricted(code):
    import sys
    from io import StringIO

    old_stdout = sys.stdout
    redirected_output = sys.stdout = StringIO()
    try:
        exec(code, _restricted_globals(), {})
        output = redirected_output.getvalue()
        if not output.strip():
            output = "Code executed successfully but no output was printed."
    except Exception as e:
        output = f"Error executing code: {str(e)}"
    finally:
        sys.stdout = old_stdout
    return output


def _run_via_sandbox_service(code, sandbox_url, timeout_seconds):
    url = sandbox_url.rstrip("/") + "/run"
    payload = json.dumps({"code": code, "timeout_seconds": timeout_seconds}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    # A few seconds of slack over the sandbox's own execution timeout, for
    # the request/response round trip itself.
    with urllib.request.urlopen(req, timeout=timeout_seconds + 5) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    if result.get("success"):
        output = result.get("output")
        if output in (None, ""):
            return "Code executed successfully but no output was printed."
        return output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    return f"Error executing code: {result.get('error') or 'unknown error'}"


def run_python_code(code, timeout_seconds=10):
    sandbox_url = getattr(settings, "SANDBOX_URL", "")
    if sandbox_url:
        try:
            return _run_via_sandbox_service(code, sandbox_url, timeout_seconds)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            logger.warning(
                "Sandbox service unreachable (%s) -- falling back to restricted local exec", e,
            )
    return _run_local_restricted(code)
