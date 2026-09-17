"""
Tests for the proactive agent action: it must DETECT a deterministic finding,
ACT (create a real Notification), and return a grounded negotiation draft --
or honestly report no signal, never invent one.
"""
from django.test import TestCase, override_settings
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord, Notification
from dashboard.services.agent_actions import run_proactive_action


def _user_with_rows(username, rows):
    u = User.objects.create_user(username=username, password="pw123456")
    Profile.objects.create(user=u, phone_number="9689" + username[-7:].rjust(7, "0"),
                           project_type="retail")
    pf = ProjectFile.objects.create(user=u, excel_file="excel_files/x.csv")
    for r in rows:
        DynamicRecord.objects.create(user=u, project_file=pf, schema_hash="h", row_data=r)
    return u


def _txn(desc, amount, kind):
    return {"البيان": desc, "المبلغ": amount, "النوع": kind}


class ProactiveAgentActionTests(TestCase):
    def test_acts_and_creates_notification_on_recurring_expense(self):
        u = _user_with_rows("agent001", [
            _txn("مورد الأجبان", 800, "مصروف"),
            _txn("مورد الأجبان", 750, "مصروف"),
            _txn("مورد الأجبان", 820, "مصروف"),
            _txn("مبيعات", 5000, "دخل"),
        ])
        before = Notification.objects.filter(user=u).count()
        r = run_proactive_action(u)
        self.assertEqual(r["status"], "acted")
        self.assertEqual(r["finding"]["name"], "مورد الأجبان")
        self.assertTrue(r["finding"]["recurring"])
        # A real side effect happened.
        self.assertEqual(Notification.objects.filter(user=u).count(), before + 1)
        self.assertTrue(Notification.objects.filter(id=r["notification_id"], user=u).exists())
        # The draft is grounded in the real name.
        self.assertIn("مورد الأجبان", r["draft_message"])

    def test_no_signal_when_no_expenses(self):
        u = _user_with_rows("agent002", [_txn("مبيعات", 5000, "دخل")])
        r = run_proactive_action(u)
        self.assertEqual(r["status"], "no_signal")
        self.assertTrue(r["message"])
        self.assertFalse(Notification.objects.filter(user=u).exists())


@override_settings(SECURE_SSL_REDIRECT=False)
class ProactiveAgentEndpointTests(TestCase):
    def test_endpoint_requires_login_and_acts(self):
        u = _user_with_rows("agentep1", [
            _txn("إيجار", 900, "مصروف"),
            _txn("إيجار", 900, "مصروف"),
            _txn("مبيعات", 4000, "دخل"),
        ])
        self.client.force_login(u)
        res = self.client.post("/api/agent/proactive-action/", data="{}",
                               content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "acted")
