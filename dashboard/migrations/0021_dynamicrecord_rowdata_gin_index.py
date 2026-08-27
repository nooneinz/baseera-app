from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


def create_gin_index_if_postgres(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        # SQLite (local dev/tests) has no GIN index support -- this index
        # only makes sense against the Postgres deployment.
        return
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS dynrec_rowdata_gin_idx "
        "ON dashboard_dynamicrecord USING gin (row_data)"
    )


def drop_gin_index_if_postgres(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP INDEX IF EXISTS dynrec_rowdata_gin_idx")


class Migration(migrations.Migration):
    """
    Task 7 (P0 hardening): a GIN index on DynamicRecord.row_data so
    containment/key queries against it scale as the table grows across
    every uploaded file for every user.

    GIN indexes are a Postgres-only feature, so the actual DDL only runs
    when the database vendor is postgresql; on SQLite this is a no-op and
    only Django's migration state is updated (matching models.py's
    Meta.indexes), so local dev/tests are unaffected.
    """

    dependencies = [
        ("dashboard", "0020_active_agents_blank"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="dynamicrecord",
                    index=GinIndex(fields=["row_data"], name="dynrec_rowdata_gin_idx"),
                ),
            ],
            database_operations=[
                migrations.RunPython(create_gin_index_if_postgres, drop_gin_index_if_postgres),
            ],
        ),
    ]
