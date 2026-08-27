"""
Regression test for a reported bug (with screenshot): a strategic marketing
question ("اذا عطيتني خطة تسويقية للمشروع") in the committee (multi-agent)
route got a boilerplate "outside my expertise" reply from every single
specialist in the committee, making the "committee" look scripted rather
than a real discussion.

Root cause: DOMAIN_AGENT_MAP had no 'marketing' domain at all, so a
marketing question matched zero domains, and select_committee_agents()'s
fallback unconditionally forced in 'supply_chain' and 'pricing' -- two
specialists genuinely unrelated to the question -- alongside 'financial'.
All three then (correctly, given their narrow personas) declined the same
irrelevant question.

Fix: a 'marketing' domain/persona was added, and the zero-match fallback now
routes to ['general', 'marketing'] (the two agents actually built to handle
an undomained strategic ask) instead of forcing the financial/supply_chain/
pricing trio onto every unmatched question.
"""
from django.test import TestCase

from dashboard.services import orchestrator
from dashboard.services.ai_service import GeminiAIService


class CommitteeAgentSelectionTests(TestCase):
    def test_marketing_question_selects_marketing_agent_not_unrelated_specialists(self):
        agents = orchestrator.select_committee_agents("اذا عطيتني خطة تسويقية للمشروع")
        self.assertIn("marketing", agents)
        self.assertNotIn("supply_chain", agents)
        self.assertNotIn("pricing", agents)

    def test_zero_domain_match_falls_back_to_general_and_marketing(self):
        # A message with a strategic-decision keyword but no domain signal
        # at all must not force irrelevant specialists into the committee.
        agents = orchestrator.select_committee_agents("هذا قرار مصيري للشركة")
        self.assertEqual(agents[:2], ["general", "marketing"])

    def test_financial_question_still_gets_financial_plus_padding(self):
        # Existing behavior for a genuinely financial/operational question
        # must be unaffected by the zero-match fallback change.
        agents = orchestrator.select_committee_agents("ما وضعنا المالي وهامش الربح؟")
        self.assertIn("financial", agents)

    def test_exact_reported_message_routes_end_to_end_to_multi_agent_with_marketing(self):
        result = orchestrator.route_message(
            user_id=None, message="اذا عطيتني خطة تسويقية للمشروع", lang="ar",
        )
        self.assertEqual(result["route"], orchestrator.ROUTE_MULTI_AGENT)
        self.assertIn("marketing", result["agent_ids"])

    def test_marketing_agent_persona_is_registered(self):
        meta = GeminiAIService().get_agent_meta("marketing", lang="ar")
        self.assertEqual(meta["id"], "marketing")
        self.assertIn("تسويق", meta["system_prompt_ar"])
