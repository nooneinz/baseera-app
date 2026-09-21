"""
Tests for the live analysis theater: the step builder must be grounded in the
user's real rows (never invent figures), and the endpoint must be scoped to the
requesting user and never 500.
"""
import json

from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord
from dashboard.services.analysis_theater import build_analysis_steps


def _txn(desc, amount, kind):
    return {"البيان": desc, "المبلغ": amount, "النوع": kind}


class BuildStepsTests(TestCase):
    def test_empty_rows_returns_waiting_verdict(self):
        steps = build_analysis_steps([])
        self.assertTrue(steps)
        self.assertEqual(steps[0]["phase"], "read")
        self.assertEqual(steps[-1]["phase"], "verdict")

    def test_steps_are_grounded_in_real_numbers(self):
        rows = [_txn("مورد الأجبان", 800, "مصروف"),
                _txn("مورد الأجبان", 760, "مصروف"),
                _txn("مبيعات", 5000, "دخل")]
        steps = build_analysis_steps(rows)
        phases = [s["phase"] for s in steps]
        self.assertIn("split", phases)       # income/expense split computed
        self.assertIn("recurring", phases)   # recurring supplier detected
        # The split result carries the real net (5000 - 1560 = 3,440).
        split = next(s for s in steps if s["phase"] == "split")
        self.assertIn("3,440", split["result"])
        # The recurring step names the real supplier, never an invented one.
        rec = next(s for s in steps if s["phase"] == "recurring")
        self.assertIn("مورد الأجبان", rec["lines"][0])

    def test_never_raises_on_garbage(self):
        # Non-dict / malformed rows must not blow up the builder.
        steps = build_analysis_steps([None, 5, {"x": 1}])  # type: ignore
        self.assertTrue(steps)


class LiveAnalysisEndpointTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user(username="theateruser", password="pw123456")
        Profile.objects.create(user=self.u, phone_number="96890001111")
        pf = ProjectFile.objects.create(user=self.u, excel_file="excel_files/x.csv")
        for r in [_txn("إيجار", 300, "مصروف"), _txn("مبيعات", 900, "دخل")]:
            DynamicRecord.objects.create(user=self.u, project_file=pf, schema_hash="h", row_data=r)

    def test_requires_login(self):
        res = self.client.get("/api/agent/live-analysis/")
        self.assertIn(res.status_code, (301, 302))  # redirected to login

    def test_returns_grounded_steps_for_owner(self):
        self.client.force_login(self.u)
        res = self.client.get("/api/agent/live-analysis/")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.content)
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["count"], 2)
        self.assertEqual(data["steps"][0]["phase"], "read")
