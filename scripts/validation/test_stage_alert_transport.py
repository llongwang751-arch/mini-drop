"""本地假时钟和传输回归，不启动 Docker、HTTP 或数据库。"""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import stage_alert_transport as probe


INTERNAL_CONFIG = """global:
  resolve_timeout: 2m
route:
  receiver: drop-audit
  group_wait: 30s
  group_interval: 5m
receivers:
- name: drop-audit
  webhook_configs:
  - send_resolved: true
    http_config:
      authorization:
        type: Bearer
        credentials_file: /run/secrets/alert_webhook_token
      follow_redirects: true
    url: http://maintenance:9105/alerts
    url_file: ""
    max_alerts: 50
templates: []
"""


def counts(firing=0, resolved=0):
    return {
        "total": firing + resolved,
        "firing": firing,
        "resolved": resolved,
        "other": 0,
        "first_firing": 41 if firing else None,
        "first_resolved": 42 if resolved else None,
    }


class Clock:
    value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.value += duration

    def now(self):
        return datetime(2026, 10, 18, tzinfo=timezone.utc) + timedelta(
            seconds=self.value
        )


class Host:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.last = counts()
        self.names = []

    def preflight(self):
        pass

    def audit(self, name):
        self.names.append(name)
        self.last = next(self.snapshots, self.last)
        return probe.parse_counts(json.dumps(self.last).encode())


class API:
    def __init__(self, existing=None):
        self.posts = []
        self.existing = existing or []

    def check_receivers(self):
        probe.validate_receivers(INTERNAL_CONFIG)

    def active(self, name):
        return self.existing

    def post(self, alert):
        self.posts.append(alert)


