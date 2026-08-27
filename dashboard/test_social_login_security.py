"""
Security fix: the demo social-login stand-in (no real Google/Facebook
OAuth verification -- it maps every visitor for a given provider onto one
shared account) used to set a real, hardcoded password ("social_password")
on that shared account. Since Django's normal username/password login form
isn't restricted to this flow, anyone who saw that string in the source
could log into the shared account directly, bypassing this view entirely.
"""
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.test import TestCase


class SocialLoginDummySecurityTests(TestCase):
    def test_visiting_the_social_login_url_still_logs_the_user_in(self):
        response = self.client.get("/social-login/google/", follow=True)
        self.assertTrue(response.context["user"].is_authenticated)
        self.assertEqual(response.context["user"].username, "google_user")

    def test_the_created_account_has_no_usable_password(self):
        self.client.get("/social-login/google/")
        user = User.objects.get(username="google_user")
        self.assertFalse(user.has_usable_password())

    def test_the_old_hardcoded_password_can_no_longer_authenticate_the_account(self):
        self.client.get("/social-login/facebook/")
        authenticated = authenticate(username="facebook_user", password="social_password")
        self.assertIsNone(authenticated)
