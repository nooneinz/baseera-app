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
