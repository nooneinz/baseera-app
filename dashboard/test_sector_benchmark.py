"""
Tests for sector benchmarking: honest "building" state until enough peers,
real comparisons once peers exist, and the per-user metric computation.
"""
from django.test import TestCase, override_settings
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord
from dashboard.services import sector_benchmark


def _make_user(username, project_type, expense, income):
    u = User.objects.create_user(username=username, password="pw123456")
    Profile.objects.create(user=u, phone_number="9689" + username[-7:].rjust(7, "0"),
                           project_type=project_type)
    pf = ProjectFile.objects.create(user=u, excel_file="excel_files/x.csv")
    DynamicRecord.objects.create(user=u, project_file=pf, schema_hash="h",
                                 row_data={"البيان": "مبيعات", "المبلغ": income, "النوع": "دخل"})
    DynamicRecord.objects.create(user=u, project_file=pf, schema_hash="h",
                                 row_data={"البيان": "إيجار", "المبلغ": expense, "النوع": "مصروف"})
    return u


class SectorBenchmarkTests(TestCase):
    def test_compute_user_metrics_expense_ratio(self):
        rows = [
            {"البيان": "مبيعات", "المبلغ": 1000, "النوع": "دخل"},
            {"البيان": "إيجار", "المبلغ": 400, "النوع": "مصروف"},
        ]
        m = sector_benchmark.compute_user_metrics(rows)
        self.assertIn("expense_ratio", m)
        self.assertEqual(m["expense_ratio"], 40.0)

    def test_building_state_when_not_enough_peers(self):
        me = _make_user("solo1234", "fnb", expense=500, income=1000)
        result = sector_benchmark.sector_benchmark_for(me)
        self.assertEqual(result["status"], "building")
        self.assertEqual(result["comparisons"], [])
        self.assertTrue(result["message"])

    def test_ready_with_real_comparison_once_enough_peers(self):
        me = _make_user("meuser01", "retail", expense=800, income=1000)  # 80% expense ratio
        # 3 peers in same sector with lower expense ratios (median ~40%).
        _make_user("peer0001", "retail", expense=400, income=1000)  # 40%
        _make_user("peer0002", "retail", expense=350, income=1000)  # 35%
        _make_user("peer0003", "retail", expense=450, income=1000)  # 45%
        result = sector_benchmark.sector_benchmark_for(me)
        self.assertEqual(result["status"], "ready")
        exp = [c for c in result["comparisons"] if c["metric"] == "expense_ratio"]
        self.assertTrue(exp)
        c = exp[0]
        self.assertEqual(c["direction"], "above")     # my expenses higher than peers
        self.assertFalse(c["is_good"])                # higher expenses = not good
        self.assertGreater(c["delta_pct"], 0)


@override_settings(SECURE_SSL_REDIRECT=False)
class SectorBenchmarkEndpointTests(TestCase):
    def test_endpoint_requires_login_and_returns_json(self):
        u = _make_user("epuser01", "fnb", expense=500, income=1000)
        self.client.force_login(u)
        res = self.client.get("/api/benchmark/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(res.json()["status"], ("ready", "building"))
