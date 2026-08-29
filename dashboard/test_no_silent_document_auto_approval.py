"""
Regression test for a reported bug (with screenshots): an ordinary chat
answer (structured, over ~220 chars -- true of almost any real answer) was
silently written into ApprovedPlan via autoSaveAnalysisToDocuments() /
/api/documents/auto-save/, firing automatically after every chat reply with
only a passive toast as notice. ApprovedPlan is the exact same table/page
("الخطط المعتمدة") the escalation chain's real "Apply" button uses for a
decision the user explicitly confirmed (see test_agent_escalation_chain.py
and the impact-tracking fields added alongside it) -- so a plain chat
answer about a retention strategy showed up looking exactly like an
approved decision nobody approved, breaking the "the agent proposes, the
human decides" guarantee the rest of the platform enforces everywhere else
(the hard constraint gate, the escalation chain's Apply confirmation, the
chat's own "تطبيق التوصية" button).

ask_basira.html no longer calls autoSaveAnalysisToDocuments() automatically
after a chat reply completes. The function and its endpoint
(/api/documents/auto-save/) are left in place, unused, so a future EXPLICIT
"save this as a document" button can still opt into it deliberately --
only the silent, unconditional trigger is removed.
"""
from django.test import TestCase
from django.contrib.auth.models import User

from dashboard.models import Profile


class NoSilentDocumentAutoApprovalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="no_silent_save_user", password="pw123456")
        Profile.objects.create(
            user=self.user, company_name="Test Co", project_type="retail", phone_number="96891112222",
        )
        self.client.login(username="no_silent_save_user", password="pw123456")

    def test_chat_reply_completion_no_longer_auto_saves_a_document(self):
        html = self.client.get("/ask-basira/").content.decode("utf-8")
        # The automatic trigger right after a stream completes must be gone.
        self.assertNotIn("autoSaveAnalysisToDocuments(text, cleanHistoryReply)", html)

    def test_the_save_function_and_endpoint_still_exist_for_a_future_opt_in_button(self):
        html = self.client.get("/ask-basira/").content.decode("utf-8")
        self.assertIn("function autoSaveAnalysisToDocuments", html)
        self.assertIn("/api/documents/auto-save/", html)
