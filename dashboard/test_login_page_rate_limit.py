"""
Production bug: /login/ and /register/ were rate-limited on EVERY request,
GET page-loads included, via two stacked security.rate_limit() decorators
(a 5/min and a 10/min counter, both keyed by client IP, both incremented on
every hit regardless of HTTP method). A handful of page loads -- a couple
of browser tabs, a couple of retries, or several people behind the same
office/mobile-carrier NAT (routine in Oman) -- was enough to burn through
the whole per-IP budget and get the entire shared IP locked out of the
LOGIN PAGE ITSELF, not just repeated login attempts, returning a raw
{"status": "error", "message": "Too many requests..."} JSON blob instead of
the page.

These tests prove: a GET to /login/ or /register/ is never rate-limited no
matter how many times it's requested, while a POST (an actual login/
register attempt -- the thing that actually needs brute-force protection)
still is.
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse


class LoginPageIsNeverRateLimitedTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_get_to_login_page_is_never_rate_limited_even_after_many_requests(self):
        url = reverse("login")
        # Old behavior capped the shared budget at 5/minute; hit it well
        # past that to prove GET is genuinely exempt now, not just under
        # the old ceiling.
        for _ in range(25):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotEqual(response.get("Content-Type", ""), "application/json")

    def test_get_to_register_page_is_never_rate_limited_even_after_many_requests(self):
        url = reverse("register")
        for _ in range(25):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotEqual(response.get("Content-Type", ""), "application/json")

    def test_post_login_attempts_are_still_rate_limited_past_the_cap(self):
        """
        The actual protection this exists for (brute-forcing the login
        form) must still work -- only the GET page-view path was ever the
        bug.
        """
        url = reverse("login")
        responses = [
            self.client.post(url, {"username": "nobody", "password": "wrong"})
            for _ in range(15)
        ]
        statuses = [r.status_code for r in responses]
        self.assertIn(429, statuses, "POST login attempts should still be rate-limited past the cap")

    def test_post_register_attempts_are_still_rate_limited_past_the_cap(self):
        url = reverse("register")
        responses = [
            self.client.post(url, {"username": f"flood_{i}", "password": "x"})
            for i in range(15)
        ]
        statuses = [r.status_code for r in responses]
        self.assertIn(429, statuses, "POST register attempts should still be rate-limited past the cap")

    def test_a_real_login_still_works_after_several_page_views(self):
        """
        End-to-end sanity: the exact real-world sequence a user actually
        hits -- load the login page a few times, then submit real,
        correct credentials -- must succeed.
        """
        User.objects.create_user(username="genuine_user", password="correct-password-1")
        url = reverse("login")
        for _ in range(8):
            self.client.get(url)
        response = self.client.post(url, {"username": "genuine_user", "password": "correct-password-1"}, follow=True)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
