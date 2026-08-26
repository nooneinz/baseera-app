"""
Tests for the proactive, conditional multi-agent escalation chain
(dashboard/services/agent_escalation_chain.py + /api/escalation-chain/).

Core guarantees under test:
  - The chain never fires on data with no genuine anomaly (no manufactured
    narrative just to fill four stages).
  - Financial always runs once Audit finds something real, and its cash
    impact ratio is exact arithmetic against total recorded revenue.
  - Supply Chain only runs when the audit finding is actually
    procurement/inventory-flavored -- not for every finding.
  - Pricing only runs when the financial impact crosses the materiality
    threshold -- not for every finding, and its offer is grounded in real
    healthy-margin products from the data, never invented.
"""
import json
from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.services.agent_escalation_chain import (
    run_escalation_chain,
    detect_recurring_outflows,
    MATERIALITY_REVENUE_SHARE,
)
from dashboard.models import ProjectFile, DynamicRecord


class RecurringOutflowDetectionTests(TestCase):
    def test_repeated_debit_description_is_flagged_with_real_totals(self):
        rows = [
            {"التاريخ": "2024-01-01", "الوصف": "سحب نقدي", "المبلغ": 500, "النوع": "debit"},
            {"التاريخ": "2024-01-05", "الوصف": "سحب نقدي", "المبلغ": 500, "النوع": "debit"},
            {"التاريخ": "2024-01-10", "الوصف": "سحب نقدي", "المبلغ": 500, "النوع": "debit"},
            {"التاريخ": "2024-01-12", "الوصف": "إيداع راتب", "المبلغ": 5000, "النوع": "credit"},
        ]
        findings = detect_recurring_outflows(rows, min_occurrences=3)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["description"], "سحب نقدي")
        self.assertEqual(findings[0]["count"], 3)
        self.assertEqual(findings[0]["total_amount"], 1500.0)

    def test_without_a_direction_column_it_refuses_to_guess(self):
        # Same description repeated, but no type/direction column at all --
        # this could just as easily be a recurring sale, so it must NOT be
        # labeled an outflow.
        rows = [
            {"التاريخ": "2024-01-01", "الوصف": "قهوة", "المبلغ": 15},
            {"التاريخ": "2024-01-02", "الوصف": "قهوة", "المبلغ": 15},
            {"التاريخ": "2024-01-03", "الوصف": "قهوة", "المبلغ": 15},
        ]
        self.assertEqual(detect_recurring_outflows(rows), [])

    def test_below_min_occurrences_is_not_flagged(self):
        rows = [
            {"الوصف": "سحب نقدي", "المبلغ": 500, "النوع": "debit"},
            {"الوصف": "سحب نقدي", "المبلغ": 500, "النوع": "debit"},
        ]
        self.assertEqual(detect_recurring_outflows(rows, min_occurrences=3), [])


