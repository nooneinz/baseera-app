"""
Latency fix: route_message()'s LLM secondary classifier ("give the LLM a
second opinion on an uncertain SINGLE_FILE heuristic call") used to run
unconditionally for every single-file message, adding a full extra live
API round trip before the actual answer even started streaming -- paid on
almost every normal business question, not just genuinely ambiguous ones.

classify_route()'s OFF_TOPIC branches both require "not has_business_signal"
to fire at all, so once a message has a clear business-domain keyword,
OFF_TOPIC is structurally unreachable regardless of what a live second
opinion would say. That means the secondary classifier is now skipped
whenever the message already carries a business-domain signal, and still
runs for genuinely ambiguous messages (no business keyword at all) exactly
as before.
"""
from django.test import TestCase

from dashboard.services import orchestrator


class _CountingFakeAIService:
    """Tracks whether the LLM secondary classifier was ever invoked."""

    def __init__(self, verdict="SINGLE_FILE"):
        self.calls = 0
        self.client = self
        self.verdict = verdict

    @property
    def models(self):
        return self

    def generate_content(self, model, contents):
        self.calls += 1

        class _R:
            pass

        r = _R()
        r.text = self.verdict
        return r


class SkipSecondaryClassifierWithBusinessSignalTests(TestCase):
    def test_a_clear_business_question_never_reaches_the_llm_classifier(self):
        fake_ai = _CountingFakeAIService()
        result = orchestrator.route_message(
            user_id=None, message="أسباب ارتفاع الهدر", lang="ar", ai_service=fake_ai,
        )
        self.assertEqual(result["route"], orchestrator.ROUTE_SINGLE_FILE)
        self.assertEqual(fake_ai.calls, 0)

    def test_a_revenue_question_never_reaches_the_llm_classifier(self):
        fake_ai = _CountingFakeAIService()
        orchestrator.route_message(
            user_id=None, message="أسباب ارتفاع الإيرادات؟", lang="ar", ai_service=fake_ai,
        )
        self.assertEqual(fake_ai.calls, 0)

    def test_an_english_business_question_never_reaches_the_llm_classifier(self):
        fake_ai = _CountingFakeAIService()
        orchestrator.route_message(
            user_id=None, message="What is driving the increase in cost?", lang="en", ai_service=fake_ai,
        )
        self.assertEqual(fake_ai.calls, 0)

    def test_a_genuinely_ambiguous_message_still_gets_the_live_second_opinion(self):
        """
        Regression guard: the safety net must still exist for messages
        with NO business-domain signal at all -- these are exactly the
        cases classify_route() can't rule OFF_TOPIC out for on its own.
        """
        fake_ai = _CountingFakeAIService(verdict="OFF_TOPIC")
        result = orchestrator.route_message(
            user_id=None, message="ايش رايك بالطقس اليوم", lang="ar", ai_service=fake_ai,
        )
        self.assertEqual(fake_ai.calls, 1)
        self.assertEqual(result["route"], orchestrator.ROUTE_OFF_TOPIC)

    def test_multi_agent_route_never_needed_the_llm_classifier_anyway(self):
        fake_ai = _CountingFakeAIService()
        result = orchestrator.route_message(
            user_id=None, message="احتاج خطة استراتيجية للتوسع", lang="ar", ai_service=fake_ai,
        )
        self.assertEqual(result["route"], orchestrator.ROUTE_MULTI_AGENT)
        self.assertEqual(fake_ai.calls, 0)
