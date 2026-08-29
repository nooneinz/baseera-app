"""
Feature: recognizing invoices and bank statements as such, not just "a
file". Vision OCR already classified photographed documents (receipt/
invoice/bank_statement/check/handwritten_ledger) but that signal was
computed and immediately discarded -- never stored on ProjectFile, never
shown anywhere. A new lightweight (no live AI call) keyword classifier
does the same for text-extractable PDFs. Both now persist onto
ProjectFile.document_type and render as a badge on the Documents page.
"""
import io
import json
import random
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from dashboard.models import Profile, ProjectFile
from dashboard.services.validation_service import (
    validate_financial_file,
    _classify_pdf_document_type,
)


class ClassifyPdfDocumentTypeTests(TestCase):
    def test_bank_statement_keywords_win(self):
        text = "كشف حساب بنكي شهري\nالرصيد الافتتاحي: 1000\nالرصيد الختامي: 1500"
        self.assertEqual(_classify_pdf_document_type(text.lower()), 'bank_statement')

    def test_english_bank_statement_keywords(self):
        text = "Account Statement\nOpening Balance: 1000\nClosing Balance: 1500 IBAN: OM12345"
        self.assertEqual(_classify_pdf_document_type(text.lower()), 'bank_statement')

    def test_invoice_keywords(self):
        text = "فاتورة ضريبية\nرقم الفاتورة: 5521\nالمورد: شركة الاختبار"
        self.assertEqual(_classify_pdf_document_type(text.lower()), 'invoice')

    def test_english_invoice_keywords(self):
        text = "TAX INVOICE\nInvoice Number: INV-2026-004\nBill To: Test Co\nVAT: 5%"
        self.assertEqual(_classify_pdf_document_type(text.lower()), 'invoice')

    def test_neither_falls_back_to_other(self):
        text = "some generic financial document with a price and a total"
        self.assertEqual(_classify_pdf_document_type(text.lower()), 'other')

    def test_bank_statement_wins_over_invoice_when_both_present(self):
        # A bank statement can legitimately mention "invoice" in a
        # transaction description -- the statement-level keywords take
        # priority since that's the document as a whole.
        text = "كشف حساب بنكي\nالرصيد الافتتاحي: 500\ndescription: paid invoice number 12"
        self.assertEqual(_classify_pdf_document_type(text.lower()), 'bank_statement')


def _make_real_pdf_with_text(text_lines):
    """
    A genuinely valid, single-page PDF with real embedded text (a standard
    /Helvetica content stream, no external PDF library needed) -- built by
    hand instead of mocking pdfplumber's internals, so this exercises the
    real PDF parsing path end-to-end, the same way a real uploaded invoice
    or bank statement PDF would be read.
    """
    content_lines = ["BT", "/F1 12 Tf", "50 250 Td"]
    for i, line in enumerate(text_lines):
        if i > 0:
            content_lines.append("0 -16 Td")
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content_lines.append(f"({escaped}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_offset = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode())
    buf.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode())

    pdf_bytes = buf.getvalue()
    # Pad a trailing PDF comment to clear the Validation Layer's 1KB floor
    # without disturbing the xref table (comments are ignored by parsers).
    if len(pdf_bytes) < 1200:
        pdf_bytes += b"\n%% padding " + (b"x" * (1200 - len(pdf_bytes)))
    return pdf_bytes


class PdfValidationSetsDocumentTypeTests(TestCase):
    def _validate(self, text_lines):
        pdf_bytes = _make_real_pdf_with_text(text_lines)
        file_obj = SimpleUploadedFile("doc.pdf", pdf_bytes, content_type="application/pdf")
        return validate_financial_file(file_obj)

    def test_invoice_pdf_is_classified(self):
        result = self._validate(["Tax Invoice", "Invoice Number: 100", "Price: 50 Total: 50"])
        self.assertTrue(result["is_valid"], result["message"])
        self.assertEqual(result["document_type"], "invoice")

    def test_bank_statement_pdf_is_classified(self):
        result = self._validate(["Bank Statement", "Opening Balance: 100", "Total: 50 Date: 2026-01-01"])
        self.assertTrue(result["is_valid"], result["message"])
        self.assertEqual(result["document_type"], "bank_statement")