class EscalationChainTests(TestCase):
    """run_escalation_chain(): the core conditional-escalation contract."""

    def test_healthy_data_does_not_trigger_the_chain_at_all(self):
        rows = [
            {"الصنف": "قلم", "سعر الوحدة": 10, "تكلفة الوحدة": 5, "الكمية": 10},
            {"الصنف": "دفتر", "سعر الوحدة": 20, "تكلفة الوحدة": 8, "الكمية": 5},
        ]
        result = run_escalation_chain(rows, ai_service=None, lang="ar")
        self.assertFalse(result["triggered"])
        self.assertEqual(result["stages"], [])
        self.assertIn("لم يرصد", result["reason_not_triggered"])

    def test_empty_rows_do_not_crash_and_do_not_trigger(self):
        result = run_escalation_chain([], ai_service=None, lang="ar")
        self.assertFalse(result["triggered"])

    def test_procurement_signal_escalates_through_audit_financial_supply_chain(self):
        # Dead stock (a procurement-flavored signal) with revenue present, but
        # the flagged amount is a tiny fraction of revenue -> Pricing must NOT
        # be pulled in (below materiality).
        rows = [
            {"المنتج": "صنف راكد", "الكمية": 50, "إجمالي المبيعات": 0},
            {"المنتج": "صنف نشط", "الكمية": 20, "إجمالي المبيعات": 1000000},
        ]
        result = run_escalation_chain(rows, ai_service=None, lang="ar")
        self.assertTrue(result["triggered"])
        by_id = {s["agent_id"]: s for s in result["stages"]}

        self.assertTrue(by_id["audit"]["triggered"])
        self.assertTrue(by_id["financial"]["triggered"])
        self.assertTrue(by_id["supply_chain"]["triggered"])
        # dead_stock carries currency_amount=0.0, so the flagged amount is 0
        # -> no material cash impact -> Pricing must be skipped, not guessed.
        self.assertFalse(by_id["pricing"]["triggered"])
        self.assertIsNotNone(by_id["pricing"]["skip_reason"])

    def test_non_procurement_signal_skips_supply_chain(self):
        # thin_margin is a pure pricing/margin issue, not procurement --
        # Supply Chain must be skipped for it.
        rows = [
            {"الصنف": "قلم", "سعر الوحدة": 10.4, "تكلفة الوحدة": 10, "الكمية": 5},
            {"الصنف": "قلم", "سعر الوحدة": 10.3, "تكلفة الوحدة": 10, "الكمية": 5},
            {"الصنف": "قلم", "سعر الوحدة": 10.2, "تكلفة الوحدة": 10, "الكمية": 5},
        ]
        result = run_escalation_chain(rows, ai_service=None, lang="ar")
        self.assertTrue(result["triggered"])
        by_id = {s["agent_id"]: s for s in result["stages"]}
        self.assertTrue(by_id["audit"]["triggered"])
        self.assertFalse(by_id["supply_chain"]["triggered"])

    def test_material_cash_impact_pulls_in_pricing_with_real_healthy_margin_products(self):
        # below_cost_sales with a large loss relative to total revenue ->
        # crosses MATERIALITY_REVENUE_SHARE -> Pricing must run, and must
        # ground its offer in the real highest-margin product ("دفتر").
        rows = [
            {"الصنف": "قلم", "سعر الوحدة": 1, "تكلفة الوحدة": 100, "الكمية": 10, "إجمالي المبيعات": 10},
            {"الصنف": "دفتر", "سعر الوحدة": 20, "تكلفة الوحدة": 8, "الكمية": 5, "إجمالي المبيعات": 100},
        ]
        result = run_escalation_chain(rows, ai_service=None, lang="ar")
        by_id = {s["agent_id"]: s for s in result["stages"]}

        impact_pct = by_id["financial"]["finding"]["impact_ratio_percent"]
        self.assertIsNotNone(impact_pct)
        self.assertGreaterEqual(impact_pct / 100, MATERIALITY_REVENUE_SHARE)

        self.assertTrue(by_id["pricing"]["triggered"])
        margin_names = [p["name"] for p in by_id["pricing"]["finding"]["healthy_margin_products"]]
        self.assertIn("دفتر", margin_names)
        self.assertIn("دفتر", by_id["pricing"]["narrative"])

    def test_no_ai_service_still_returns_real_fallback_narratives_for_every_triggered_stage(self):
        rows = [
            {"الصنف": "قلم", "سعر الوحدة": 5, "تكلفة الوحدة": 6, "الكمية": 10, "إجمالي المبيعات": 50},
        ]
        result = run_escalation_chain(rows, ai_service=None, lang="ar")
        self.assertTrue(result["triggered"])
        for stage in result["stages"]:
            if stage["triggered"]:
                self.assertFalse(stage["ai_used"])
                self.assertTrue(stage["narrative"])


class EscalationChainApiEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="escalation_user", password="pw123456")
        self.client.force_login(self.user)

    def test_endpoint_returns_triggered_chain_from_real_uploaded_records(self):
        pf = ProjectFile.objects.create(user=self.user, excel_file="excel_files/esc_test.xlsx")
        DynamicRecord.objects.create(
            user=self.user, project_file=pf, schema_hash="x",
            row_data={"الصنف": "قلم", "سعر الوحدة": 5, "تكلفة الوحدة": 6, "الكمية": 10},
        )
        response = self.client.post(
            "/api/escalation-chain/", data=json.dumps({}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["triggered"])
        self.assertTrue(any(s["agent_id"] == "audit" for s in data["stages"]))

    def test_endpoint_requires_login(self):
        self.client.logout()
        response = self.client.post(
            "/api/escalation-chain/", data=json.dumps({}), content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 200)

    def test_endpoint_honestly_reports_not_triggered_for_healthy_data(self):
        pf = ProjectFile.objects.create(user=self.user, excel_file="excel_files/healthy.xlsx")
        DynamicRecord.objects.create(
            user=self.user, project_file=pf, schema_hash="x",
            row_data={"الصنف": "قلم", "سعر الوحدة": 10, "تكلفة الوحدة": 5, "الكمية": 10},
        )
        response = self.client.post(
            "/api/escalation-chain/", data=json.dumps({}), content_type="application/json",
        )
        data = response.json()
        self.assertFalse(data["triggered"])
        self.assertEqual(data["stages"], [])
