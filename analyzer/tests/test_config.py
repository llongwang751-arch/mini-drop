"""配置回归只使用导入夹具值，不使用真实密钥。"""

from contextlib import redirect_stdout
import io
import json
import os
import logging
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from drop_analyzer.config import Config


ENV = {
    "DATABASE_URL": "fixture-dsn",
    "S3_ACCESS_KEY": "fixture-access",
    "S3_SECRET_KEY": "fixture-secret",
}


class ConfigurationTests(unittest.TestCase):
    def test_cli_env_file_defaults_and_effective_metrics_are_used(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {**ENV, "LEASE_SECONDS": "90"}, clear=True),
        ):
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "LEASE_SECONDS": 120,
                        "RENEW_SECONDS": 15,
                        "ANALYZER_METRICS_PORT": 0,
                    }
                )
            )
            config = Config.load(["--config", str(path), "--set", "LEASE_SECONDS=180"])
            self.assertEqual(
                (config.lease_seconds, config.renew_seconds, config.metrics_port),
                (180, 15, 0),
            )
            self.assertEqual(config.analysis_download_bytes, 32 * 1024**2)

    def test_secret_file_precedence_and_snapshot_do_not_hash_secrets(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV, clear=True),
        ):
            secret = Path(directory) / "secret"
            secret.write_text("first-fixture-secret\n")
            os.environ["S3_SECRET_KEY_FILE"] = str(secret)
            config = Config.load([])
            snapshot, digest = config.snapshot()
            self.assertEqual(config.s3_secret_key, "first-fixture-secret")
            secret.write_text("second-fixture-secret\n")
            changed = Config.load([])
            self.assertEqual(changed.snapshot()[1], digest)
            encoded = json.dumps(snapshot)
            self.assertNotIn("fixture-secret", encoded)
            self.assertNotIn(str(secret), encoded)
            self.assertNotIn("minio:9000", encoded)
            self.assertNotEqual(
                Config.load(["--set", "POLL_SECONDS=3"]).snapshot()[1], digest
            )

    def test_unknown_secret_cli_large_file_control_and_nonfinite_are_refused(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV, clear=True),
        ):
            for assignment in (
                "UNKNOWN_OPTION=x",
                "S3_SECRET_KEY=private-fixture",
                "POLL_SECONDS=nan",
                "WORKER_ID=bad\nvalue",
            ):
                with self.subTest(assignment=assignment), self.assertRaises(ValueError):
                    Config.load(["--set", assignment])
            path = Path(directory) / "config.json"
            for content in (
                '{"UNKNOWN_OPTION":1}',
                '{"S3_SECRET_KEY":"private-fixture"}',
                '{"LEASE_SECONDS":60,"LEASE_SECONDS":90}',
                '{"WORKER_ID":"bad\\u0001value"}',
                " " * 65537,
            ):
                path.write_text(content)
                with self.subTest(content=content[:50]), self.assertRaises(ValueError):
                    Config.load(["--config", str(path)])

    def test_health_accepts_same_config_without_requiring_database_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            config = Config.load(
                ["--set", "HEALTH_FILE=/tmp/imported-health.json"],
                require_credentials=False,
            )
            self.assertEqual(config.health_file, "/tmp/imported-health.json")

    def test_telemetry_uses_effective_configuration_instead_of_reopening_env(self):
        from drop_analyzer import telemetry

        with patch.dict(
            os.environ, {**ENV, "ANALYZER_METRICS_PORT": "12345"}, clear=True
        ):
            config = Config.load(["--set", "ANALYZER_METRICS_PORT=0"])
            with patch.object(telemetry, "ThreadingHTTPServer") as server:
                telemetry.start(config)
            server.assert_not_called()

    def test_log_schema_and_unknown_fields_never_emit_urls_or_exception_values(self):
        from drop_analyzer.worker import log

        output = io.StringIO()
        with redirect_stdout(output):
            log(
                "analysis_failed",
                error_code="INVALID_INPUT",
                raw="private profile",
                url="https://fixture/?token=secret",
                error_type="ValueError",
            )
        record = json.loads(output.getvalue())
        required = {
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
        self.assertTrue(required <= record.keys())
        self.assertNotIn("private profile", output.getvalue())
        self.assertNotIn("https://", output.getvalue())

    def test_health_cli_uses_shared_effective_health_path(self):
        import time

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            path.write_text(json.dumps({"ready": True, "updated_at": time.time()}))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "drop_analyzer.health",
                    "--set",
                    "HEALTH_FILE=" + str(path),
                ],
                env={**os.environ, **ENV},
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0)
            self.assertFalse(result.stdout or result.stderr)

    def test_stdlib_library_logging_never_formats_secret_exception_or_url(self):
        from contextlib import redirect_stderr
        from drop_analyzer.runtime import install_logging

        errors = io.StringIO()
        with (
            patch.object(logging.getLogger(), "handlers", []),
            patch.object(logging.getLogger(), "level", logging.WARNING),
            redirect_stderr(errors),
        ):
            install_logging("drop-analyzer", "fixture")
            logging.getLogger("fixture.transport").error(
                "URL https://fixture/?token=%s", "private-fixture"
            )
        self.assertNotIn("private-fixture", errors.getvalue())
        self.assertNotIn("https://", errors.getvalue())
        self.assertEqual(
            json.loads(errors.getvalue())["component"], "fixture.transport"
        )

    def test_startup_log_snapshot_contains_every_effective_setting(self):
        from drop_analyzer.runtime import emit

        with patch.dict(os.environ, ENV, clear=True):
            visible, digest = Config.load([]).snapshot()
        output = io.StringIO()
        with redirect_stdout(output):
            emit(
                "drop-analyzer",
                "fixture",
                "configuration_loaded",
                effective_config=visible,
                config_hash=digest,
            )
        recorded = json.loads(output.getvalue())["effective_config"]
        self.assertEqual(set(recorded), set(visible))
        self.assertEqual(recorded["S3_SECRET_KEY"], "[redacted]")


if __name__ == "__main__":
    unittest.main()
