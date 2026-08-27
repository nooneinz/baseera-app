"""
Task 2 (scoped): real Gemini Function Declarations + a bounded autonomous
ReAct loop for Baseera's non-financial agent tools.

The central guarantee under test: financial/decision actions have NO
callable tool at all (a hard constraint enforced in code, not merely
described in a prompt), while the three non-financial tools are executed
for real via genuine function calls -- never via regex-parsed text tags.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from google.genai import types

from dashboard.models import AgentMemory, Notification
from dashboard.services.agent_tools import build_agent_tools, run_react_preloop, should_attempt_react
from dashboard.services.ai_service import GeminiAIService


def _function_call_response(name, args):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
                )
            )
        ]
    )


def _text_response(text):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))]
    )


class BuildAgentToolsHardConstraintTests(TestCase):
    """The function-calling surface must never include a financial/
    decision-metric tool, no matter what the model is told."""

    def test_only_the_three_non_financial_tools_are_ever_declared(self):
        tool = build_agent_tools()
        names = {fd.name for fd in tool.function_declarations}
        self.assertEqual(names, {"run_python_code", "create_notification", "save_memory"})

    def test_no_financial_or_decision_tool_names_are_present(self):
        tool = build_agent_tools()
        names = {fd.name for fd in tool.function_declarations}
        forbidden = {"update_decision_metric", "resolve_risk", "resolve_leak", "apply_agent_decision"}
        self.assertEqual(names & forbidden, set())


class RunReactPreloopTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="react_user", password="pw123456")

    def test_no_op_when_ai_service_has_no_client(self):
        fake_ai_service = MagicMock()
        fake_ai_service.client = None
        result = run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash")
        self.assertEqual(result, "base prompt")

    def test_real_python_tool_executes_and_observation_is_appended(self):
        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = [
            _function_call_response("run_python_code", {"code": "print(2 + 2)"}),
            _text_response("no more tools needed"),
        ]
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        result = run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash")

        self.assertIn("Action: run_python_code", result)
        self.assertIn("Observation: 4", result)  # print(2+2) really executed

    def test_create_notification_tool_creates_a_real_notification(self):
        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = [
            _function_call_response(
                "create_notification",
                {"title": "Reminder", "message": "Follow up on invoice", "notif_type": "info"},
            ),
            _text_response("done"),
        ]
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        self.assertEqual(Notification.objects.filter(user=self.user).count(), 0)
        run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash")
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)
        notif = Notification.objects.get(user=self.user)
        self.assertEqual(notif.title, "Reminder")

    def test_save_memory_tool_saves_a_real_memory(self):
        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = [
            _function_call_response("save_memory", {"content": "User prefers cash-flow-first recommendations."}),
            _text_response("done"),
        ]
        fake_client.models.embed_content.side_effect = Exception("embedding unavailable in test")
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        self.assertEqual(AgentMemory.objects.filter(user=self.user).count(), 0)
        run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash")
        self.assertEqual(AgentMemory.objects.filter(user=self.user).count(), 1)

    def test_disallowed_tool_name_is_never_executed(self):
        """
        Hard constraint: even if a live model somehow requested a
        financial-sounding tool name (it never has a declaration for one,
        but this proves the code-level gate too), the loop must refuse it
        rather than execute anything.
        """
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = _function_call_response(
            "update_decision_metric", {"metric": "strategy", "status": "active"}
        )
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        result = run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash")

        # Refused immediately -- the prompt comes back unchanged, no
        # Action/Observation trace for the disallowed tool.
        self.assertEqual(result, "base prompt")
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 0)

    def test_loop_is_bounded_and_does_not_run_forever(self):
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = _function_call_response(
            "run_python_code", {"code": "print('again')"}
        )
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash", max_iterations=3)

        self.assertEqual(fake_client.models.generate_content.call_count, 3)

    def test_a_failed_model_call_ends_the_loop_without_raising(self):
        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = Exception("503 model overloaded")
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        result = run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash")
        self.assertEqual(result, "base prompt")

    def test_on_state_callback_is_invoked_when_a_tool_runs(self):
        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = [
            _function_call_response("run_python_code", {"code": "print('x')"}),
            _text_response("done"),
        ]
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        seen = []
        run_react_preloop(
            fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash",
            on_state=lambda msg: seen.append(msg),
        )
        self.assertTrue(any("AGENT_LOG:" in m for m in seen))

    def test_no_tool_used_returns_the_prompt_completely_unchanged(self):
        """
        The common case (no tool call at all) must cost nothing extra --
        no closing instruction, no re-opened "model:" cue, byte-identical
        to the input.
        """
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = _text_response("just an answer, no tool needed")
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        result = run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash")
        self.assertEqual(result, "base prompt")

    def test_after_a_tool_runs_the_model_is_told_not_to_leak_it_and_gets_a_fresh_cue(self):
        """
        Regression test for the exact bug reported in production: the raw
        [[tool call/code appeared in the user-visible answer instead of a
        synthesized final response, because the augmented prompt just
        continued mid-completion with no instruction to switch back to a
        normal answer. This proves the fix: after any tool call, the
        returned prompt tells the model the trace is internal-only and
        re-opens a clean "model: " turn for the real answer.
        """
        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = [
            _function_call_response("run_python_code", {"code": "print(42)"}),
            _text_response("no more tools needed"),
        ]
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        result_ar = run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash", lang="ar")
        self.assertTrue(result_ar.rstrip().endswith("model:") or result_ar.endswith("model: "))
        self.assertIn("لم ير أي كود", result_ar)

    def test_english_closing_instruction_for_english_conversations(self):
        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = [
            _function_call_response("run_python_code", {"code": "print(1)"}),
            _text_response("done"),
        ]
        fake_ai_service = MagicMock()
        fake_ai_service.client = fake_client

        result = run_react_preloop(fake_ai_service, "base prompt", self.user.id, "gemini-3.6-flash", lang="en")
        self.assertIn("has not seen any code", result)
        self.assertTrue(result.endswith("model: "))


class ShouldAttemptReactGateTests(TestCase):
    """
    Latency gate: an ordinary analytical question must NOT trigger the
    (costly) ReAct pre-loop at all, only messages that plausibly need one
    of the three tools.
    """

    def test_ordinary_analytical_question_does_not_trigger(self):
        self.assertFalse(should_attempt_react("ما سبب ارتفاع الهدر؟"))
        self.assertFalse(should_attempt_react("What is the reason for the high waste?"))

    def test_calculation_request_triggers(self):
        self.assertTrue(should_attempt_react("احسبلي الربح الشهري"))
        self.assertTrue(should_attempt_react("Can you calculate the monthly profit for me?"))

    def test_memory_request_triggers(self):
        self.assertTrue(should_attempt_react("تذكر هذا للمرة القادمة"))
        self.assertTrue(should_attempt_react("Please remember this for next time"))

    def test_reminder_request_triggers(self):
        self.assertTrue(should_attempt_react("ذكرني بمراجعة الفاتورة غداً"))
        self.assertTrue(should_attempt_react("Remind me to review the invoice tomorrow"))

    def test_empty_or_none_never_triggers(self):
        self.assertFalse(should_attempt_react(""))
        self.assertFalse(should_attempt_react(None))


class _FakeChunk:
    def __init__(self, text):
        self.text = text


class GenerateChatStreamReactIntegrationTests(TestCase):
    """
    End-to-end through the real chat entry point: the ReAct pre-loop runs
    before the final streamed answer, its tool call is real (not a text
    tag), and the Observation it produces actually reaches the prompt used
    for the final answer -- all without changing the stream's existing
    wire format.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="react_stream_user", password="pw123456")

    def test_tool_runs_and_its_observation_reaches_the_final_prompt(self):
        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = [
            _function_call_response("run_python_code", {"code": "print(21 * 2)"}),
            _text_response("no more tools needed"),
        ]
        fake_client.models.generate_content_stream.return_value = iter(
            [_FakeChunk("Final answer text."), _FakeChunk("STATUS___:DONE")]
        )

        # Real constructor (sets up system prompts / standard_agents as
        # usual), then swap in the fake client so no real network call
        # happens for either the ReAct pre-loop or the final stream.
        service = GeminiAIService()
        service.client = fake_client

        stream = service.generate_chat_stream(
            messages_list=[{"role": "user", "content": "Can you calculate 21 times 2 for me?"}],
            file_context="",
            user_id=self.user.id,
            agent_id="general",
            lang="en",
        )
        raw_chunks = list(stream)

        import json as _json
        texts = []
        for raw in raw_chunks:
            payload = _json.loads(raw[len("data: "):].strip())
            parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])
            if parts and "text" in parts[0]:
                texts.append(parts[0]["text"])
        full_output = "".join(texts)
        # The tool-use progress notice reached the (existing) stream format.
        self.assertIn("AGENT_LOG:", full_output)

        # The real tool actually ran and its result was fed back as an
        # Observation into the prompt used for the final answer -- and the
        # model was told to answer normally rather than echo the trace.
        final_call_kwargs = fake_client.models.generate_content_stream.call_args.kwargs
        self.assertIn("Observation:", final_call_kwargs["contents"])
        self.assertIn("42", final_call_kwargs["contents"])
        self.assertTrue(final_call_kwargs["contents"].endswith("model: "))

    def test_ordinary_question_skips_the_preloop_entirely(self):
        """
        The latency gate: a plain analytical question (no calculation/
        memory/reminder wording) must never even call the pre-loop's
        generate_content -- only the normal final generate_content_stream,
        with the prompt completely untouched by this feature.
        """
        fake_client = MagicMock()
        fake_client.models.generate_content_stream.return_value = iter(
            [_FakeChunk("Waste is high because of spoilage."), _FakeChunk("STATUS___:DONE")]
        )

        service = GeminiAIService()
        service.client = fake_client

        stream = service.generate_chat_stream(
            messages_list=[{"role": "user", "content": "What is the reason for the high waste?"}],
            file_context="",
            user_id=self.user.id,
            agent_id="general",
            lang="en",
        )
        list(stream)

        fake_client.models.generate_content.assert_not_called()
        fake_client.models.generate_content_stream.assert_called_once()