def _real_png_bytes():
    import PIL.Image
    random.seed(11)
    img = PIL.Image.new("RGB", (40, 40))
    img.putdata([
        (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        for _ in range(40 * 40)
    ])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_FAKE_OCR_RESULT = {
    "status": "accept",
    "message": "تم استخراج 3 حركات مالية من الصورة بنجاح.",
    "is_financial": True,
    "document_type": "invoice",
    "rows": [{"date": "2026-01-01", "description": "item", "amount": 10.0, "type": "debit"}],
}


class DocumentTypePersistedOnUploadTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="doctype_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="doctype_user", password="pw123456")

    def test_real_pdf_invoice_uploads_end_to_end_through_portal(self):
        # The exact path a user hits clicking "Upload Bank Statement (PDF)"
        # on /onboarding/upload/ -- a real PDF, not mocked, all the way
        # through validate -> save -> process_excel_to_db.
        pdf_bytes = _make_real_pdf_with_text([
            "Tax Invoice", "Invoice Number: 777", "Date: 2026-01-05",
            "Price: 25 Total: 25", "Price: 40 Total: 40", "Price: 15 Total: 15",
        ])
        pdf_file = SimpleUploadedFile("invoice.pdf", pdf_bytes, content_type="application/pdf")
        response = self.client.post(reverse("portal"), {"excel_file": pdf_file})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

        pf = ProjectFile.objects.filter(user=self.user).first()
        self.assertIsNotNone(pf)
        self.assertEqual(pf.document_type, "invoice")

    def test_portal_upload_persists_an_image_classified_document_type(self):
        img = SimpleUploadedFile("invoice_photo.jpg", _real_png_bytes(), content_type="image/jpeg")
        with patch(
            "dashboard.services.vision_ocr_service.ocr_extract_financial_rows",
            return_value=_FAKE_OCR_RESULT,
        ):
            self.client.post(reverse("portal"), {"excel_file": img})
        pf = ProjectFile.objects.filter(user=self.user).first()
        self.assertIsNotNone(pf)
        self.assertEqual(pf.document_type, "invoice")

    def test_chat_upload_persists_an_image_classified_document_type(self):
        img = SimpleUploadedFile("bank_statement_photo.jpg", _real_png_bytes(), content_type="image/jpeg")
        fake_result = dict(_FAKE_OCR_RESULT, document_type="bank_statement")
        with patch(
            "dashboard.services.vision_ocr_service.ocr_extract_financial_rows",
            return_value=fake_result,
        ):
            response = self.client.post(reverse("api_chat_upload_file"), {"file": img})
        self.assertTrue(response.json()["success"], response.json())
        pf = ProjectFile.objects.filter(user=self.user).first()
        self.assertEqual(pf.document_type, "bank_statement")

    def test_sample_data_is_tagged_as_a_spreadsheet(self):
        self.client.post(reverse("use_sample_data"))
        pf = ProjectFile.objects.filter(user=self.user).first()
        self.assertEqual(pf.document_type, "spreadsheet")

    def test_manual_note_is_tagged_as_a_manual_note(self):
        self.client.post(reverse("save_manual_note"), {
            "note_title": "t", "note_content": "some financial note content",
        })
        pf = ProjectFile.objects.filter(user=self.user).first()
        self.assertEqual(pf.document_type, "manual_note")


class DatasetsPageDocumentTypeBadgeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="doctype_badge_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="doctype_badge_user", password="pw123456")

    def test_invoice_badge_renders(self):
        ProjectFile.objects.create(user=self.user, excel_file="excel_files/x.pdf", document_type="invoice")
        html = self.client.get(reverse("datasets")).content.decode("utf-8")
        self.assertIn("فاتورة", html)

    def test_no_badge_for_a_plain_spreadsheet(self):
        ProjectFile.objects.create(user=self.user, excel_file="excel_files/x.xlsx", document_type="spreadsheet")
        html = self.client.get(reverse("datasets")).content.decode("utf-8")
        self.assertNotIn("تم التعرف على نوع المستند", html)
