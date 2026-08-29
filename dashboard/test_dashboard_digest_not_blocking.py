"""
Root-cause fix for a reported "the site is abnormally slow" complaint:
dashboard() used to generate the Weekly Digest (Business Pulse Report)
INLINE in the page's GET request whenever it was missing -- a live,
synchronous Gemini API call (ai_service.generate_weekly_digest_for_user)
blocking the entire dashboard page render on a real model round trip,
every single time an account with data but no cached digest loaded the
page (e.g. the very first visit after uploading, or any time the earlier
upload-time background generation -- see test_upload_speed.py -- hadn't
finished or had failed silently).

This is now split in two:
1. dashboard() only ever READS a cached WeeklyDigest and renders whatever
   it finds (including nothing yet) -- the page never blocks on the AI
   call.
2. A new endpoint, /api/weekly-digest/generate/, does the actual
   generate-if-missing work and returns the rendered card as HTML.
   dashboard.html's own JS calls it asynchronously after the page has
   already rendered, only when the server told it (weekly_digest_pending)
   that there's nothing cached yet.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase

from dashboard.models import Profile, ProjectFile, DynamicRecord, WeeklyDigest


class DashboardDoesNotBlockOnTheDigestCallTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="digest_block_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="digest_block_user", password="pw123456")

        pf = ProjectFile.objects.create(user=self.user)
        DynamicRecord.objects.create(user=self.user, project_file=pf, row_data={"المنتج": "a", "السعر": 10})

    def test_dashboard_get_never_calls_the_live_digest_generator(self):
        with patch(
            "dashboard.services.ai_service.GeminiAIService.generate_weekly_digest_for_user",
        ) as mocked:
            response = self.client.get("/dashboard/")
            self.assertEqual(response.status_code, 200)
            mocked.assert_not_called()

    def test_dashboard_flags_a_missing_digest_as_pending_and_ships_the_fetch_script(self):
        html = self.client.get("/dashboard/").content.decode("utf-8")
        self.assertIn('id="weeklyDigestContent"', html)
        self.assertIn("/api/weekly-digest/generate/", html)

    def test_dashboard_does_not_re_fetch_once_a_digest_is_already_cached(self):
        WeeklyDigest.objects.create(
            user=self.user, week_label="cached", summary_text="already have one",
            top_risks=[], top_opportunities=[], action_plan=[],
        )
        html = self.client.get("/dashboard/").content.decode("utf-8")
        self.assertIn("already have one", html)
        # The explanatory HTML comment near the container mentions this URL
        # unconditionally -- what must NOT be present is the actual fetch
        # call, gated by {% if weekly_digest_pending %}.
        self.assertNotIn('fetch("/api/weekly-digest/generate/"', html)


class GenerateWeeklyDigestApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="digest_api_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="digest_api_user", password="pw123456")

        pf = ProjectFile.objects.create(user=self.user)
        DynamicRecord.objects.create(user=self.user, project_file=pf, row_data={"المنتج": "a", "السعر": 10})

    def test_generates_and_persists_a_digest_when_none_exists(self):
        def fake_digest(self_, sample_str, user):
            return WeeklyDigest.objects.create(
                user=user, week_label="generated", summary_text="fresh summary",
                top_risks=["risk 1"], top_opportunities=[], action_plan=["do this"],
            )

        with patch(
            "dashboard.services.ai_service.GeminiAIService.generate_weekly_digest_for_user",
            fake_digest,
        ):
            response = self.client.get("/api/weekly-digest/generate/")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["ready"])
            self.assertIn("fresh summary", data["html"])
            self.assertIn("risk 1", data["html"])

        self.assertTrue(WeeklyDigest.objects.filter(user=self.user).exists())

    def test_is_idempotent_and_never_regenerates_an_existing_digest(self):
        WeeklyDigest.objects.create(
            user=self.user, week_label="already there", summary_text="do not touch me",
            top_risks=[], top_opportunities=[], action_plan=[],
        )
        with patch(
            "dashboard.services.ai_service.GeminiAIService.generate_weekly_digest_for_user",
        ) as mocked:
            response = self.client.get("/api/weekly-digest/generate/")
            mocked.assert_not_called()
        self.assertIn("do not touch me", response.json()["html"])
        self.assertEqual(WeeklyDigest.objects.filter(user=self.user).count(), 1)

    def test_requires_login(self):
        anon_client = Client()
        response = anon_client.get("/api/weekly-digest/generate/")
        self.assertNotEqual(response.status_code, 200)
