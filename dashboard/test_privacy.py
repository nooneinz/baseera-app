"""
Tests for the privacy redaction layer that runs before any user rows are sent
to an external model (Gemini). Identifying fields must be masked; financial
figures the agent needs must survive.
"""
import json
from django.test import TestCase

from dashboard.services.privacy import (
    redact_row,
    redact_rows_for_ai,
    redacted_json,
    MASK,
)


class PrivacyRedactionTests(TestCase):
    def test_sensitive_headers_are_masked(self):
        row = {
            "اسم العميل": "محمد أحمد",
            "رقم الحساب": "1234567890123",
            "البريد الإلكتروني": "m@example.com",
            "الهاتف": "96891234567",
        }
        out = redact_row(row)
        self.assertEqual(out["اسم العميل"], MASK)
        self.assertEqual(out["رقم الحساب"], MASK)
        self.assertEqual(out["البريد الإلكتروني"], MASK)
        self.assertEqual(out["الهاتف"], MASK)

    def test_financial_fields_survive(self):
        row = {"البيان": "إيجار المحل", "المبلغ": 1250, "النوع": "مصروف"}
        out = redact_row(row)
        self.assertEqual(out["البيان"], "إيجار المحل")
        self.assertEqual(out["المبلغ"], 1250)
        self.assertEqual(out["النوع"], "مصروف")

    def test_value_level_masking_regardless_of_column(self):
        # Even a harmless-looking column must not leak an email/long number.
        row = {"ملاحظة": "تواصل عبر test@mail.com أو الحساب 12345678901"}
        out = redact_row(row)
        self.assertIn(MASK, out["ملاحظة"])
        self.assertNotIn("test@mail.com", out["ملاحظة"])
        self.assertNotIn("12345678901", out["ملاحظة"])

    def test_short_numbers_are_not_masked(self):
        # An amount like 45000 (5 digits) must stay intact.
        row = {"الوصف": "مبيعات بقيمة 45000 ريال"}
        out = redact_row(row)
        self.assertIn("45000", out["الوصف"])

    def test_redacted_json_caps_and_serializes(self):
        rows = [{"اسم": f"x{i}", "المبلغ": i} for i in range(200)]
        text = redacted_json(rows, cap=10)
        parsed = json.loads(text)
        self.assertEqual(len(parsed), 10)
        self.assertTrue(all(r["اسم"] == MASK for r in parsed))

    def test_redacted_json_empty_is_blank(self):
        self.assertEqual(redacted_json([]), "")
        self.assertEqual(redacted_json(None), "")

    def test_non_dict_rows_passthrough(self):
        self.assertEqual(redact_rows_for_ai(["a", 1]), ["a", 1])
