import json

from django.test import TestCase, Client
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.contrib.auth.models import User

from dashboard.security import validate_uploaded_file, build_safe_filename


class SecurityHardeningTests(TestCase):
    def test_secure_defaults_are_applied(self):
        self.assertFalse(settings.CORS_ALLOW_ALL_ORIGINS)
        self.assertNotIn("*", settings.ALLOWED_HOSTS)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")

    def test_rejects_unsafe_uploads(self):
        bad_file = SimpleUploadedFile(
            "payload.exe",
            b"not a real file",
            content_type="application/x-msdownload",
        )
        with self.assertRaises(ValueError):
            validate_uploaded_file(bad_file, max_size_bytes=5 * 1024 * 1024)

    def test_builds_safe_filename(self):
        safe_name = build_safe_filename("report.csv")
        self.assertTrue(safe_name.endswith(".csv"))
        self.assertNotIn("../", safe_name)


class CsrfProtectionEnforcedTests(TestCase):
    """
    A dozen session-authenticated, state-changing endpoints (custom-agent
    CRUD, committee threads, chat, boardroom debate, decision application)
    used to carry @csrf_exempt -- meaning a page a logged-in user merely
    visited elsewhere could trigger these actions on their behalf without
    their knowledge. Removing the decorator only actually fixes anything
    if Django's real CSRF middleware is then enforced end-to-end; the
    Django TestCase client does NOT enforce CSRF by default (that's why
    the ordinary test suite kept passing throughout), so this uses a
    client with enforce_csrf_checks=True to prove the protection is real,
    not just "the decorator is gone".
    """

    def setUp(self):
        self.user = User.objects.create_user(username="csrf_test_user", password="pw123456")
        self.enforcing_client = Client(enforce_csrf_checks=True)
        self.enforcing_client.login(username="csrf_test_user", password="pw123456")

    def _post_without_token(self, url, payload=None):
        return self.enforcing_client.post(
            url, data=json.dumps(payload or {}), content_type="application/json",
        )

    def test_chat_api_rejects_a_request_with_no_csrf_token(self):
        response = self._post_without_token("/api/insights/chat", {"message": "test"})
        self.assertEqual(response.status_code, 403)

    def test_create_custom_agent_rejects_a_request_with_no_csrf_token(self):
        response = self._post_without_token("/api/custom-agents/create/", {"name": "x"})
        self.assertEqual(response.status_code, 403)

    def test_apply_agent_decision_rejects_a_request_with_no_csrf_token(self):
        response = self._post_without_token("/api/dashboard/apply-agent-decision/", {})
        self.assertEqual(response.status_code, 403)

    def test_boardroom_debate_rejects_a_request_with_no_csrf_token(self):
        response = self._post_without_token("/api/boardroom/debate/", {"topic": "x"})
        self.assertEqual(response.status_code, 403)

    def test_committee_save_thread_rejects_a_request_with_no_csrf_token(self):
        response = self._post_without_token("/api/committee/save-thread/", {})
        self.assertEqual(response.status_code, 403)

    def test_a_request_carrying_the_real_csrf_token_is_accepted(self):
        """
        Positive control: this isn't just "everything 403s" -- a request
        that actually carries the token Django issued for this session
        goes through to the view's own logic instead of being blocked.
        """
        # Warm up the CSRF cookie for this session -- this must be a real
        # template-rendering page (one that renders {% csrf_token %} in a
        # form), since a plain JSON API view never triggers Django to set
        # the csrftoken cookie on its response.
        self.enforcing_client.get("/ask-basira/")
        token = self.enforcing_client.cookies.get("csrftoken")
        self.assertIsNotNone(token, "Django should have issued a CSRF cookie")

        response = self.enforcing_client.post(
            "/api/custom-agents/create/",
            data=json.dumps({"name": "", "role_title": "", "department": "", "system_prompt": ""}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token.value,
        )
        # Not a 403 -- the request passed CSRF validation and reached the
        # view (which may still reject the empty payload on its own terms).
        self.assertNotEqual(response.status_code, 403)
