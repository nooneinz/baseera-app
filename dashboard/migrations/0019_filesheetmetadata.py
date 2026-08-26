import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0018_companystrategicprofile"),
    ]

    operations = [
        migrations.CreateModel(
            name="FileSheetMetadata",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("sheet_name", models.CharField(max_length=255, verbose_name="اسم الورقة/الجدول")),
                (
                    "status",
                    models.CharField(
                        choices=[("accept", "مقبول"), ("warning", "مقبول مع تحذير")],
                        default="accept",
                        max_length=20,
                    ),
                ),
                ("columns", models.JSONField(default=list, verbose_name="أسماء الأعمدة")),
                ("row_count", models.IntegerField(default=0)),
                ("date_range_start", models.DateField(blank=True, null=True)),
                ("date_range_end", models.DateField(blank=True, null=True)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("sales", "مبيعات"),
                            ("expenses", "مصروفات"),
                            ("invoices", "فواتير"),
                            ("inventory", "مخزون"),
                            ("bank", "كشف حساب بنكي"),
                            ("other", "أخرى"),
                        ],
                        default="other",
                        max_length=20,
                        verbose_name="التصنيف المبدئي",
                    ),
                ),
                (
                    "keywords",
                    models.JSONField(
                        default=list,
                        help_text="رموز نصية مستخرجة من اسم الورقة والأعمدة لأغراض البحث الهجين",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "project_file",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sheet_metadata",
                        to="dashboard.projectfile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Retrieval Sheet Metadata",
            },
        ),
    ]
