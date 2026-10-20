"""Imported host-counter fixtures; no database, S3 or production access."""
from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import maintenance


@unittest.skipUnless(hasattr(os, "statvfs") and hasattr(os, "mkfifo"), "Real Linux filesystem regression")
class MetricsBoundaryTests(unittest.TestCase):
    def test_unreadable_optional_file_keeps_real_http_metrics_available_and_logs_no_content(self):
        server = maintenance.SafeHTTPServer(("127.0.0.1", 0), maintenance.Metrics)
        server.config = {"MONITORING_STATUS_FILE": "/imported-fixture/status.prom"}
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        captured = io.StringIO()
        try:
            with redirect_stderr(captured), patch.object(maintenance.os, "open", side_effect=PermissionError("IMPORTED_SECRET_MUST_NOT_APPEAR")):
                with urlopen(f"http://127.0.0.1:{server.server_port}/metrics", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"drop_storage_reconcile_last_success", response.read(16384))
            self.assertNotIn("IMPORTED_SECRET", captured.getvalue())
            self.assertIn("MONITORING_STATUS_UNAVAILABLE", captured.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_only_bounded_valid_counter_lines_are_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "imported-status.prom"
            path.write_bytes(b"drop_backup_last_success 123\ndrop_backup_restore_duration_seconds 1.25\ndrop_backup_last_success 1.2.3\nIMPORTED_SECRET_MUST_NOT_APPEAR\n")
            self.assertEqual(maintenance.external_monitoring_metrics(path), b"drop_backup_last_success 123\ndrop_backup_restore_duration_seconds 1.25\n")
            path.write_bytes(b"drop_backup_last_success 123\n" + b"x" * 4097)
            self.assertEqual(maintenance.external_monitoring_metrics(path), b"")
            self.assertEqual(maintenance.external_monitoring_metrics(path.parent), b"")

    def test_missing_file_is_optional_and_a_fifo_cannot_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "imported-fifo"
            self.assertEqual(maintenance.external_monitoring_metrics(path), b"")
            os.mkfifo(path)
            self.assertEqual(maintenance.external_monitoring_metrics(path), b"")


if __name__ == "__main__":
    unittest.main()
