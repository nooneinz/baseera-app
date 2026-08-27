from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase


class AiHealthEndpointTests(TestCase):
    """
    Task 3 (P0 - Resilience): /api/ai/health/ must be an *active* check --
    it has to actually call the configured Gemini model, not just report
    "healthy" because an API key string exists. These tests mock the
    Gemini client directly so they stay deterministic and don't spend real
    API quota, while still exercising the view's own logic end to end.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="health_test_user", password="pw123456")
        self.client.login(username="health_test_user", password="pw123456")
        cache.delete("baseera:ai_health_status")

    def tearDown(self):
        cache.delete("baseera:ai_health_status")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get("/api/ai/health/")
        self.assertNotEqual(response.status_code, 200)

    def test_reports_unavailable_when_no_client_is_configured(self):
        with patch("dashboard.services.ai_service.GeminiAIService.__init__", return_value=None):
            with patch.object(
                __import__("dashboard.services.ai_service", fromlist=["GeminiAIService"]).GeminiAIService,
                "client",
                None,
                create=True,
            ):
                response = self.client.get("/api/ai/health/")
        data = response.json()
        self.assertFalse(data["available"])
        self.assertEqual(data["detail"], "no_api_key_configured")

    def test_reports_available_when_the_live_call_succeeds(self):
        fake_client = MagicMock()
        fake_client.models.count_tokens.return_value = MagicMock()

        with patch("dashboard.services.ai_service.GeminiAIService.__init__", return_value=None):
            with patch.object(
                __import__("dashboard.services.ai_service", fromlist=["GeminiAIService"]).GeminiAIService,
                "client",
                fake_client,
                create=True,
            ):
                response = self.client.get("/api/ai/health/")
        data = response.json()
        self.assertTrue(data["available"])
        self.assertEqual(data["detail"], "ok")
        fake_client.models.count_tokens.assert_called_once()

    def test_reports_unavailable_when_the_live_call_raises(self):
        fake_client = MagicMock()
        fake_client.models.count_tokens.side_effect = Exception("503 model overloaded")

        with patch("dashboard.services.ai_service.GeminiAIService.__init__", return_value=None):
            with patch.object(
                __import__("dashboard.services.ai_service", fromlist=["GeminiAIService"]).GeminiAIService,
                "client",
                fake_client,
                create=True,
            ):
                response = self.client.get("/api/ai/health/")
        data = response.json()
        self.assertFalse(data["available"])
        self.assertIn("overloaded", data["detail"])

    def test_result_is_cached_so_repeated_polls_do_not_hit_the_api_again(self):
        fake_client = MagicMock()
        fake_client.models.count_tokens.return_value = MagicMock()

        with patch("dashboard.services.ai_service.GeminiAIService.__init__", return_value=None):
            with patch.object(
                __import__("dashboard.services.ai_service", fromlist=["GeminiAIService"]).GeminiAIService,
                "client",
                fake_client,
                create=True,
            ):
                self.client.get("/api/ai/health/")
                self.client.get("/api/ai/health/")

        # Cached on the first call -- the second poll must not re-invoke it.
        self.assertEqual(fake_client.models.count_tokens.call_count, 1)
