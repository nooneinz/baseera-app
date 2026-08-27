"""
Tests for the Orchestrator's active-file fallback (dashboard/services/
orchestrator.py).

Concrete bug this fixes: asking an analytical question about an INFERRED
metric ("ما سبب ارتفاع الهدر؟" -- waste is never a literal column, it's
computed by waste_analyzer.py from price/cost/qty patterns) got the canned
"لم أتمكن من العثور على المستند المطلوب للإجابة" (file-not-found) reply
even though the user had a real, already-analyzed file in their account --
because the Retrieval Layer's search is a literal keyword/column matcher
and "هدر" was never going to be a term any real sheet was indexed under.

Fixed with a fallback: when the Retrieval Layer's search finds ZERO
candidates (not "found several, ambiguous" -- that's a separate, untouched
code path), and the account has at least one indexed sheet, route_message
now falls back to that user's most recently uploaded sheet as the active
context instead of asking "did you mean a different file?" -- there is
nothing ambiguous about which file a user with one dataset is asking
about. The "file not found" prompt is now reserved for accounts with
genuinely zero indexed sheets.
"""
from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.services import orchestrator
from dashboard.models import ProjectFile, FileSheetMetadata


class ActiveFileFallbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="active_file_user", password="pw123456")

    def test_analytical_query_with_no_keyword_match_falls_back_to_the_active_file(self):
        pf = ProjectFile.objects.create(user=self.user, excel_file="excel_files/sales.xlsx")
        FileSheetMetadata.objects.create(
            project_file=pf, sheet_name="مبيعات يناير", status="accept",
            columns=["الصنف", "سعر الوحدة", "تكلفة الوحدة", "الكمية"], row_count=50,
            category="sales", keywords=["مبيعات", "الصنف", "سعر", "تكلفة", "الكمية"],
        )

        result = orchestrator.route_message(
            self.user.id, "ما سبب وجود ارتفاع هدر؟", lang="ar",
        )

        # The literal bug: this must NOT be the file-not-found refusal.
        self.assertIsNone(result["direct_reply"])
        self.assertFalse(result["needs_confirmation"])
        self.assertIn("مبيعات يناير", result["matched_sheet_note"])
        self.assertIn("active file fallback", result["matched_sheet_note"])

    def test_falls_back_to_the_most_recently_uploaded_file_when_several_exist(self):
        older = ProjectFile.objects.create(user=self.user, excel_file="excel_files/old.xlsx")
        FileSheetMetadata.objects.create(
            project_file=older, sheet_name="بيانات قديمة", status="accept",
            columns=["الصنف", "سعر"], row_count=10, category="sales", keywords=["الصنف", "سعر"],
        )
        newer = ProjectFile.objects.create(user=self.user, excel_file="excel_files/new.xlsx")
        FileSheetMetadata.objects.create(
            project_file=newer, sheet_name="بيانات حديثة", status="accept",
            columns=["الصنف", "سعر"], row_count=10, category="sales", keywords=["الصنف", "سعر"],
        )

        result = orchestrator.route_message(
            self.user.id, "ما سبب وجود ارتفاع هدر؟", lang="ar",
        )
        self.assertIn("بيانات حديثة", result["matched_sheet_note"])

    def test_zero_indexed_sheets_still_gets_the_honest_missing_file_reply(self):
        # Has a ProjectFile but it was never indexed (e.g. indexing failed) --
        # nothing to honestly fall back to.
        ProjectFile.objects.create(user=self.user, excel_file="excel_files/unindexed.xlsx")

        result = orchestrator.route_message(
            self.user.id, "ما سبب وجود ارتفاع هدر؟", lang="ar",
        )
        self.assertTrue(result["needs_confirmation"])
        self.assertIsNotNone(result["direct_reply"])

    def test_account_with_no_files_at_all_is_unaffected(self):
        result = orchestrator.route_message(
            self.user.id, "ما سبب وجود ارتفاع هدر؟", lang="ar",
        )
        self.assertTrue(result["needs_confirmation"])
        action_ids = [a["action_id"] for a in result["suggested_actions"]]
        self.assertNotIn("switch_file", action_ids)
        self.assertIn("upload_new_file", action_ids)

    def test_product_specific_query_with_no_keyword_match_is_unaffected(self):
        """
        Regression guard: the "chicken vs meat file" spec scenario also
        finds zero Retrieval Layer candidates (no lexical overlap between
        "دجاج" and the meat file's keywords) -- but it must NOT trigger the
        active-file fallback, because "دجاج" is a specific product/subject,
        not an inferred-metric term like "هدر". Silently defaulting to the
        meat file here would answer about the wrong product; the fallback
        is deliberately gated to a narrow term list that excludes this case.
        """
        pf = ProjectFile.objects.create(user=self.user, excel_file="excel_files/meat_sales.xlsx")
        FileSheetMetadata.objects.create(
            project_file=pf, sheet_name="مبيعات اللحوم", status="accept",
            columns=["التاريخ", "السعر", "الكمية"], row_count=10, category="sales",
            keywords=["مبيعات", "لحوم", "لحم", "التاريخ", "السعر", "الكمية"],
        )

        result = orchestrator.route_message(
            self.user.id, "ما هي توقعات شراء الدجاج للشهر القادم؟", lang="ar",
        )
        self.assertTrue(result["needs_confirmation"])
        self.assertEqual(result["matched_sheet_note"], "")
