"""
Tests for the weekly Business Pulse job (service, cron endpoint, and message).
"""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord, WeeklyDigest
from dashboard.services import weekly_pulse

_SECRET = "test-cron-secret"


class _FakeDigest:
    summary_text = "أداء مستقر مع نمو 5%."
    top_risks = ["بيع صنف تحت التكلفة"]
    top_opportunities = ["وسّع الترويج للأصناف الرابحة"]
    action_plan = ["أعد تسعير وجبة الغداء"]
    week_label = "تقرير تجريبي"


class WeeklyPulseServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pulse_user", password="pw123456")
        Profile.objects.create(user=self.user, phone_number="96891234567")
        pf = ProjectFile.objects.create(user=self.user, excel_file="excel_files/x.csv")
        DynamicRecord.objects.create(user=self.user, project_file=pf, schema_hash="h", row_data={"a": 1})

    def test_run_weekly_pulse_generates_for_users_with_data(self):
        # Stub the AI digest generation (no network); count what the job did.
        with patch("dashboard.services.ai_service.GeminiAIService.generate_weekly_digest_for_user",
                   return_value=_FakeDigest()):
            result = weekly_pulse.run_weekly_pulse(push=False)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["generated"], 1)
        self.assertEqual(result["pushed"], 0)

    def test_users_without_data_are_skipped(self):
        User.objects.create_user(username="empty_user", password="pw123456")
        with patch("dashboard.services.ai_service.GeminiAIService.generate_weekly_digest_for_user",
                   return_value=_FakeDigest()):
            result = weekly_pulse.run_weekly_pulse(push=False)
        # Only the user with a ProjectFile is processed.
        self.assertEqual(result["processed"], 1)

    def test_build_pulse_message_is_short_and_grounded(self):
        msg = weekly_pulse.build_pulse_message(_FakeDigest(), "https://baseera.it.com/dashboard/")
        self.assertIn("نبض أعمالك", msg)
        self.assertIn("أهم خطر", msg)
        self.assertIn("baseera.it.com/dashboard", msg)


@override_settings(SECURE_SSL_REDIRECT=False)
class WeeklyPulseCronEndpointTests(TestCase):
    def _post(self, secret=_SECRET):
        headers = {"HTTP_X_BASEERA_CRON_SECRET": secret} if secret is not None else {}
        return self.client.post("/api/cron/weekly-pulse/", **headers)

    def test_requires_secret(self):
        import os
        os.environ["CRON_SECRET"] = _SECRET
        self.assertEqual(self._post(secret="wrong").status_code, 401)
        self.assertEqual(self._post(secret=None).status_code, 401)

    def test_valid_secret_runs_job(self):
        import os
        os.environ["CRON_SECRET"] = _SECRET
        with patch("dashboard.services.weekly_pulse.run_weekly_pulse",
                   return_value={"processed": 2, "generated": 2, "pushed": 0}):
            res = self._post()
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["generated"], 2)
