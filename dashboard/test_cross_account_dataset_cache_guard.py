"""
Regression test for a reported bug (with screenshots): a brand-new account
showed another account's uploaded dataset -- KPIs, charts, and a "records/
columns" summary that clearly belonged to a prior real upload -- even
though the server-side "upload your first file" empty-state banner
rendered correctly for that same request.

Root cause: dashboard.html/ask_basira.html/boardroom.html all read a
"basira_dataset" preview from localStorage as a fallback whenever the
server-rendered dataset is empty. localStorage is scoped to the BROWSER,
never to whichever account happens to be logged in server-side -- so
switching accounts (a genuinely new signup, or an existing different
account) in the same browser silently inherited whatever a previous login
had cached there.

base.html now renders a small guard script, before {% block content %}
(and therefore before any page-specific script gets a chance to touch that
cache), that compares the currently authenticated user id against whichever
account's data was last cached in this browser and wipes the dataset-preview
keys on any mismatch. These tests cover what's actually verifiable
server-side: the guard renders with the right user id, appears before the
page's own content/scripts in the HTML, and is a safe no-op for anonymous
pages. The client-side branching logic itself (compare ids, clear on
mismatch) is plain, easily-reviewed JS with no server dependency to mock.
"""
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse

from dashboard.models import Profile


class CrossAccountDatasetCacheGuardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user_a = User.objects.create_user(username="cache_guard_user_a", password="pw123456")
        Profile.objects.create(user=self.user_a, company_name="A", project_type="retail", phone_number="96891111111")
        self.user_b = User.objects.create_user(username="cache_guard_user_b", password="pw123456")
        Profile.objects.create(user=self.user_b, company_name="B", project_type="retail", phone_number="96892222222")

    def test_guard_script_embeds_the_authenticated_users_id(self):
        self.client.login(username="cache_guard_user_a", password="pw123456")
        html = self.client.get(reverse("dashboard")).content.decode("utf-8")
        self.assertIn(f'var currentUserId = "{self.user_a.id}"', html)

    def test_different_accounts_get_different_embedded_ids(self):
        self.client.login(username="cache_guard_user_a", password="pw123456")
        html_a = self.client.get(reverse("dashboard")).content.decode("utf-8")
        self.client.logout()
        self.client.login(username="cache_guard_user_b", password="pw123456")
        html_b = self.client.get(reverse("dashboard")).content.decode("utf-8")

        self.assertIn(f'var currentUserId = "{self.user_a.id}"', html_a)
        self.assertIn(f'var currentUserId = "{self.user_b.id}"', html_b)
        self.assertNotIn(f'var currentUserId = "{self.user_a.id}"', html_b)

    def test_guard_renders_before_the_page_content_block(self):
        # Must run before dashboard.html's own inline script has a chance
        # to read the stale localStorage cache -- verified by document order,
        # not just presence.
        self.client.login(username="cache_guard_user_a", password="pw123456")
        html = self.client.get(reverse("dashboard")).content.decode("utf-8")
        guard_pos = html.find("basira_active_user_id")
        content_marker_pos = html.find("server-dataset")  # dashboard.html-specific content
        self.assertNotEqual(guard_pos, -1)
        self.assertNotEqual(content_marker_pos, -1)
        self.assertLess(guard_pos, content_marker_pos)

    def test_anonymous_page_renders_a_safe_no_op_guard(self):
        html = self.client.get(reverse("welcome")).content.decode("utf-8")
        self.assertIn('var currentUserId = ""', html)
