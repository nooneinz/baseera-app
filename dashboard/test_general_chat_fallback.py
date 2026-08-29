"""
Regression tests for a reported bug: any message that reached the
Retrieval Layer's true dead end (zero matching sheets, no active-file
fallback) got the exact same hard-coded "couldn't find the document"
refusal -- including ordinary conversation that was never trying to look
up file data in the first place, and that no keyword list can ever fully
enumerate for a non-technical user's natural phrasing.

llm_needs_file_data() now gets one live model call at exactly that dead
end (never earlier -- see test_orchestrator_latency_gate.py for why the
existing secondary classifier is skipped whenever a business signal is
present) to ask whether the message needed file data at all. A "GENERAL"
verdict means: don't refuse, let the general agent answer naturally
instead (its own system prompt already keeps it from inventing financial
figures or wandering off the business/finance scope).
"""
from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.services import orchestrator


class _ScriptedFakeAIService:
    """Returns a scripted sequence of verdicts, one per live call, so a
    test can control both the existing secondary classifier (first call,
    when it fires) and llm_needs_file_data (the next call) independently."""

    def __init__(self, verdicts):
        self._verdicts = list(verdicts)
        self.calls = 0
        self.client = self

    @property
    def models(self):
        return self

    def generate_content(self, model, contents):
        self.calls += 1

        class _R:
            pass

        r = _R()
        r.text = self._verdicts.pop(0) if self._verdicts else ""
        return r


class LlmNeedsFileDataUnitTests(TestCase):
    def test_general_verdict_returns_false(self):
        fake_ai = _ScriptedFakeAIService(["GENERAL"])
        self.assertFalse(orchestrator.llm_needs_file_data(fake_ai, "كيفك يعني كيف الحال؟"))

    def test_needs_data_verdict_returns_true(self):
        fake_ai = _ScriptedFakeAIService(["NEEDS_DATA"])
        self.assertTrue(orchestrator.llm_needs_file_data(fake_ai, "كم كانت مبيعات دجاج الشهر اللي فات"))

    def test_no_client_returns_none(self):
        class _NoClient:
            client = None
        self.assertIsNone(orchestrator.llm_needs_file_data(_NoClient(), "شي"))

    def test_call_failure_returns_none_not_a_crash(self):
        class _Boom:
            client = property(lambda self: self)
            models = property(lambda self: self)

            def generate_content(self, model, contents):
                raise RuntimeError("down")

        self.assertIsNone(orchestrator.llm_needs_file_data(_Boom(), "شي"))


class RouteMessageGeneralChatFallbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="chat_fallback_user", password="pw123456")

    def test_general_chat_gets_a_natural_route_not_a_refusal(self):
        # No business signal, not a recognized greeting/filler pattern, and
        # (with zero files uploaded) no sheet metadata to match -- exactly
        # the dead end this fix targets. First live call is the existing
        # secondary classifier (kept at SINGLE_FILE), second is the new check.
        fake_ai = _ScriptedFakeAIService(["SINGLE_FILE", "GENERAL"])
        result = orchestrator.route_message(
            user_id=self.user.id, message="طيب وبعدين", lang="ar", ai_service=fake_ai,
        )
        self.assertEqual(result["route"], orchestrator.ROUTE_SINGLE_FILE)
        self.assertIsNone(result["direct_reply"])
        self.assertFalse(result["needs_confirmation"])
        self.assertEqual(result["matched_sheet_note"], "")
        self.assertEqual(fake_ai.calls, 2)

    def test_a_real_data_question_still_gets_the_honest_missing_file_reply(self):
        fake_ai = _ScriptedFakeAIService(["SINGLE_FILE", "NEEDS_DATA"])
        result = orchestrator.route_message(
            user_id=self.user.id, message="كم كانت مبيعات دجاج الشهر اللي فات", lang="ar", ai_service=fake_ai,
        )
        self.assertTrue(result["needs_confirmation"])
        self.assertEqual(result["direct_reply"], orchestrator.missing_file_reply("ar"))

    def test_without_a_live_ai_service_the_conservative_refusal_is_unchanged(self):
        # No second opinion available -> never guess "general chat", keep
        # the existing honest behavior exactly as before this fix.
        result = orchestrator.route_message(
            user_id=self.user.id, message="طيب وبعدين", lang="ar", ai_service=None,
        )
        self.assertTrue(result["needs_confirmation"])
        self.assertEqual(result["direct_reply"], orchestrator.missing_file_reply("ar"))

    def test_the_exact_reported_followup_message_no_longer_refuses(self):
        # From the report: after "كيفك" was answered, the user's next
        # message still needs to be able to reach a natural reply.
        fake_ai = _ScriptedFakeAIService(["SINGLE_FILE", "GENERAL"])
        result = orchestrator.route_message(
            user_id=self.user.id, message="ساعدني", lang="ar", ai_service=fake_ai,
        )
        self.assertIsNone(result["direct_reply"])
        self.assertFalse(result["needs_confirmation"])
