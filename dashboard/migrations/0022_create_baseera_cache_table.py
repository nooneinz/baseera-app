from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    # createcachetable inspects settings.CACHES and creates a table for
    # every configured DatabaseCache backend (see baseera_web/settings.py).
    # If REDIS_URL is set in this environment, CACHES uses RedisCache
    # instead and this is a safe no-op -- no table is created or needed.
    call_command("createcachetable")


def drop_cache_table(apps, schema_editor):
    schema_editor.execute("DROP TABLE IF EXISTS baseera_cache_table")


class Migration(migrations.Migration):
    """
    Task 7 (P0 hardening): creates the DB-backed cache table used by
    security.rate_limit() and the AI health-check endpoint so their shared
    counters/results are genuinely shared across every gunicorn/Render
    worker process (and every dyno, if scaled horizontally), instead of
    each worker silently keeping its own independent LocMemCache.
    """

    dependencies = [
        ("dashboard", "0021_dynamicrecord_rowdata_gin_index"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
