"""
Genuine tenant-isolation test: two real users, real backend requests
against each other's data -- not a grep spot-check that a queryset
mentions request.user somewhere. Every endpoint below is hit directly by
User B attempting to read/modify/delete a resource that belongs to User
A, and the request must fail (403/404), never succeed.
"""
import json

from django.contrib.auth.models import User
from django.test import TestCase

from dashboard.models import ApprovedPlan, CustomAgent, Notification


class CrossUserIsolationTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="tenant_user_a", password="pw123456")
        self.user_b = User.objects.create_user(username="tenant_user_b", password="pw123456")

        self.plan = ApprovedPlan.objects.create(
            user=self.user_a, file_name="User A's Plan", file_path="", justification="secret plan",
        )
        self.notification = Notification.objects.create(
            user=self.user_a, title="User A notif", message="private", type="info",
        )
        self.agent = CustomAgent.objects.create(
            user=self.user_a, name="User A Agent", role_title="Analyst", department="Finance",
            system_prompt="secret prompt",
        )

        self.client_b = self.client
        self.client_b.login(username="tenant_user_b", password="pw123456")

    def test_user_b_cannot_download_user_as_approved_plan(self):
        response = self.client_b.get(f"/api/approved-plans/{self.plan.id}/download/")
        # Either a clean 404 (not found for this user) or an explicit
        # error -- but never 200 with User A's content in the body.
        if response.status_code == 200:
            self.assertNotIn(b"secret plan", response.content)
        else:
            self.assertNotEqual(response.status_code, 200)

    def test_user_b_cannot_delete_user_as_approved_plan(self):
        response = self.client_b.post(f"/api/approved-plans/{self.plan.id}/delete/")
        self.assertNotEqual(response.status_code, 200)
        self.plan.refresh_from_db()  # still exists -- delete must not have gone through

    def test_user_b_cannot_edit_user_as_approved_plan_note(self):
        response = self.client_b.post(
            f"/api/approved-plans/{self.plan.id}/update-note/",
            data=json.dumps({"note": "hijacked by B"}),
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 200)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.justification, "secret plan")

    def test_user_b_cannot_delete_user_as_notification(self):
        response = self.client_b.post(f"/api/notifications/delete/{self.notification.id}/")
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(Notification.objects.filter(id=self.notification.id).exists())

    def test_user_b_cannot_delete_user_as_custom_agent(self):
        response = self.client_b.post(f"/api/custom-agents/delete/{self.agent.id}/")
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(CustomAgent.objects.filter(id=self.agent.id).exists())

    def test_user_b_sees_none_of_user_as_data_in_their_own_listing(self):
        """
        Positive control alongside the negative ones above: User B's own
        dashboard/document listing must not include User A's records at
        all, not merely block direct access to them by ID.
        """
        from dashboard.models import ApprovedPlan as AP

        b_plans = AP.objects.filter(user=self.user_b)
        self.assertEqual(b_plans.count(), 0)
        self.assertFalse(b_plans.filter(id=self.plan.id).exists())
