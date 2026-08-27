"""
Regression test for a reported bug (with screenshot): "اهلاً" (a plain
greeting, with a trailing tanwin fathatan diacritic) fell through to the
"couldn't find the document" refusal instead of the direct greeting reply.

Root cause: _is_pure_greeting() compared the raw message text directly
against GREETING_OPENERS without ever normalizing it (unlike every other
keyword-list comparison in this module, which goes through
_normalize_arabic_letters()). A diacritic anywhere in the message -- most
commonly the tanwin fathatan Saudi users type at the end of "اهلاً"/"مرحباً"
-- broke the exact-string match, so the message fell all the way through to
ROUTE_SINGLE_FILE, found no matching file, and got the generic "couldn't
find the document" reply on what was just a hello.
"""
from django.test import TestCase

from dashboard.services import orchestrator


class ArabicDiacriticsNormalizationTests(TestCase):
    def test_the_exact_reported_greeting_is_recognized_with_diacritic(self):
        self.assertTrue(orchestrator._is_pure_greeting("اهلاً"))
        self.assertTrue(orchestrator._is_pure_greeting("مرحباً"))

    def test_the_exact_reported_message_routes_to_greeting_not_single_file(self):
        route = orchestrator.classify_route("اهلاً", has_uploaded_files=False)
        self.assertEqual(route, orchestrator.ROUTE_GREETING)

    def test_route_message_end_to_end_does_not_refuse_with_no_document_found(self):
        result = orchestrator.route_message(user_id=None, message="اهلاً", lang="ar")
        self.assertEqual(result["route"], orchestrator.ROUTE_GREETING)
        self.assertEqual(result["direct_reply"], orchestrator.greeting_reply("ar"))
        self.assertFalse(result["needs_confirmation"])

    def test_diacritics_are_also_stripped_in_keyword_matching(self):
        # A diacritic elsewhere in a business-domain message must not break
        # the same keyword checks that already tolerate the ة/ه typo.
        self.assertTrue(orchestrator._has_business_domain_signal("خُطّة استراتيجية للمستقبل"))
        self.assertTrue(orchestrator._matches_any(["استراتيجية"], "خُطّة استراتيجيّة جديدة"))
