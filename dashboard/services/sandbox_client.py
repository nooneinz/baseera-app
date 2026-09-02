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
    filesystem/network isolation. It only strips the handful of builtins
    an AI-generated snippet has no legitimate need for and that are the
    actual exfiltration/tampering surface (file I/O, dynamic import,
    re-entrant eval/exec, process control), so an obvious one-liner like
    `open('/etc/passwd').read()` fails immediately instead of silently
    succeeding. Treat it as reducing the blast radius, not eliminating it.
    A wall-clock timeout is still enforced (see _run_local_restricted) so
    a runaway snippet can't hang the request -- but since Python threads
    can't be forcibly killed, a snippet that actually ignores the timeout
    (e.g. blocked on I/O, not just slow) keeps its worker thread running
    in the background rather than being terminated.

Either path always returns a plain string and never raises -- a failed or
blocked tool call is meant to read as "no tool ran" to the agent loop, not
as a reason to withhold the final answer (see agent_tools.py's module
docstring for that same "never blocks the final answer" contract).
"""
import builtins
import json
import logging
import threading
import urllib.error
import urllib.request
from io import StringIO

from django.conf import settings

logger = logging.getLogger(__name__)

# Builtins an AI-generated snippet has no legitimate need for and that are
# the actual exfiltration/tampering surface, blocked in the local fallback
# path. Not exhaustive/bulletproof -- see module docstring.
_BLOCKED_BUILTINS = {
    "open", "__import__", "eval", "exec", "compile",
    "input", "exit", "quit", "breakpoint", "help",
}


def _restricted_globals(output_buffer):
    safe_builtins = {
        name: obj for name, obj in vars(builtins).items()
        if name not in _BLOCKED_BUILTINS
    }
    # print() is rebound to write into a buffer private to this call,
    # instead of leaving the snippet to write through the real print()
    # onto the process-wide sys.stdout -- swapping that out for the
    # duration of exec() (the original approach) is not thread-safe: two
    # requests running this fallback at the same time would each
    # overwrite the other's redirect and could capture/leak each other's
    # output. A buffer captured in this closure has no such shared state.
    def _print(*args, sep=" ", end="\n", **kwargs):
        output_buffer.write(sep.join(str(a) for a in args))
        output_buffer.write(end)
    safe_builtins["print"] = _print
    return {"__builtins__": safe_builtins}


def _exec_and_capture(code):
    buffer = StringIO()
    try:
        exec(code, _restricted_globals(buffer), {})
        output = buffer.getvalue()
        if not output.strip():
            output = "Code executed successfully but no output was printed."
        return output
    except Exception as e:
        return f"Error executing code: {str(e)}"


def _run_local_restricted(code, timeout_seconds=10):
    result_box = {}

    def worker():
        result_box["value"] = _exec_and_capture(code)

    # A daemon thread, spawned fresh per call, rather than a shared
    # ThreadPoolExecutor: Python threads can't be killed, so a snippet
    # that ignores its timeout (e.g. `while True: pass`) keeps this
    # thread running forever -- daemon=True means that leaked thread is
    # simply abandoned at interpreter/worker-process shutdown instead of
    # blocking it. A shared non-daemon pool would instead accumulate
    # threads that keep the whole gunicorn worker alive indefinitely,
    # turning "reject one slow snippet" into "this process can never
    # exit cleanly again".
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout_seconds)
    if t.is_alive():
        return (
            f"Error executing code: timed out after {timeout_seconds}s "
            "(no process isolation in this fallback path -- see module docstring)"
        )
    return result_box.get("value", "Error executing code: unknown failure")


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
    return _run_local_restricted(code, timeout_seconds=timeout_seconds)
