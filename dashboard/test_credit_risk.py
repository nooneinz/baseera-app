"""
Early warning before selling on credit: deterministic signals, the pre-sale
check, the number guard on the worded alert, and the tenant-scoped endpoints.
"""
import json
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from dashboard.models import CreditWatchlistEntry, DynamicRecord
from dashboard.services.credit_risk_analyzer import (
    check_customer,
    compute_credit_risk_signals,
    normalize_name,
    phrase_credit_alert,
)


def _row(customer, amount, date, terms=30, late=None):
    r = {"اسم العميل": customer, "قيمة الفاتورة": amount, "تاريخ الفاتورة": date, "مدة الآجل": terms}
    if late is not None:
        r["أيام التأخير"] = late
    return r


def _types(result, customer=None):
    return {s["type"] for s in result["signals"]
            if customer is None or normalize_name(s["customer"]) == normalize_name(customer)}


class ConcentrationTests(TestCase):
    def test_customer_above_share_is_flagged(self):
        rows = [_row("النور", 600, "2025-01-05"), _row("الأمل", 200, "2025-01-06"),
                _row("الريان", 100, "2025-01-07"), _row("الفجر", 100, "2025-01-08")]
        r = compute_credit_risk_signals(rows)
        self.assertTrue(r["analyzable"])
        self.assertIn("revenue_concentration", _types(r, "النور"))
        self.assertNotIn("revenue_concentration", _types(r, "الأمل"))

    def test_exactly_at_threshold_is_not_flagged(self):
        rows = [_row(n, 100, "2025-01-05") for n in ("أ", "ب", "ج", "د")]  # each exactly 25%
        self.assertNotIn("revenue_concentration", _types(compute_credit_risk_signals(rows)))

    def test_needs_minimum_customers(self):
        rows = [_row("النور", 900, "2025-01-05"), _row("الأمل", 100, "2025-01-06")]
        self.assertNotIn("revenue_concentration", _types(compute_credit_risk_signals(rows)))


class LatePaymentTests(TestCase):
    def test_rising_lateness_is_flagged(self):
        rows = [_row("النور", 100, f"2025-0{m}-01", late=d) for m, d in zip(range(1, 5), (0, 3, 12, 25))]
        r = compute_credit_risk_signals(rows)
        sig = next(s for s in r["signals"] if s["type"] == "late_payment_trend")
        self.assertEqual(sig["values"]["latest_late_days"], 25)
        self.assertEqual(sig["evidence_count"], 4)

    def test_late_but_not_rising_is_not_flagged(self):
        rows = [_row("النور", 100, f"2025-0{m}-01", late=20) for m in range(1, 5)]
        self.assertNotIn("late_payment_trend", _types(compute_credit_risk_signals(rows)))

    def test_lateness_from_paid_and_due_dates(self):
        rows = [{"العميل": "النور", "المبلغ": 100, "تاريخ الاستحقاق": due, "تاريخ السداد": paid}
                for due, paid in (("2025-01-01", "2025-01-01"), ("2025-02-01", "2025-02-04"),
                                  ("2025-03-01", "2025-03-15"), ("2025-04-01", "2025-04-30"))]
        r = compute_credit_risk_signals(rows)
        self.assertEqual(r["columns_used"]["due"], "تاريخ الاستحقاق")
        self.assertEqual(r["columns_used"]["paid"], "تاريخ السداد")
        self.assertIn("late_payment_trend", _types(r))


