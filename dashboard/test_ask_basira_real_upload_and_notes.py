"""
Regression tests for three related reported bugs, all traced to the same
root cause: "Ask Basira"'s chat attach button never actually reached the
backend.

1. A company trying Baseera on mobile couldn't upload a photo of their
   paper records -- because chatFileInput's accept was hardcoded to
   ".csv" only, so the phone's native file picker never offered the
   camera/photo option at all for that input.
2. Even when a file *could* be picked, the whole upload was parsed
   entirely client-side with PapaParse into localStorage and never sent
   to the server: no ProjectFile, no DynamicRecord, nothing a dashboard
   or chat agent could ever see -- despite showing a fake "Agent
   Proposal / data is ready" approval screen.
3. A company whose records are only handwritten notes typed their
   situation directly into the chat and got told to upload a file
   instead, because there was no path from Ask Basira for typed notes to
   become real data (the existing "Enter Financial Notes" flow only
   existed on the onboarding /portal/ page, and even there it never
   called process_excel_to_db() -- a note was archived as a .txt file
   and never actually analyzed).

Fixed by:
- chatFileInput now accepts .xlsx/.xls/.csv/.pdf/.jpg/.jpeg/.png.
- A new api_chat_upload_file endpoint runs a real chat-attached file
  through the exact same validate -> save -> process -> index pipeline
  portal() uses (including the existing financial-vision OCR path for
  images).
- save_manual_note() now calls process_excel_to_db() on the note it
  saves, and a matching "Enter Financial Notes" modal was added to Ask
  Basira itself, not just /portal/.
"""
import io
import os

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from dashboard.models import Profile, ProjectFile, DynamicRecord


def _real_csv_bytes():
    # validate_financial_file rejects anything under 1024 bytes as "empty
    # or too small" -- 15 short rows fell just under that, so this needs
    # enough rows to clear it comfortably.
    lines = ["التاريخ,الصنف,سعر الوحدة,الكمية,إجمالي المبيعات"]
    for i in range(40):
        lines.append(f"2026-01-{(i % 28) + 1:02d},منتج{i},{10 + i}.5,{i + 2},{(10 + i) * (i + 2)}")
    return ("\n".join(lines)).encode("utf-8")


class AskBasiraTemplateNoLongerCsvOnlyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="chat_upload_tpl_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="chat_upload_tpl_user", password="pw123456")

    def test_chat_file_input_accepts_more_than_just_csv(self):
        html = self.client.get(reverse("ask_basira")).content.decode("utf-8")
        self.assertIn('id="chatFileInput"', html)
        self.assertNotIn('id="chatFileInput" accept=".csv"', html)
        self.assertIn(".xlsx,.xls,.csv,.pdf,.jpg,.jpeg,.png", html)

    def test_real_upload_is_wired_to_the_backend_endpoint(self):
        html = self.client.get(reverse("ask_basira")).content.decode("utf-8")
        self.assertIn(reverse("api_chat_upload_file"), html)
        self.assertIn("uploadChatFile(file)", html)

    def test_manual_notes_modal_is_reachable_from_ask_basira(self):
        html = self.client.get(reverse("ask_basira")).content.decode("utf-8")
        self.assertIn('id="chatNotesModal"', html)
        self.assertIn("openChatNotesModal()", html)
        self.assertIn(reverse("save_manual_note"), html)


class ApiChatUploadFileTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="chat_upload_api_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="chat_upload_api_user", password="pw123456")

    def test_requires_login(self):
        anon = Client()
        response = anon.post(reverse("api_chat_upload_file"))
        self.assertNotEqual(response.status_code, 200)

    def test_rejects_when_no_file_attached(self):
        response = self.client.post(reverse("api_chat_upload_file"))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_a_real_csv_becomes_actual_records_not_just_a_localstorage_preview(self):
        csv_file = SimpleUploadedFile("sales.csv", _real_csv_bytes(), content_type="text/csv")
        response = self.client.post(reverse("api_chat_upload_file"), {"file": csv_file})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"], data.get("message"))
        self.assertGreater(data["recordCount"], 0)

        pf = ProjectFile.objects.filter(user=self.user).first()
        self.assertIsNotNone(pf)
        self.assertTrue(DynamicRecord.objects.filter(user=self.user, project_file=pf).exists())
        self.assertEqual(self.client.session.get("active_file_id"), pf.id)

    def test_an_invalid_file_is_rejected_and_not_persisted(self):
        bad_file = SimpleUploadedFile("not_a_real_file.xlsx", b"tiny", content_type="application/octet-stream")
        response = self.client.post(reverse("api_chat_upload_file"), {"file": bad_file})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertFalse(ProjectFile.objects.filter(user=self.user).exists())

    def test_upload_is_scoped_to_the_uploading_user(self):
        other = User.objects.create_user(username="chat_upload_other_user", password="pw123456")
        Profile.objects.create(user=other, company_name="Y", project_type="retail", phone_number="96899998888")

        csv_file = SimpleUploadedFile("sales.csv", _real_csv_bytes(), content_type="text/csv")
        self.client.post(reverse("api_chat_upload_file"), {"file": csv_file})
        self.assertFalse(ProjectFile.objects.filter(user=other).exists())


class SaveManualNoteActuallyAnalyzesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="manual_note_user", password="pw123456")
        Profile.objects.create(user=self.user, company_name="X", project_type="retail", phone_number="96891112222")
        self.client.login(username="manual_note_user", password="pw123456")

    def test_note_creates_a_project_file(self):
        response = self.client.post(reverse("save_manual_note"), {
            "note_title": "وضعي المالي",
            "note_content": "الإيرادات هذا الشهر منخفضة والمصروفات مرتفعة، المبيعات تراجعت 20%.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProjectFile.objects.filter(user=self.user).exists())

    def test_process_excel_to_db_is_actually_invoked_for_the_note(self):
        # Regression guard for the exact bug: previously nothing downstream
        # of ProjectFile.objects.create() ever ran for a manual note.
        from unittest.mock import patch
        with patch("dashboard.views.process_excel_to_db", return_value=(True, None)) as mocked:
            self.client.post(reverse("save_manual_note"), {
                "note_title": "test",
                "note_content": "some financial note content",
            })
            mocked.assert_called_once()

    def test_respects_a_safe_next_url(self):
        response = self.client.post(reverse("save_manual_note"), {
            "note_title": "t",
            "note_content": "c",
            "next": reverse("ask_basira"),
        })
        self.assertRedirects(response, reverse("ask_basira"))

    def test_ignores_an_unsafe_next_url(self):
        response = self.client.post(reverse("save_manual_note"), {
            "note_title": "t",
            "note_content": "c",
            "next": "https://evil.example.com/",
        })
        self.assertRedirects(response, reverse("datasets"))

    def test_empty_content_is_rejected_with_no_project_file_created(self):
        response = self.client.post(reverse("save_manual_note"), {
            "note_title": "t",
            "note_content": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProjectFile.objects.filter(user=self.user).exists())
