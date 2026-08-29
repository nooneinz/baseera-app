"""
A PDF with no extractable text layer (a phone photo of a paper invoice or
a printer's scan-to-PDF output, not a digitally-generated PDF) used to be
rejected outright with "Scan is not currently supported" -- even though
Baseera already has a full financial-vision OCR pipeline for exactly this
kind of document, just gated behind uploading it as a .jpg/.png instead.

validate_financial_file's PDF branch now renders the first page to an
image (pdfplumber's own pypdfium2-backed renderer -- no extra system
dependency) and runs it through that same OCR pass instead of rejecting.
process_excel_to_db's PDF branch was also missing the same
"prefer already-extracted rows" priority the image branch already had, so
it's covered here too.
"""
import io
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from dashboard.models import Profile, ProjectFile, DynamicRecord
from dashboard.services.validation_service import validate_financial_file


def _make_no_text_pdf():
    """A genuinely valid, openable single-page PDF with zero text content
    -- pdfplumber's text extraction correctly finds nothing on it, the
    same as a real scanned page would (whose "text" is actually pixels)."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] >>",
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
    return pdf_bytes + b"\n%% padding " + (b"x" * 1200)


_ACCEPT_OCR_RESULT = {
    "status": "accept",
    "message": "تم استخراج 2 حركة مالية من الصورة بنجاح.",
    "is_financial": True,
    "document_type": "invoice",
    "rows": [
        {"date": "2026-01-01", "description": "item one", "amount": 10.0, "type": "debit"},
        {"date": "2026-01-02", "description": "item two", "amount": 20.0, "type": "credit"},
    ],
}

_REJECT_OCR_RESULT = {
    "status": "reject",
    "message": "هذه الصورة لا تبدو مستنداً مالياً (فاتورة، إيصال، كشف حساب).",
    "is_financial": False,
    "document_type": "other",
    "rows": [],
}


class ScannedPdfValidationTests(TestCase):
    def test_a_no_text_pdf_is_ocrd_instead_of_rejected_outright(self):
        file_obj = SimpleUploadedFile("scan.pdf", _make_no_text_pdf(), content_type="application/pdf")
        with patch(
            "dashboard.services.vision_ocr_service.ocr_extract_financial_rows",
            return_value=_ACCEPT_OCR_RESULT,
        ):
            result = validate_financial_file(file_obj)

        self.assertTrue(result["is_valid"], result["message"])
        self.assertEqual(result["status"], "accept")
        self.assertEqual(len(result["extracted_rows"]), 2)
        self.assertEqual(result["document_type"], "invoice")
        self.assertEqual(result["accepted_sheets"], ["scanned_document"])

    def test_a_non_financial_scan_is_still_correctly_rejected(self):
        file_obj = SimpleUploadedFile("scan.pdf", _make_no_text_pdf(), content_type="application/pdf")
        with patch(
            "dashboard.services.vision_ocr_service.ocr_extract_financial_rows",
            return_value=_REJECT_OCR_RESULT,
        ):
            result = validate_financial_file(file_obj)

        self.assertFalse(result["is_valid"])
        # The specific OCR reason, not the old generic "scan not supported"
        # message -- the file WAS scanned/analyzed, it just wasn't financial.
        self.assertIn("لا تبدو مستنداً مالياً", result["message"])

    def test_a_genuinely_unrenderable_pdf_still_gets_a_clear_message(self):
        # Not a real PDF at all past the header -- pdfplumber opens it (the
        # outer try/except only wraps the whole function) but the page
        # rasterization step itself has nothing to render.
        file_obj = SimpleUploadedFile(
            "broken.pdf",
            b"%PDF-1.4\n%% not a real page structure " + (b"x" * 1200),
            content_type="application/pdf",
        )
        result = validate_financial_file(file_obj)
        self.assertFalse(result["is_valid"])
        self.assertTrue(result["message"])


class ScannedPdfEndToEndUploadTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="scanned_pdf_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="scanned_pdf_user", password="pw123456")

    def test_scanned_pdf_upload_creates_real_records_via_portal(self):
        pdf_file = SimpleUploadedFile("scan.pdf", _make_no_text_pdf(), content_type="application/pdf")
        with patch(
            "dashboard.services.vision_ocr_service.ocr_extract_financial_rows",
            return_value=_ACCEPT_OCR_RESULT,
        ):
            response = self.client.post(reverse("portal"), {"excel_file": pdf_file})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

        pf = ProjectFile.objects.filter(user=self.user).first()
        self.assertIsNotNone(pf)
        self.assertEqual(pf.document_type, "invoice")
        self.assertEqual(DynamicRecord.objects.filter(user=self.user, project_file=pf).count(), 2)

    def test_process_excel_to_db_prefers_pre_extracted_rows_over_reparsing(self):
        # Regression guard for the exact bug: the PDF branch used to ignore
        # extracted_rows entirely and always re-run parse_pdf_to_df, unlike
        # the image branch which already prioritized it correctly.
        from dashboard.views import process_excel_to_db

        pdf_file = SimpleUploadedFile("scan.pdf", _make_no_text_pdf(), content_type="application/pdf")
        pf = ProjectFile.objects.create(user=self.user, excel_file=pdf_file, document_type="invoice")

        with patch("dashboard.views.parse_pdf_to_df") as mocked_parser:
            success, error = process_excel_to_db(
                pf, self.user, extracted_rows=_ACCEPT_OCR_RESULT["rows"],
            )
            mocked_parser.assert_not_called()

        self.assertTrue(success, error)
        self.assertEqual(DynamicRecord.objects.filter(user=self.user, project_file=pf).count(), 2)
