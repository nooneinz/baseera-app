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
import unittest
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


def _real_pdf_bytes_and_mocked_text(text):
    """
    A minimal, syntactically valid single-page PDF (structure only --
    pdfplumber needs real PDF bytes to open the file without raising, but
    the actual extracted text is controlled by mocking Page.extract_text
    so the test doesn't depend on rendering real glyphs into the page).
    """
    pdf_bytes = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF"
    )
    return pdf_bytes


def _pdfplumber_importable():
    """
    This sandbox's system 'cryptography' install (pulled in transitively
    via pdfminer, pdfplumber's own dependency) is missing its compiled
    _cffi_backend and PanicException()s on import -- unrelated to any code
    here, and not something safe to "fix" by touching sandbox system
    packages. Skip the pdfplumber-dependent tests where that's true rather
    than fail the suite on an environment problem; they still run for real
    wherever pdfplumber imports cleanly (e.g. a real deploy/CI environment).
    """
    try:
        import pdfplumber  # noqa: F401
        return True
    except BaseException:
        return False


@unittest.skipUnless(
    _pdfplumber_importable(),
    "pdfplumber is not importable in this environment (see _pdfplumber_importable's docstring)",
)
class PdfValidationSetsDocumentTypeTests(TestCase):
    def _validate_with_mocked_text(self, text):
        pdf_bytes = _real_pdf_bytes_and_mocked_text(text)
        # Padded to clear the 1024-byte floor.
        pdf_bytes = pdf_bytes + b"\n% padding " + (b"x" * 1024)
        file_obj = SimpleUploadedFile("doc.pdf", pdf_bytes, content_type="application/pdf")

        class _FakePage:
            def extract_text(self_inner):
                return text

        class _FakePdf:
            pages = [_FakePage()]

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        with patch("pdfplumber.open", return_value=_FakePdf()):
            return validate_financial_file(file_obj)

    def test_invoice_pdf_is_classified(self):
        result = self._validate_with_mocked_text("Tax Invoice\nInvoice Number: 100\nPrice: 50 Total: 50")
        self.assertTrue(result["is_valid"])
        self.assertEqual(result["document_type"], "invoice")

    def test_bank_statement_pdf_is_classified(self):
        result = self._validate_with_mocked_text("Bank Statement\nOpening Balance: 100\nTotal: 50 Date: 2026-01-01")
        self.assertTrue(result["is_valid"])
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
