"""
Tests for the READ-ONLY financial query tools exposed to the ReAct agent
(get_runway / get_cashflow / get_benchmark). They must compute real numbers
from the user's own rows via the deterministic engines, and never mutate data.
"""
import json
from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord
from dashboard.services import agent_tools


def _make_user(username, rows):
    u = User.objects.create_user(username=username, password="pw123456")
    Profile.objects.create(user=u, phone_number="9689" + username[-7:].rjust(7, "0"),
                           project_type="retail")
    pf = ProjectFile.objects.create(user=u, excel_file="excel_files/x.csv")
    for r in rows:
        DynamicRecord.objects.create(user=u, project_file=pf, schema_hash="h", row_data=r)
    return u


def _txn(desc, amount, kind, date=None):
    row = {"البيان": desc, "المبلغ": amount, "النوع": kind}
    if date:
        row["التاريخ"] = date
    return row


class AgentFinancialToolsTests(TestCase):
    def test_get_cashflow_returns_real_totals(self):
        u = _make_user("cfuser01", [
            _txn("مبيعات", 1000, "دخل"),
            _txn("إيجار", 400, "مصروف"),
        ])
        out = json.loads(agent_tools._get_cashflow_tool(u.id))
        self.assertEqual(out["total_income"], 1000)
        self.assertEqual(out["total_expense"], 400)
        self.assertEqual(out["net"], 600)

    def test_get_runway_returns_a_status(self):
        u = _make_user("rwuser02", [
            _txn("مبيعات", 3000, "دخل", "2026-01-05"),
            _txn("إيجار", 3500, "مصروف", "2026-01-20"),
            _txn("مبيعات", 3000, "دخل", "2026-02-05"),
            _txn("إيجار", 3500, "مصروف", "2026-02-20"),
        ])
        out = json.loads(agent_tools._get_runway_tool(u.id))
        self.assertIn(out["status"], ("burning", "surplus", "critical", "insufficient"))

    def test_get_benchmark_returns_status(self):
        u = _make_user("bmuser03", [_txn("مبيعات", 1000, "دخل"), _txn("إيجار", 400, "مصروف")])
        out = json.loads(agent_tools._get_benchmark_tool(u.id))
        self.assertIn(out["status"], ("ready", "building"))

    def test_tools_never_create_or_delete_rows(self):
        u = _make_user("robot04", [_txn("مبيعات", 500, "دخل")])
        before = DynamicRecord.objects.filter(user=u).count()
        agent_tools._get_cashflow_tool(u.id)
        agent_tools._get_runway_tool(u.id)
        agent_tools._get_benchmark_tool(u.id)
        self.assertEqual(DynamicRecord.objects.filter(user=u).count(), before)

    def test_no_user_is_handled_gracefully(self):
        # Never raises on a missing user session.
        self.assertIsInstance(agent_tools._get_cashflow_tool(None), str)
        self.assertIsInstance(agent_tools._get_runway_tool(None), str)

    def test_recent_files_lists_uploads(self):
        u = _make_user("files05", [_txn("مبيعات", 500, "دخل")])
        out = agent_tools._get_recent_files_tool(u.id)
        parsed = json.loads(out)
        self.assertTrue(parsed and parsed[0]["rows"] >= 1)

    def test_waste_summary_returns_shape(self):
        u = _make_user("waste06", [
            {"الصنف": "وجبة", "سعر البيع": 4.5, "التكلفة": 5.2, "الكمية": 10},
        ])
        out = json.loads(agent_tools._get_waste_summary_tool(u.id))
        self.assertIn("total_waste", out)
        self.assertIn("top_sources", out)

    def test_draft_negotiation_grounds_in_recurring_expense(self):
        u = _make_user("nego07", [
            _txn("مورد الأجبان", 800, "مصروف"),
            _txn("مورد الأجبان", 750, "مصروف"),
            _txn("مبيعات", 5000, "دخل"),
        ])
        msg = agent_tools._draft_negotiation_tool(u.id)
        self.assertIn("مورد الأجبان", msg)

    def test_search_documents_never_raises_without_user(self):
        self.assertIsInstance(agent_tools._search_documents_tool(None, "إيجار"), str)

    def test_describe_dataset_profiles_the_data(self):
        u = _make_user("desc08", [
            _txn("مبيعات", 1000, "دخل"),
            _txn("إيجار", 400, "مصروف"),
            _txn("رواتب", 600, "مصروف"),
        ])
        out = json.loads(agent_tools._describe_dataset_tool(u.id))
        self.assertEqual(out["rows"], 3)
        self.assertIn("المبلغ", out.get("numeric", {}))
        self.assertEqual(out["numeric"]["المبلغ"]["max"], 1000)

    def test_count_where_numeric_and_contains(self):
        u = _make_user("cnt09", [
            _txn("مبيعات", 1500, "دخل"),
            _txn("إيجار", 400, "مصروف"),
            _txn("مورد", 1200, "مصروف"),
        ])
        gt = json.loads(agent_tools._count_where_tool(u.id, "المبلغ", ">", "1000"))
        self.assertEqual(gt["count"], 2)  # 1500 and 1200
        exp = json.loads(agent_tools._count_where_tool(u.id, "النوع", "==", "مصروف"))
        self.assertEqual(exp["count"], 2)

    def test_count_where_rejects_bad_operator(self):
        u = _make_user("cnt10", [_txn("مبيعات", 100, "دخل")])
        self.assertIn("operator", agent_tools._count_where_tool(u.id, "المبلغ", "DROP", "1").lower())
