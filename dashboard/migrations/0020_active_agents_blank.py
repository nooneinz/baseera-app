from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0019_filesheetmetadata"),
    ]

    operations = [
        migrations.AlterField(
            model_name="companystrategicprofile",
            name="active_agents",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
