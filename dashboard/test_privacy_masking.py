"""
Tests for the dashboard's privacy/hide-toggle masking:

1. mask_sensitive_numbers (dashboard/templatetags/custom_filters.py): wraps
   real numeric figures inside AI-generated narrative text (Weekly Digest
   summary/risks/action plan) in a `sensitive-value` span, so the hide
   toggle actually covers those numbers -- they were previously plain text
   with no mask at all.

2. The CSS overlap bug: the waste KPI card's "detail" span could hold a
   full diagnosis SENTENCE (not just a short number), and forcing
   -webkit-text-security + letter-spacing onto a whole sentence caused it
   to overflow its card and overlap the layout below when the hide toggle
   was on. Covered here by asserting the template no longer tags that
   specific span as sensitive-value (the masked CSS rule itself is a
   browser rendering concern, not something a Django test can render, but
   this pins the template-level fix).
"""
from django.test import TestCase

from dashboard.templatetags.custom_filters import mask_sensitive_numbers


class MaskSensitiveNumbersFilterTests(TestCase):
    def test_masks_a_comma_grouped_decimal_revenue_figure(self):
        text = "إجمالي الإيرادات المرصودة 4,429.02 ر.ع. هذا الشهر"
        out = mask_sensitive_numbers(text)
        self.assertIn('<span class="sensitive-value">4,429.02</span>', out)
        self.assertIn("إجمالي الإيرادات المرصودة", out)

    def test_masks_a_percentage(self):
        out = mask_sensitive_numbers("نمو بنسبة 18.5% في الأرباح")
        self.assertIn('<span class="sensitive-value">18.5%</span>', out)

    def test_masks_a_plain_whole_number(self):
        out = mask_sensitive_numbers("تم فحص 50 سجلاً")
        self.assertIn('<span class="sensitive-value">50</span>', out)

    def test_numbered_list_marker_is_not_masked(self):
        # "1. الخطوة الأولى" -- the list marker itself must stay readable,
        # not become a lone masked dot.
        out = mask_sensitive_numbers("1. الخطوة الأولى للتنفيذ")
        self.assertEqual(out, "1. الخطوة الأولى للتنفيذ")
        self.assertNotIn("sensitive-value", out)

    def test_empty_and_none_pass_through_unchanged(self):
        self.assertEqual(mask_sensitive_numbers(""), "")
        self.assertIsNone(mask_sensitive_numbers(None))

    def test_output_is_escaped_against_injected_markup(self):
        out = mask_sensitive_numbers("<script>alert(1)</script> 50")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn('<span class="sensitive-value">50</span>', out)


class WasteKpiDetailNotOverMaskedTests(TestCase):
    """
    Pins the fix for the waste KPI card's "detail" line: it can hold a full
    AI diagnosis sentence (via analyzeWasteWithAI), not just a short
    number, and dot-masking a whole sentence with forced letter-spacing is
    what overflowed the card. The template must not tag that specific
    element as sensitive-value.
    """
    def test_waste_kpi_detail_span_is_not_tagged_sensitive_value(self):
        with open("dashboard/templates/dashboard/dashboard.html", encoding="utf-8") as f:
            content = f.read()
        self.assertIn('id="wasteKpiDetail"', content)
        # The specific detail span for the waste card must render with an
        # empty class (not "sensitive-value") when k.isWaste is true.
        self.assertIn(
            '<span class="${k.isWaste ? \'\' : \'sensitive-value\'}" ${k.isWaste ? \'id="wasteKpiDetail"\' : \'\'}>${k.d}</span>',
            content,
        )
