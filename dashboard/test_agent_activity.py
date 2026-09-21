"""
Tests for the Agent Activity feature: the tracked-run service (grounded,
cancellable) and the owner-scoped live endpoints.
"""
import json
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord, AgentRun, AgentRunStep
from dashboard.services import agent_activity


def _txn(desc, amount, kind):
    return {"البيان": desc, "المبلغ": amount, "النوع": kind}


def _user_with_data(username):
    u = User.objects.create_user(username=username, password="pw123456")
    Profile.objects.create(user=u, phone_number="96890000000")
    pf = ProjectFile.objects.create(user=u, excel_file="excel_files/x.csv")
    for r in [_txn("مورد الأجبان", 800, "مصروف"),
              _txn("مورد الأجبان", 760, "مصروف"),
              _txn("مبيعات", 5000, "دخل")]:
        DynamicRecord.objects.create(user=u, project_file=pf, schema_hash="h", row_data=r)
    return u


class ServiceTests(TestCase):
    def test_start_push_complete_flow(self):
        u = User.objects.create_user(username="svc1", password="pw123456")
        run = agent_activity.start_run(u, "المحلل المالي", "مهمة")
        self.assertIsNotNone(run)
        self.assertEqual(run.status, "queued")

        agent_activity.push_step(run, "جاري تحليل البيانات")
        run.refresh_from_db()
        self.assertEqual(run.status, "running")
        self.assertEqual(run.progress_state, "جاري تحليل البيانات")
        self.assertEqual(run.steps.count(), 1)

        agent_activity.complete_run(run, "اكتمل التحليل.", status="done")
        run.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertEqual(run.result_summary, "اكتمل التحليل.")

    def test_is_cancelled_reads_db(self):
        u = User.objects.create_user(username="svc2", password="pw123456")
        run = agent_activity.start_run(u, "x", "y")
        self.assertFalse(agent_activity.is_cancelled(run.id))
        AgentRun.objects.filter(pk=run.id).update(cancel_requested=True)
        self.assertTrue(agent_activity.is_cancelled(run.id))

    def test_run_analysis_agent_produces_grounded_steps(self):
        u = _user_with_data("svc3")
        run = agent_activity.start_run(u, "المحلل المالي", "تحليل")
        with patch("dashboard.services.agent_activity.time.sleep", return_value=None):
            agent_activity.run_analysis_agent(u.id, run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertGreaterEqual(run.steps.count(), 3)
        # A real recurring supplier from the data appears in the log, never invented.
        joined = " ".join(AgentRunStep.objects.filter(run=run).values_list("message", flat=True))
        self.assertIn("مورد الأجبان", joined)

    def test_run_analysis_agent_honors_cancellation(self):
        u = _user_with_data("svc4")
        run = agent_activity.start_run(u, "المحلل المالي", "تحليل")
        AgentRun.objects.filter(pk=run.id).update(cancel_requested=True)
        with patch("dashboard.services.agent_activity.time.sleep", return_value=None):
            agent_activity.run_analysis_agent(u.id, run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, "cancelled")


class EndpointTests(TestCase):
    def setUp(self):
        self.u = _user_with_data("ep_owner")
        self.other = User.objects.create_user(username="ep_other", password="pw123456")

    def test_start_requires_login(self):
        res = self.client.post("/api/agents/activity/start/")
        self.assertIn(res.status_code, (301, 302))

    def test_start_creates_run_and_returns_id(self):
        self.client.force_login(self.u)
        # Don't spawn the real background thread in the test.
        with patch("threading.Thread") as Thread:
            res = self.client.post("/api/agents/activity/start/")
            self.assertTrue(Thread.called)
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.content)
        self.assertEqual(data["status"], "success")
        self.assertTrue(AgentRun.objects.filter(id=data["run_id"], user=self.u).exists())

    def test_activity_is_owner_scoped(self):
        run = agent_activity.start_run(self.u, "x", "y")
        self.client.force_login(self.other)
        res = self.client.get(f"/api/agents/activity/{run.id}/")
        self.assertEqual(res.status_code, 404)   # not the owner

    def test_activity_returns_serialized_run_for_owner(self):
        run = agent_activity.start_run(self.u, "المحلل", "مهمة")
        agent_activity.push_step(run, "جاري البحث في المصادر")
        self.client.force_login(self.u)
        res = self.client.get(f"/api/agents/activity/{run.id}/")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.content)
        self.assertEqual(data["run"]["status"], "running")
        self.assertEqual(len(data["run"]["steps"]), 1)

    def test_cancel_sets_flag_for_owner_only(self):
        run = agent_activity.start_run(self.u, "x", "y")
        # Other user cannot cancel.
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f"/api/agents/activity/{run.id}/cancel/").status_code, 404)
        # Owner can.
        self.client.force_login(self.u)
        res = self.client.post(f"/api/agents/activity/{run.id}/cancel/")
        self.assertEqual(res.status_code, 200)
        run.refresh_from_db()
        self.assertTrue(run.cancel_requested)
