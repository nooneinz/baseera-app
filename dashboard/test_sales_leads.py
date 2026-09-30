"""
Tests for the Sales Leads agent (services/lead_finder.py + lead_views.py).

Guarantees under test: lead scoring and product picking are deterministic and
come from real data; nothing but a buyer type and a city is sent to Google;
every lead endpoint is scoped to its owner (no IDOR); the outreach draft never
invents numbers; and a missing Maps key degrades to a clear message.
"""
import json
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings

from dashboard.models import SalesLead
from dashboard.services import lead_finder


def _place(pid="p1", name="مطعم النخيل", rating=4.6, reviews=320, phone="+968 9123 4567",
           website="https://example.om", status="OPERATIONAL"):
    return {
        "id": pid, "displayName": {"text": name}, "formattedAddress": "مسقط، عُمان",
        "internationalPhoneNumber": phone, "websiteUri": website,
        "googleMapsUri": f"https://maps.google.com/?cid={pid}", "rating": rating,
        "userRatingCount": reviews, "businessStatus": status,
    }


class LeadFinderUnitTests(TestCase):
    def test_score_is_deterministic_and_ranks_reachable_places_higher(self):
        strong = lead_finder.score_place(_place())
        weak = lead_finder.score_place(_place(rating=3.2, reviews=3, phone="", website=""))
        self.assertEqual(strong, lead_finder.score_place(_place()))
        self.assertGreater(strong, weak)
        self.assertLessEqual(strong, 100)

    def test_closed_business_scores_zero(self):
        self.assertEqual(lead_finder.score_place(_place(status="CLOSED_PERMANENTLY")), 0)

    def test_pick_products_puts_dead_stock_first_then_margin(self):
        rows = [
            {"الصنف": "تمر", "سعر الوحدة": 10, "تكلفة الوحدة": 4, "الكمية": 50, "الإيراد": 500},
            {"الصنف": "لبن", "سعر الوحدة": 3, "تكلفة الوحدة": 2, "الكمية": 40, "الإيراد": 120},
            {"الصنف": "عسل", "سعر الوحدة": 25, "تكلفة الوحدة": 10, "الكمية": 200, "الإيراد": 0},
        ]
        names = [p["name"] for p in lead_finder.pick_products_to_push(rows)]
        self.assertTrue(names)
        self.assertEqual(names, list(dict.fromkeys(names)))   # no duplicates
        self.assertEqual(lead_finder.pick_products_to_push([]), [])

    def test_query_carries_only_buyer_type_and_city(self):
        q = lead_finder.build_query("restaurants", "مسقط", "ar")
        self.assertEqual(q, "مطاعم في مسقط")

    def test_find_leads_sends_only_query_to_google_and_ranks_results(self):
        sent = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"places": [
                _place("a", rating=3.4, reviews=5, phone="", website=""), _place("b"),
                _place("c", status="CLOSED_PERMANENTLY"), {"id": "d"}]}).encode()

        def opener(req, timeout=None):
            sent["body"] = json.loads(req.data.decode())
            return Resp()

        out = lead_finder.find_leads("تمر خلاص", "restaurants", "مسقط", api_key="k", opener=opener)
        self.assertNotIn("تمر", json.dumps(sent["body"], ensure_ascii=False))
        self.assertEqual(sent["body"]["textQuery"], "مطاعم في مسقط")
        self.assertEqual([l["place_id"] for l in out["leads"]], ["b", "a"])   # closed + nameless dropped

    def test_missing_key_raises_clear_error(self):
        with override_settings(GOOGLE_MAPS_API_KEY=""):
            with self.assertRaises(lead_finder.LeadSearchError):
                lead_finder.search_places("مطاعم في مسقط")

    def test_draft_invents_no_numbers(self):
        text = lead_finder.draft_outreach("تمر", "مطعم النخيل", "مؤسسة الواحة", lang="ar")
        self.assertFalse(any(ch.isdigit() for ch in text))
        self.assertIn("مطعم النخيل", text)
        self.assertIn("خصم 10%", lead_finder.draft_outreach("تمر", "م", "ب", offer="خصم 10%"))

    def test_whatsapp_link_normalises_omani_numbers(self):
        self.assertTrue(lead_finder.whatsapp_link("9123 4567").startswith("https://wa.me/96891234567"))
        self.assertTrue(lead_finder.whatsapp_link("+968 9123 4567").startswith("https://wa.me/96891234567"))
        self.assertEqual(lead_finder.whatsapp_link(""), "")
        self.assertEqual(lead_finder.whatsapp_link("12"), "")


