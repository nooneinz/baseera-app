import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from dashboard.models import (
    Profile, ProjectFile, CustomAgent, CommitteeThread, CommitteeMessage,
    Notification, SalesGoal, AnomalyAlert
)
from dashboard.services.ai_service import GeminiAIService, cosine_similarity


class BaseeraViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="analyst_test",
            email="analyst@example.com",
            password="SecurePassword123!"
        )
        self.profile = Profile.objects.create(
            user=self.user,
            company_name="شركة بصيرة للاختبار",
            project_type="pharmacy",
            phone_number="96891234567"
        )
        self.admin_user = User.objects.create_superuser(
            username="super_admin_test",
            email="admin@example.com",
            password="AdminPassword123!"
        )

    def test_welcome_page(self):
        response = self.client.get(reverse("welcome"))
        self.assertEqual(response.status_code, 200)

    def test_login_page_get_and_post(self):
        # GET login page
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        
        # POST valid login
        response = self.client.post(reverse("login"), {
            "username": "analyst_test",
            "password": "SecurePassword123!"
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(response.url, [reverse("dashboard"), "/dashboard/"])

    def test_register_flow_and_portal_redirect(self):
        # Register new account
        response = self.client.post(reverse("register"), {
            "username": "new_user_om",
            "email": "new_user@example.om",
            "password": "Password1234!",
            "company_name": "مؤسسة الابتكار",
            "cr_number": "CR-998877",
            "project_type": "retail",
            "phone": "96899998888"
        })
        # Should redirect to portal
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("portal"))

    def test_portal_page_authenticated(self):
        self.client.login(username="analyst_test", password="SecurePassword123!")
        response = self.client.get(reverse("portal"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مساحة العمل جاهزة")

    def test_dashboard_page_authenticated(self):
        self.client.login(username="analyst_test", password="SecurePassword123!")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_public_pages(self):
        pages = ["contact", "about", "privacy", "terms", "pricing"]
        for page in pages:
            with self.subTest(page=page):
                response = self.client.get(reverse(page))
                self.assertEqual(response.status_code, 200)

    def test_datasets_page_authenticated(self):
        self.client.login(username="analyst_test", password="SecurePassword123!")
        response = self.client.get(reverse("datasets"))
        self.assertEqual(response.status_code, 200)

    def test_reports_page_authenticated(self):
        self.client.login(username="analyst_test", password="SecurePassword123!")
        response = self.client.get(reverse("reports"))
        self.assertEqual(response.status_code, 200)

    def test_settings_page_authenticated(self):
        self.client.login(username="analyst_test", password="SecurePassword123!")
        response = self.client.get(reverse("settings"))
        self.assertEqual(response.status_code, 200)

    def test_notifications_page_authenticated(self):
        self.client.login(username="analyst_test", password="SecurePassword123!")
        response = self.client.get(reverse("notifications"))
        self.assertEqual(response.status_code, 200)

    def test_admin_dashboard_access_control(self):
        # Regular user should be redirected or blocked
        self.client.login(username="analyst_test", password="SecurePassword123!")
        response = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(response.status_code, 302)

        # Super admin should access
        self.client.login(username="super_admin_test", password="AdminPassword123!")
        response = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(response.status_code, 200)


class BaseeraAgentsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="agent_tester",
            email="agent_tester@example.com",
            password="TestPassword123!"
        )
        self.profile = Profile.objects.create(
            user=self.user,
            company_name="شركة الذكاء الاصطناعي",
            project_type="retail"
        )
        self.client.login(username="agent_tester", password="TestPassword123!")

    def test_agents_hub_view(self):
        response = self.client.get(reverse("agents_hub"))
        self.assertEqual(response.status_code, 200)

    def test_all_agents_gallery_view(self):
        response = self.client.get(reverse("all_agents_gallery"))
        self.assertEqual(response.status_code, 200)

    def test_boardroom_view(self):
        response = self.client.get(reverse("boardroom"))
        self.assertEqual(response.status_code, 200)

    def test_agents_workspace_view(self):
        response = self.client.get(reverse("agents_workspace"))
        self.assertEqual(response.status_code, 200)

    def test_ask_basira_default_agent(self):
        response = self.client.get(reverse("ask_basira") + "?agent_id=general")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["agent_id"], "general")

    def test_ask_basira_specialized_agents(self):
        agents = ["finance", "marketing", "operations", "strategy", "compliance"]
        for agent in agents:
            with self.subTest(agent=agent):
                response = self.client.get(reverse("ask_basira") + f"?agent_id={agent}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["agent_id"], agent)

    def test_custom_agent_creation_and_ask_basira(self):
        # Create a custom agent
        custom_agent = CustomAgent.objects.create(
            user=self.user,
            name="مستشار الصيدليات الذكي",
            role_title="Pharmacy Consultant",
            system_prompt="أنت خبير في إدارة مخزون وأرباح الصيدليات في عُمان.",
            icon="pill",
            color="emerald"
        )
        self.assertEqual(custom_agent.name, "مستشار الصيدليات الذكي")

        # Access ask_basira with this custom agent
        response = self.client.get(reverse("ask_basira") + f"?agent_id=custom_{custom_agent.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["custom_agent"].name, "مستشار الصيدليات الذكي")
        self.assertIn("custom_agents_list", response.context)
        self.assertIn(custom_agent, response.context["custom_agents_list"])

    def test_committee_thread_save_and_retrieve(self):
        # Save a committee conversation thread
        thread_data = {
            "title": "مناقشة توسع الفرع الثاني",
            "messages": [
                {"sender": "المستشار المالي", "role": "finance", "text": "العائد المتوقع 22%"},
                {"sender": "مستشار التسويق", "role": "marketing", "text": "نوصي بحملة إعلانية رقمية"}
            ]
        }
        response = self.client.post(
            reverse("api_committee_save_thread"),
            data=json.dumps(thread_data),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        resp_json = response.json()
        self.assertEqual(resp_json.get("status"), "success")
        thread_id = resp_json.get("thread_id")
        self.assertIsNotNone(thread_id)

        # Retrieve committee threads
        get_response = self.client.get(reverse("api_committee_get_threads"))
        self.assertEqual(get_response.status_code, 200)
        get_json = get_response.json()
        self.assertEqual(get_json.get("status"), "success")
        self.assertTrue(len(get_json.get("threads", [])) > 0)
        self.assertEqual(get_json["threads"][0]["title"], "مناقشة توسع الفرع الثاني")

    def test_chat_api_greeting_and_analysis(self):
        # 1. Test Arabic question
        payload_ar = {
            "message": "ما هو صافي الأرباح وكيف نرفع المبيعات؟",
            "agent_id": "general"
        }
        response_ar = self.client.post(
            reverse("chat_api"),
            data=json.dumps(payload_ar),
            content_type="application/json"
        )
        self.assertEqual(response_ar.status_code, 200)
        self.assertIn("text/event-stream", response_ar["Content-Type"])
        chunks_ar = list(response_ar.streaming_content)
        self.assertTrue(len(chunks_ar) > 0)

        # 2. Test English question
        payload_en = {
            "message": "What is the projected revenue growth for this quarter?",
            "agent_id": "general"
        }
        response_en = self.client.post(
            reverse("chat_api"),
            data=json.dumps(payload_en),
            content_type="application/json"
        )
        self.assertEqual(response_en.status_code, 200)
        self.assertIn("text/event-stream", response_en["Content-Type"])
        chunks_en = list(response_en.streaming_content)
        self.assertTrue(len(chunks_en) > 0)

    def test_ai_service_cosine_similarity(self):
        # Identical vectors
        v1 = [1.0, 2.0, 3.0]
        v2 = [1.0, 2.0, 3.0]
        sim = cosine_similarity(v1, v2)
        self.assertAlmostEqual(sim, 1.0, places=4)

        # Orthogonal vectors
        v3 = [1.0, 0.0]
        v4 = [0.0, 1.0]
        self.assertAlmostEqual(cosine_similarity(v3, v4), 0.0, places=4)

        # Empty vector handling
        self.assertEqual(cosine_similarity([], []), 0)

    def test_ai_service_persona_prompts(self):
        service = GeminiAIService()
        self.assertIn("بصيرة", service.system_prompt_ar)
        self.assertIn("Baseera", service.system_prompt_en)
        self.assertIn("Strict Formatting Rules", service.system_prompt_en)

    def test_ar_and_en_system_prompts_grant_the_same_capabilities(self):
        """
        Regression guard: the Arabic and English main system prompts must
        describe the exact same tools/behavior, just in a different
        language -- one language must never be told about a capability
        (like RUN_PYTHON code execution) that the other isn't. This exact
        asymmetry once caused an English-only code-execution capability
        that, when ported to Arabic to "match", broke a mandatory spec
        test live against the real model (it started running Python for a
        scenario that should have gone straight to a file-not-found
        answer) -- so parity was restored by trimming English back down to
        Arabic's verified scope instead, and this test pins that scope.
        """
        service = GeminiAIService()
        # Both prompts must be silent on RUN_PYTHON / code execution --
        # that capability code path in generate_chat_stream is never
        # advertised to either language.
        self.assertNotIn("RUN_PYTHON", service.system_prompt_ar)
        self.assertNotIn("RUN_PYTHON", service.system_prompt_en)
        # Both prompts must describe the same 3 Decision Platform actions.
        for action in ("UPDATE_DECISION_METRIC", "RESOLVE_RISK", "RESOLVE_LEAK"):
            self.assertIn(action, service.system_prompt_ar)
            self.assertIn(action, service.system_prompt_en)
        # Both prompts must describe file_proposal / approval_checkpoint.
        for tag in ("file_proposal", "approval_checkpoint", "internal_simulation"):
            self.assertIn(tag, service.system_prompt_ar)
            self.assertIn(tag, service.system_prompt_en)
