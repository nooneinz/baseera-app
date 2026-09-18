"""
Tests for the daily Gemini cost cap. The Nth call within the limit is allowed;
the one past it is blocked; and a cache error fails open (never blocks).
"""
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.core.cache import cache

from dashboard.services import ai_quota


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class AIQuotaTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_within_then_over_limit(self):
        allowed = [ai_quota.within_daily_quota("user:1", 3)[0] for _ in range(4)]
        # first 3 allowed, 4th blocked
        self.assertEqual(allowed, [True, True, True, False])

    def test_check_gemini_quota_respects_per_user(self):
        with patch.dict("os.environ", {"GEMINI_DAILY_LIMIT_PER_USER": "2", "GEMINI_DAILY_LIMIT_GLOBAL": "100"}):
            self.assertTrue(ai_quota.check_gemini_quota(user_id=42))
            self.assertTrue(ai_quota.check_gemini_quota(user_id=42))
            self.assertFalse(ai_quota.check_gemini_quota(user_id=42))  # 3rd exceeds per-user cap of 2

    def test_fails_open_on_cache_error(self):
        with patch("django.core.cache.cache.add", side_effect=Exception("boom")):
            allowed, used = ai_quota.within_daily_quota("user:x", 1)
        self.assertTrue(allowed)  # never block on infra error