class AlertTransportTests(unittest.TestCase):
    def test_redacted_loaded_urls_require_separate_verified_reload_and_nested_name_is_metadata(self):
        loaded = INTERNAL_CONFIG.replace("- name: drop-audit", "- name: drop-audit\n  labels:\n    name: drop-audit").replace("url: http://maintenance:9105/alerts", "url: <secret>")
        with self.assertRaises(probe.Failure):
            probe.validate_receivers(loaded)
        probe.validate_receivers(loaded, verified_redacted_url=True)
        with self.assertRaises(probe.Failure):
            probe.validate_receivers(loaded.replace("webhook_configs:", "email_configs:"), verified_redacted_url=True)

    def test_runtime_receiver_rejects_other_notification_paths(self):
        probe.validate_receivers(INTERNAL_CONFIG)
        for invalid in (
            INTERNAL_CONFIG.replace("    url: http://maintenance:9105/alerts\n", ""),
            INTERNAL_CONFIG.replace("maintenance:9105", "external.example"),
            INTERNAL_CONFIG.replace("send_resolved: true", "send_resolved: false"),
            INTERNAL_CONFIG.replace("webhook_configs:", "email_configs:"),
            INTERNAL_CONFIG.replace(
                "templates: []", "- name: another\n  slack_configs: []\ntemplates: []"
            ),
            INTERNAL_CONFIG.replace('url_file: ""', "url_file: /another-target"),
            INTERNAL_CONFIG.replace(
                "- name: drop-audit", "- <<: *unexpected\n  name: drop-audit"
            ),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(probe.Failure):
                probe.validate_receivers(invalid)

    def test_stage_container_and_loopback_binding_are_mandatory(self):
        valid = {
            "id": "a" * 64,
            "project": "dropfullstage",
            "service": "alertmanager",
            "running": True,
            "ports": {"9093/tcp": [{"HostIp": "127.0.0.1", "HostPort": "19093"}]},
        }
        self.assertEqual(probe.validate_container(valid, "alertmanager"), "a" * 64)
        for field, bad in (
            ("project", "drop"),
            ("running", False),
            ("ports", {"9093/tcp": [{"HostIp": "0.0.0.0", "HostPort": "19093"}]}),
            ("ports", {"9093/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9093"}]}),
        ):
            with self.subTest(field=field), self.assertRaises(probe.Failure):
                probe.validate_container(valid | {field: bad}, "alertmanager")

    def test_firing_then_same_identity_resolved_proves_only_transport(self):
        clock, host, api, proof = (
            Clock(),
            Host([counts(), counts(), counts(1), counts(1), counts(1, 1)]),
            API(),
            {},
        )
        with patch.object(probe.secrets, "token_hex", return_value="a" * 32):
            probe.exercise(host, api, proof, clock.monotonic, clock.sleep, clock.now)
        self.assertTrue(proof["success"])
        self.assertEqual(
            (proof["firing_audit_count"], proof["resolved_audit_count"]), (1, 1)
        )
        self.assertTrue(proof["firing_before_resolved"])
        self.assertEqual(len(api.posts), 2)
        self.assertEqual(api.posts[0]["labels"], api.posts[1]["labels"])
        self.assertEqual(api.posts[0]["startsAt"], api.posts[1]["startsAt"])
        self.assertGreater(api.posts[0]["endsAt"], api.posts[1]["endsAt"])
        self.assertEqual(set(host.names), {"ImportedStageTransportProbe_" + "a" * 32})
        self.assertNotIn("rules_triggered", proof)

    def test_reuse_is_rejected_before_any_post(self):
        for history, active in (([counts(1)], []), ([counts()], [{"labels": {}}])):
            api, proof, clock = API(active), {}, Clock()
            with (
                self.subTest(history=history),
                self.assertRaisesRegex(probe.Failure, "REUSED"),
            ):
                probe.exercise(
                    Host(history), api, proof, clock.monotonic, clock.sleep, clock.now
                )
            self.assertEqual(api.posts, [])

    def test_audit_query_is_fixed_read_only_and_names_are_bound(self):
        self.assertIn("BEGIN READ ONLY", probe.AUDIT_SQL)
        self.assertIn("statement_timeout", probe.AUDIT_SQL)
        self.assertIn("resource_id=:'alert_name'", probe.AUDIT_SQL)
        self.assertNotIn("DELETE", probe.AUDIT_SQL)
        self.assertNotIn("UPDATE", probe.AUDIT_SQL)
        for invalid in (
            "existing-alert",
            "ImportedStageTransportProbe_" + "a" * 31,
            "a';SELECT 1;--",
        ):
            with self.assertRaises(probe.Failure):
                probe.alert_name(invalid)
        for invalid in (
            counts() | {"total": True},
            counts(1) | {"first_firing": None},
            counts(1) | {"other": 1},
            counts(1) | {"total": 2},
            counts(1) | {"unexpected": "private-value"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(probe.Failure):
                probe.parse_counts(json.dumps(invalid).encode())

    def test_timeout_is_bounded_and_only_own_alert_is_resolved(self):
        clock, api, proof = Clock(), API(), {}
        with self.assertRaisesRegex(probe.Failure, "DEADLINE"):
            probe.exercise(
                Host([counts()]), api, proof, clock.monotonic, clock.sleep, clock.now
            )
        self.assertEqual(clock.value, 480)
        self.assertEqual(len(api.posts), 2)
        self.assertEqual(api.posts[0]["labels"], api.posts[1]["labels"])
        self.assertTrue(proof["cleanup_resolution_submitted"])

    def test_unknown_cli_and_exceptions_do_not_expose_private_values(self):
        for argv in (
            [
                "--confirm-stage",
                "dropfullstage",
                "--origin",
                "https://secret.example/?token=private",
            ],
            ["--confirm-stage", "drop"],
            ["--confirm-stage", "dropfullstage", "--run-id", "private"],
        ):
            output = io.StringIO()
            with redirect_stdout(output), patch.object(probe, "run") as run:
                self.assertEqual(probe.main(argv), 1)
            run.assert_not_called()
            self.assertEqual(
                json.loads(output.getvalue())["error_code"], "OPTIONS_INVALID"
            )
            self.assertNotIn("private", output.getvalue())
        output = io.StringIO()
        with (
            redirect_stdout(output),
            patch.object(
                probe,
                "run",
                side_effect=RuntimeError("https://secret/?password=private"),
            ),
        ):
            self.assertEqual(probe.main(["--confirm-stage", "dropfullstage"]), 1)
        self.assertEqual(
            json.loads(output.getvalue())["error_code"], "STAGE_ALERT_TRANSPORT_FAILED"
        )
        self.assertNotIn("private", output.getvalue())

    def test_command_output_limit_and_fixed_local_daemon(self):
        for payload, valid in (
            (b"bounded", True),
            (b"x" * (probe.MAX_OUTPUT + 1), False),
        ):
            process = SimpleNamespace(
                stdout=io.BytesIO(payload),
                returncode=0,
                wait=lambda timeout: None,
                kill=lambda: None,
            )
            with patch.object(probe.subprocess, "Popen", return_value=process) as spawn:
                if valid:
                    self.assertEqual(probe.docker(["controlled-fixture"]), payload)
                else:
                    with self.assertRaisesRegex(probe.Failure, "COMMAND_OUTPUT_LIMIT"):
                        probe.docker(["controlled-fixture"])
            self.assertEqual(
                spawn.call_args.kwargs["env"],
                {
                    "PATH": "/usr/bin:/bin",
                    "DOCKER_HOST": "unix:///var/run/docker.sock",
                },
            )
            self.assertEqual(spawn.call_args.kwargs["stderr"], probe.subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
