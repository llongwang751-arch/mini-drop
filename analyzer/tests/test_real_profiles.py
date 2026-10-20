"""Native-format regressions using the recorded real-tool fixture manifest."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import subprocess
import sys
import sqlite3
import time

from drop_analyzer.external import analyze_jfr, analyze_memray, optimize_bolt
from drop_analyzer.hprof import analyze_hprof
from drop_analyzer.native import analyze_bolt, native_cpu, native_heap
from drop_analyzer.pprof import analyze_pprof
from drop_analyzer.isolate import analyze_isolated

FIXTURES = Path(__file__).parent / "fixtures" / "collected"


@unittest.skipUnless(
    (FIXTURES / "manifest.json").exists(),
    "Generate actual tool profiles with scripts/generate_real_fixtures.py",
)
class RealProfileTests(unittest.TestCase):
    def test_manifest_integrity_for_all_imported_tool_captures(self):
        manifest = json.loads((FIXTURES / "manifest.json").read_text())
        self.assertIn("Imported fixtures", manifest["provenance"])
        for row in manifest["files"]:
            raw = (FIXTURES / row["name"]).read_bytes()
            self.assertEqual(len(raw), row["size"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["sha256"])

    def test_real_go_cpu_and_heap_types(self):
        cpu = analyze_pprof((FIXTURES / "go-cpu.pprof").read_bytes(), kind="GO_CPU")
        self.assertGreater(cpu["metadata"]["sample_count"], 0)
        self.assertTrue(any("busy" in row["name"] for row in cpu["top"]["inclusive"]))
        for sample_type in (
            "alloc_space",
            "alloc_objects",
            "inuse_space",
            "inuse_objects",
        ):
            heap = analyze_pprof(
                (FIXTURES / "go-heap.pprof").read_bytes(),
                kind="GO_HEAP",
                params={"sample_type": sample_type},
            )
            self.assertEqual(heap["metadata"]["profile_type"], sample_type)
            self.assertGreater(heap["metadata"]["sample_count"], 0)

    def test_real_hprof_object_graph_retention_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = (FIXTURES / "java-heap.hprof").read_bytes()
            parsed = analyze_hprof(raw, work_dir=directory)
            heap = parsed["sections"]["heap"]
            self.assertGreater(heap["totals"]["objects"], 1000)
            self.assertGreater(heap["totals"]["root_count"], 0)
            self.assertTrue(
                any(row["retained_bytes"] >= 1024 * 1024 for row in heap["dominators"])
            )
            self.assertTrue(heap["leak_paths"][0]["path"])
            self.assertTrue(
                analyze_hprof(raw, work_dir=directory)["metadata"]["index_resumed"]
            )

    @unittest.skipUnless(
        os.name == "posix", "SIGKILL and Linux resource budgets required"
    )
    def test_real_hprof_sigkill_resumes_checkpointed_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {
                "pipeline": "hprof_v1",
                "kind": "JAVA_HEAP",
                "format": "hprof",
                "raw_path": str((FIXTURES / "java-heap.hprof").resolve()),
                "work_dir": directory,
                "index_dir": str(root / "indexes"),
                "params": {},
                "artifact": {},
                "limits": {
                    "address_space_bytes": 512 * 1024**2,
                    "cpu_seconds": 90,
                    "wall_seconds": 120,
                    "output_bytes": 32 * 1024**2,
                },
            }
            request = root / "request.json"
            request.write_text(json.dumps(context))
            child = subprocess.Popen(
                [sys.executable, "-m", "drop_analyzer.child", str(request)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            checkpoint = False
            try:
                deadline = time.monotonic() + 10
                while child.poll() is None and time.monotonic() < deadline:
                    for database in (
                        (root / "indexes").glob("*.sqlite3")
                        if (root / "indexes").exists()
                        else []
                    ):
                        try:
                            with sqlite3.connect(database, timeout=0.1) as connection:
                                row = connection.execute(
                                    "SELECT value FROM meta WHERE key='offset'"
                                ).fetchone()
                                checkpoint = bool(row and int(row[0]) > 1000)
                        except sqlite3.OperationalError:
                            pass
                    if checkpoint:
                        break
                    time.sleep(0.01)
                self.assertTrue(
                    checkpoint,
                    "child must commit an index checkpoint before injected crash",
                )
            finally:
                child.kill()
                child.wait(timeout=5)
            resumed = analyze_isolated(context, check_lease=lambda: None)
            self.assertTrue(resumed["parsed"]["metadata"]["index_resumed"])
            self.assertGreater(
                resumed["parsed"]["sections"]["heap"]["totals"]["objects"], 1000
            )

    @unittest.skipUnless(shutil.which("jfr"), "OpenJDK jfr tool is required")
    def test_real_jfr_execution_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            parsed = analyze_jfr(FIXTURES / "java-cpu.jfr", work_dir=directory)
            self.assertGreater(parsed["metadata"]["sample_count"], 0)
            self.assertTrue(
                any(
                    "HeapFixture.busy" in row["name"]
                    for row in parsed["top"]["inclusive"]
                )
            )

    @unittest.skipUnless(os.name == "posix", "memray supports Linux/macOS")
    def test_real_memray_reader(self):
        parsed = analyze_memray(FIXTURES / "python-memory.bin")
        memory = parsed["sections"]["memory"]
        self.assertGreater(memory["peak_live_bytes"], 1024 * 1024)
        self.assertGreater(memory["live_bytes"], 0)
        self.assertIsNone(memory["pagefaults"]["minor"])

    def test_real_native_profiles(self):
        self.assertGreater(
            native_cpu((FIXTURES / "gperf-cpu.profile").read_bytes())["metadata"][
                "sample_count"
            ],
            0,
        )
        for kind, name in (
            ("GPERF_HEAP", "gperf-heap.profile"),
            ("JEPROF_HEAP", "jemalloc-heap.profile"),
        ):
            parsed = native_heap((FIXTURES / name).read_bytes(), kind=kind)
            self.assertGreater(parsed["metadata"]["sample_count"], 1024 * 1024)

    def test_real_bolt_instrumentation_profile(self):
        parsed = analyze_bolt((FIXTURES / "bolt.fdata").read_bytes())
        self.assertGreater(parsed["sections"]["binary"]["totals"]["branches"], 0)

    @unittest.skipUnless(
        shutil.which("llvm-bolt") or shutil.which("llvm-bolt-16"),
        "LLVM BOLT is required",
    )
    def test_real_bolt_generates_elf_without_running_it(self):
        with tempfile.TemporaryDirectory() as directory:
            result = optimize_bolt(
                FIXTURES / "bolt.fdata",
                FIXTURES / "bolt-target.elf",
                work_dir=directory,
            )
            self.assertEqual(
                result["status"], "generated_requires_regression_validation"
            )
            self.assertEqual(
                (Path(directory) / "optimized.elf").read_bytes()[:4], b"\x7fELF"
            )


if __name__ == "__main__":
    unittest.main()
