"""
Task 7 (P0 hardening): a real shared cache backend for rate limiting, and
a GIN index on DynamicRecord.row_data for Postgres at scale.
"""
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.cache import cache
from django.test import TestCase

from dashboard.models import DynamicRecord


class SharedCacheBackendTests(TestCase):
    def test_cache_backend_is_not_the_per_process_default(self):
        """
        LocMemCache is per-process -- on a multi-worker deployment each
        gunicorn/Render worker would keep its own independent counter, so
        security.rate_limit()'s stated limits were never actually enforced
        platform-wide. This must be a backend genuinely shared across
        processes (DatabaseCache by default, or Redis if provisioned).
        """
        backend = settings.CACHES["default"]["BACKEND"]
        self.assertNotIn("locmem", backend.lower())

    def test_cache_round_trip_works(self):
        """
        Proves the configured backend is actually usable end to end (for
        DatabaseCache, that its table was created by the 0022 migration),
        not just declared in settings.
        """
        cache.set("baseera:infra_hardening_smoke_test", "ok", timeout=5)
        self.assertEqual(cache.get("baseera:infra_hardening_smoke_test"), "ok")


class DynamicRecordGinIndexTests(TestCase):
    def test_model_declares_a_gin_index_on_row_data(self):
        indexes = DynamicRecord._meta.indexes
        gin_indexes = [i for i in indexes if isinstance(i, GinIndex)]
        self.assertTrue(gin_indexes, "DynamicRecord should declare a GinIndex")
        self.assertIn("row_data", gin_indexes[0].fields)
