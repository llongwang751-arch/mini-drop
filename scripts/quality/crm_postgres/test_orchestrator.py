"""明确合成Docker边界回归：重现角色未就绪时REST不可提前启动的故障。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "crm_isolated_orchestrator", Path(__file__).with_name("run_isolated.py")
)
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)


class StartupOrderTests(unittest.TestCase):
    def execute_fixture(self, worker_ready):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            bundle, output = base / "bundle", base / "output"
            bundle.mkdir()
            (bundle / "manifest.private.json").write_text("{}", encoding="utf-8")
            created, resources = [], {}
            ready = False

            def docker(*arguments, **_kwargs):
                nonlocal ready
                result = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
                if arguments[:2] == ("network", "create"):
                    label = arguments[arguments.index("--label") + 1].split("=", 1)[1]
                    resources[arguments[-1]] = {"Labels": {"drop.crm.quality": label}}
                if arguments[0] == "run":
                    name = arguments[arguments.index("--name") + 1]
                    label = arguments[arguments.index("--label") + 1].split("=", 1)[1]
                    created.append(name.rsplit("-", 1)[-1])
                    resources[name] = {
                        "Config": {"Labels": {"drop.crm.quality": label}},
                        "State": {"Running": True},
                    }
                    if name.endswith("-runner"):
                        if worker_ready:
                            ready = True
                            (output / "bootstrap-ready.json").write_text(
                                json.dumps(
                                    {"schema_version": 1, "bootstrap_ready": True}
                                ),
                                encoding="utf-8",
                            )
                        else:
                            resources[name]["State"]["Running"] = False
                        (output / "acceptance.json").write_text(
                            json.dumps({"passed": worker_ready, "stage": "fixture"}),
                            encoding="utf-8",
                        )
                    if name.endswith("-rest") and not ready:
                        raise ValueError("SYNTHETIC_REST_ROLE_NOT_READY")
                if arguments[0] == "wait":
                    result.stdout = b"0" if worker_ready else b"1"
                return result

            def inspect(kind, name):
                return (
                    {"Id": "sha256:synthetic-fixture"}
                    if kind == "image"
                    else resources.get(name)
                )

            with (
                patch.object(orchestrator.sys, "platform", "linux"),
                patch.object(orchestrator.os, "geteuid", return_value=0, create=True),
                patch.object(orchestrator.os, "chown", create=True),
                patch.object(
                    orchestrator,
                    "checked_bundle",
                    return_value={
                        "original_source_manifest_sha256": "synthetic-source",
                        "generated_suite_sha256": "synthetic-suite",
                    },
                ),
                patch.object(orchestrator, "docker", side_effect=docker),
                patch.object(orchestrator, "inspect", side_effect=inspect),
            ):
                result = orchestrator.run(
                    bundle,
                    output,
                    runner_image="fixture-runner",
                    postgres_image="fixture-db",
                    rest_image="fixture-rest",
                )
            return result, created

    def test_rest_starts_only_after_actual_worker_bootstrap_signal(self):
        result, created = self.execute_fixture(True)
        self.assertEqual(created, ["db", "runner", "rest"])
        self.assertTrue(result["bootstrap_ready"])
        self.assertTrue(result["passed"])
        self.assertEqual(set(result["cleanup"].values()), {"removed"})

    def test_worker_failure_before_signal_never_starts_rest_and_preserves_failure(self):
        result, created = self.execute_fixture(False)
        self.assertEqual(created, ["db", "runner"])
        self.assertFalse(result["bootstrap_ready"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["worker_exit_code"], 1)
        self.assertEqual(result["worker_stage"], "fixture")

    def test_oversized_marker_cannot_authorize_rest_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "ready.json"
            marker.write_bytes(b" " * 257)
            with self.assertRaisesRegex(ValueError, "BOOTSTRAP_MARKER_BOUND"):
                orchestrator.wait_for_bootstrap(marker, "synthetic-runner")


if __name__ == "__main__":
    unittest.main()
