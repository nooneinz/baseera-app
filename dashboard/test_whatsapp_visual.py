"""
Tests for the on-demand WhatsApp financial visual: the request gate, that a
real PNG is rendered from the user's own numbers, and that process_and_reply
sends it (as an image) only when a visual was actually asked for.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DynamicRecord
from dashboard.services import whatsapp_visual, whatsapp_service


def _txn(desc, amount, kind):
    return {"البيان": desc, "المبلغ": amount, "النوع": kind}


class VisualRenderTests(TestCase):
    def test_wants_visual_gate(self):
        self.assertTrue(whatsapp_visual.wants_visual("ابي صورة لأرقامي"))
        self.assertTrue(whatsapp_visual.wants_visual("اعطني تقرير"))
        self.assertTrue(whatsapp_visual.wants_visual("send me a chart"))
        self.assertFalse(whatsapp_visual.wants_visual("كم ربحت هذا الشهر؟"))

    def test_render_returns_a_real_png(self):
        png = whatsapp_visual.render_summary_image([
            _txn("مبيعات", 5200, "دخل"),
            _txn("مورد", 1800, "مصروف"),
            _txn("إيجار", 700, "مصروف"),
        ])
        self.assertTrue(png)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))  # valid PNG header

    def test_render_none_without_financial_data(self):
        # No income/expense signal -> nothing to chart, returns None (no blank image).
        self.assertIsNone(whatsapp_visual.render_summary_image([]))
        self.assertIsNone(whatsapp_visual.render_summary_image([{"الصنف": "وجبة"}]))


class ProcessAndReplyVisualTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="viz_user", password="pw123456")
        Profile.objects.create(user=self.user, phone_number="91234567")
        pf = ProjectFile.objects.create(user=self.user, excel_file="excel_files/x.csv")
        for r in [_txn("مبيعات", 5000, "دخل"), _txn("مورد", 1500, "مصروف")]:
            DynamicRecord.objects.create(user=self.user, project_file=pf, schema_hash="h", row_data=r)

    def test_image_is_sent_when_a_visual_is_requested(self):
        with patch("dashboard.services.whatsapp_service.handle_inbound",
                   return_value={"status": "agent", "reply": "تفضلي 📊"}), \
             patch("dashboard.services.whatsapp_service.send_whatsapp_reply", return_value=True), \
             patch("dashboard.services.whatsapp_service.send_whatsapp_image", return_value=True) as img:
            whatsapp_service.process_and_reply("96891234567", text="ابي صورة لأرقامي")
        self.assertTrue(img.called)  # a real image was generated and sent

    def test_no_image_for_a_plain_question(self):
        with patch("dashboard.services.whatsapp_service.handle_inbound",
                   return_value={"status": "agent", "reply": "ربحك 3500"}), \
             patch("dashboard.services.whatsapp_service.send_whatsapp_reply", return_value=True), \
             patch("dashboard.services.whatsapp_service.send_whatsapp_image", return_value=True) as img:
            whatsapp_service.process_and_reply("96891234567", text="كم ربحت؟")
        self.assertFalse(img.called)
