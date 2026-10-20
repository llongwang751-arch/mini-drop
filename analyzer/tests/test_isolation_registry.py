"""验证格式注册表及隔离子进程的资源、清理和取消边界，测试输入为受控夹具。"""

import hashlib
from pathlib import Path
import tempfile
import unittest
import os
import subprocess
import sys
import time

from drop_analyzer.errors import AnalysisError
from drop_analyzer.isolate import analyze_isolated, terminate, process_group_usage
from drop_analyzer.registry import KIND_PIPELINES, get_pipeline
from drop_analyzer.worker import cleanup_indexes


class IsolationRegistryTests(unittest.TestCase):
    def context(self, directory, raw=b"root;leaf 5"):
        path = Path(directory) / "raw"
        path.write_bytes(raw)
        return {
            "pipeline": "folded_v1",
            "kind": "PERF_CPU",
            "format": "folded",
            "raw_path": str(path),
            "work_dir": directory,
            "params": {},
            "artifact": {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "content_type": "text/plain",
            },
            "limits": {
                "address_space_bytes": 128 * 1024**2,
                "cpu_seconds": 2,
                "wall_seconds": 3,
                "output_bytes": 1024**2,
            },
        }

    def test_all_public_collection_kinds_have_unique_registry_entry(self):
        self.assertEqual(len(KIND_PIPELINES), 17)
        for kind, pipeline in KIND_PIPELINES.items():
            self.assertIn(kind, get_pipeline(pipeline.pipeline).kinds)
        with self.assertRaises(AnalysisError):
            get_pipeline("shell")

    def test_real_subprocess_result_and_decoder_error_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            value = analyze_isolated(self.context(directory), check_lease=lambda: None)
            self.assertEqual(value["parsed"]["metadata"]["sample_count"], 5)
            self.assertGreaterEqual(value["metrics"]["duration_ms"], 0)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AnalysisError) as failed:
                analyze_isolated(
                    self.context(directory, b"malformed"), check_lease=lambda: None
                )
            self.assertEqual(failed.exception.code, "INVALID_INPUT")

    @unittest.skipUnless(os.name == "posix", "Linux RLIMIT_AS is required")
    def test_100k_nodes_fit_default_child_budget_and_next_is_rejected(self):
        raw = b"\n".join(f"function_{index:06d} 1".encode() for index in range(99_999))
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(directory, raw)
            context["limits"].update(
                address_space_bytes=512 * 1024**2,
                cpu_seconds=30,
                wall_seconds=60,
                output_bytes=32 * 1024**2,
            )
            value = analyze_isolated(context, check_lease=lambda: None)
            self.assertEqual(value["parsed"]["metadata"]["node_count"], 100_000)
            self.assertLess(value["metrics"]["max_rss_bytes"], 512 * 1024**2)
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(directory, raw + b"\nfunction_over_limit 1")
            context["limits"].update(
                address_space_bytes=512 * 1024**2,
                cpu_seconds=30,
                wall_seconds=60,
                output_bytes=32 * 1024**2,
            )
            with self.assertRaises(AnalysisError) as failed:
                analyze_isolated(context, check_lease=lambda: None)
            self.assertEqual(failed.exception.code, "INPUT_LIMIT_EXCEEDED")

    def test_isolation_wall_budget_kills_child(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(directory)
            context["limits"]["wall_seconds"] = 0
            with self.assertRaises(AnalysisError) as failed:
                analyze_isolated(context, check_lease=lambda: None)
            self.assertEqual(failed.exception.code, "ANALYSIS_TIMEOUT")

    @unittest.skipUnless(os.name == "posix", "Linux RLIMIT_AS is required")
    def test_address_space_budget_fails_without_publishing_result(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(directory)
            context["limits"]["address_space_bytes"] = 16 * 1024**2
            with self.assertRaises(AnalysisError) as failed:
                analyze_isolated(context, check_lease=lambda: None)
            self.assertIn(
                failed.exception.code,
                {"ANALYSIS_RESOURCE_LIMIT", "ANALYSIS_MEMORY_LIMIT"},
            )

    def test_every_pipeline_rejects_invalid_native_format(self):
        for kind, pipeline in KIND_PIPELINES.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                context = self.context(directory, b"invalid")
                context.update(
                    kind=kind, pipeline=pipeline.pipeline, format=pipeline.formats[0]
                )
                with self.assertRaises(AnalysisError):
                    analyze_isolated(context, check_lease=lambda: None)

    def test_every_pipeline_enforces_input_budget_before_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(directory)
            with Path(context["raw_path"]).open("wb") as profile:
                profile.truncate(32 * 1024**2 + 1)
            for kind, pipeline in KIND_PIPELINES.items():
                with self.subTest(kind=kind):
                    context.update(
                        kind=kind,
                        pipeline=pipeline.pipeline,
                        format=pipeline.formats[0],
                    )
                    with self.assertRaises(AnalysisError) as failed:
                        pipeline.validate(context)
                    self.assertEqual(failed.exception.code, "INPUT_LIMIT_EXCEEDED")

    def test_cache_scavenger_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "indexes"
            root.mkdir()
            owned = root / ("hprof-" + "a" * 64 + ".sqlite3")
            owned.write_bytes(b"cache")
            unrelated = root / "important.sqlite3"
            unrelated.write_bytes(b"keep")
            cleanup_indexes(directory, max_bytes=0)
            self.assertFalse(owned.exists())
            self.assertTrue(unrelated.exists())

    @unittest.skipUnless(
        sys.platform.startswith("linux"), "Linux process group accounting required"
    )
    def test_actual_process_tree_accounting_and_group_cleanup(self):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);time.sleep(30)",
            ],
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                usage = process_group_usage(child.pid)
                if usage and usage["processes"] == 2:
                    break
                time.sleep(0.05)
            self.assertEqual(usage["processes"], 2)
            self.assertGreater(usage["rss_bytes"], 0)
            self.assertGreaterEqual(usage["cpu_seconds"], 0)
        finally:
            terminate(child)
        self.assertLess(child.returncode, 0)


if __name__ == "__main__":
    unittest.main()