class SpikeAndTermsTests(TestCase):
    def test_new_customer_spike(self):
        rows = [_row("النور", 100, f"2025-0{m}-01") for m in range(1, 6)]
        rows += [_row("جديد", 100, "2025-05-01"), _row("جديد", 900, "2025-05-20")]
        r = compute_credit_risk_signals(rows)
        self.assertIn("order_spike", _types(r, "جديد"))
        self.assertNotIn("order_spike", _types(r, "النور"))

    def test_long_standing_customer_spike_is_not_flagged(self):
        rows = [_row("النور", 100, f"2024-{m:02d}-01") for m in range(1, 13)] + [_row("النور", 900, "2025-01-01")]
        rows += [_row("الأمل", 100, "2025-01-02")]
        self.assertNotIn("order_spike", _types(compute_credit_risk_signals(rows)))

    def test_unusual_terms(self):
        rows = [_row(n, 100, "2025-01-01", terms=30) for n in ("أ", "ب", "ج", "د")] + [_row("هـ", 100, "2025-01-02", terms=90)]
        r = compute_credit_risk_signals(rows)
        self.assertIn("unusual_terms", _types(r, "هـ"))
        self.assertEqual(len([s for s in r["signals"] if s["type"] == "unusual_terms"]), 1)


class WatchlistTests(TestCase):
    def test_name_normalisation_matches(self):
        self.assertEqual(normalize_name("مؤسسة  النور"), normalize_name("موسسه النور "))
        self.assertEqual(normalize_name("أحمد"), normalize_name("احمد"))

    def test_watchlist_signal_and_high_level(self):
        rows = [_row("مؤسسة النور", 100, "2025-01-01"), _row("الأمل", 100, "2025-01-02")]
        r = compute_credit_risk_signals(rows, watchlist=[{"customer_name": "موسسه النور", "reason": "شيك مرتجع"}])
        self.assertIn("user_watchlist", _types(r, "مؤسسة النور"))
        self.assertEqual(r["customers"][normalize_name("مؤسسة النور")]["risk_level"], "high")

    def test_watchlist_works_without_data(self):
        r = check_customer([], "عميل غير موجود", watchlist=[{"customer_name": "عميل غير موجود", "reason": ""}])
        self.assertEqual(r["risk_level"], "high")
        self.assertFalse(r["found_in_data"])


class CheckCustomerTests(TestCase):
    def setUp(self):
        self.rows = [_row(n, 100, "2025-01-01", terms=30) for n in ("أ", "ب", "ج", "د")] + [_row("النور", 100, "2025-01-05")]

    def test_clean_customer_is_low(self):
        r = check_customer(self.rows, "النور")
        self.assertEqual(r["risk_level"], "low")
        self.assertTrue(r["found_in_data"])

    def test_proposed_amount_and_terms(self):
        r = check_customer(self.rows, "النور", proposed_amount=700, proposed_terms_days=90)
        self.assertEqual({s["type"] for s in r["signals"]}, {"proposed_spike", "proposed_terms"})
        self.assertEqual(r["risk_level"], "high")

    def test_normal_proposal_adds_nothing(self):
        r = check_customer(self.rows, "النور", proposed_amount=120, proposed_terms_days=30)
        self.assertEqual(r["signals"], [])


class _FakeAI:
    def __init__(self, text):
        self.client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=text)))


class PhraseGuardTests(TestCase):
    def setUp(self):
        rows = [_row("أ", 100, "2025-01-01"), _row("ب", 100, "2025-01-01"), _row("النور", 100, "2025-01-05")]
        self.check = check_customer(rows, "النور", proposed_amount=700)

    def test_uses_model_text_when_numbers_match(self):
        out = phrase_credit_alert(self.check, _FakeAI("البيع المقترح 700 ر.ع أكبر من المعتاد، اطلب دفعة مقدّمة."))
        self.assertTrue(out["ai_used"])

    def test_invented_number_falls_back_to_template(self):
        out = phrase_credit_alert(self.check, _FakeAI("العميل متأخر 45 يوماً، اطلب دفعة مقدّمة."))
        self.assertFalse(out["ai_used"])
        self.assertIn("700", out["text"])

    def test_no_signals_never_calls_model(self):
        clean = check_customer([_row("النور", 100, "2025-01-05")], "النور")
        out = phrase_credit_alert(clean, _FakeAI("أي نص 999"))
        self.assertFalse(out["ai_used"])


