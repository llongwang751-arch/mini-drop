"""文件系统、假宿主与 Linux 文件锁回归，不运行 Docker、Syft 或网络。"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import host_sbom as sbom

REF = "drop-api:20261018.1-rc3"
ID = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
PRIVATE = "IMPORTED_ENV_COOKIE_LABEL_PRIVATE_PATH"
PACKAGE = {
    "type": "library",
    "name": "imported-package",
    "version": "1.0",
    "bom-ref": "fixture-pkg",
}


def document():
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "properties": [{"name": "syft:image:labels:secret", "value": PRIVATE}],
            "Env": [PRIVATE],
            "Cmd": [PRIVATE],
            "component": {"name": "/private/" + PRIVATE},
        },
        "components": [dict(PACKAGE)],
        "dependencies": [{"ref": "fixture-pkg", "dependsOn": []}],
    }


class FakeHost:
    def __init__(self):
        self.inspections = 0
        self.scans = []
        self.changed = False
        self.fail_scan = False
        self.fail_gate = False
        self.bad_document = None
        self.race_record = None
        self.gates = []

    def inspect(self, reference):
        self.inspections += 1
        if self.inspections == 2 and self.race_record:
            self.race_record.write_text("untouched competing record")
        return {
            "image_id": OTHER if self.changed and self.inspections > 1 else ID,
            "architecture": "amd64",
            "os": "linux",
            "size": 4096,
        }

    def disk_size(self, reference, expected_id):
        return 8192

    def gate(self, root, content_size, disk_size):
        self.gates.append((root, content_size, disk_size))
        if self.fail_gate:
            raise sbom.Failure("SBOM_RESOURCE_GATE")

    def scan(self, syft, image_id, output, workspace):
        self.scans.append(image_id)
        (workspace / "owned-nested").mkdir()
        (workspace / "owned-nested" / "temporary").write_text("synthetic scratch")
        if self.fail_scan:
            raise sbom.Failure("SBOM_SCAN_FAILED")
        if self.bad_document == "directory":
            output.mkdir()
        else:
            output.write_bytes(
                self.bad_document
                if self.bad_document is not None
                else json.dumps(document()).encode()
            )


class HostSBOMTests(unittest.TestCase):
    def test_containerd_disk_size_is_exact_and_projects_no_image_metadata(self):
        payload = json.dumps(
            [{"Id": ID, "Size": 2650000000, "Labels": {"secret": PRIVATE}}]
        ).encode()
        result = sbom.disk_metadata(payload, ID)
        self.assertEqual(result, {"image_id": ID, "disk_size_bytes": 2650000000})
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_list_disk_metadata_rejects_ambiguous_ids_types_and_shapes(self):
        for value in (
            [],
            [{"Id": ID, "Size": 1}, {"Id": ID, "Size": 1}],
            {"Id": ID, "Size": 1},
            [{"Id": OTHER, "Size": 1}],
            [{"Id": ID, "Size": True}],
            [{"Id": ID, "Size": "2650000000"}],
            [{"Id": ID, "Size": 0}],
            [{"Id": ID, "Size": -1}],
            [{"Id": ID, "Size": 64 * sbom.GIB + 1}],
            [{"Id": ID, "Size": float("nan")}],
        ):
            with self.subTest(value=value), self.assertRaises(sbom.Failure):
                sbom.disk_metadata(json.dumps(value).encode(), ID)

    def test_list_disk_metadata_rejects_duplicate_keys_and_unbounded_json(self):
        for payload in (
            ('[{"Id":"' + ID + '","Size":1,"Size":2}]').encode(),
            b"x" * 65537,
            b"\xff",
            b"[" * 1000,
        ):
            with (
                self.subTest(size=len(payload)),
                self.assertRaisesRegex(sbom.Failure, "^SBOM_DISK_METADATA_REJECTED$"),
            ):
                sbom.disk_metadata(payload, ID)

    def test_daemon_list_uses_only_fixed_unix_endpoint_and_reference_filter(self):
        response = Mock(status=200)
        response.getheaders.return_value = [("Content-Type", "application/json")]
        response.read.return_value = json.dumps(
            [{"Id": ID, "Size": 2650000000}]
        ).encode()
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(sbom, "DockerConnection", return_value=connection):
            self.assertEqual(sbom.Host().disk_size(REF, ID), 2650000000)
        args, kwargs = connection.request.call_args
        self.assertEqual(args[0], "GET")
        target = urlsplit(args[1])
        self.assertEqual(target.path, "/v1.52/images/json")
        self.assertFalse(target.scheme or target.netloc)
        query = parse_qs(target.query)
        self.assertEqual(json.loads(query["filters"][0]), {"reference": [REF]})
        self.assertEqual(set(query), {"filters", "all"})
        self.assertEqual(query["all"], ["1"])
        self.assertNotIn(PRIVATE, json.dumps(kwargs))
        response.read.assert_called_once_with(65537)
        connection.close.assert_called_once()

    def test_daemon_list_rejects_http_errors_and_invalid_body_limits(self):
        for status, headers, body in (
            (302, [("Location", "https://private.invalid")], b""),
            (500, [], PRIVATE.encode()),
            (200, [("Content-Length", "65537")], b""),
            (200, [("Content-Length", "-1")], b""),
            (200, [("Content-Length", "1"), ("Content-Length", "2")], b""),
            (200, [("Content-Length", "1"), ("Transfer-Encoding", "chunked")], b""),
            (200, [], b"x" * 65537),
            (200, [("Content-Length", "1")], b"[]"),
        ):
            response = Mock(status=status)
            response.getheaders.return_value = headers
            response.read.return_value = body
            connection = Mock()
            connection.getresponse.return_value = response
            with (
                self.subTest(status=status, headers=headers),
                patch.object(sbom, "DockerConnection", return_value=connection),
                self.assertRaises(sbom.Failure) as caught,
            ):
                sbom.Host().disk_size(REF, ID)
            self.assertNotIn(PRIVATE, str(caught.exception))
            self.assertNotIn("private.invalid", str(caught.exception))
            connection.close.assert_called_once()

    def test_daemon_list_failures_close_connection_without_native_diagnostics(self):
        for phase in ("request", "getresponse", "read"):
            response = Mock(status=200)
            response.getheaders.return_value = []
            connection = Mock()
            connection.getresponse.return_value = response
            getattr(
                response if phase == "read" else connection, phase
            ).side_effect = OSError(PRIVATE)
            with (
                self.subTest(phase=phase),
                patch.object(sbom, "DockerConnection", return_value=connection),
                self.assertRaisesRegex(
                    sbom.Failure, "^SBOM_DISK_QUERY_FAILED$"
                ) as caught,
            ):
                sbom.Host().disk_size(REF, ID)
            self.assertIsNone(caught.exception.__cause__)
            connection.close.assert_called_once()

    def test_unix_connection_never_uses_tcp_proxy_or_environment(self):
        connection = sbom.DockerConnection()
        with (
            patch.object(sbom.socket, "AF_UNIX", 1, create=True),
            patch.object(sbom.socket, "socket") as create,
        ):
            connection.connect()
            create.assert_called_once_with(sbom.socket.AF_UNIX, sbom.socket.SOCK_STREAM)
        create.return_value.settimeout.assert_called_once_with(30)
        create.return_value.connect.assert_called_once_with("/var/run/docker.sock")
        connection.close()

    def test_daemon_query_validates_reference_and_digest_before_connection(self):
        with patch.object(sbom, "DockerConnection") as connection:
            for reference, image_id in (("drop-api:*", ID), (REF, "sha256:bad")):
                with self.subTest(reference=reference), self.assertRaises(sbom.Failure):
                    sbom.Host().disk_size(reference, image_id)
            connection.assert_not_called()

    def test_only_the_explicit_cached_tools_base_can_be_triaged(self):
        reference = "drop-core-full-tools-base:v2"
        self.assertEqual(sbom.image_spec(reference + "=" + ID), (reference, ID))
        self.assertEqual(sbom.unique_specs([reference + "=" + ID]), [(reference, ID)])
        for other in (
            "drop-core-full-tools-base:latest",
            "drop-core-full-tools-base:v3",
            "registry/drop-core-full-tools-base:v2",
        ):
            with self.subTest(reference=other), self.assertRaises(sbom.Failure):
                sbom.image_spec(other + "=" + ID)

    def test_tools_base_inventory_is_explicitly_not_a_release_component(self):
        with tempfile.TemporaryDirectory() as folder:
            reference = "drop-core-full-tools-base:v2"
            record = sbom.collect_one(
                FakeHost(), Path(folder), Path("syft"), reference, ID
            )
            self.assertIs(record["diagnostic_only"], True)
            self.assertIs(record["release_component"], False)
            target, _ = sbom.names(Path(folder), reference)
            self.assertEqual(
                json.loads(target.read_bytes())["metadata"]["component"]["name"],
                reference,
            )

    def test_only_exact_known_release_refs_and_digest_are_accepted(self):
        for component in ("core", "agent", "api", "analyzer", "web", "ops"):
            self.assertEqual(
                sbom.image_spec(f"drop-{component}:20261018.1={ID}")[1], ID
            )
        for value in (
            f"registry/drop-api:20261018.1={ID}",
            f"drop-api:latest={ID}",
            f"drop-api:20261018.1;echo={ID}",
            f"drop-unknown:20261018.1={ID}",
            f"drop-api:20261018.1-rc0={ID}",
            f"drop-api:20261301.1={ID}",
            f"drop-api:20261018.1={ID}/bad",
            "../private",
        ):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(sbom.Failure, "^SBOM_IMAGE_REJECTED$"),
            ):
                sbom.image_spec(value)

    def test_duplicate_component_is_rejected_even_under_a_different_tag(self):
        with self.assertRaisesRegex(sbom.Failure, "IMAGE_SET"):
            sbom.unique_specs([REF + "=" + ID, "drop-api:20261018.1-rc2=" + OTHER])
        self.assertEqual(len(sbom.unique_specs([REF + "=" + ID])), 1)

    def test_optional_nginx_platform_is_distinct_from_the_six_applications(self):
        values = [
            "drop-" + name + ":20261018.1-rc1=" + ID
            for name in ("core", "agent", "api", "analyzer", "web", "ops", "nginx")
        ]
        self.assertEqual(len(sbom.unique_specs(values)), 7)
        with self.assertRaisesRegex(sbom.Failure, "IMAGE_SET"):
            sbom.unique_specs(values + ["drop-nginx:20261018.1=" + OTHER])

    def test_inspect_projects_only_valid_typed_public_fields(self):
        payload = {"Id": ID, "Os": "linux", "Architecture": "amd64", "Size": 123}
        self.assertEqual(
            sbom.inspect_metadata(json.dumps(payload).encode())["image_id"], ID
        )
        for change in (
            {"Env": PRIVATE},
            {"Labels": {"secret": PRIVATE}},
            {"Size": True},
            {"Size": -1},
            {"Architecture": "unknown"},
        ):
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(sbom.Failure, "^SBOM_INSPECT_REJECTED$"),
            ):
                sbom.inspect_metadata(json.dumps(payload | change).encode())

    def test_docker_inspect_requests_no_environment_command_or_labels(self):
        result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"Id": ID, "Os": "linux", "Architecture": "amd64", "Size": 123}
            ).encode(),
        )
        with patch.object(sbom.subprocess, "run", return_value=result) as execute:
            self.assertEqual(sbom.Host().inspect(REF)["size"], 123)
        argv = execute.call_args.args[0]
        self.assertIn("--format", argv)
        self.assertNotIn("Env", argv[argv.index("--format") + 1])
        self.assertNotIn("Labels", argv[argv.index("--format") + 1])
        self.assertNotIn("Cmd", argv[argv.index("--format") + 1])

    def test_resource_thresholds_enforce_disk_memory_and_pressure(self):
        quiet = "some avg10=0.00 avg60=0.00\nfull avg10=0.00 avg60=0.00\n"
        mem = "MemAvailable: 786432 kB\n"
        sbom.resource_gate(sbom.GIB + 579, 123, 456, mem, quiet, quiet)
        cases = [
            (sbom.GIB + 578, mem, quiet, quiet),
            (2 * sbom.GIB, "MemAvailable: 786431 kB", quiet, quiet),
            (
                2 * sbom.GIB,
                mem,
                quiet.replace("some avg10=0.00", "some avg10=1.01"),
                quiet,
            ),
            (
                2 * sbom.GIB,
                mem,
                quiet,
                quiet.replace("full avg10=0.00", "full avg10=1.01"),
            ),
        ]
        for free, memory, memory_psi, io_psi in cases:
            with (
                self.subTest(case=cases.index((free, memory, memory_psi, io_psi))),
                self.assertRaisesRegex(sbom.Failure, "^SBOM_RESOURCE_GATE$"),
            ):
                sbom.resource_gate(free, 123, 456, memory, memory_psi, io_psi)

    def test_disk_reserve_covers_daemon_tar_and_unpacked_layers(self):
        quiet = "some avg10=0.00 avg60=0.00\nfull avg10=0.00 avg60=0.00\n"
        with self.assertRaisesRegex(sbom.Failure, "^SBOM_RESOURCE_GATE$"):
            sbom.resource_gate(
                2 * sbom.GIB,
                sbom.GIB,
                sbom.GIB,
                "MemAvailable: 786432 kB\n",
                quiet,
                quiet,
            )

    def test_packed_size_cannot_underbudget_real_containerd_snapshots(self):
        # 导入的大小观测夹具，不是实时 Docker 或 Syft 调用。
        content_size, disk_size = 690937462, 2650000000
        quiet = "some avg10=0.00\nfull avg10=0.00\n"
        mem = "MemAvailable: 786432 kB\n"
        required = content_size + disk_size + sbom.GIB
        for free in (3 * sbom.GIB, required - 1):
            with (
                self.subTest(free=free),
                self.assertRaisesRegex(sbom.Failure, "^SBOM_RESOURCE_GATE$"),
            ):
                sbom.resource_gate(free, content_size, disk_size, mem, quiet, quiet)
        sbom.resource_gate(required, content_size, disk_size, mem, quiet, quiet)

    def test_missing_or_nonfinite_pressure_metrics_fail_closed(self):
        for value in (
            "some avg10=NaN\nfull avg10=0",
            "some avg10=0",
            "some avg10=-1\nfull avg10=0",
            "some avg10=0\nsome avg10=0\nfull avg10=0",
        ):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(sbom.Failure, "METRICS_REJECTED"),
            ):
                sbom.pressure(value)

    def test_syft_environment_does_not_inherit_credentials_or_overrides(self):
        with patch.dict(
            os.environ,
            {
                "SECRET": PRIVATE,
                "HTTP_PROXY": PRIVATE,
                "SYFT_CONFIG": PRIVATE,
                "DOCKER_CONTEXT": PRIVATE,
            },
        ):
            environment = sbom.clean_environment(Path("/owned"))
        self.assertNotIn(PRIVATE, json.dumps(environment))
        self.assertEqual(environment["SYFT_PARALLELISM"], "1")
        self.assertEqual(environment["GOMAXPROCS"], "1")
        self.assertEqual(environment["DOCKER_HOST"], "unix:///var/run/docker.sock")
        self.assertEqual(environment["SYFT_CHECK_FOR_APP_UPDATE"], "false")

    def test_syft_uses_exact_daemon_digest_scope_and_suppresses_native_output(self):
        with patch.object(
            sbom.subprocess, "run", return_value=SimpleNamespace(returncode=0)
        ) as execute:
            sbom.Host().scan(
                Path("/approved/syft"), ID, Path("/owned/out.json"), Path("/owned")
            )
        argv = execute.call_args.args[0]
        self.assertEqual(argv[1], "docker:" + ID)
        self.assertEqual(argv[argv.index("--scope") + 1], "squashed")
        self.assertEqual(
            argv[argv.index("--output") + 1],
            "cyclonedx-json=" + str(Path("/owned/out.json")),
        )
        self.assertEqual(execute.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(execute.call_args.kwargs["stdout"], subprocess.DEVNULL)

    def test_success_preserves_inventory_but_omits_image_labels_and_private_metadata(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            root, host = Path(folder), FakeHost()
            record = sbom.collect_one(host, root, Path("syft"), REF, ID)
            target, record_path = sbom.names(root, REF)
            actual = json.loads(target.read_bytes())
            self.assertEqual(actual["components"], [PACKAGE])
            self.assertEqual(actual["dependencies"], document()["dependencies"])
            self.assertNotIn(PRIVATE, target.read_text())
            self.assertNotIn(PRIVATE, record_path.read_text())
            self.assertEqual(record["components"], 1)
            self.assertEqual(record["source"], "docker-daemon")
            self.assertEqual(record["sbom_sha256"], sbom.sha256(target.read_bytes()))
            self.assertEqual(
                set(record),
                {
                    "reference",
                    "image_id",
                    "os",
                    "architecture",
                    "size",
                    "disk_size_bytes",
                    "source",
                    "sbom_file",
                    "sbom_sha256",
                    "components",
                    "syft_binary_sha256",
                },
            )
            self.assertNotIn("archive_sha256", record)
            self.assertEqual(host.scans, [ID])
            self.assertEqual(host.inspections, 2)
            self.assertEqual(record["disk_size_bytes"], 8192)
            self.assertEqual(host.gates, [(root, 4096, 8192)])
            self.assertEqual(set(root.iterdir()), {target, record_path})

    def test_list_identity_race_stops_before_workspace_gate_or_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            host, root = FakeHost(), Path(folder)
            with (
                patch.object(
                    host,
                    "disk_size",
                    side_effect=sbom.Failure("SBOM_DISK_METADATA_REJECTED"),
                ) as query,
                self.assertRaisesRegex(sbom.Failure, "^SBOM_DISK_METADATA_REJECTED$"),
            ):
                sbom.collect_one(host, root, Path("syft"), REF, ID)
            query.assert_called_once_with(REF, ID)
            self.assertEqual(host.gates, [])
            self.assertEqual(host.scans, [])
            self.assertEqual(list(root.iterdir()), [])

    def test_expected_digest_mismatch_stops_before_gate_or_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost()
            with self.assertRaisesRegex(sbom.Failure, "IMAGE_ID_CHANGED"):
                sbom.collect_one(host, Path(folder), Path("syft"), REF, OTHER)
            self.assertEqual(host.scans, [])
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_retag_during_scan_never_publishes_a_misbound_sbom(self):
        with tempfile.TemporaryDirectory() as folder:
            host, root = FakeHost(), Path(folder)
            host.changed = True
            marker = root / "unrelated-marker"
            marker.write_text("keep")
            with self.assertRaisesRegex(sbom.Failure, "IMAGE_ID_CHANGED"):
                sbom.collect_one(host, root, Path("syft"), REF, ID)
            self.assertEqual(list(root.iterdir()), [marker])

    def test_scan_failure_cleans_only_this_component_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            host, root = FakeHost(), Path(folder)
            host.fail_scan = True
            marker = root / "unknown-directory"
            marker.mkdir()
            (marker / "keep").write_text("keep")
            with self.assertRaisesRegex(sbom.Failure, "^SBOM_SCAN_FAILED$"):
                sbom.collect_one(host, root, Path("syft"), REF, ID)
            self.assertEqual(list(root.iterdir()), [marker])
            self.assertEqual((marker / "keep").read_text(), "keep")

    def test_existing_output_is_preserved_and_never_scanned(self):
        with tempfile.TemporaryDirectory() as folder:
            root, host = Path(folder), FakeHost()
            target, _ = sbom.names(root, REF)
            target.write_text("existing result")
            with self.assertRaisesRegex(sbom.Failure, "OUTPUT_EXISTS"):
                sbom.collect_one(host, root, Path("syft"), REF, ID)
            self.assertEqual(target.read_text(), "existing result")
            self.assertEqual(host.inspections, 0)

    def test_resource_refusal_stops_before_creating_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost()
            host.fail_gate = True
            with self.assertRaisesRegex(sbom.Failure, "RESOURCE_GATE"):
                sbom.collect_one(host, Path(folder), Path("syft"), REF, ID)
            self.assertEqual(host.scans, [])
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_malformed_empty_and_special_sbom_outputs_are_rejected(self):
        for value in (
            b"",
            b"not JSON " + PRIVATE.encode(),
            b"[]",
            b'{"bomFormat":"CycloneDX","components":[]}',
            "directory",
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                host = FakeHost()
                host.bad_document = value
                with self.assertRaisesRegex(sbom.Failure, "^SBOM_DOCUMENT_REJECTED$"):
                    sbom.collect_one(host, Path(folder), Path("syft"), REF, ID)
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_publishing_record_race_does_not_overwrite_the_competing_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root, host = Path(folder), FakeHost()
            target, record = sbom.names(root, REF)
            host.race_record = record
            with self.assertRaises(FileExistsError):
                sbom.collect_one(host, root, Path("syft"), REF, ID)
            self.assertFalse(target.exists())
            self.assertEqual(record.read_text(), "untouched competing record")

    def test_replaced_workspace_inode_is_not_recursively_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                self.assertRaisesRegex(sbom.Failure, "TEMP_IDENTITY_CHANGED"),
                sbom.owned_workspace(root) as owned,
            ):
                owned.rename(root / "moved-original")
                owned.mkdir()
                (owned / "unknown-marker").write_text("do not delete")
            self.assertTrue((owned / "unknown-marker").exists())
            self.assertTrue((root / "moved-original").exists())

    def test_unsafe_directory_ownership_mode_or_symlink_is_rejected(self):
        for mode, uid in (
            (stat.S_IFLNK | 0o700, 0),
            (stat.S_IFDIR | 0o777, 0),
            (stat.S_IFDIR | 0o700, 10001),
        ):
            with (
                self.subTest(mode=mode, uid=uid),
                patch.object(
                    Path,
                    "lstat",
                    return_value=SimpleNamespace(st_mode=mode, st_uid=uid),
                ),
                self.assertRaisesRegex(sbom.Failure, "DIRECTORY_REJECTED"),
            ):
                sbom.secure_directory(Path("ignored"), private=True)

    def test_timeout_errors_do_not_expose_native_cmd_stderr_or_cause(self):
        error = subprocess.TimeoutExpired([PRIVATE], 1, output=PRIVATE, stderr=PRIVATE)
        with (
            patch.object(sbom.subprocess, "run", side_effect=error),
            self.assertRaises(sbom.Failure) as result,
        ):
            sbom.Host().scan(Path("/syft"), ID, Path("/out"), Path("/tmp"))
        self.assertEqual(str(result.exception), "SBOM_SCAN_FAILED")
        self.assertIsNone(result.exception.__cause__)
        self.assertNotIn(PRIVATE, str(result.exception))

    def test_cli_parse_errors_never_echo_private_paths_or_argument_values(self):
        with self.assertRaisesRegex(sbom.Failure, "^SBOM_ARGUMENTS_REJECTED$"):
            sbom.main(["--unrecognized", PRIVATE])


class CollectorLockTests(unittest.TestCase):
    """可跨平台的边界测试；真实 flock 行为在独立 Linux 用例验证。"""

    def test_existing_lock_metadata_must_be_private_regular_root_owned_single_link(
        self,
    ):
        valid = {"st_mode": stat.S_IFREG | 0o600, "st_uid": 0, "st_nlink": 1}
        sbom.validate_lock(SimpleNamespace(**valid))
        for change in (
            {"st_mode": stat.S_IFDIR | 0o600},
            {"st_mode": stat.S_IFIFO | 0o600},
            {"st_mode": stat.S_IFLNK | 0o600},
            {"st_mode": stat.S_IFREG | 0o640},
            {"st_uid": 10001},
            {"st_nlink": 2},
        ):
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(sbom.Failure, "^SBOM_LOCK_REJECTED$"),
            ):
                sbom.validate_lock(SimpleNamespace(**(valid | change)))

    def test_lock_descriptor_is_closed_without_unlink_or_explicit_unlock(self):
        info = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1, st_dev=1, st_ino=2
        )
        flock = Mock()
        fake = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=flock)
        with (
            patch.object(sys, "platform", "linux"),
            patch.dict(sys.modules, {"fcntl": fake}),
            patch.multiple(
                sbom.os,
                O_NOFOLLOW=0x20000,
                O_CLOEXEC=0x80000,
                O_NONBLOCK=0x800,
                create=True,
            ),
            patch.object(sbom.os, "open", return_value=42) as opened,
            patch.object(sbom.os, "fstat", return_value=info),
            patch.object(sbom.os, "close") as closed,
            patch.object(Path, "lstat", return_value=info),
            patch.object(Path, "unlink") as unlink,
            sbom.collector_lock(Path("synthetic-root")) as descriptor,
        ):
            self.assertEqual(descriptor, 42)
        self.assertEqual(opened.call_args.args[2], 0o600)
        self.assertFalse(opened.call_args.args[1] & os.O_TRUNC)
        flock.assert_called_once_with(42, fake.LOCK_EX | fake.LOCK_NB)
        closed.assert_called_once_with(42)
        unlink.assert_not_called()

    def test_lock_errors_do_not_expose_paths_and_always_close_descriptor(self):
        info = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1, st_dev=1, st_ino=2
        )
        for exception, code in (
            (BlockingIOError(PRIVATE), "SBOM_COLLECTOR_BUSY"),
            (OSError(PRIVATE), "SBOM_LOCK_UNAVAILABLE"),
        ):
            fake = SimpleNamespace(
                LOCK_EX=2, LOCK_NB=4, flock=Mock(side_effect=exception)
            )
            with (
                self.subTest(code=code),
                patch.object(sys, "platform", "linux"),
                patch.dict(sys.modules, {"fcntl": fake}),
                patch.multiple(
                    sbom.os,
                    O_NOFOLLOW=0x20000,
                    O_CLOEXEC=0x80000,
                    O_NONBLOCK=0x800,
                    create=True,
                ),
                patch.object(sbom.os, "open", return_value=42),
                patch.object(sbom.os, "fstat", return_value=info),
                patch.object(sbom.os, "close") as closed,
                patch.object(Path, "lstat", return_value=info),
                self.assertRaisesRegex(sbom.Failure, "^" + code + "$") as result,
                sbom.collector_lock(Path(PRIVATE)),
            ):
                self.fail("failed flock must not yield")
            closed.assert_called_once_with(42)
            self.assertIsNone(result.exception.__cause__)

    def test_syft_inherits_only_the_explicit_collector_lock_descriptor(self):
        with patch.object(
            sbom.subprocess, "run", return_value=SimpleNamespace(returncode=0)
        ) as execute:
            sbom.Host(lock_descriptor=42).scan(
                Path("/approved/syft"), ID, Path("/owned/out.json"), Path("/owned")
            )
        self.assertEqual(execute.call_args.kwargs["pass_fds"], (42,))
        self.assertTrue(execute.call_args.kwargs["close_fds"])


@unittest.skipUnless(
    sys.platform == "linux" and getattr(os, "geteuid", lambda: -1)() == 0,
    "requires native Linux root and real flock; Windows pure tests are insufficient",
)
class LinuxCollectorLockTests(unittest.TestCase):
    """不用 Docker、Syft 或网络，使用真实进程、flock、SIGKILL 和本次拥有的临时文件。"""

    def wait_ready(self, process, marker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if marker.is_file():
                return
            if process.poll() is not None:
                self.fail("synthetic lock holder exited before readiness")
            time.sleep(0.02)
        self.fail("synthetic lock holder readiness timed out")

    def test_concurrent_process_is_busy_sigkill_releases_and_old_workspace_is_preserved(
        self,
    ):
        script = """
