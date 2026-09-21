"""
Tests for the engagement engine: the financial radar (grounded proactive
alerts) and the re-engagement nudges for quiet users. Outbound WhatsApp is
stubbed so no real messages are sent.
"""
from unittest.mock import patch

from django.utils import timezone
from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord, SystemLog
from dashboard.services import engagement


def _txn(desc, amount, kind):
    return {"البيان": desc, "المبلغ": amount, "النوع": kind}


def _user_with_expense(username):
    u = User.objects.create_user(username=username, password="pw123456")
    Profile.objects.create(user=u, phone_number="9689" + username[-7:].rjust(7, "0"))
    pf = ProjectFile.objects.create(user=u, excel_file="excel_files/x.csv")
    for r in [_txn("مورد الأجبان", 800, "مصروف"),
              _txn("مورد الأجبان", 760, "مصروف"),
              _txn("مبيعات", 5000, "دخل")]:
        DynamicRecord.objects.create(user=u, project_file=pf, schema_hash="h", row_data=r)
    return u


class DailyRadarTests(TestCase):
    def test_flags_and_pushes_a_grounded_finding(self):
        _user_with_expense("radaruser1")
        with patch("dashboard.services.engagement._outbound", return_value=True) as out:
            res = engagement.run_daily_radar(push=True)
        self.assertEqual(res["flagged"], 1)
        self.assertEqual(res["pushed"], 1)
        # The alert names the real recurring item, never an invented one.
        sent = out.call_args[0][1]
        self.assertIn("مورد الأجبان", sent)

    def test_throttled_so_it_does_not_spam_twice(self):
        _user_with_expense("radaruser2")
        with patch("dashboard.services.engagement._outbound", return_value=True):
            engagement.run_daily_radar(push=True)
            second = engagement.run_daily_radar(push=True)  # same day
        self.assertEqual(second["pushed"], 0)  # throttled

    def test_user_without_data_is_skipped(self):
        u = User.objects.create_user(username="nodata1", password="pw123456")
        Profile.objects.create(user=u, phone_number="96890000000")
        with patch("dashboard.services.engagement._outbound", return_value=True):
            res = engagement.run_daily_radar(push=True)
        self.assertEqual(res["processed"], 0)


class ReengagementTests(TestCase):
    def test_nudges_a_quiet_user(self):
        u = User.objects.create_user(username="quiet1", password="pw123456")
        Profile.objects.create(user=u, phone_number="96891112223")
        with patch("dashboard.services.engagement._outbound", return_value=True) as out:
            res = engagement.run_reengagement(push=True)
        self.assertEqual(res["nudged"], 1)
        self.assertTrue(out.called)

    def test_recently_active_user_is_skipped(self):
        u = User.objects.create_user(username="active1", password="pw123456")
        Profile.objects.create(user=u, phone_number="96893334445")
        SystemLog.objects.create(user=u, action_type="واتساب / WhatsApp Chat", details="recent")
        with patch("dashboard.services.engagement._outbound", return_value=True):
            res = engagement.run_reengagement(push=True)
        self.assertEqual(res["nudged"], 0)  # active -> not nudged

    def test_nudge_is_throttled(self):
        u = User.objects.create_user(username="quiet2", password="pw123456")
        Profile.objects.create(user=u, phone_number="96895556667")
        with patch("dashboard.services.engagement._outbound", return_value=True):
            engagement.run_reengagement(push=True)
            second = engagement.run_reengagement(push=True)
        self.assertEqual(second["nudged"], 0)  # already nudged within window
