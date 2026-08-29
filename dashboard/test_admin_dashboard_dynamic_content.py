"""
Regression tests for a review of the Super Admin dashboard requested by the
user: several pieces of the page were static/hardcoded text presented as if
they were real data, and one was a literal privacy leak. Specifically:

1. The sidebar profile card fell back to a hardcoded stranger's email
   ("hasansajjadux@gmail.com") whenever the logged-in admin account had no
   email set -- looked like real account data but wasn't, and leaked an
   unrelated person's address into the admin's own profile card.
2. The Overview tab's "Stocks Graph" card rendered two completely fabricated
   Chart.js datasets ("Metrics Curve 1/2" with arbitrary numbers unrelated
   to any real metric) plus a meaningless "4.5 Points" badge, and had two
   literal English typos in the month labels ("Agu", "Otc"). Replaced with
   a real 12-month trend computed from AIUsageLog/User signups.
3. The "Security Alerts & Activity" toggle in Settings was hardcoded
   `checked` with no backing setting and no `name` attribute -- looked like
   a saved, active preference but did nothing. Now disabled and labeled as
   not wired up yet, instead of silently lying about its own state.
4. The "SSL Protected" security badge was a static label always shown as
   true regardless of the actual connection. Now derived from
   request.is_secure().
5. The admin's own "Current Company" fallback showed Baseera's own company
   name ("شركة منصة بصيرة الذكية") when the admin's profile had none set --
   looked like real company data. Now shows a neutral "not set" label.

These tests cover what's server-verifiable: the leaked email is gone, the
real trend numbers reach the page, the fake dataset labels/typos are gone,
the toggle no longer claims to be active, and the security badge follows
the request's actual scheme.
"""
import json
import re

from django.test import TestCase, Client
from django.contrib.auth.models import User

from dashboard.models import Profile, AIUsageLog


class AdminDashboardDynamicContentTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin_review_test", email="", password="AdminPass123!"
        )
        Profile.objects.create(user=self.admin, company_name="", project_type="retail", phone_number="96891112222")
        self.client.login(username="admin_review_test", password="AdminPass123!")

    def test_no_hardcoded_stranger_email_fallback(self):
        html = self.client.get("/admin-dashboard/").content.decode("utf-8")
        self.assertNotIn("hasansajjadux", html)

    def test_no_fabricated_chart_data_or_labels(self):
        html = self.client.get("/admin-dashboard/").content.decode("utf-8")
        for leftover in ["Metrics Curve 1", "Metrics Curve 2", "4.5 Points", "'Agu'", "'Otc'"]:
            self.assertNotIn(leftover, html)

    def test_real_trend_counts_reach_the_page(self):
        other_user = User.objects.create_user(username="trend_probe_user", password="pw123456")
        Profile.objects.create(user=other_user, company_name="X", project_type="retail", phone_number="96891119999")
        AIUsageLog.objects.create(user=other_user, query="test query")
        AIUsageLog.objects.create(user=other_user, query="another query")

        html = self.client.get("/admin-dashboard/").content.decode("utf-8")
        # The two AIUsageLog rows just created fall in the current month, the
        # last bucket of the 12-month series -- its data array must end in 2,
        # not an arbitrary hardcoded number.
        match = re.search(
            r"label: isAr \? 'استعلامات الذكاء الاصطناعي' : 'AI Queries',\s*data: (\[[^\]]*\])",
            html,
        )
        self.assertIsNotNone(match, "AI usage trend dataset not found in rendered chart script")
        data = json.loads(match.group(1))
        self.assertEqual(len(data), 12)
        self.assertEqual(data[-1], 2)

    def test_security_alerts_toggle_no_longer_claims_to_be_active(self):
        html = self.client.get("/admin-dashboard/").content.decode("utf-8")
        # The old markup rendered `<input type="checkbox" checked class="sr-only peer">`
        # unconditionally. It must now be disabled instead of silently "on".
        self.assertNotIn('type="checkbox" checked class="sr-only peer"', html)
        self.assertIn('type="checkbox" disabled class="sr-only peer"', html)

    def test_security_badge_reflects_actual_request_scheme_insecure(self):
        html = self.client.get("/admin-dashboard/").content.decode("utf-8")
        self.assertIn("بدون تشفير", html)
        self.assertNotIn("حماية عالية (SSL)", html)

    def test_security_badge_reflects_actual_request_scheme_secure(self):
        html = self.client.get("/admin-dashboard/", secure=True).content.decode("utf-8")
        self.assertIn("حماية عالية (SSL)", html)

    def test_unset_company_shows_neutral_label_not_baseeras_own_name(self):
        html = self.client.get("/admin-dashboard/").content.decode("utf-8")
        self.assertNotIn("شركة منصة بصيرة الذكية", html)
        self.assertIn("غير محدد", html)

    def test_admin_settings_redirect_no_longer_404s(self):
        # admin_settings used to redirect to the never-routed "/super-admin/"
        # path, which 404'd every time. It must now land on the real,
        # existing admin dashboard URL.
        response = self.client.get("/admin-settings/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/admin-dashboard/?tab=settings")
        follow_up = self.client.get(response.url)
        self.assertEqual(follow_up.status_code, 200)
