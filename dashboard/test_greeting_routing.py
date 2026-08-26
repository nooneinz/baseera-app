"""
Tests for the Orchestrator's greeting handling (dashboard/services/
orchestrator.py).

Concrete bug this fixes: a bare greeting ("هلا") or a greeting with a real
request attached ("السلام عليكم احتاج منك خطة") was getting the canned
off-topic refusal instead of a normal reply. Root cause: classify_route()'s
heuristic correctly returned SINGLE_FILE for these (no off-topic trigger
matched), but route_message() then asks llm_classify_route() for a second
opinion on any SINGLE_FILE result -- and that secondary classifier's own
job description ("no relevance to finance/business at all") technically
fits a bare "hi", so once the underlying model actually started responding
(it had been silently 404ing before), it started reclassifying greetings
and vague help requests as OFF_TOPIC.

Fixed with a dedicated ROUTE_GREETING short-circuit that is decided BEFORE
either the heuristic's off-topic checks or the LLM secondary classifier
ever run, plus a widened BUSINESS_DOMAIN_KEYWORDS list ("خطة" etc.) so a
greeting-plus-real-request routes confidently via the heuristic alone.
"""
from django.test import TestCase

from dashboard.services import orchestrator


class _CountingFakeAIService:
    """Tracks whether the LLM secondary classifier was ever invoked."""
    def __init__(self):
        self.calls = 0
        self.client = self

    @property
    def models(self):
        return self

    def generate_content(self, model, contents):
        self.calls += 1
        class _R:
            text = "OFF_TOPIC"
        return _R()


class PureGreetingClassificationTests(TestCase):
    def test_bare_arabic_greeting_is_classified_as_greeting(self):
        self.assertEqual(orchestrator.classify_route("هلا"), orchestrator.ROUTE_GREETING)

    def test_bare_english_greeting_is_classified_as_greeting(self):
        self.assertEqual(orchestrator.classify_route("hello"), orchestrator.ROUTE_GREETING)

    def test_greeting_plus_small_talk_is_still_a_greeting(self):
        self.assertEqual(orchestrator.classify_route("هلا كيفك"), orchestrator.ROUTE_GREETING)

    def test_greeting_with_a_real_request_is_not_treated_as_pure_greeting(self):
        # "السلام عليكم احتاج منك خطة" -- a greeting WITH an actual request
        # attached must go through normal routing, not a canned hello. With
        # "خطة" now recognized as a business/strategic signal, this
        # confidently routes to MULTI_AGENT via the heuristic alone.
        route = orchestrator.classify_route("السلام عليكم احتاج منك خطة")
        self.assertNotEqual(route, orchestrator.ROUTE_GREETING)
        self.assertEqual(route, orchestrator.ROUTE_MULTI_AGENT)

    def test_off_topic_trivia_is_unaffected_by_the_greeting_check(self):
        route = orchestrator.classify_route("ما عاصمة مسقط؟", has_uploaded_files=False)
        self.assertEqual(route, orchestrator.ROUTE_OFF_TOPIC)


class GreetingRouteMessageTests(TestCase):
    def test_greeting_gets_a_direct_warm_reply_not_a_refusal(self):
        result = orchestrator.route_message(user_id=None, message="هلا", lang="ar")
        self.assertEqual(result["route"], orchestrator.ROUTE_GREETING)
        self.assertIsNotNone(result["direct_reply"])
        self.assertNotIn("خارج اختصاصي", result["direct_reply"])

    def test_greeting_never_reaches_the_llm_secondary_classifier(self):
        """
        Regression guard: even if the LLM client is available and would
        (wrongly) say OFF_TOPIC for a bare greeting, it must never be asked
        in the first place -- a confident ROUTE_GREETING heuristic hit is
        decided before route_message's SINGLE_FILE-only LLM upgrade path.
        """
        fake_ai = _CountingFakeAIService()
        result = orchestrator.route_message(user_id=None, message="هلا", lang="ar", ai_service=fake_ai)
        self.assertEqual(result["route"], orchestrator.ROUTE_GREETING)
        self.assertEqual(fake_ai.calls, 0)
