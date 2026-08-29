"""
Tests for the Approved Plans file lifecycle (dashboard/api_views.py).

Covers a concrete production bug: deleting an ApprovedPlan row whose
file_path is empty (the model's own default, or an older row created
before a real file was ever attached to it) crashed with
IsADirectoryError -- os.path.join(BASE_DIR, "") resolves to BASE_DIR
itself, and os.path.exists() is True for a directory too, so the old code
called os.remove() on a directory instead of skipping a plan with nothing
to clean up on disk.
"""
import os
import json
from django.conf import settings
from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.models import ApprovedPlan


class DeletePlanApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="plan_user", password="pw123456")
        self.client.force_login(self.user)

    def test_deleting_a_plan_with_empty_file_path_does_not_crash(self):
        plan = ApprovedPlan.objects.create(
            user=self.user, file_name="خطة قديمة", file_path="", justification="test",
        )
        response = self.client.post(f"/api/approved-plans/{plan.id}/delete/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(ApprovedPlan.objects.filter(id=plan.id).exists())

    def test_deleting_a_plan_with_a_real_file_removes_it_from_disk(self):
        rel_path = "sandbox/approved_plans/test_delete_plan.txt"
        full_path = os.path.join(settings.BASE_DIR, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write("plan body")

        plan = ApprovedPlan.objects.create(
            user=self.user, file_name="خطة حقيقية", file_path=rel_path, justification="test",
        )
        try:
            response = self.client.post(f"/api/approved-plans/{plan.id}/delete/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            self.assertFalse(os.path.isfile(full_path))
        finally:
            if os.path.isfile(full_path):
                os.remove(full_path)

    def test_deleting_a_plan_belonging_to_another_user_is_not_found(self):
        other = User.objects.create_user(username="other_user", password="pw123456")
        plan = ApprovedPlan.objects.create(user=other, file_name="ملف آخر", file_path="")
        response = self.client.post(f"/api/approved-plans/{plan.id}/delete/")
        # Ownership-scoped lookup fails -> handled as an error, never deletes
        # someone else's plan.
        self.assertEqual(response.json()["status"], "error")
        self.assertTrue(ApprovedPlan.objects.filter(id=plan.id).exists())


class UpdatePlanNoteApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="note_user", password="pw123456")
        self.client.force_login(self.user)

    def test_updating_the_note_persists_it(self):
        plan = ApprovedPlan.objects.create(user=self.user, file_name="خطة", file_path="")
        response = self.client.post(
            f"/api/approved-plans/{plan.id}/update-note/",
            data=json.dumps({"note": "ملاحظة تجريبية"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        plan.refresh_from_db()
        self.assertEqual(plan.justification, "ملاحظة تجريبية")


class RecordPlanImpactApiTests(TestCase):
    """
    Closes the "detect -> decide -> act -> measured impact" loop: a plan's
    baseline_metric_value (captured from a real computed number at approval
    time, see api_apply_agent_decision) is compared against a user-reported
    current value, and the resulting status is computed arithmetically --
    never guessed by an AI model.
    """
    def setUp(self):
        self.user = User.objects.create_user(username="impact_user", password="pw123456")
        self.client.force_login(self.user)

    def _post_impact(self, plan_id, current_value):
        return self.client.post(
            f"/api/approved-plans/{plan_id}/record-impact/",
            data=json.dumps({"current_value": current_value}),
            content_type="application/json",
        )

    def test_lower_current_value_is_recorded_as_improved(self):
        plan = ApprovedPlan.objects.create(
            user=self.user, file_name="خطة", file_path="",
            baseline_metric_label="المبلغ المرصود", baseline_metric_value=1000.0,
        )
        response = self._post_impact(plan.id, 400)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["impact_status"], "improved")
        plan.refresh_from_db()
        self.assertEqual(plan.impact_status, "improved")
        self.assertEqual(plan.current_metric_value, 400.0)
        self.assertIsNotNone(plan.impact_measured_at)

    def test_higher_current_value_is_recorded_as_worsened(self):
        plan = ApprovedPlan.objects.create(
            user=self.user, file_name="خطة", file_path="",
            baseline_metric_label="المبلغ المرصود", baseline_metric_value=1000.0,
        )
        response = self._post_impact(plan.id, 1600)
        self.assertEqual(response.json()["impact_status"], "worsened")
        plan.refresh_from_db()
        self.assertEqual(plan.impact_status, "worsened")

    def test_value_within_tolerance_is_recorded_as_unchanged(self):
        plan = ApprovedPlan.objects.create(
            user=self.user, file_name="خطة", file_path="",
            baseline_metric_label="المبلغ المرصود", baseline_metric_value=1000.0,
        )
        # 2% tolerance on a baseline of 1000 -> 1005 is within noise.
        response = self._post_impact(plan.id, 1005)
        self.assertEqual(response.json()["impact_status"], "unchanged")

    def test_plan_without_a_baseline_refuses_to_measure_impact(self):
        plan = ApprovedPlan.objects.create(user=self.user, file_name="خطة قديمة", file_path="")
        response = self._post_impact(plan.id, 400)
        self.assertEqual(response.json()["status"], "error")
        plan.refresh_from_db()
        self.assertEqual(plan.impact_status, "pending")

    def test_recording_impact_on_another_users_plan_is_not_found(self):
        other = User.objects.create_user(username="other_impact_user", password="pw123456")
        plan = ApprovedPlan.objects.create(
            user=other, file_name="ملف آخر", file_path="", baseline_metric_value=1000.0,
        )
        response = self._post_impact(plan.id, 400)
        self.assertEqual(response.json()["status"], "error")
        plan.refresh_from_db()
        self.assertEqual(plan.impact_status, "pending")


class ApplyAgentDecisionBaselineTests(TestCase):
    """The chat/dashboard "Apply" flow now optionally forwards a real
    baseline number (e.g. the escalation chain's flagged_amount) which
    must land on the created ApprovedPlan untouched."""

    def setUp(self):
        self.user = User.objects.create_user(username="apply_user", password="pw123456")
        self.client.force_login(self.user)

    def test_baseline_metric_is_stored_on_the_created_plan(self):
        response = self.client.post(
            "/api/dashboard/apply-agent-decision/",
            data=json.dumps({
                "action_payload": "supply_chain_inventory_action",
                "plan_content": "توصية تجريبية",
                "baseline_metric_value": 1234.5,
                "baseline_metric_label": "المبلغ المرصود عند اعتماد الخطة",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        plan = ApprovedPlan.objects.filter(user=self.user).latest("created_at")
        self.assertEqual(plan.baseline_metric_value, 1234.5)
        self.assertEqual(plan.baseline_metric_label, "المبلغ المرصود عند اعتماد الخطة")
        self.assertEqual(plan.impact_status, "pending")

    def test_apply_without_a_baseline_still_works_as_before(self):
        response = self.client.post(
            "/api/dashboard/apply-agent-decision/",
            data=json.dumps({"action_payload": "UPDATE_DECISION_METRIC|x|active|improving", "plan_content": "خطة"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        plan = ApprovedPlan.objects.filter(user=self.user).latest("created_at")
        self.assertIsNone(plan.baseline_metric_value)


class DatasetsPageImpactColumnRenderTests(TestCase):
    """
    Renders the real "الخطط المعتمدة" table (dashboard/templates/dashboard/
    datasets.html) with a plan in every impact state -- no baseline,
    pending, improved, worsened, unchanged -- to catch a Django template
    syntax error in the new "الأثر" column that a smoke test with an empty
    queryset would never exercise (this codebase has a prior real bug of
    exactly that shape: a missing {% endblock %} that only broke rendering
    once real content flowed through it).
    """
    def setUp(self):
        self.user = User.objects.create_user(username="impact_render_user", password="pw123456")
        self.client.force_login(self.user)

    def test_every_impact_state_renders_without_error(self):
        ApprovedPlan.objects.create(user=self.user, file_name="خطة بلا باعث", file_path="")
        ApprovedPlan.objects.create(
            user=self.user, file_name="خطة معلقة", file_path="",
            baseline_metric_label="المبلغ المرصود", baseline_metric_value=1000.0,
        )
        for status, current in [("improved", 400.0), ("worsened", 1600.0), ("unchanged", 1005.0)]:
            ApprovedPlan.objects.create(
                user=self.user, file_name=f"خطة {status}", file_path="",
                baseline_metric_label="المبلغ المرصود", baseline_metric_value=1000.0,
                current_metric_value=current, impact_status=status,
            )

        response = self.client.get("/datasets/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("تسجيل القيمة الحالية", content)
        self.assertIn("recordPlanImpact", content)
        # Human-readable choice labels (ApprovedPlan.get_impact_status_display)
        for label in ["تحسّن", "تراجع", "بلا تغيير"]:
            self.assertIn(label, content)
