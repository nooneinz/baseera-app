"""
Regression tests for two reported bugs (with screenshots), both surfacing
the same underlying symptom: a message that should never require a file
lookup at all lands on the generic "couldn't find the document" refusal.

Bug 1: "كيفك" (how are you), sent on its own with no "هلا"/"مرحبا" opener in
front, was never recognized as small talk -- _SMALL_TALK_FILLERS was only
ever checked as a *suffix* after a greeting opener. With files already
uploaded, that skips the off-topic short-circuit in classify_route() (it
only fires when has_uploaded_files is False) and the bare filler falls all
the way through to ROUTE_SINGLE_FILE, finds nothing to match, and gets the
missing-document reply for what was never a real question.

Bug 2: "عطيني ارباح الشهر القادم" (give me next month's profits) -- the
plural "أرباح"/"ارباح" wasn't in BUSINESS_DOMAIN_KEYWORDS or
INFERRED_METRIC_TERMS, only the singular "ربح"/"الربح". The codebase
already lists both singular and plural for "مصروف"/"مصاريف"; this was a
missing plural variant for the same word family.
"""
from django.test import TestCase

from dashboard.services import orchestrator


class StandaloneSmallTalkTests(TestCase):
    def test_bare_filler_with_no_opener_is_recognized_as_small_talk(self):
        for filler in ["كيفك", "شخبارك", "شلونك", "كيف حالك"]:
            self.assertTrue(orchestrator._is_pure_greeting(filler), filler)

    def test_the_exact_reported_message_routes_to_greeting_even_with_files_uploaded(self):
        # This is the critical part of the bug: has_uploaded_files=True is
        # what made the old off-topic short-circuit unreachable.
        route = orchestrator.classify_route("كيفك", has_uploaded_files=True)
        self.assertEqual(route, orchestrator.ROUTE_GREETING)

    def test_route_message_end_to_end_replies_with_a_greeting_not_a_refusal(self):
        result = orchestrator.route_message(user_id=None, message="كيفك", lang="ar")
        self.assertEqual(result["route"], orchestrator.ROUTE_GREETING)
        self.assertEqual(result["direct_reply"], orchestrator.greeting_reply("ar"))

    def test_a_filler_word_with_a_real_request_attached_is_not_pure_small_talk(self):
        # Regression guard: this fix must not swallow a real question that
        # merely contains a filler-like word.
        self.assertFalse(orchestrator._is_pure_greeting("كيفك احتاج تقرير المبيعات"))

    def test_filler_after_an_opener_still_works_as_before(self):
        self.assertTrue(orchestrator._is_pure_greeting("هلا كيفك"))


class PluralProfitTermTests(TestCase):
    def test_business_signal_matches_the_plural_form(self):
        self.assertTrue(orchestrator._has_business_domain_signal("عطيني ارباح الشهر القادم"))
        self.assertTrue(orchestrator._has_business_domain_signal("الأرباح ارتفعت هذا الشهر"))

    def test_inferred_metric_terms_also_matches_the_plural_form(self):
        import re
        patterns = [re.escape(t) for t in orchestrator.INFERRED_METRIC_TERMS]
        self.assertTrue(orchestrator._matches_any(patterns, "عطيني ارباح الشهر القادم"))

    def test_the_exact_reported_message_no_longer_falls_through_as_off_topic(self):
        # Business signal now present -> classify_route's off-topic branches
        # (which require "not has_business_signal") are unreachable for it.
        route = orchestrator.classify_route("عطيني ارباح الشهر القادم", has_uploaded_files=True)
        self.assertNotEqual(route, orchestrator.ROUTE_OFF_TOPIC)

    def test_singular_form_is_unaffected(self):
        self.assertTrue(orchestrator._has_business_domain_signal("وش الربح هذا الشهر؟"))
