from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds impact tracking to ApprovedPlan: a real baseline number captured
    at approval time (from agent_escalation_chain's computed finding) and
    a user-reported current value, so the platform can show whether a
    recommendation actually improved things instead of stopping at "applied".
    """

    dependencies = [
        ("dashboard", "0023_materiality_threshold_percent"),
    ]

    operations = [
        migrations.AddField(
            model_name="approvedplan",
            name="baseline_metric_label",
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name="وصف المؤشر الأساسي"),
        ),
        migrations.AddField(
            model_name="approvedplan",
            name="baseline_metric_value",
            field=models.FloatField(blank=True, null=True, verbose_name="القيمة الأساسية عند الاعتماد"),
        ),
        migrations.AddField(
            model_name="approvedplan",
            name="current_metric_value",
            field=models.FloatField(blank=True, null=True, verbose_name="القيمة الحالية المُسجّلة"),
        ),
        migrations.AddField(
            model_name="approvedplan",
            name="impact_status",
            field=models.CharField(
                choices=[
                    ("pending", "لم يُقاس بعد"),
                    ("improved", "تحسّن"),
                    ("worsened", "تراجع"),
                    ("unchanged", "بلا تغيير"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="approvedplan",
            name="impact_measured_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="تاريخ قياس الأثر"),
        ),
    ]
