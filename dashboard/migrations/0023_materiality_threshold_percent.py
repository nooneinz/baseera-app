from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds an owner-configurable materiality threshold to CompanyStrategicProfile,
    mirroring the existing max_investment_limit/cash_reserve_floor pattern.
    See agent_escalation_chain.run_escalation_chain for how it's consumed.
    """

    dependencies = [
        ("dashboard", "0022_create_baseera_cache_table"),
    ]

    operations = [
        migrations.AddField(
            model_name="companystrategicprofile",
            name="materiality_threshold_percent",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
    ]
