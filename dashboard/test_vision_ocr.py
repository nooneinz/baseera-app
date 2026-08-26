"""
Tests for the Financial Vision OCR feature (dashboard/services/
vision_ocr_service.py) and its integration into validate_financial_file
for image uploads (.jpg/.jpeg/.png).

This is the literal "fish test" applied to a real photo upload: a genuine
image (correct MIME, correct extension) of something that is NOT a
financial document must be rejected — not on MIME/extension grounds (it's
a real image), but because a vision pass looked at it and it isn't one.
"""
import io
import json
import random
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile

from dashboard.services.validation_service import validate_financial_file
from dashboard.services.vision_ocr_service import (
    ocr_extract_financial_rows,
    REJECT_NOT_FINANCIAL_AR,
    REJECT_UNREADABLE_AR,
    SERVICE_UNAVAILABLE_AR,
)


def _make_real_png_bytes():
    """A genuinely valid, fully-decodable PNG comfortably past the 1KB
    floor the Validation Layer enforces (random noise so it doesn't
    compress down below that floor)."""
    import PIL.Image
    random.seed(7)
    img = PIL.Image.new("RGB", (60, 60))
    img.putdata([
        (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        for _ in range(60 * 60)
    ])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_REAL_PNG = _make_real_png_bytes()


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, response_obj):
        self._response_obj = response_obj

    def generate_content(self, model, contents):
        if isinstance(self._response_obj, Exception):
            raise self._response_obj
        return _FakeResponse(json.dumps(self._response_obj, ensure_ascii=False))


class _FakeAIService:
    """Stands in for GeminiAIService without any real network call."""
    def __init__(self, response_obj):
        self.client = _FakeClient(response_obj)


class _FakeClient:
    def __init__(self, response_obj):
        self.models = _FakeModels(response_obj)


def _upload(name="photo.jpg"):
    return SimpleUploadedFile(name, _REAL_PNG, content_type="image/png")


class VisionOcrServiceTests(TestCase):
    def test_no_ai_service_is_rejected_honestly_not_silently_accepted(self):
        result = ocr_extract_financial_rows(_upload(), ai_service=None)
        self.assertEqual(result["status"], "reject")
        self.assertEqual(result["is_financial"], None)
        self.assertEqual(result["message"], SERVICE_UNAVAILABLE_AR)

    def test_non_financial_photo_is_rejected_the_fish_test_for_images(self):
        """A real photo (e.g. of a fish, a person, a landscape) that the
        vision model correctly identifies as non-financial must be
        rejected, even though its MIME/extension are perfectly valid."""
        fake_ai = _FakeAIService({
            "is_financial": False, "readable": False, "rows": [],
            "reason": "صورة سمكة، لا تحتوي بيانات مالية",
        })
        result = ocr_extract_financial_rows(_upload("fish.jpg"), ai_service=fake_ai)
        self.assertEqual(result["status"], "reject")
        self.assertFalse(result["is_financial"])
        self.assertIn(REJECT_NOT_FINANCIAL_AR, result["message"])

    def test_financial_but_illegible_document_is_rejected(self):
        fake_ai = _FakeAIService({
            "is_financial": True, "readable": False, "rows": [],
            "reason": "الخط اليدوي غير واضح",
        })
        result = ocr_extract_financial_rows(_upload("blurry.jpg"), ai_service=fake_ai)
        self.assertEqual(result["status"], "reject")
        self.assertIn(REJECT_UNREADABLE_AR, result["message"])

    def test_real_receipt_is_accepted_with_real_extracted_rows(self):
        fake_ai = _FakeAIService({
            "is_financial": True, "readable": True, "document_type": "receipt",
            "rows": [
                {"date": "2024-01-15", "description": "قهوة", "amount": 3.5, "type": "debit"},
                {"date": "2024-01-15", "description": "كرواسان", "amount": 2.0, "type": "debit"},
            ],
            "reason": "",
        })
        result = ocr_extract_financial_rows(_upload("receipt.jpg"), ai_service=fake_ai)
        self.assertEqual(result["status"], "accept")
        self.assertTrue(result["is_financial"])
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["amount"], 3.5)

    def test_mostly_illegible_handwritten_ledger_is_a_warning_not_a_silent_accept(self):
        fake_ai = _FakeAIService({
            "is_financial": True, "readable": True, "document_type": "handwritten_ledger",
            "rows": [
                {"date": None, "description": "غير واضح", "amount": None, "type": None},
                {"date": None, "description": "غير واضح", "amount": None, "type": None},
                {"date": "2024-02-01", "description": "دفعة عميل", "amount": 100.0, "type": "credit"},
            ],
            "reason": "",
        })
        result = ocr_extract_financial_rows(_upload("ledger.jpg"), ai_service=fake_ai)
        self.assertEqual(result["status"], "warning")

    def test_ai_service_error_degrades_to_honest_unavailable_message(self):
        fake_ai = _FakeAIService(RuntimeError("network down"))
        result = ocr_extract_financial_rows(_upload("x.jpg"), ai_service=fake_ai)
        self.assertEqual(result["status"], "reject")
        self.assertEqual(result["message"], SERVICE_UNAVAILABLE_AR)


class ValidateFinancialFileImageIntegrationTests(TestCase):
    """validate_financial_file() end-to-end for image uploads."""

    def test_disguised_non_image_file_rejected_on_mime_before_any_ai_call(self):
        """A text file renamed to .jpg must be rejected at the MIME stage --
        the AI vision call must never even be attempted."""
        fake_ai = _FakeAIService(RuntimeError("should never be called"))
        text_content = (b"not an image, just text " * 100)
        upload = SimpleUploadedFile("fake.jpg", text_content, content_type="text/plain")

        result = validate_financial_file(upload, ai_service=fake_ai)

        self.assertFalse(result["is_valid"])
        self.assertEqual(result["status"], "reject")
        self.assertIn("ليس صورة حقيقية", result["message"])

    def test_real_photo_of_something_non_financial_is_rejected(self):
        fake_ai = _FakeAIService({
            "is_financial": False, "readable": False, "rows": [], "reason": "قطة",
        })
        upload = _upload("cat.png")
        result = validate_financial_file(upload, ai_service=fake_ai)
        self.assertFalse(result["is_valid"])
        self.assertEqual(result["status"], "reject")

    def test_real_receipt_photo_is_accepted_with_extracted_rows_attached(self):
        fake_ai = _FakeAIService({
            "is_financial": True, "readable": True, "document_type": "receipt",
            "rows": [{"date": "2024-01-15", "description": "قهوة", "amount": 3.5, "type": "debit"}],
            "reason": "",
        })
        upload = _upload("receipt.png")
        result = validate_financial_file(upload, ai_service=fake_ai)

        self.assertTrue(result["is_valid"])
        self.assertEqual(result["status"], "accept")
        self.assertEqual(result["accepted_sheets"], ["receipt.png"])
        self.assertEqual(len(result["extracted_rows"]), 1)
        self.assertEqual(result["extracted_rows"][0]["amount"], 3.5)
