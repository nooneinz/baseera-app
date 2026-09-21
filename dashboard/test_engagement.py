"""
Tests for the engagement engine: the financial radar (grounded proactive
alerts) and the re-engagement nudges for quiet users. Outbound WhatsApp is
stubbed so no real messages are sent.
"""
from unittest.mock import patch

from django.utils import timezone
from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord, SystemLog, Notification
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


def _user_with_runway_crisis(username):
    """Dated income/expense rows where expense far outweighs income -> the
    runway estimate goes critical, which the radar must prioritise over any
    recurring-expense finding in the same rows."""
    u = User.objects.create_user(username=username, password="pw123456")
    Profile.objects.create(user=u, phone_number="9689" + username[-7:].rjust(7, "0"))
    pf = ProjectFile.objects.create(user=u, excel_file="excel_files/x.csv")
    for r in [{"التاريخ": "2026-01-05", "المبلغ": 200, "النوع": "دخل"},
              {"التاريخ": "2026-02-05", "المبلغ": 3000, "النوع": "مصروف"}]:
        DynamicRecord.objects.create(user=u, project_file=pf, schema_hash="h", row_data=r)
    return u


def _user_with_below_cost(username):
    """Rows carrying product/price/cost/qty where price < cost -> the waste
    analyzer sees below-cost sales. No date/type columns, so runway is
    'insufficient' and waste is the highest-priority grounded signal."""
    u = User.objects.create_user(username=username, password="pw123456")
    Profile.objects.create(user=u, phone_number="9689" + username[-7:].rjust(7, "0"))
    pf = ProjectFile.objects.create(user=u, excel_file="excel_files/x.csv")
    for r in [{"product": "برجر", "price": 3, "cost": 5, "qty": 10},
              {"product": "بيتزا", "price": 8, "cost": 6, "qty": 4}]:
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


class SiteNotificationMirrorTests(TestCase):
    """Every engagement alert is mirrored to the on-site notification bell,
    and that mirror happens even when the WhatsApp push fails (the website is
    always reachable, WhatsApp is subject to the 24h window)."""

    def test_radar_creates_site_notification_even_when_whatsapp_fails(self):
        u = _user_with_expense("sitenotif1")
        # _outbound returns False -> WhatsApp did not go out.
        with patch("dashboard.services.engagement._outbound", return_value=False):
            res = engagement.run_daily_radar(push=True)
        self.assertEqual(res["pushed"], 0)        # WhatsApp failed
        self.assertEqual(res["notified"], 1)      # website still notified
        notif = Notification.objects.filter(user=u).first()
        self.assertIsNotNone(notif)
        self.assertIn("مورد الأجبان", notif.message)  # grounded, real item

    def test_nudge_creates_site_notification(self):
        u = User.objects.create_user(username="sitenudge1", password="pw123456")
        Profile.objects.create(user=u, phone_number="96897778889")
        with patch("dashboard.services.engagement._outbound", return_value=False):
            res = engagement.run_reengagement(push=True)
        self.assertEqual(res["notified"], 1)
        self.assertTrue(Notification.objects.filter(user=u).exists())

    def test_month_end_creates_site_notification(self):
        u = _user_with_expense("sitemonth1")
        with patch("dashboard.services.engagement._outbound", return_value=False):
            res = engagement.run_month_end_report(push=True, force=True)
        self.assertEqual(res["notified"], 1)
        notif = Notification.objects.filter(user=u).first()
        self.assertIsNotNone(notif)
        self.assertIn("تقريرك الشهري", notif.title)


class RadarPriorityTests(TestCase):
    """The radar sends the single highest-priority grounded signal:
    liquidity (runway) > waste > big recurring expense."""

    def test_liquidity_alert_takes_priority(self):
        _user_with_runway_crisis("runwaycrisis1")
        with patch("dashboard.services.engagement._outbound", return_value=True) as out:
            res = engagement.run_daily_radar(push=True)
        self.assertEqual(res["pushed"], 1)
        sent = out.call_args[0][1]
        self.assertIn("سيولة", sent)  # a liquidity alert, not a recurring-expense one

    def test_waste_alert_when_no_liquidity_signal(self):
        _user_with_below_cost("wasteuser1")
        with patch("dashboard.services.engagement._outbound", return_value=True) as out:
            res = engagement.run_daily_radar(push=True)
        self.assertEqual(res["pushed"], 1)
        sent = out.call_args[0][1]
        self.assertIn("هدر", sent)  # a waste alert


class MonthEndReportTests(TestCase):
    def test_force_pushes_grounded_monthly_summary(self):
        _user_with_expense("monthly1")
        with patch("dashboard.services.engagement._outbound", return_value=True) as out:
            res = engagement.run_month_end_report(push=True, force=True)
        self.assertEqual(res["pushed"], 1)
        sent = out.call_args[0][1]
        self.assertIn("تقريرك الشهري", sent)

    def test_skips_when_not_first_of_month(self):
        from datetime import datetime, timezone as _dt_tz
        _user_with_expense("monthly2")
        not_first = datetime(2026, 9, 15, tzinfo=_dt_tz.utc)
        with patch("django.utils.timezone.now", return_value=not_first), \
             patch("dashboard.services.engagement._outbound", return_value=True):
            res = engagement.run_month_end_report(push=True)  # force defaults to False
        self.assertTrue(res.get("skipped"))
        self.assertEqual(res["pushed"], 0)

    def test_throttled_within_the_same_month(self):
        _user_with_expense("monthly3")
        with patch("dashboard.services.engagement._outbound", return_value=True):
            engagement.run_month_end_report(push=True, force=True)
            second = engagement.run_month_end_report(push=True, force=True)
        self.assertEqual(second["pushed"], 0)  # already reported this month


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