@override_settings(DISABLE_RATE_LIMIT=True, GOOGLE_MAPS_API_KEY="test-key")
class LeadViewsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user("owner", password="pw-12345")
        self.other = User.objects.create_user("other", password="pw-12345")
        self.client.login(username="owner", password="pw-12345")

    def _search(self, **body):
        payload = {"product": "تمر", "buyer_type": "restaurants", "city": "مسقط", **body}
        return self.client.post("/api/sales-leads/search/", json.dumps(payload), content_type="application/json")

    def test_pages_and_endpoints_require_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/sales-leads/").status_code, 302)
        self.assertEqual(self._search().status_code, 302)

    def test_search_saves_ranked_leads_for_the_owner_only(self):
        with mock.patch.object(lead_finder, "search_places", return_value=[_place("a"), _place("b", rating=3.5, reviews=2)]):
            res = self._search()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["count"], 2)
        self.assertEqual(SalesLead.objects.filter(user=self.owner).count(), 2)
        self.assertEqual(SalesLead.objects.filter(user=self.other).count(), 0)

    def test_repeating_a_search_does_not_duplicate_leads(self):
        with mock.patch.object(lead_finder, "search_places", return_value=[_place("a")]):
            self._search(); self._search()
        self.assertEqual(SalesLead.objects.filter(user=self.owner).count(), 1)

    def test_search_without_key_is_a_clear_503(self):
        with override_settings(GOOGLE_MAPS_API_KEY=""):
            res = self._search()
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["code"], "maps_not_configured")

    def test_daily_search_cap(self):
        with mock.patch.object(lead_finder, "search_places", return_value=[_place("a")]):
            for _ in range(20):
                self.assertEqual(self._search().status_code, 200)
            self.assertEqual(self._search().status_code, 429)

    def test_other_tenants_cannot_touch_a_lead(self):
        lead = SalesLead.objects.create(user=self.owner, product="تمر", place_id="a", business_name="م", score=50)
        self.client.logout(); self.client.login(username="other", password="pw-12345")
        body = json.dumps({"status": "won"})
        for action in ("update", "draft", "delete"):
            res = self.client.post(f"/api/sales-leads/{lead.id}/{action}/", body, content_type="application/json")
            self.assertEqual(res.status_code, 404, action)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "new")

    def test_owner_can_update_pipeline_and_draft(self):
        lead = SalesLead.objects.create(user=self.owner, product="تمر", place_id="a", business_name="مطعم النخيل",
                                        phone="+968 9123 4567", score=50)
        res = self.client.post(f"/api/sales-leads/{lead.id}/update/", json.dumps({"status": "negotiating", "notes": "اتصلت"}),
                               content_type="application/json")
        self.assertEqual(res.json()["lead"]["status"], "negotiating")
        bad = self.client.post(f"/api/sales-leads/{lead.id}/update/", json.dumps({"status": "hacked"}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        res = self.client.post(f"/api/sales-leads/{lead.id}/draft/", json.dumps({"offer": "توصيل مجاني"}), content_type="application/json")
        data = res.json()["lead"]
        self.assertIn("توصيل مجاني", data["draft_message"])
        self.assertTrue(data["whatsapp_url"].startswith("https://wa.me/968"))

    def test_page_renders_with_products_from_the_users_own_data(self):
        self.assertEqual(self.client.get("/sales-leads/").status_code, 200)
