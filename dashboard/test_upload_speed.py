"""
Upload speed: process_excel_to_db() used to call the live Gemini API and
wait for the full response (generate_weekly_digest_for_user) INSIDE the
upload request itself, before returning success/failure to the caller --
so every file upload, regardless of which page it came from, blocked on a
real network round trip just to populate the "أهم المؤشرات" (top
indicators) on the Decision Platform, even though those numbers are only
ever AI-narrated commentary on top of the already-computed deterministic
signals (see waste_analyzer.py's docstring for the same two-stage
principle applied here).

These tests prove the digest generation now happens in the background --
process_excel_to_db() itself returns quickly regardless of how slow the
live model call is, and the digest work still genuinely happens (it is
deferred, not dropped).
"""
import io
import os
import threading
import time
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TransactionTestCase

from dashboard.models import ProjectFile
from dashboard.views import process_excel_to_db


def _write_real_csv(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("المنتج,السعر,الكمية\n")
        for i in range(20):
            f.write(f"منتج{i},{10 + i},{i + 1}\n")


class UploadDoesNotBlockOnTheLiveDigestCallTests(TransactionTestCase):
    """
    TransactionTestCase, not TestCase: the background thread started by
    process_excel_to_db() opens its own real database connection to write
    the digest, which deadlocks against TestCase's outer wrapping
    transaction on SQLite. TransactionTestCase commits normally instead,
    which is exactly what a real request/background-thread pair does in
    production.
    """
    def setUp(self):
        self.user = User.objects.create_user(username="upload_speed_user", password="pw123456")

        # ProjectFile.excel_file is upload_to="excel_files/", rooted at
        # MEDIA_ROOT -- write the fixture there so FileField.path resolves
        # to a real file instead of a media-relative name that doesn't
        # exist on disk.
        upload_dir = os.path.join(settings.MEDIA_ROOT, "excel_files")
        os.makedirs(upload_dir, exist_ok=True)
        file_name = f"upload_speed_test_{os.getpid()}.csv"
        self.file_path = os.path.join(upload_dir, file_name)
        _write_real_csv(self.file_path)

        self.project_file = ProjectFile.objects.create(
            user=self.user, excel_file=f"excel_files/{file_name}",
        )

    def tearDown(self):
        if os.path.exists(self.file_path):
            os.remove(self.file_path)

    def test_process_excel_to_db_returns_before_the_slow_live_call_finishes(self):
        call_started = threading.Event()
        call_finished = threading.Event()

        def slow_fake_digest(self_, sample_str, user):
            call_started.set()
            time.sleep(0.6)
            call_finished.set()

        with patch(
            "dashboard.services.ai_service.GeminiAIService.generate_weekly_digest_for_user",
            slow_fake_digest,
        ):
            started_at = time.monotonic()
            success, error = process_excel_to_db(self.project_file, self.user)
            elapsed = time.monotonic() - started_at

            self.assertTrue(success, error)
            # The mocked call takes 0.6s; if this were still synchronous
            # the function could not possibly return before that.
            self.assertLess(elapsed, 0.4)

            # The work is deferred, not dropped -- it does eventually
            # happen. Waited for here, still inside the patch, since the
            # background thread runs after process_excel_to_db already
            # returned.
            self.assertTrue(call_started.wait(timeout=2), "background digest generation was never invoked")
            call_finished.wait(timeout=2)
            self.assertTrue(call_finished.is_set())

    def test_the_digest_generation_still_actually_runs_in_the_background(self):
        from dashboard.models import WeeklyDigest

        done = threading.Event()

        def fake_digest(self_, sample_str, user):
            WeeklyDigest.objects.create(
                user=user, week_label="test", summary_text="ok",
                top_risks=[], top_opportunities=[], action_plan=[],
            )
            done.set()

        with patch(
            "dashboard.services.ai_service.GeminiAIService.generate_weekly_digest_for_user",
            fake_digest,
        ):
            success, error = process_excel_to_db(self.project_file, self.user)
            self.assertTrue(success, error)
            self.assertTrue(done.wait(timeout=2), "background digest never completed")
        self.assertTrue(WeeklyDigest.objects.filter(user=self.user).exists())