import sys, signal
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import host_sbom as sbom
root = Path(sys.argv[2])
with sbom.collector_lock(root), sbom.owned_workspace(root) as workspace:
    (workspace / 'synthetic-evidence').write_bytes(b'preserve after SIGKILL')
    (root / 'ready').write_bytes(workspace.name.encode())
    signal.pause()
"""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(Path(__file__).parent.resolve()),
                    folder,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                self.wait_ready(process, root / "ready")
                lock = root / ".collector.lock"
                identity = lock.stat()
                with (
                    self.assertRaisesRegex(sbom.Failure, "^SBOM_COLLECTOR_BUSY$"),
                    sbom.collector_lock(root),
                ):
                    self.fail("second process acquired the held lock")
                old = root / (root / "ready").read_text()
                process.kill()
                process.wait(timeout=5)
                with sbom.collector_lock(root), sbom.owned_workspace(root):
                    self.assertEqual(lock.stat().st_ino, identity.st_ino)
                self.assertEqual(
                    (old / "synthetic-evidence").read_bytes(), b"preserve after SIGKILL"
                )
                self.assertTrue(lock.is_file())
                self.assertEqual(list(root.glob(".drop-sbom-*")), [old])
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)

    def test_inherited_scan_lock_outlives_parent_close_and_releases_after_scan_exit(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            root, process = Path(folder), None
            try:
                with sbom.collector_lock(root) as descriptor:
                    process = subprocess.Popen(
                        [sys.executable, "-c", "import signal; signal.pause()"],
                        pass_fds=(descriptor,),
                        close_fds=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                with (
                    self.assertRaisesRegex(sbom.Failure, "^SBOM_COLLECTOR_BUSY$"),
                    sbom.collector_lock(root),
                ):
                    self.fail("scan still owns the inherited lock")
                process.kill()
                process.wait(timeout=5)
                with sbom.collector_lock(root):
                    pass
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)

    def test_legacy_marker_is_reused_without_changing_bytes_or_inode(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock = root / ".collector.lock"
            lock.write_bytes(b"host-sbom-collector\n")
            lock.chmod(0o600)
            inode = lock.stat().st_ino
            for _ in range(2):
                with sbom.collector_lock(root):
                    self.assertEqual(lock.stat().st_ino, inode)
                    self.assertEqual(lock.read_bytes(), b"host-sbom-collector\n")
            self.assertTrue(lock.exists())

    def test_special_linked_and_nonprivate_lock_files_fail_without_modification(self):
        for kind in ("symlink", "hardlink", "fifo", "directory", "mode"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                lock, original = root / ".collector.lock", root / "untouched"
                original.write_bytes(b"untouched")
                original.chmod(0o600)
                if kind == "symlink":
                    lock.symlink_to(original)
                elif kind == "hardlink":
                    os.link(original, lock)
                elif kind == "fifo":
                    os.mkfifo(lock, 0o600)
                elif kind == "directory":
                    lock.mkdir()
                else:
                    lock.write_bytes(b"untouched")
                    lock.chmod(0o644)
                before = lock.lstat()
                with (
                    self.assertRaisesRegex(sbom.Failure, "^SBOM_LOCK_REJECTED$"),
                    sbom.collector_lock(root),
                ):
                    self.fail("unsafe lock was accepted")
                self.assertEqual(lock.lstat().st_ino, before.st_ino)
                self.assertEqual(original.read_bytes(), b"untouched")

    def test_replaced_lock_inode_is_detected_without_removing_either_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock, moved = root / ".collector.lock", root / "moved-lock"
            with (
                self.assertRaisesRegex(sbom.Failure, "^SBOM_LOCK_IDENTITY_CHANGED$"),
                sbom.collector_lock(root),
            ):
                lock.rename(moved)
                lock.write_bytes(b"unknown replacement")
                lock.chmod(0o600)
            self.assertTrue(moved.is_file())
            self.assertEqual(lock.read_bytes(), b"unknown replacement")


if __name__ == "__main__":
    unittest.main()
