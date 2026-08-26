"""
Tests for the AI-driven waste detection feature (dashboard/services/
waste_analyzer.py + the /api/analyze-waste/ endpoint).

The core guarantee under test: waste is inferred from real arithmetic
evidence in the uploaded rows (below-cost sales, explicit waste quantities,
thin margins, inconsistent pricing, dead stock) — never guessed as a flat
percentage of total sales, and the LLM (when available) is only allowed to
interpret numbers that were already computed deterministically here.
"""
import json
from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.services.waste_analyzer import compute_waste_signals, analyze_waste
from dashboard.models import ProjectFile, DynamicRecord


class WasteSignalComputationTests(TestCase):
    """Deterministic pre-pass: every number must trace back to real rows."""

    def test_below_cost_sales_detected_with_exact_loss_total(self):
        rows = [
            {"الصنف": "قلم", "سعر الوحدة": 5, "تكلفة الوحدة": 6, "الكمية": 10},
            {"الصنف": "دفتر", "سعر الوحدة": 20, "تكلفة الوحدة": 8, "الكمية": 5},
            {"الصنف": "قلم", "سعر الوحدة": 4.5, "تكلفة الوحدة": 6, "الكمية": 3},
        ]
        result = compute_waste_signals(rows)

        self.assertTrue(result["analyzable"])
        below_cost = next(s for s in result["signals"] if s["type"] == "below_cost_sales")
        # (6-5)*10 + (6-4.5)*3 = 10 + 4.5 = 14.5 -- exact, not a guessed percentage.
        self.assertEqual(below_cost["currency_amount"], 14.5)
        self.assertEqual(result["total_waste"], 14.5)

    def test_explicit_waste_quantity_column_is_valued_at_unit_cost(self):
        rows = [
            {"الصنف": "لبن", "تكلفة الوحدة": 2.0, "كمية مهدرة": 5, "الكمية": 20},
            {"الصنف": "لبن", "تكلفة الوحدة": 2.0, "كمية مهدرة": 0, "الكمية": 15},
            {"الصنف": "خبز", "تكلفة الوحدة": 1.0, "كمية مهدرة": 10, "الكمية": 30},
        ]
        result = compute_waste_signals(rows)
        explicit = next(s for s in result["signals"] if s["type"] == "explicit_waste")
        # 5*2.0 + 10*1.0 = 20.0
        self.assertEqual(explicit["currency_amount"], 20.0)

    def test_no_relevant_columns_is_honestly_not_analyzable(self):
        rows = [
            {"ملاحظة": "شيء ما", "التاريخ": "2024-01-01"},
            {"ملاحظة": "شيء آخر", "التاريخ": "2024-01-02"},
        ]
        result = compute_waste_signals(rows)
        self.assertFalse(result["analyzable"])
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["total_waste"], 0.0)

    def test_healthy_data_with_no_waste_signals_stays_honest(self):
        # Consistent, healthy margins -> no signals should fire.
        rows = [
            {"الصنف": "قلم", "سعر الوحدة": 10, "تكلفة الوحدة": 5, "الكمية": 10},
            {"الصنف": "قلم", "سعر الوحدة": 10, "تكلفة الوحدة": 5, "الكمية": 8},
            {"الصنف": "دفتر", "سعر الوحدة": 20, "تكلفة الوحدة": 8, "الكمية": 5},
        ]
        result = compute_waste_signals(rows)
        self.assertTrue(result["analyzable"])
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["total_waste"], 0.0)

    def test_price_inconsistency_signal_flags_same_item_wide_spread(self):
        rows = [
            {"الصنف": "شاي", "سعر الوحدة": 10},
            {"الصنف": "شاي", "سعر الوحدة": 10},
            {"الصنف": "شاي", "سعر الوحدة": 6},  # 40% below the high price
        ]
        result = compute_waste_signals(rows)
        inconsistency = next((s for s in result["signals"] if s["type"] == "price_inconsistency"), None)
        self.assertIsNotNone(inconsistency)


class AnalyzeWasteEndToEndTests(TestCase):
    """analyze_waste(): never invents a number, and degrades gracefully with no AI client."""

    def test_no_ai_client_still_returns_real_computed_total_and_fallback_text(self):
        rows = [
            {"الصنف": "قلم", "سعر الوحدة": 5, "تكلفة الوحدة": 6, "الكمية": 10},
        ]
        output = analyze_waste(rows, ai_service=None, lang="ar")
        self.assertTrue(output["analyzable"])
        self.assertEqual(output["total_waste"], 10.0)
        self.assertFalse(output["ai_used"])
        self.assertIn("بيع بأقل من التكلفة", output["diagnosis"])

    def test_not_analyzable_gives_honest_message_not_a_zero_pretending_to_be_real(self):
        rows = [{"note": "x"}, {"note": "y"}]
        output = analyze_waste(rows, ai_service=None, lang="ar")
        self.assertFalse(output["analyzable"])
        self.assertEqual(output["total_waste"], 0.0)
        self.assertIn("لا يحتوي الملف", output["diagnosis"])

    def test_empty_rows_do_not_crash(self):
        output = analyze_waste([], ai_service=None, lang="ar")
        self.assertFalse(output["analyzable"])


class WasteApiEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="waste_user", password="pw123456")
        self.client.force_login(self.user)

    def test_endpoint_returns_real_signal_from_uploaded_records(self):
        pf = ProjectFile.objects.create(user=self.user, excel_file="excel_files/waste_test.xlsx")
        DynamicRecord.objects.create(
            user=self.user, project_file=pf, schema_hash="x",
            row_data={"الصنف": "قلم", "سعر الوحدة": 5, "تكلفة الوحدة": 6, "الكمية": 10},
        )
        DynamicRecord.objects.create(
            user=self.user, project_file=pf, schema_hash="x",
            row_data={"الصنف": "دفتر", "سعر الوحدة": 20, "تكلفة الوحدة": 8, "الكمية": 5},
        )

        response = self.client.post(
            "/api/analyze-waste/", data=json.dumps({}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["analyzable"])
        self.assertEqual(data["total_waste"], 10.0)

    def test_endpoint_requires_login(self):
        self.client.logout()
        response = self.client.post(
            "/api/analyze-waste/", data=json.dumps({}), content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 200)

    def test_endpoint_scopes_to_requested_file_id(self):
        pf1 = ProjectFile.objects.create(user=self.user, excel_file="excel_files/f1.xlsx")
        pf2 = ProjectFile.objects.create(user=self.user, excel_file="excel_files/f2.xlsx")
        # pf1 has a real below-cost signal; pf2 is healthy.
        DynamicRecord.objects.create(
            user=self.user, project_file=pf1, schema_hash="x",
            row_data={"الصنف": "قلم", "سعر الوحدة": 5, "تكلفة الوحدة": 6, "الكمية": 10},
        )
        DynamicRecord.objects.create(
            user=self.user, project_file=pf2, schema_hash="x",
            row_data={"الصنف": "دفتر", "سعر الوحدة": 20, "تكلفة الوحدة": 8, "الكمية": 5},
        )

        response = self.client.post(
            "/api/analyze-waste/", data=json.dumps({"file_id": pf2.id}), content_type="application/json",
        )
        data = response.json()
        self.assertEqual(data["total_waste"], 0.0)
