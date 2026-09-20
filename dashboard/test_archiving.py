"""
Tests for the archiving & document-management core: integrity fingerprinting,
lifecycle (received / needs_review / verified / archived), human-in-the-loop
verification, the append-only audit trail, and report traceability.
"""
import json

from django.test import TestCase, override_settings
from django.contrib.auth.models import User

from dashboard.models import Profile, ProjectFile, DocumentAuditEntry, FinancialReport
from dashboard.services import archiving


def _pf(user, document_type=None):
    return ProjectFile.objects.create(
        user=user, excel_file="excel_files/x.csv", document_type=document_type,
    )


class ArchivingServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="arch_u", password="pw123456")

    def test_sha256_is_stable_and_detects_change(self):
        a = archiving.sha256_hex(b"hello")
        self.assertEqual(a, archiving.sha256_hex("hello"))  # str == bytes
        self.assertNotEqual(a, archiving.sha256_hex(b"hello!"))
        self.assertEqual(len(a), 64)

    def test_stamp_fingerprints_and_opens_lifecycle_and_audits(self):
        pf = _pf(self.user, document_type="spreadsheet")
        archiving.stamp_document(pf, raw_bytes=b"col1,col2\n1,2\n", user=self.user)
        pf.refresh_from_db()
        self.assertEqual(len(pf.content_hash), 64)
        self.assertEqual(pf.archive_status, "received")  # structured -> no review
        self.assertTrue(DocumentAuditEntry.objects.filter(project_file=pf, action="received").exists())

    def test_ai_read_document_starts_in_needs_review(self):
        pf = _pf(self.user, document_type="invoice")  # OCR-read
        archiving.stamp_document(pf, raw_bytes=b"\xff\xd8\xff jpeg-ish", user=self.user)
        pf.refresh_from_db()
        self.assertEqual(pf.archive_status, "needs_review")
        self.assertTrue(DocumentAuditEntry.objects.filter(project_file=pf, action="needs_review").exists())

    def test_human_verify_records_who_and_when(self):
        pf = _pf(self.user, document_type="receipt")
        archiving.stamp_document(pf, raw_bytes=b"receipt", user=self.user)
        archiving.human_verify(pf, self.user, note="راجعت الأرقام")
        pf.refresh_from_db()
        self.assertEqual(pf.archive_status, "verified")
        self.assertEqual(pf.verified_by, self.user)
        self.assertIsNotNone(pf.verified_at)
        self.assertTrue(DocumentAuditEntry.objects.filter(project_file=pf, action="human_verified").exists())

    def test_integrity_detects_tampering(self):
        pf = _pf(self.user)
        archiving.stamp_document(pf, raw_bytes=b"original bytes", user=self.user)
        self.assertTrue(archiving.verify_integrity(pf, raw_bytes=b"original bytes"))
        self.assertFalse(archiving.verify_integrity(pf, raw_bytes=b"TAMPERED"))
        self.assertTrue(DocumentAuditEntry.objects.filter(project_file=pf, action="integrity_failed").exists())

    def test_issue_report_stamps_and_links_sources(self):
        pf1 = _pf(self.user)
        pf2 = _pf(self.user)
        report = archiving.issue_report(self.user, "تقرير الربع الأول", "المحتوى الكامل للتقرير",
                                        source_files=[pf1, pf2])
        self.assertIsInstance(report, FinancialReport)
        self.assertEqual(len(report.content_hash), 64)
        self.assertEqual(set(report.source_files.values_list("id", flat=True)), {pf1.id, pf2.id})
        # Traceability: each source document shows the report in its audit trail.
        self.assertTrue(DocumentAuditEntry.objects.filter(project_file=pf1, action="report_issued").exists())

    def test_archive_document_finalizes(self):
        pf = _pf(self.user)
        archiving.stamp_document(pf, raw_bytes=b"x", user=self.user)
        archiving.archive_document(pf, user=self.user)
        pf.refresh_from_db()
        self.assertEqual(pf.archive_status, "archived")
        self.assertIsNotNone(pf.archived_at)


@override_settings(SECURE_SSL_REDIRECT=False)
class ArchivingEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="arch_ep", password="pw123456")
        Profile.objects.create(user=self.user, phone_number="90000001")

    def test_verify_endpoint_confirms_own_document_only(self):
        pf = _pf(self.user, document_type="invoice")
        archiving.stamp_document(pf, raw_bytes=b"img", user=self.user)
        self.client.force_login(self.user)
        res = self.client.post("/api/documents/verify/",
                               data=json.dumps({"document_id": pf.id, "note": "ok"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["archive_status"], "verified")

    def test_verify_endpoint_rejects_another_users_document(self):
        other = User.objects.create_user(username="intruder", password="pw123456")
        pf = _pf(other, document_type="invoice")
        self.client.force_login(self.user)
        res = self.client.post("/api/documents/verify/",
                               data=json.dumps({"document_id": pf.id}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 404)

    def test_audit_endpoint_returns_trail(self):
        pf = _pf(self.user, document_type="spreadsheet")
        archiving.stamp_document(pf, raw_bytes=b"a,b\n1,2\n", user=self.user)
        self.client.force_login(self.user)
        res = self.client.get("/api/documents/audit/", {"document_id": pf.id})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(len(body["content_hash"]), 64)
        self.assertTrue(any(e["action"] == "received" for e in body["audit_trail"]))
