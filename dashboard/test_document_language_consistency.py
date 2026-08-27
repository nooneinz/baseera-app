"""
Bug report (with screenshots): a downloaded approved-plan document mixed
Arabic and English -- the document's own structural labels ("Approval
date", "Approved by", the footer, ...) were hardcoded in Arabic no matter
what language the app was set to, while the actual title/content had
been created in English mode. Switching the whole app to a language is
supposed to switch every generated report to that same language, not
just the UI chrome around it.

Root cause: dashboard/api_views.py's download_plan_api() and
dashboard/views.py's api_auto_save_document() never looked at the
session/cookie language at all -- their document templates were Arabic
string literals with no English branch.
"""
import json

from django.contrib.auth.models import User
from django.test import TestCase

from dashboard.models import ApprovedPlan


class DownloadPlanApiLanguageConsistencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="doc_lang_user", password="pw123456")
        self.client.login(username="doc_lang_user", password="pw123456")
        self.plan = ApprovedPlan.objects.create(
            user=self.user,
            file_name="Revenue Strategy",
            file_path="",
            justification="Auto-archived from Ask Basira - Executive Insights & Strategies",
        )

    def _download(self, lang):
        session = self.client.session
        session["lang"] = lang
        session.save()
        return self.client.get(f"/api/approved-plans/{self.plan.id}/download/")

    def test_arabic_session_produces_an_arabic_only_document(self):
        response = self._download("ar")
        text = response.content.decode("utf-8")
        self.assertIn("وثيقة الخطة التنفيذية والقرار المعتمد", text)
        self.assertIn("تاريخ ووقت الاعتماد", text)
        self.assertIn("تم التوثيق والاعتماد بواسطة", text)
        self.assertNotIn("EXECUTIVE PLAN", text)
        self.assertNotIn("Approval date", text)

    def test_english_session_produces_an_english_only_document(self):
        response = self._download("en")
        text = response.content.decode("utf-8")
        self.assertIn("EXECUTIVE PLAN & APPROVED DECISION DOCUMENT", text)
        self.assertIn("Approval date & time", text)
        self.assertIn("Documented and approved via", text)
        self.assertNotIn("وثيقة الخطة التنفيذية", text)
        self.assertNotIn("تاريخ ووقت الاعتماد", text)


class AutoSaveDocumentLanguageConsistencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="autosave_lang_user", password="pw123456")
        self.client.login(username="autosave_lang_user", password="pw123456")

    def _auto_save(self, lang, **payload):
        session = self.client.session
        session["lang"] = lang
        session.save()
        body = {
            "content": "A" * 60,
            "user_query": "what is driving profit",
        }
        body.update(payload)
        return self.client.post(
            "/api/documents/auto-save/", data=json.dumps(body), content_type="application/json",
        )

    def test_defaults_and_fallback_title_are_english_in_english_mode(self):
        response = self._auto_save("en")
        data = response.json()
        self.assertEqual(data["status"], "success")
        plan = ApprovedPlan.objects.get(id=data["doc_id"])
        # The justification wrapper phrase, the source, and the category
        # must all be English -- not an Arabic sentence wrapping English
        # words (the exact reported bug).
        self.assertIn("Auto-archived from", plan.justification)
        self.assertNotIn("أرشفة تلقائية", plan.justification)
        self.assertIn("Ask Basira", plan.justification)
        self.assertIn("Report:", data["title"])

    def test_defaults_and_fallback_title_are_arabic_in_arabic_mode(self):
        response = self._auto_save("ar")
        data = response.json()
        plan = ApprovedPlan.objects.get(id=data["doc_id"])
        self.assertIn("أرشفة تلقائية من", plan.justification)
        self.assertNotIn("Auto-archived from", plan.justification)
        self.assertIn("اسأل بصيرة", plan.justification)
        self.assertIn("تقرير:", data["title"])

    def test_explicit_title_from_the_client_is_never_overridden(self):
        response = self._auto_save("en", title="My Custom Title")
        data = response.json()
        self.assertEqual(data["title"], "My Custom Title")
