"""导入的合成 Docker/SQL 夹具，不访问 Docker 或网络。"""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "capture-release-evidence.py"
SPEC = importlib.util.spec_from_file_location("capture_release_evidence", MODULE)
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)

PROJECT = "drop-fixture"
CONTAINER = "a" * 64
IMAGE = "sha256:" + "b" * 64
SECRET = "IMPORTED_SECRET_MUST_NEVER_APPEAR"


def imported_container():
    return {
        "Id": CONTAINER,
        "Image": IMAGE,
        "Name": SECRET,
        "Config": {
            "User": "10001:10001",
            "Env": ["DATABASE_URL=postgres://root:" + SECRET],
            "Image": "drop-api:20261018.1",
            "Labels": {
                "com.docker.compose.project": PROJECT,
                "com.docker.compose.service": "api",
                "arbitrary": SECRET,
            },
            "Cmd": [SECRET],
        },
        "State": {
            "Status": "running",
            "OOMKilled": False,
            "StartedAt": "2026-10-18T02:00:00.123456789Z",
            "Error": SECRET,
            "Health": {
                "Status": "healthy",
                "Log": [{"Output": SECRET}],
            },
        },
        "HostConfig": {
            "CapAdd": ["PERFMON", "BPF"],
            "CapDrop": ["ALL"],
            "Privileged": False,
            "ReadonlyRootfs": True,
            "PidMode": "",
            "Memory": 268435456,
            "MemorySwap": 536870912,
            "NanoCpus": 500000000,
            "CpuQuota": 0,
            "CpuPeriod": 0,
            "PidsLimit": 128,
            "NetworkMode": PROJECT + "_control",
            "SecurityOpt": [
                "no-new-privileges:true",
                'seccomp={"imported":"' + SECRET + '"}',
            ],
            "Binds": [SECRET],
            "LogConfig": {"Config": {"address": SECRET}},
        },
        "Mounts": [
            {"Type": "volume", "Name": "drop-fixture_pg_data", "RW": True},
            {"Type": "bind", "Source": SECRET, "Destination": SECRET},
        ],
        "NetworkSettings": {
            "Ports": {
                "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}],
                "50051/tcp": None,
            },
            "Networks": {PROJECT + "_control": {"Aliases": [SECRET]}},
        },
    }


