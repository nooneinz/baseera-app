"""
Tests for the WhatsApp inbound endpoint (/api/integrations/whatsapp/inbound/)
and its handler. The endpoint is what the external n8n workflow calls; it is
secret-protected and identifies the sender by phone number.
"""
import base64
import json
import sys
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile
from dashboard.services import whatsapp_service


_SECRET = "test-wa-secret-value"


@override_settings(SECURE_SSL_REDIRECT=False)
class WhatsAppInboundEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wa_user", password="pw123456")
        Profile.objects.create(user=self.user, phone_number="+968 9123 4567", company_name="متجر تجريبي")

    def _post(self, body, secret=_SECRET):
        headers = {"HTTP_X_BASEERA_WEBHOOK_SECRET": secret} if secret is not None else {}
        return self.client.post(
            "/api/integrations/whatsapp/inbound/",
            data=json.dumps(body), content_type="application/json", **headers,
        )

    @override_settings()
    def test_missing_or_wrong_secret_is_unauthorized(self):
        with self.settings():
            import os
            os.environ["WHATSAPP_WEBHOOK_SECRET"] = _SECRET
            self.assertEqual(self._post({"phone": "96891234567"}, secret="wrong").status_code, 401)
            self.assertEqual(self._post({"phone": "96891234567"}, secret=None).status_code, 401)

    def test_registered_text_falls_back_to_help_when_ai_unavailable(self):
        import os
        os.environ["WHATSAPP_WEBHOOK_SECRET"] = _SECRET
        # No GEMINI client (generate_agent_reply returns None) -> static help.
        with patch("dashboard.services.whatsapp_service.generate_agent_reply", return_value=None):
            res = self._post({"phone": "96891234567", "text": "مرحبا"})
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["status"], "text")
        self.assertIn("بصيرة", payload["reply"])

    def test_registered_text_routes_to_the_agent_when_ai_is_available(self):
        import os
        os.environ["WHATSAPP_WEBHOOK_SECRET"] = _SECRET
        # When the AI client is available, a text question is answered by the
        # real agent (here stubbed) rather than the static help fallback.
        with patch("dashboard.services.whatsapp_service.generate_agent_reply",
                   return_value="ربحك هذا الشهر تقريباً 320 ر.ع."):
            res = self._post({"phone": "96891234567", "text": "كم ربحت هذا الشهر؟"})
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["status"], "agent")
        self.assertIn("320", payload["reply"])

    def test_unregistered_phone_gets_onboarding_reply(self):
        import os
        os.environ["WHATSAPP_WEBHOOK_SECRET"] = _SECRET
        res = self._post({"phone": "96890000000", "text": "hi"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "unregistered")

    def test_registered_csv_upload_returns_a_grounded_reply(self):
        import os
        os.environ["WHATSAPP_WEBHOOK_SECRET"] = _SECRET
        # A tiny sales file with a below-cost line -> real waste signal.
        csv = "الصنف,سعر البيع,التكلفة,الكمية\nوجبة,4.5,5.2,10\nعصير,2.0,1.0,5\n"
        b64 = base64.b64encode(csv.encode("utf-8")).decode("ascii")

        # validate_financial_file uses python-magic (libmagic) for true MIME
        # sniffing, which isn't installed in every CI image. We stub only the
        # validation gate; the real save -> process_excel_to_db -> insight
        # pipeline still runs on the actual CSV bytes.
        fake_validation = {
            "is_valid": True, "status": "accept", "message": "",
            "accepted_sheets": ["csv_file"], "document_type": "spreadsheet",
            "extracted_rows": None,
        }
        # validation_service imports python-magic at module load; inject a
        # stub so it's importable in a CI image without libmagic, then patch
        # the validator itself.
        with patch.dict(sys.modules, {"magic": MagicMock()}):
            with patch("dashboard.services.validation_service.validate_financial_file", return_value=fake_validation):
                res = self._post({"phone": "96891234567", "media_base64": b64, "media_mime": "text/csv"})

        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["reply"].strip())  # non-empty reply
        # A file was really created and processed for this user.
        self.assertTrue(ProjectFile.objects.filter(user=self.user).exists())

    def test_media_id_is_downloaded_by_baseera_and_processed(self):
        # n8n forwards only the WhatsApp media id; Baseera downloads the bytes
        # itself and runs the SAME upload -> process -> insight pipeline. We
        # stub the Meta download (no live token in CI) and the MIME gate; the
        # real CSV still flows through the actual pipeline.
        import os
        os.environ["WHATSAPP_WEBHOOK_SECRET"] = _SECRET
        csv = "الصنف,سعر البيع,التكلفة,الكمية\nوجبة,4.5,5.2,10\nعصير,2.0,1.0,5\n"
        fake_validation = {
            "is_valid": True, "status": "accept", "message": "",
            "accepted_sheets": ["csv_file"], "document_type": "spreadsheet",
            "extracted_rows": None,
        }
        with patch("dashboard.services.whatsapp_service.download_whatsapp_media",
                   return_value=(csv.encode("utf-8"), "text/csv")):
            with patch.dict(sys.modules, {"magic": MagicMock()}):
                with patch("dashboard.services.validation_service.validate_financial_file",
                           return_value=fake_validation):
                    res = self._post({"phone": "96891234567", "media_id": "MID.9x"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "success")
        self.assertTrue(ProjectFile.objects.filter(user=self.user).exists())

    def test_media_id_that_cannot_be_fetched_falls_back_gracefully(self):
        # If Baseera can't fetch the media (no token / Meta hiccup), the turn
        # degrades to a normal text turn instead of erroring.
        import os
        os.environ["WHATSAPP_WEBHOOK_SECRET"] = _SECRET
        with patch("dashboard.services.whatsapp_service.download_whatsapp_media",
                   return_value=(None, None)):
            with patch("dashboard.services.whatsapp_service.generate_agent_reply", return_value=None):
                res = self._post({"phone": "96891234567", "media_id": "MID.dead"})
        self.assertEqual(res.status_code, 200)
        # No media bytes -> not a "success" upload; it's handled as text.
        self.assertNotEqual(res.json()["status"], "success")

    def test_invalid_upload_records_a_failure_log(self):
        import os
        os.environ["WHATSAPP_WEBHOOK_SECRET"] = _SECRET
        from dashboard.models import SystemLog
        b64 = base64.b64encode(b"not a real financial file").decode("ascii")
        fake_validation = {"is_valid": False, "status": "reject", "reason": "unreadable"}
        with patch.dict(sys.modules, {"magic": MagicMock()}):
            with patch("dashboard.services.validation_service.validate_financial_file", return_value=fake_validation):
                res = self._post({"phone": "96891234567", "media_base64": b64, "media_mime": "image/jpeg"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "invalid")
        # The failure is now visible in the logs instead of silent.
        self.assertTrue(
            SystemLog.objects.filter(user=self.user, action_type__icontains="Failure").exists()
        )


class WhatsAppServiceUnitTests(TestCase):
    def test_resolve_user_by_phone_matches_on_last_8_digits(self):
        u = User.objects.create_user(username="p_user", password="pw123456")
        Profile.objects.create(user=u, phone_number="91234567")
        self.assertEqual(whatsapp_service.resolve_user_by_phone("968 9123 4567"), u)
        self.assertIsNone(whatsapp_service.resolve_user_by_phone("99999999"))

    def test_build_reply_prefers_waste_then_cashflow_then_glance(self):
        waste_rows = [{"الصنف": "وجبة", "سعر البيع": 4.5, "التكلفة": 5.2, "الكمية": 10}]
        self.assertIn("هدر", whatsapp_service.build_reply_from_rows(waste_rows))

        cash_rows = [
            {"البيان": "إيجار", "المبلغ": 500, "النوع": "مصروف"},
            {"البيان": "مبيعات", "المبلغ": 700, "النوع": "دخل"},
        ]
        self.assertIn("تدفّق", whatsapp_service.build_reply_from_rows(cash_rows))

    def test_whatsapp_text_runs_the_same_react_agent_for_tool_questions(self):
        # A tool-worthy question (e.g. "compute my monthly profit") must go
        # through the SAME bounded ReAct + tools pre-loop the website uses --
        # so WhatsApp is fully agentic and gives identical results, not a
        # lighter reply path.
        u = User.objects.create_user(username="wa_react", password="pw123456")
        Profile.objects.create(user=u, phone_number="91112223")
        fake_ai = MagicMock()
        fake_ai.client.models.generate_content.return_value = MagicMock(text="ربحك 600 ر.ع")
        with patch("dashboard.services.ai_service.GeminiAIService", return_value=fake_ai), \
             patch("dashboard.services.agent_tools.run_react_preloop",
                   return_value="ENRICHED PROMPT") as rp:
            out = whatsapp_service.generate_agent_reply(u, "احسبلي الربح الشهري")
        self.assertTrue(rp.called)  # the real agent loop ran
        self.assertEqual(out, "ربحك 600 ر.ع")

    def test_download_whatsapp_media_without_token_is_graceful(self):
        import os
        os.environ.pop("WHATSAPP_GRAPH_TOKEN", None)
        self.assertEqual(whatsapp_service.download_whatsapp_media("MID.1"), (None, None))
        # A blank media id is likewise a no-op, never a crash.
        self.assertEqual(whatsapp_service.download_whatsapp_media(""), (None, None))

    def test_whatsapp_greeting_skips_the_react_agent(self):
        # An ordinary greeting must NOT pay for the extra tool round trip.
        u = User.objects.create_user(username="wa_hi", password="pw123456")
        Profile.objects.create(user=u, phone_number="93334445")
        fake_ai = MagicMock()
        fake_ai.client.models.generate_content.return_value = MagicMock(text="هلا والله 👋")
        with patch("dashboard.services.ai_service.GeminiAIService", return_value=fake_ai), \
             patch("dashboard.services.agent_tools.run_react_preloop") as rp:
            out = whatsapp_service.generate_agent_reply(u, "مرحبا كيف حالك")
        self.assertFalse(rp.called)  # gate skipped the loop
        self.assertEqual(out, "هلا والله 👋")
