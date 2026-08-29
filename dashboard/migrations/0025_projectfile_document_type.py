from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds document_type to ProjectFile: vision OCR already classifies
    photographed documents (receipt/invoice/bank_statement/check/
    handwritten_ledger) and a new lightweight keyword pass does the same
    for text-extractable PDFs, but neither signal was ever stored -- it
    was computed and immediately discarded. This persists it so uploaded
    invoices, bank statements, etc. can actually be labeled as such
    instead of just "a file".
    """

    dependencies = [
        ("dashboard", "0024_approvedplan_impact_tracking"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectfile",
            name="document_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("invoice", "فاتورة / Invoice"),
                    ("bank_statement", "كشف حساب بنكي / Bank Statement"),
                    ("receipt", "إيصال / Receipt"),
                    ("check", "شيك / Check"),
                    ("handwritten_ledger", "دفتر محاسبي يدوي / Handwritten Ledger"),
                    ("manual_note", "ملاحظة مكتوبة / Manual Note"),
                    ("spreadsheet", "جدول بيانات / Spreadsheet"),
                    ("other", "أخرى / Other"),
                ],
                max_length=30,
                null=True,
                verbose_name="نوع المستند / Document Type",
            ),
        ),
    ]