class CaptureTests(unittest.TestCase):
    def test_reader_bounds_real_child_output_and_hides_child_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(evidence, "MAX_OUTPUT_BYTES", 1024):
                with self.assertRaises(evidence.CaptureError) as raised:
                    evidence.run_read_only(
                        [sys.executable, "-c", "print('x'*2048)"], directory
                    )
                self.assertEqual(str(raised.exception), "OUTPUT_LIMIT")
            with self.assertRaises(evidence.CaptureError) as raised:
                evidence.run_read_only(
                    [
                        sys.executable,
                        "-c",
                        "import sys;sys.stderr.write('" + SECRET + "');sys.exit(1)",
                    ],
                    directory,
                )
            self.assertEqual(str(raised.exception), "READ_ONLY_COMMAND_FAILED")
            with patch.object(evidence, "COMMAND_TIMEOUT_SECONDS", 0.05):
                with self.assertRaises(evidence.CaptureError) as raised:
                    evidence.run_read_only(
                        [sys.executable, "-c", "import time;time.sleep(1)"], directory
                    )
                self.assertEqual(str(raised.exception), "READ_ONLY_COMMAND_TIMEOUT")

    def test_only_whitelisted_runtime_fields_are_output(self):
        result = evidence.sanitize_container(imported_container(), PROJECT, "api")
        rendered = json.dumps(result)
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn("Env", rendered)
        self.assertNotIn("Labels", rendered)
        self.assertEqual(result["image_id"], IMAGE)
        self.assertEqual(result["image_reference"], "drop-api:20261018.1")
        self.assertEqual(result["configured_user"], "10001:10001")
        self.assertEqual(result["health"], "healthy")
        self.assertEqual(result["pid_mode"], "private")
        self.assertEqual(
            result["named_volumes"],
            [{"name": "drop-fixture_pg_data", "read_only": False}],
        )
        self.assertEqual(result["published_ports"][0]["host_port"], 18080)
        self.assertEqual(result["limits"]["memory_bytes"], 268435456)

    def test_default_user_and_missing_health_are_explicit(self):
        container = imported_container()
        container["Config"]["User"] = ""
        del container["State"]["Health"]
        result = evidence.sanitize_container(container, PROJECT, "api")
        self.assertEqual(result["configured_user"], "0")
        self.assertEqual(result["health"], "not_configured")

    def test_unexpected_scope_and_secret_shaped_allowed_fields_fail_closed(self):
        paths = [
            (("Config", "Labels", "com.docker.compose.project"), "other-project"),
            (("Config", "Labels", "com.docker.compose.service"), "other-service"),
            (("Config", "User"), "root:" + SECRET),
            (("Config", "Image"), "registry.test/user:" + SECRET),
            (("State", "Health", "Status"), SECRET),
            (("State", "StartedAt"), SECRET),
            (("Image",), "registry.test/?token=" + SECRET),
            (("HostConfig", "CapAdd"), ["SECRET_CAPABILITY"]),
            (("HostConfig", "ReadonlyRootfs"), SECRET),
            (("HostConfig", "Memory"), True),
            (("HostConfig", "PidMode"), SECRET),
            (("HostConfig", "NetworkMode"), SECRET),
            (("State", "OOMKilled"), SECRET),
            (("RestartCount",), True),
        ]
        for keys, value in paths:
            with self.subTest(keys=keys):
                container = imported_container()
                node = container
                for key in keys[:-1]:
                    node = node[key]
                node[keys[-1]] = value
                with self.assertRaises(evidence.CaptureError) as raised:
                    evidence.sanitize_container(container, PROJECT, "api")
                self.assertNotIn(SECRET, str(raised.exception))

    def test_restart_oom_seccomp_and_network_are_safe_runtime_evidence(self):
        container = imported_container()
        container["RestartCount"] = 2
        container["State"]["OOMKilled"] = True
        container["HostConfig"]["NetworkMode"] = "container:" + CONTAINER
        container["NetworkSettings"]["Networks"] = {}
        result = evidence.sanitize_container(container, PROJECT, "api")
        self.assertEqual(result["restart_count"], 2)
        self.assertTrue(result["oom_killed"])
        self.assertTrue(result["security"]["no_new_privileges"])
        self.assertEqual(result["security"]["seccomp"]["mode"], "custom")
        self.assertRegex(
            result["security"]["seccomp"]["option_sha256"], r"^[a-f0-9]{64}$"
        )
        self.assertEqual(result["network"], {"mode": "container", "names": []})
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(CONTAINER, json.dumps(result["network"]))

    def test_unknown_network_or_security_option_cannot_leak_values(self):
        for field, value in (("network", {SECRET: {}}), ("security", [SECRET])):
            container = imported_container()
            if field == "network":
                container["NetworkSettings"]["Networks"] = value
            else:
                container["HostConfig"]["SecurityOpt"] = value
            with self.assertRaises(evidence.CaptureError) as raised:
                evidence.sanitize_container(container, PROJECT, "api")
            self.assertNotIn(SECRET, str(raised.exception))

    def test_container_pid_target_is_classified_without_echoing_target(self):
        container = imported_container()
        container["HostConfig"]["PidMode"] = "container:" + CONTAINER
        result = evidence.sanitize_container(container, PROJECT, "api")
        self.assertEqual(result["pid_mode"], "container")

    def test_invalid_mount_or_port_cannot_smuggle_url(self):
        for bad in ("https://user:" + SECRET + "@host/path", "x\n" + SECRET):
            container = imported_container()
            container["Mounts"][0]["Name"] = bad
            with self.assertRaises(evidence.CaptureError):
                evidence.sanitize_container(container, PROJECT, "api")
            container = imported_container()
            container["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostIp"] = bad
            with self.assertRaises(evidence.CaptureError):
                evidence.sanitize_container(container, PROJECT, "api")

    def test_public_url_accepts_only_canonical_origin(self):
        for value in (
            "http://120.24.187.205",
            "https://example.test",
            "http://127.0.0.1:18080",
            "https://[::1]:443",
        ):
            self.assertTrue(evidence.validate_public_url(value))
        for value in (
            "http://user:" + SECRET + "@host",
            "https://host/?token=" + SECRET,
            "https://host/#" + SECRET,
            "https://host/path",
            "file:///etc/passwd",
            "https://host\n",
        ):
            with self.subTest(value=value):
                with self.assertRaises(evidence.CaptureError) as raised:
                    evidence.validate_public_url(value)
                self.assertNotIn(SECRET, str(raised.exception))

    def test_compose_ps_accepts_array_and_json_lines_but_not_duplicates(self):
        record = {"ID": CONTAINER, "Service": "api", "Command": SECRET}
        expected = [(CONTAINER, "api")]
        self.assertEqual(
            evidence.parse_compose_ps(json.dumps([record]), {"api"}), expected
        )
        self.assertEqual(
            evidence.parse_compose_ps(json.dumps(record) + "\n", {"api"}), expected
        )
        with self.assertRaises(evidence.CaptureError):
            evidence.parse_compose_ps(json.dumps([record, record]), {"api"})

    def test_sql_queries_are_read_only_and_table_names_are_fixed_allowlist(self):
        query = evidence.counts_query(["tasks", "artifacts"])
        self.assertIn("BEGIN TRANSACTION READ ONLY", query)
        self.assertIn("statement_timeout", query)
        self.assertIn("count(*) FROM public.tasks", query)
        self.assertNotIn(SECRET, query)
        with self.assertRaises(evidence.CaptureError):
            evidence.counts_query(["tasks; DROP TABLE users"])

    def test_database_shape_drops_unknown_fields_and_rejects_non_integer_counts(self):
        result = evidence.sanitize_database(
            {"schema_version": 5, "table_counts": {"tasks": 3}, "secret": SECRET},
            ["tasks"],
        )
        self.assertEqual(result, {"schema_version": 5, "table_counts": {"tasks": 3}})
        for counts in ({"tasks": True}, {"tasks": SECRET}, {"arbitrary": 1}):
            with self.assertRaises(evidence.CaptureError):
                evidence.sanitize_database(
                    {"schema_version": 5, "table_counts": counts}, ["tasks"]
                )

    def test_collection_invokes_only_existing_compose_and_inspect(self):
        commands = []

        def runner(argv, cwd, stdin=None):
            commands.append((argv, stdin))
            if argv[-2:] == ["config", "--services"]:
                return "api\npostgres\n"
            if argv[-6:] == [
                "ps",
                "--all",
                "--no-trunc",
                "--orphans=false",
                "--format",
                "json",
            ]:
                return json.dumps([{"ID": CONTAINER, "Service": "api"}])
            if argv[:3] == ["docker", "inspect", "--type=container"]:
                return json.dumps([imported_container()])
            self.fail("Unexpected command")

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "compose.yml").write_text("services: {}\n")
            report = evidence.capture(
                Path(directory),
                PROJECT,
                "20261018.1",
                "http://127.0.0.1",
                False,
                runner,
            )
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertEqual(len(commands), 3)
        self.assertEqual(report["missing_services"], ["postgres"])
        self.assertFalse(report["database"]["captured"])
        self.assertTrue(
            all(
                not set(argv)
                & {
                    "up",
                    "run",
                    "start",
                    "stop",
                    "restart",
                    "down",
                    "logs",
                    "config --format json",
                }
                for argv, _ in commands
            )
        )

    def test_database_capture_uses_secret_file_and_fixed_read_only_queries(self):
        container = imported_container()
        container["Config"]["Labels"]["com.docker.compose.service"] = "postgres"
        queries = []

        def runner(argv, cwd, stdin=None):
            if argv[-2:] == ["config", "--services"]:
                return "postgres\n"
            if "ps" in argv:
                return json.dumps([{"ID": CONTAINER, "Service": "postgres"}])
            if "inspect" in argv:
                return json.dumps([container])
            self.assertEqual(argv[-5:-1], ["-T", "postgres", "sh", "-ec"])
            self.assertEqual(argv[-1], evidence.POSTGRES_COMMAND)
            self.assertIn("/run/secrets/database_password", argv[-1])
            self.assertNotIn(SECRET, argv[-1])
            self.assertIn("BEGIN TRANSACTION READ ONLY", stdin)
            queries.append(stdin)
            if len(queries) == 1:
                return json.dumps(
                    {
                        "schema_version": 5,
                        "tables": ["artifacts", "tasks"],
                        "arbitrary": SECRET,
                    }
                )
            return json.dumps(
                {
                    "schema_version": 5,
                    "table_counts": {"artifacts": 4, "tasks": 2},
                    "arbitrary": SECRET,
                }
            )

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "compose.yml").write_text("services: {}\n")
            report = evidence.capture(
                Path(directory), PROJECT, "20261018.1", "http://127.0.0.1", True, runner
            )
        self.assertEqual(len(queries), 2)
        self.assertEqual(
            report["database"]["table_counts"], {"artifacts": 4, "tasks": 2}
        )
        self.assertTrue(report["database"]["captured"])
        self.assertNotIn(SECRET, json.dumps(report))

    def test_schema_change_during_capture_is_not_accepted(self):
        container = imported_container()
        container["Config"]["Labels"]["com.docker.compose.service"] = "postgres"
        outputs = iter(
            [
                "postgres\n",
                json.dumps([{"ID": CONTAINER, "Service": "postgres"}]),
                json.dumps([container]),
                json.dumps({"schema_version": 4, "tables": ["tasks"]}),
                json.dumps({"schema_version": 5, "table_counts": {"tasks": 2}}),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "compose.yml").write_text("services: {}\n")
            with self.assertRaises(evidence.CaptureError) as raised:
                evidence.capture(
                    Path(directory),
                    PROJECT,
                    "20261018.1",
                    "http://127.0.0.1",
                    True,
                    lambda *args: next(outputs),
                )
        self.assertEqual(str(raised.exception), "SCHEMA_CHANGED_DURING_CAPTURE")

    def test_cli_failures_never_echo_subprocess_diagnostics_or_inputs(self):
        for arguments in (
            ["--public-url", "https://user:" + SECRET + "@host"],
            ["--unknown", SECRET],
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = evidence.main(arguments)
            self.assertEqual(code, 2)
            self.assertNotIn(SECRET, stdout.getvalue() + stderr.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "compose.yml").write_text("services: {}\n")
            arguments = [
                "--deploy-dir",
                directory,
                "--project",
                PROJECT,
                "--release",
                "20261018.1",
                "--public-url",
                "http://127.0.0.1",
            ]
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                patch.object(
                    evidence, "run_read_only", side_effect=RuntimeError(SECRET)
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = evidence.main(arguments)
            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertNotIn(SECRET, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
