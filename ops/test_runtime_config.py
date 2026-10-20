"""本地配置夹具，不访问存储、数据库或网络。"""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from ops_runtime import Config, ConfigurationError, emit

ENV = {
    "DATABASE_URL": "fixture-dsn",
    "S3_ACCESS_KEY": "fixture-access",
    "S3_SECRET_KEY": "fixture-secret",
}


class RuntimeConfigTests(unittest.TestCase):
    def test_maintenance_effective_settings_use_cli_env_file_defaults(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ, {**ENV, "MAINTENANCE_INTERVAL_SECONDS": "180"}, clear=True
            ),
        ):
            path = Path(directory) / "config.json"
            path.write_text(
                '{"MAINTENANCE_INTERVAL_SECONDS":90,"MAINTENANCE_METRICS_PORT":0}'
            )
            config = Config.load(
                ["--config", str(path), "--set", "MAINTENANCE_INTERVAL_SECONDS=60"]
            )
            self.assertEqual(config["MAINTENANCE_INTERVAL_SECONDS"], 60)
            self.assertEqual(config["MAINTENANCE_METRICS_PORT"], 0)

    def test_encryption_flags_and_file_limits_remain_fail_closed(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV, clear=True),
        ):
            path = Path(directory) / "config.json"
            path.write_text('{"ENCRYPTION_MAX_OBJECTS":100}')
            config = Config.load(
                ["--config", str(path), "--max-objects", "20"], mode="encryption"
            )
            self.assertEqual(config["ENCRYPTION_MAX_OBJECTS"], 20)
            self.assertFalse(config.apply)
            with self.assertRaises(ConfigurationError):
                Config.load(["--apply"], mode="encryption")
            with self.assertRaises(ConfigurationError):
                Config.load(
                    ["--set", "DATABASE_URL=private-fixture"], mode="encryption"
                )

    def test_env_file_overrides_raw_secret_and_secret_change_does_not_change_hash(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV, clear=True),
        ):
            secret = Path(directory) / "secret"
            secret.write_text("first-fixture\n")
            os.environ["S3_SECRET_KEY_FILE"] = str(secret)
            first = Config.load([])
            secret.write_text("second-fixture\n")
            second = Config.load([])
            self.assertEqual(first["S3_SECRET_KEY"], "first-fixture")
            self.assertEqual(first.snapshot()[1], second.snapshot()[1])
            self.assertNotIn("fixture", json.dumps(first.snapshot()[0]))
            self.assertNotIn("http://", json.dumps(first.snapshot()[0]))

    def test_unknown_controls_duplicate_large_or_sensitive_json_are_rejected(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV, clear=True),
        ):
            path = Path(directory) / "config.json"
            for value in (
                '{"UNKNOWN":1}',
                '{"S3_SECRET_KEY":"fixture"}',
                '{"S3_BUCKET":"bad\\u0000bucket"}',
                '{"MAINTENANCE_INTERVAL_SECONDS":10,"MAINTENANCE_INTERVAL_SECONDS":20}',
                " " * 65537,
            ):
                path.write_text(value)
                with self.assertRaises(ConfigurationError):
                    Config.load(["--config", str(path)])

    def test_log_has_uniform_schema_and_drops_arbitrary_values(self):
        output = io.StringIO()
        with redirect_stdout(output):
            emit(
                "drop-maintenance",
                "0.2.3",
                "storage_reconcile_failed",
                stage="reconcile",
                error_code="STORAGE_OR_DATABASE_FAILURE",
                exception="private-fixture",
                url="https://fixture/?token=private",
            )
        record = json.loads(output.getvalue())
        self.assertTrue(
            {
                "time",
                "level",
                "service",
                "version",
                "request_id",
                "task_id",
                "attempt_id",
                "agent_id",
                "stage",
                "duration_ms",
                "error_code",
                "message",
            }
            <= record.keys()
        )
        self.assertNotIn("private", output.getvalue())

    def test_maintenance_boot_applies_resolved_endpoint_port_and_interval(self):
        import maintenance
        import logging

        event = Mock()
        event.is_set.side_effect = [False, True]
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {"yes": False}
        connector = Mock()
        connector.return_value.__enter__ = Mock(return_value=connection)
        connector.return_value.__exit__ = Mock(return_value=False)
        output = io.StringIO()
        with (
            patch.dict(os.environ, ENV, clear=True),
            patch.object(maintenance, "Minio") as client,
            patch.object(maintenance, "configure_lifecycle"),
            patch.object(maintenance, "SafeHTTPServer") as server,
            patch.object(maintenance.threading, "Event", return_value=event),
            patch.object(maintenance.threading, "Thread"),
            patch.object(maintenance.signal, "signal"),
            patch.object(maintenance.psycopg, "connect", connector),
            patch.object(logging.getLogger(), "handlers", []),
            redirect_stderr(output),
        ):
            self.assertEqual(
                maintenance.main(
                    [
                        "--set",
                        "S3_ENDPOINT=https://fixture-store:9001",
                        "--set",
                        "MAINTENANCE_METRICS_PORT=0",
                        "--set",
                        "MAINTENANCE_INTERVAL_SECONDS=60",
                    ]
                ),
                0,
            )
        self.assertEqual(client.call_args.args[0], "fixture-store:9001")
        self.assertTrue(client.call_args.kwargs["secure"])
        self.assertEqual(server.call_args.args[0], ("0.0.0.0", 0))
        event.wait.assert_called_once_with(60)
        self.assertNotIn("fixture-dsn", output.getvalue())
        self.assertNotIn("https://", output.getvalue())

    def test_migration_report_stdout_remains_one_json_and_logs_are_sanitized(self):
        import encrypt_existing
        import logging

        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, ENV, clear=True),
            patch.object(
                encrypt_existing,
                "Minio",
                side_effect=RuntimeError(
                    "private-fixture https://example/?token=secret"
                ),
            ),
            patch.object(logging.getLogger(), "handlers", []),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            self.assertEqual(encrypt_existing.main([]), 1)
        self.assertEqual(
            json.loads(output.getvalue())["error"], "STORAGE_OR_DATABASE_FAILURE"
        )
        self.assertNotIn("private-fixture", errors.getvalue())
        self.assertNotIn("https://", errors.getvalue())
        self.assertEqual(len(errors.getvalue().splitlines()), 2)

    def test_health_uses_configured_port_and_is_silent_without_credentials(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from ops_runtime import health

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ready\n")

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        output, errors = io.StringIO(), io.StringIO()
        try:
            with (
                patch.dict(os.environ, {}, clear=True),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                self.assertEqual(
                    health(
                        [
                            "--set",
                            "MAINTENANCE_METRICS_HOST=127.0.0.1",
                            "--set",
                            "MAINTENANCE_METRICS_PORT=" + str(server.server_port),
                        ]
                    ),
                    0,
                )
            self.assertFalse(output.getvalue() or errors.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
