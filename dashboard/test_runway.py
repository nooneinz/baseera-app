"""
Tests for the deterministic cash-flow runway engine. Every number must come
straight from the rows; the engine must refuse (status 'insufficient') rather
than guess when the file lacks amount/type/date.
"""
import datetime
from django.test import TestCase, override_settings
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord
from dashboard.services.runway import compute_runway


def _txn(desc, amount, kind, date):
    return {"البيان": desc, "المبلغ": amount, "النوع": kind, "التاريخ": date}


class RunwayEngineTests(TestCase):
    def test_insufficient_without_date(self):
        rows = [{"المبلغ": 100, "النوع": "دخل"}]
        self.assertEqual(compute_runway(rows)["status"], "insufficient")

    def test_insufficient_without_type(self):
        rows = [{"المبلغ": 100, "التاريخ": "2026-01-01"}]
        self.assertEqual(compute_runway(rows)["status"], "insufficient")

    def test_burning_projects_a_runway_date(self):
        # Two months, losing 500/month, estimated cash = net = -1000... make it
        # positive by adding an opening income so cash is positive but burning.
        rows = [
            _txn("مبيعات", 3000, "دخل", "2026-01-05"),
            _txn("إيجار", 3500, "مصروف", "2026-01-20"),
            _txn("مبيعات", 3000, "دخل", "2026-02-05"),
            _txn("إيجار", 3500, "مصروف", "2026-02-20"),
        ]
        # net per month = -500; cumulative net = -1000 -> critical (cash<=0)
        r = compute_runway(rows, today=datetime.date(2026, 3, 1))
        self.assertEqual(r["status"], "critical")
        self.assertEqual(r["avg_monthly_net"], -500.0)

    def test_burning_with_balance_column_uses_real_cash(self):
        rows = [
            {"البيان": "مبيعات", "المبلغ": 2000, "النوع": "دخل",
             "التاريخ": "2026-01-05", "الرصيد": 12000},
            {"البيان": "إيجار", "المبلغ": 3000, "النوع": "مصروف",
             "التاريخ": "2026-01-20", "الرصيد": 9000},
            {"البيان": "مبيعات", "المبلغ": 2000, "النوع": "دخل",
             "التاريخ": "2026-02-05", "الرصيد": 11000},
            {"البيان": "إيجار", "المبلغ": 3000, "النوع": "مصروف",
             "التاريخ": "2026-02-20", "الرصيد": 8000},
        ]
        r = compute_runway(rows, today=datetime.date(2026, 3, 1))
        self.assertEqual(r["status"], "burning")
        self.assertFalse(r["cash_estimated"])
        self.assertEqual(r["current_cash"], 8000)      # latest balance
        self.assertEqual(r["monthly_burn"], 1000.0)    # net -1000/month
        self.assertEqual(r["runway_months"], 8.0)      # 8000 / 1000
        self.assertIsNotNone(r["runway_date"])

    def test_surplus_when_income_exceeds_expense(self):
        rows = [
            _txn("مبيعات", 5000, "دخل", "2026-01-05"),
            _txn("إيجار", 1000, "مصروف", "2026-01-20"),
        ]
        r = compute_runway(rows)
        self.assertEqual(r["status"], "surplus")
        self.assertGreater(r["avg_monthly_net"], 0)


@override_settings(SECURE_SSL_REDIRECT=False)
class RunwayEndpointTests(TestCase):
    def test_endpoint_requires_login_and_returns_json(self):
        u = User.objects.create_user(username="rwuser", password="pw123456")
        Profile.objects.create(user=u, phone_number="96890000000", project_type="retail")
        pf = ProjectFile.objects.create(user=u, excel_file="excel_files/x.csv")
        for d, a, k in [("2026-01-05", 5000, "دخل"), ("2026-01-20", 1000, "مصروف")]:
            DynamicRecord.objects.create(user=u, project_file=pf, schema_hash="h",
                                         row_data=_txn("x", a, k, d))
        self.client.force_login(u)
        res = self.client.get("/api/runway/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(res.json()["status"], ("surplus", "burning", "critical", "insufficient"))
