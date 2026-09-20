"""
Archiving & document-management core for Baseera.

Turns Baseera's stored files and generated reports into a proper archive:

  * INTEGRITY  -- every archived document carries a SHA-256 fingerprint, so it
    can be proven unaltered since it entered the system.
  * LIFECYCLE  -- a document moves received -> (needs_review) -> verified ->
    archived, with the transition rule that ANYTHING read by AI (a photo of an
    invoice/receipt/ledger, OCR'd) starts as needs_review, because AI reading
    is ~85-95% accurate and a human must confirm it. Structured uploads
    (spreadsheets/CSV) skip straight past review.
  * HUMAN-IN-THE-LOOP -- human_verify() records who confirmed a document and
    when, closing the gap the AI's imperfect accuracy leaves.
  * AUDIT TRAIL -- every event writes an append-only DocumentAuditEntry (never
    updated, never deleted): the who/what/when auditors expect.
  * TRACEABILITY -- issue_report() stamps a report with its own hash and links
    it to the exact source documents it was computed from.

Deterministic and side-effect-only: never calls an AI model, never raises into
the caller's happy path for a non-critical step.
"""
import hashlib
import logging

logger = logging.getLogger(__name__)

# Document types that come from AI reading (vision OCR) and therefore need a
# human to confirm before they count as trusted. Everything else (structured
# spreadsheets/CSV) is read deterministically and needs no review.
_AI_READ_TYPES = {"invoice", "receipt", "check", "handwritten_ledger", "manual_note"}


def sha256_hex(data):
    """SHA-256 hex digest of bytes (or a str, encoded utf-8). '' for empty."""
    if data is None:
        return ""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _audit(user, project_file, action, detail="", content_hash=""):
    """Append one immutable audit row. Never raises."""
    try:
        from dashboard.models import DocumentAuditEntry
        DocumentAuditEntry.objects.create(
            user=user if getattr(user, "id", None) else None,
            project_file=project_file,
            action=action,
            detail=(detail or "")[:2000],
            content_hash=content_hash or "",
        )
    except Exception as e:
        logger.info("Audit write failed (%s): %s", action, e)


def initial_status_for(document_type):
    """Documents read by AI start needing human review; the rest are received."""
    return "needs_review" if (document_type or "") in _AI_READ_TYPES else "received"


def stamp_document(project_file, raw_bytes=None, user=None):
    """
    Fingerprint a freshly-created ProjectFile and set its opening lifecycle
    state, then record it in the audit trail. `raw_bytes` is the uploaded
    content; if omitted it is read back from the stored file. Returns the
    project_file. Never raises.
    """
    try:
        if raw_bytes is None:
            raw_bytes = _read_file_bytes(project_file)
        digest = sha256_hex(raw_bytes) if raw_bytes is not None else ""
        status = initial_status_for(getattr(project_file, "document_type", None))
        project_file.content_hash = digest
        project_file.archive_status = status
        project_file.save(update_fields=["content_hash", "archive_status"])
        _audit(user or getattr(project_file, "user", None), project_file, "received",
               detail=f"استُلمت الوثيقة ({project_file.get_archive_status_display()}).",
               content_hash=digest)
        if status == "needs_review":
            _audit(user or getattr(project_file, "user", None), project_file, "needs_review",
                   detail="قراءة آلية (OCR) — بحاجة لتأكيد بشري قبل الاعتماد.", content_hash=digest)
    except Exception as e:
        logger.info("stamp_document failed for file #%s: %s", getattr(project_file, "id", "?"), e)
    return project_file


def _read_file_bytes(project_file):
    try:
        f = project_file.excel_file
        f.open("rb")
        try:
            return f.read()
        finally:
            f.close()
    except Exception:
        return None


def verify_integrity(project_file, raw_bytes=None):
    """
    Recompute the fingerprint of the stored file and compare it to the one
    recorded at archive time. Returns True if they match (or nothing to
    compare), False on a real mismatch. Writes an audit row either way.
    """
    stored = getattr(project_file, "content_hash", "") or ""
    if not stored:
        return True  # legacy row, nothing to verify against
    if raw_bytes is None:
        raw_bytes = _read_file_bytes(project_file)
    current = sha256_hex(raw_bytes) if raw_bytes is not None else ""
    ok = bool(current) and current == stored
    _audit(getattr(project_file, "user", None), project_file,
           "integrity_ok" if ok else "integrity_failed",
           detail="تطابقت البصمة." if ok else "عدم تطابق البصمة — الوثيقة تغيّرت أو تعذّرت قراءتها.",
           content_hash=current)
    return ok


def human_verify(project_file, user, note=""):
    """
    Record that a human has reviewed and confirmed a document (closing the
    AI-accuracy gap). Moves it to 'verified' and stamps who/when. Returns the
    project_file.
    """
    from django.utils import timezone
    try:
        project_file.archive_status = "verified"
        project_file.verified_by = user if getattr(user, "id", None) else None
        project_file.verified_at = timezone.now()
        project_file.save(update_fields=["archive_status", "verified_by", "verified_at"])
        who = getattr(user, "username", "مستخدم")
        _audit(user, project_file, "human_verified",
               detail=f"راجعها وأكّدها {who}." + (f" ملاحظة: {note}" if note else ""),
               content_hash=getattr(project_file, "content_hash", ""))
    except Exception as e:
        logger.info("human_verify failed for file #%s: %s", getattr(project_file, "id", "?"), e)
    return project_file


def archive_document(project_file, user=None):
    """Move a document to its final 'archived' state (retained, immutable)."""
    from django.utils import timezone
    try:
        project_file.archive_status = "archived"
        project_file.archived_at = timezone.now()
        project_file.save(update_fields=["archive_status", "archived_at"])
        _audit(user or getattr(project_file, "user", None), project_file, "archived",
               detail="أُرشفت الوثيقة.", content_hash=getattr(project_file, "content_hash", ""))
    except Exception as e:
        logger.info("archive_document failed for file #%s: %s", getattr(project_file, "id", "?"), e)
    return project_file


def issue_report(user, title, body, source_files=None):
    """
    Persist a report issued inside the system: stamped with its own SHA-256 for
    integrity and linked to the source documents it was computed from
    (traceability). Writes a report_issued audit row. Returns the
    FinancialReport, or None on failure.
    """
    from dashboard.models import FinancialReport
    try:
        digest = sha256_hex(f"{title}\n{body}")
        report = FinancialReport.objects.create(
            user=user, title=title[:255], body=body, content_hash=digest, status="issued",
        )
        files = list(source_files or [])
        if files:
            report.source_files.set(files)
        for pf in files:
            _audit(user, pf, "report_issued",
                   detail=f"صدر تقرير «{title[:120]}» بالاعتماد على هذه الوثيقة.", content_hash=digest)
        if not files:
            _audit(user, None, "report_issued", detail=f"صدر تقرير «{title[:120]}».", content_hash=digest)
        return report
    except Exception as e:
        logger.info("issue_report failed: %s", e)
        return None


def audit_trail(project_file):
    """The append-only audit rows for a document, newest first."""
    from dashboard.models import DocumentAuditEntry
    return DocumentAuditEntry.objects.filter(project_file=project_file)
