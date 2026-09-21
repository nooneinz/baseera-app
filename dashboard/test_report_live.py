"""
Tests for the live report builder and its endpoint: the report must be grounded
in the user's real data, persisted (hashed + traceable), and owner-scoped.
"""
import json

from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord, FinancialReport
from dashboard.services.report_live import build_live_report


def _txn(desc, amount, kind):
    return {"البيان": desc, "المبلغ": amount, "النوع": kind}


def _user_with_data(username):
    u = User.objects.create_user(username=username, password="pw123456")
    Profile.objects.create(user=u, phone_number="96890000000", company_name="مطعم بصيرة")
    pf = ProjectFile.objects.create(user=u, excel_file="excel_files/x.csv")
    for r in [_txn("مورد الأجبان", 800, "مصروف"),
              _txn("مورد الأجبان", 760, "مصروف"),
              _txn("مبيعات", 5000, "دخل")]:
        DynamicRecord.objects.create(user=u, project_file=pf, schema_hash="h", row_data=r)
    return u


class BuildReportTests(TestCase):
    def test_report_is_grounded_and_structured(self):
        u = _user_with_data("rep1")
        rows = list(DynamicRecord.objects.filter(user=u).values_list("row_data", flat=True))
        rep = build_live_report(u, rows, company_name="مطعم بصيرة")
        self.assertIn("sections", rep)
        headings = [s["heading"] for s in rep["sections"]]
        self.assertIn("الملخّص التنفيذي", headings)
        self.assertIn("أكبر بند متكرّر", headings)
        # Real net (5000 - 1560 = 3,440) and the real supplier appear, not invented.
        self.assertIn("3,440", rep["plain"])
        self.assertIn("مورد الأجبان", rep["plain"])

    def test_never_raises_on_empty(self):
        u = User.objects.create_user(username="rep2", password="pw123456")
        rep = build_live_report(u, [])
        self.assertTrue(rep["sections"])          # still returns a summary + recommendations
        self.assertIn("plain", rep)


class LiveReportEndpointTests(TestCase):
    def setUp(self):
        self.u = _user_with_data("repowner")

    def test_requires_login(self):
        res = self.client.get("/api/report/live/")
        self.assertIn(res.status_code, (301, 302))

    def test_generates_and_persists_report_for_owner(self):
        self.client.force_login(self.u)
        res = self.client.get("/api/report/live/")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.content)
        self.assertEqual(data["status"], "success")
        self.assertIn("sections", data["report"])
        # A FinancialReport was persisted (hashed) for this user.
        self.assertTrue(FinancialReport.objects.filter(user=self.u).exists())
        fr = FinancialReport.objects.filter(user=self.u).first()
        self.assertTrue(fr.content_hash)          # integrity stamp present
