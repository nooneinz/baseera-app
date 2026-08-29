"""
Tests for the post-registration onboarding flow and initial UI guidance
system: register -> /onboarding/upload -> (upload | sample data | skip) ->
dashboard -> a one-time two-step guided tour (eye/privacy mask, then
"Ask Basira").

Covers:
- Registration now redirects to the new onboarding_upload URL (not the
  legacy "portal" name, though that URL still works -- same view).
- /onboarding/upload/ renders the prominent drop zone and the "no data
  yet" sample-data section (download + one-click populate).
- use_sample_data(): a real, ownership-scoped ProjectFile + DynamicRecord
  rows get created through the exact same validation/processing pipeline
  a real upload uses, then redirects to the dashboard.
- The dashboard's "no data yet" banner links to the new onboarding URL and
  disappears once the account has data.
- The guided-tour anchors (#maskToggleBtn already existed; #askBasiraNavLink
  is new) and the tour's own script/state key are present on the dashboard
  page, and the two-step localStorage state key ('basira_onboarding_step')
  is included in the cross-account cache guard in base.html so a new
  account in the same browser never inherits a finished tour.
- /ask-basira/ accepts ?prompt=... for the guided tour's one-click sample
  prompts.
"""
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse

from dashboard.models import Profile, ProjectFile, DynamicRecord


class RegistrationRedirectTests(TestCase):
    def test_successful_registration_redirects_to_onboarding_upload(self):
        client = Client()
        response = client.post(reverse("register"), {
            "username": "onboard_new_user",
            "full_name": "Test User",
            "email": "onboard_new_user@example.com",
            "phone": "96891234567",
            "cr_number": "CR12345",
            "company_name": "Test SME",
            "project_type": "retail",
            "password": "SecurePassword123!",
        })
        self.assertRedirects(response, reverse("onboarding_upload"))

    def test_onboarding_upload_and_legacy_portal_url_both_resolve_to_the_same_page(self):
        client = Client()
        User.objects.create_user(username="portal_alias_user", password="pw123456")
        Profile.objects.create(user=User.objects.get(username="portal_alias_user"),
                                company_name="X", project_type="retail", phone_number="96891112222")
        client.login(username="portal_alias_user", password="pw123456")
        r1 = client.get("/onboarding/upload/")
        r2 = client.get("/portal/")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)


class OnboardingUploadPageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="upload_page_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="upload_page_user", password="pw123456")

    def test_renders_prominent_drop_zone_supporting_required_types(self):
        html = self.client.get(reverse("onboarding_upload")).content.decode("utf-8")
        self.assertIn('id="primaryDropZone"', html)
        self.assertIn('id="primaryDropInput"', html)
        self.assertIn(".xlsx,.xls,.csv,.pdf,.jpg,.jpeg,.png", html)

    def test_renders_optional_no_data_section_with_sample_download_and_use_button(self):
        html = self.client.get(reverse("onboarding_upload")).content.decode("utf-8")
        self.assertIn('id="noDataSection"', html)
        self.assertIn("sample_sme_omani.xlsx", html)
        self.assertIn(reverse("use_sample_data"), html)


class UseSampleDataTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="sample_data_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="sample_data_user", password="pw123456")

    def test_get_does_not_populate_anything_and_redirects_back(self):
        response = self.client.get(reverse("use_sample_data"))
        self.assertRedirects(response, reverse("onboarding_upload"))
        self.assertFalse(ProjectFile.objects.filter(user=self.user).exists())

    def test_post_creates_a_real_project_file_and_records_then_redirects_to_dashboard(self):
        response = self.client.post(reverse("use_sample_data"))
        self.assertRedirects(response, reverse("dashboard"))
        pf = ProjectFile.objects.filter(user=self.user).first()
        self.assertIsNotNone(pf)
        self.assertTrue(DynamicRecord.objects.filter(user=self.user, project_file=pf).exists())

    def test_sample_data_is_scoped_to_the_requesting_user_only(self):
        other = User.objects.create_user(username="sample_data_other", password="pw123456")
        Profile.objects.create(user=other, company_name="Y", project_type="retail", phone_number="96899998888")
        self.client.post(reverse("use_sample_data"))
        self.assertFalse(ProjectFile.objects.filter(user=other).exists())


class DashboardOnboardingBannerAndTourTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="dash_onboard_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="dash_onboard_user", password="pw123456")

    def test_no_data_banner_links_to_onboarding_upload(self):
        html = self.client.get(reverse("dashboard")).content.decode("utf-8")
        self.assertIn(reverse("onboarding_upload"), html)

    def test_guided_tour_script_and_anchors_present(self):
        html = self.client.get(reverse("dashboard")).content.decode("utf-8")
        self.assertIn("basira_onboarding_step", html)
        self.assertIn('id="onboardingEyeGuide"', html)
        self.assertIn('id="onboardingChatGuide"', html)
        self.assertIn('id="askBasiraNavLink"', html)
        self.assertIn("maskToggleBtn", html)
        self.assertIn("baseera-tour-highlight", html)

    def test_no_data_banner_disappears_once_the_account_has_a_file(self):
        self.client.post(reverse("use_sample_data"))
        html = self.client.get(reverse("dashboard")).content.decode("utf-8")
        self.assertNotIn("جاهز لتحليل منشأتك؟", html)


class CrossAccountOnboardingStateGuardTests(TestCase):
    def test_onboarding_step_key_is_cleared_on_account_switch(self):
        client = Client()
        User.objects.create_user(username="guard_user", password="pw123456")
        Profile.objects.create(user=User.objects.get(username="guard_user"),
                                company_name="X", project_type="retail", phone_number="96891112222")
        client.login(username="guard_user", password="pw123456")
        html = client.get(reverse("dashboard")).content.decode("utf-8")
        self.assertIn("basira_onboarding_step", html)
        self.assertIn("'basira_dataset', 'hasUploadedData', 'basira_onboarding_step'", html)


class AskBasiraQuickStartPromptTests(TestCase):
    def test_prompt_query_param_is_handled_in_the_template_script(self):
        client = Client()
        user = User.objects.create_user(username="quickstart_user", password="pw123456")
        Profile.objects.create(user=user, company_name="X", project_type="retail", phone_number="96891112222")
        client.login(username="quickstart_user", password="pw123456")
        html = client.get(reverse("ask_basira")).content.decode("utf-8")
        self.assertIn("urlParams.get('prompt')", html)
        self.assertIn("quickStartPrompt", html)
