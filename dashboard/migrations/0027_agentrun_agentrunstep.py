from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0026_projectfile_archive_status_projectfile_archived_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentRun",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(default="", max_length=120, verbose_name="اسم الوكيل / Agent")),
                ("title", models.CharField(default="", max_length=200, verbose_name="المهمة / Task")),
                ("status", models.CharField(
                    choices=[
                        ("queued", "queued"), ("running", "running"), ("done", "done"),
                        ("cancelled", "cancelled"), ("error", "error"),
                    ],
                    default="queued", max_length=20,
                )),
                ("progress_state", models.CharField(blank=True, default="", max_length=160)),
                ("result_summary", models.TextField(blank=True, default="")),
                ("cancel_requested", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="agent_runs", to="auth.user",
                )),
            ],
            options={"verbose_name": "Agent Run", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="AgentRunStep",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("seq", models.IntegerField(default=0)),
                ("message", models.CharField(default="", max_length=300)),
                ("status", models.CharField(
                    choices=[("running", "running"), ("done", "done"), ("error", "error")],
                    default="done", max_length=20,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("run", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="steps", to="dashboard.agentrun",
                )),
            ],
            options={"verbose_name": "Agent Run Step", "ordering": ["seq", "id"]},
        ),
    ]
