"""
Regression test for a reported bug (with screenshot): "استراتيجيه للمستقبل"
(note the ه ending, not ة) fell through to the "couldn't find the
document" refusal instead of routing to the strategic multi-agent
committee.

Root cause: BUSINESS_DOMAIN_KEYWORDS only had the "استراتيجية" spelling
(تاء مربوطة). Arabic ة and ه look near-identical and are routinely typed
interchangeably (especially on mobile keyboards), so a message spelled
with the other letter silently failed the business-domain-signal check,
fell through to the default SINGLE_FILE route with no matching file, and
got the generic "couldn't find the document" reply.
"""
from django.test import TestCase

from dashboard.services import orchestrator


class ArabicTaMarbutaHaaNormalizationTests(TestCase):
    def test_business_signal_matches_both_spellings_of_strategy(self):
        self.assertTrue(orchestrator._has_business_domain_signal("استراتيجية للمستقبل"))
        self.assertTrue(orchestrator._has_business_domain_signal("استراتيجيه للمستقبل"))

    def test_the_exact_reported_message_routes_to_multi_agent_not_single_file(self):
        route = orchestrator.classify_route("استراتيجيه للمستقبل", has_uploaded_files=True)
        self.assertEqual(route, orchestrator.ROUTE_MULTI_AGENT)

    def test_route_message_end_to_end_does_not_refuse_with_no_document_found(self):
        result = orchestrator.route_message(user_id=None, message="استراتيجيه للمستقبل", lang="ar")
        self.assertEqual(result["route"], orchestrator.ROUTE_MULTI_AGENT)
        self.assertIsNone(result["direct_reply"])
        self.assertFalse(result["needs_confirmation"])

    def test_matches_any_is_also_spelling_insensitive(self):
        # STRATEGIC_DECISION_KEYWORDS or TRIVIA_PATTERNS entries spelled
        # with ة must still match a message spelled with ه, and vice versa.
        self.assertTrue(orchestrator._matches_any(["استراتيجية"], "خطة استراتيجيه جديدة"))
        self.assertTrue(orchestrator._matches_any(["استراتيجيه"], "خطة استراتيجية جديدة"))
