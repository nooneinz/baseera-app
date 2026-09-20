"""
Tests for the DIRECT Meta WhatsApp Cloud API webhook (no n8n):
GET verification handshake, payload parsing, the process-and-reply pipeline,
and the POST endpoint dispatching each message.
"""
import json
import os
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.contrib.auth.models import User

from dashboard.models import Profile
from dashboard.services import whatsapp_service


def _meta_payload(from_phone="96891234567", text=None, image_id=None, audio_id=None):
    msg = {"from": from_phone, "id": "wamid.X"}
    if image_id:
        msg["type"] = "image"
        msg["image"] = {"id": image_id, "mime_type": "image/jpeg"}
    elif audio_id:
        msg["type"] = "audio"
        msg["audio"] = {"id": audio_id, "mime_type": "audio/ogg"}
    else:
        msg["type"] = "text"
        msg["text"] = {"body": text or "مرحبا"}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


class ParseMetaMessagesTests(TestCase):
    def test_extracts_text_and_image(self):
        text = whatsapp_service.parse_meta_messages(_meta_payload(text="كم ربحي؟"))
        self.assertEqual(text[0]["phone"], "96891234567")
        self.assertEqual(text[0]["text"], "كم ربحي؟")
        self.assertIsNone(text[0]["media_id"])
        self.assertEqual(text[0]["mtype"], "text")
        img = whatsapp_service.parse_meta_messages(_meta_payload(image_id="MID.42"))
        self.assertEqual(img[0]["media_id"], "MID.42")
        self.assertEqual(img[0]["mtype"], "image")
        self.assertIsNone(img[0]["text"])

    def test_extracts_voice_note(self):
        aud = whatsapp_service.parse_meta_messages(_meta_payload(audio_id="AID.7"))
        self.assertEqual(aud[0]["media_id"], "AID.7")
        self.assertEqual(aud[0]["mtype"], "audio")

    def test_status_callbacks_yield_no_messages(self):
        # Delivery/read receipts carry `statuses`, not `messages`.
        payload = {"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}
        self.assertEqual(whatsapp_service.parse_meta_messages(payload), [])

    def test_malformed_payload_never_raises(self):
        self.assertEqual(whatsapp_service.parse_meta_messages({}), [])
        self.assertEqual(whatsapp_service.parse_meta_messages({"entry": "nope"}), [])


class ProcessAndReplyTests(TestCase):
    def test_downloads_media_then_replies(self):
        with patch("dashboard.services.whatsapp_service.download_whatsapp_media",
                   return_value=(b"bytes", "image/jpeg")) as dl, \
             patch("dashboard.services.whatsapp_service.handle_inbound",
                   return_value={"status": "success", "reply": "تم التحليل ✅"}) as hi, \
             patch("dashboard.services.whatsapp_service.send_whatsapp_reply",
                   return_value=True) as send:
            whatsapp_service.process_and_reply("96891234567", media_id="MID.1")
        dl.assert_called_once()
        hi.assert_called_once()
        send.assert_called_once_with("96891234567", "تم التحليل ✅")

    def test_voice_note_is_transcribed_then_answered_as_text(self):
        with patch("dashboard.services.whatsapp_service.download_whatsapp_media",
                   return_value=(b"oggbytes", "audio/ogg")), \
             patch("dashboard.services.whatsapp_service.transcribe_audio",
                   return_value="كم صافي ربحي؟") as tr, \
             patch("dashboard.services.whatsapp_service.handle_inbound",
                   return_value={"status": "agent", "reply": "صافي ربحك 1200"}) as hi, \
             patch("dashboard.services.whatsapp_service.send_whatsapp_reply", return_value=True) as send:
            whatsapp_service.process_and_reply("96891234567", media_id="AID.1", media_type="audio")
        tr.assert_called_once()
        # The transcript is passed to the engine as text; audio is NOT run as a file.
        self.assertEqual(hi.call_args.kwargs.get("text"), "كم صافي ربحي؟")
        self.assertIsNone(hi.call_args.kwargs.get("media_bytes"))
        send.assert_called_once()

    def test_text_message_skips_download(self):
        with patch("dashboard.services.whatsapp_service.download_whatsapp_media") as dl, \
             patch("dashboard.services.whatsapp_service.handle_inbound",
                   return_value={"status": "agent", "reply": "أهلاً"}), \
             patch("dashboard.services.whatsapp_service.send_whatsapp_reply", return_value=True) as send:
            whatsapp_service.process_and_reply("96891234567", text="مرحبا")
        dl.assert_not_called()
        send.assert_called_once()


class _SyncThread:
    """Runs the target synchronously so the endpoint test is deterministic."""
    def __init__(self, target=None, kwargs=None, daemon=None):
        self._target = target
        self._kwargs = kwargs or {}

    def start(self):
        self._target(**self._kwargs)


@override_settings(SECURE_SSL_REDIRECT=False)
class WhatsAppWebhookEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wh_user", password="pw123456")
        Profile.objects.create(user=self.user, phone_number="91234567")

    def test_get_verification_echoes_challenge_on_match(self):
        os.environ["WHATSAPP_VERIFY_TOKEN"] = "secret-verify"
        res = self.client.get("/api/integrations/whatsapp/webhook/", {
            "hub.mode": "subscribe", "hub.verify_token": "secret-verify", "hub.challenge": "12345",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content.decode(), "12345")

    def test_get_verification_rejects_wrong_token(self):
        os.environ["WHATSAPP_VERIFY_TOKEN"] = "secret-verify"
        res = self.client.get("/api/integrations/whatsapp/webhook/", {
            "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345",
        })
        self.assertEqual(res.status_code, 403)

    def test_post_dispatches_each_message_and_returns_200(self):
        os.environ.pop("WHATSAPP_APP_SECRET", None)  # signature check off for this test
        with patch("dashboard.api_views.threading.Thread", _SyncThread), \
             patch("dashboard.services.whatsapp_service.process_and_reply",
                   return_value={"status": "ok"}) as proc:
            res = self.client.post(
                "/api/integrations/whatsapp/webhook/",
                data=json.dumps(_meta_payload(text="كم ربحي؟")),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["received"], 1)
        proc.assert_called_once()
        self.assertEqual(proc.call_args.kwargs["phone"], "96891234567")

    def test_post_rejects_bad_signature_when_app_secret_set(self):
        os.environ["WHATSAPP_APP_SECRET"] = "app-secret"
        try:
            res = self.client.post(
                "/api/integrations/whatsapp/webhook/",
                data=json.dumps(_meta_payload(text="hi")),
                content_type="application/json",
                HTTP_X_HUB_SIGNATURE_256="sha256=deadbeef",
            )
            self.assertEqual(res.status_code, 403)
        finally:
            os.environ.pop("WHATSAPP_APP_SECRET", None)