@override_settings(SECURE_SSL_REDIRECT=False)
class CreditRiskEndpointTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user(username="owner_a", password="pw123456")
        self.b = User.objects.create_user(username="owner_b", password="pw123456")
        for n, amt in (("عميل أ", 800), ("الأمل", 100), ("الريان", 100)):
            DynamicRecord.objects.create(user=self.a, schema_hash="x", row_data=_row(n, amt, "2025-01-01"))
        DynamicRecord.objects.create(user=self.b, schema_hash="y", row_data=_row("سر المنافس", 5000, "2025-01-01"))

    def _post(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type="application/json")

    def test_login_required(self):
        self.assertEqual(self.client.get("/credit-risk/").status_code, 302)
        self.assertEqual(self._post("/api/credit-risk/check/", {"customer": "x"}).status_code, 302)

    def test_page_renders_scoped_to_user(self):
        self.client.force_login(self.a)
        res = self.client.get("/credit-risk/")
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn("عميل أ", body)
        self.assertNotIn("سر المنافس", body)

    def test_check_endpoint(self):
        self.client.force_login(self.a)
        d = self._post("/api/credit-risk/check/", {"customer": "عميل أ"}).json()
        self.assertEqual(d["status"], "success")
        self.assertIn("revenue_concentration", {s["type"] for s in d["signals"]})
        self.assertTrue(d["alert"])

    def test_check_does_not_see_other_tenant(self):
        self.client.force_login(self.a)
        d = self._post("/api/credit-risk/check/", {"customer": "سر المنافس"}).json()
        self.assertFalse(d["found_in_data"])

    def test_watchlist_add_duplicate_delete_isolation(self):
        self.client.force_login(self.a)
        d = self._post("/api/credit-risk/watchlist/", {"customer_name": "مؤسسة النور", "reason": "شيك مرتجع"}).json()
        self.assertEqual(d["status"], "success")
        dup = self._post("/api/credit-risk/watchlist/", {"customer_name": "موسسه النور"})
        self.assertEqual(dup.status_code, 400)
        entry_id = d["entry"]["id"]

        self.client.force_login(self.b)
        self.assertEqual(self._post(f"/api/credit-risk/watchlist/{entry_id}/delete/", {}).status_code, 404)
        self.assertEqual(self.client.get("/api/credit-risk/watchlist/").json()["watchlist"], [])

        self.client.force_login(self.a)
        self.assertEqual(self._post(f"/api/credit-risk/watchlist/{entry_id}/delete/", {}).status_code, 200)
        self.assertFalse(CreditWatchlistEntry.objects.exists())


class CreditAgentToolTests(TestCase):
    def test_tool_is_scoped_and_returns_signals(self):
        from dashboard.services.agent_tools import _check_credit_risk_tool, should_attempt_react
        a = User.objects.create_user(username="tool_a", password="pw123456")
        b = User.objects.create_user(username="tool_b", password="pw123456")
        for n, amt in (("عميل أ", 800), ("الأمل", 100), ("الريان", 100)):
            DynamicRecord.objects.create(user=a, schema_hash="x", row_data=_row(n, amt, "2025-01-01"))
        CreditWatchlistEntry.objects.create(user=b, customer_name="عميل أ", reason="قائمة مستخدم آخر")

        out = json.loads(_check_credit_risk_tool(a.id, "عميل أ"))
        self.assertEqual(out["risk_level"], "high")  # 80% share >= 1.6 x 25%
        titles = [s["title"] for s in out["signals"]]
        self.assertIn("تركّز الإيراد", titles)
        # Another tenant's watchlist entry for the same name never leaks in.
        self.assertNotIn("في قائمة المخاطر التي سجّلتها", titles)
        self.assertTrue(should_attempt_react("هل أبيع بالآجل لعميل أ؟"))
