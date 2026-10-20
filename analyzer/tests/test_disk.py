"""用导入的合成文件与目录布局验证下载、临时文件和索引缓存的磁盘预算。"""

import os
import gzip
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import subprocess
import sys
from unittest.mock import patch
import urllib3

from drop_analyzer.disk import WorkspaceBudget
from drop_analyzer.errors import AnalysisError
from drop_analyzer.isolate import analyze_isolated
from drop_analyzer.storage import ObjectStore


class DiskBudgetTests(unittest.TestCase):
    def test_download_preserves_encoded_object_bytes_for_integrity(self):
        # 导入的合成 gzip 字节就是 S3 对象正文；
        # HTTP 客户端不得透明解压后再交给校验逻辑。
        encoded = gzip.compress(b"imported synthetic fixture 7\n" * 200, mtime=0)
        response = urllib3.response.HTTPResponse(
            body=io.BytesIO(encoded),
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(encoded))},
            status=200,
            preload_content=False,
        )
        storage = ObjectStore.__new__(ObjectStore)
        storage.bucket = "fixture"
        storage.client = SimpleNamespace(get_object=lambda *args: response)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "raw"
            actual = storage.download(
                "imported-encoded-fixture",
                destination,
                expected_size=len(encoded),
                max_bytes=len(encoded),
            )
            self.assertEqual(actual, encoded)
            self.assertEqual(destination.read_bytes(), encoded)
            self.assertEqual(
                hashlib.sha256(actual).digest(), hashlib.sha256(encoded).digest()
            )

    def test_in_job_index_is_counted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "indexes"
            index.mkdir()
            (index / "cache").write_bytes(b"123")
            budget = WorkspaceBudget(
                root, max_bytes=5, min_free_bytes=0, index_dir=index
            )
            (root / "raw").write_bytes(b"12")
            self.assertEqual(budget.check()["total_bytes"], 5)
            self.assertEqual(budget.check()["index_growth_bytes"], 0)

    def test_replaced_root_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "job"
            root.mkdir()
            budget = WorkspaceBudget(root, max_bytes=10, min_free_bytes=0)
            root.rename(root.with_name("replaced"))
            root.mkdir()
            with self.assertRaises(AnalysisError) as failure:
                budget.check()
            self.assertEqual(failure.exception.code, "ANALYSIS_UNSAFE_PATH")

    def test_download_checks_space_before_each_chunk_and_closes_response(self):
        storage = ObjectStore.__new__(ObjectStore)
        storage.bucket = "fixture"
        closed = []
        response = SimpleNamespace(
            stream=lambda size, *, decode_content: iter((b"12", b"34")),
            close=lambda: closed.append("close"),
            release_conn=lambda: closed.append("release"),
        )
        storage.client = SimpleNamespace(get_object=lambda *args: response)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "raw"
            checks = []

            def before_write(size):
                checks.append(size)
                if len(checks) == 2:
                    raise AnalysisError(
                        "ANALYSIS_DISK_SPACE", "fixture", retryable=True
                    )

            with self.assertRaises(AnalysisError) as failure:
                storage.download(
                    "fixture",
                    destination,
                    expected_size=4,
                    max_bytes=4,
                    before_write=before_write,
                )
            self.assertEqual(failure.exception.code, "ANALYSIS_DISK_SPACE")
            self.assertEqual(destination.read_bytes(), b"12")
            self.assertEqual(checks, [2, 2])
        self.assertEqual(closed, ["close", "release"])

    def test_actual_child_is_killed_for_combined_disk_usage(self):
        original = subprocess.Popen
        children = []

        def launch(*args, **kwargs):
            script = "from pathlib import Path;import time;Path('first').write_bytes(b'x'*120000);Path('second').write_bytes(b'x'*120000);time.sleep(30)"
            child = original([sys.executable, "-c", script], **kwargs)
            children.append(child)
            return child

        with tempfile.TemporaryDirectory() as directory:
            context = {
                "kind": "IMPORT_COLLAPSED",
                "pipeline": "folded_v1",
                "format": "folded",
                "work_dir": directory,
                "limits": {
                    "wall_seconds": 3,
                    "cpu_seconds": 2,
                    "output_bytes": 1024**2,
                    "temp_bytes": 200000,
                    "min_free_bytes": 0,
                },
            }
            with (
                patch("drop_analyzer.isolate.subprocess.Popen", side_effect=launch),
                self.assertRaises(AnalysisError) as failure,
            ):
                analyze_isolated(context, check_lease=lambda: None)
            self.assertEqual(failure.exception.code, "ANALYSIS_DISK_LIMIT")
            self.assertIsNotNone(children[0].poll())
        self.assertFalse(Path(directory).exists())

    def test_multiple_files_are_counted_together(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = WorkspaceBudget(root, max_bytes=10, min_free_bytes=0)
            (root / "a").write_bytes(b"123456")
            (root / "nested").mkdir()
            (root / "nested" / "b").write_bytes(b"12345")
            with self.assertRaises(AnalysisError) as failure:
                budget.check()
            self.assertEqual(failure.exception.code, "ANALYSIS_DISK_LIMIT")

    def test_free_space_is_checked_before_incoming_write(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = WorkspaceBudget(directory, max_bytes=100, min_free_bytes=10)
            with patch(
                "drop_analyzer.disk.shutil.disk_usage",
                return_value=SimpleNamespace(free=12),
            ):
                with self.assertRaises(AnalysisError) as failure:
                    budget.check(additional_bytes=3)
            self.assertEqual(failure.exception.code, "ANALYSIS_DISK_SPACE")
            self.assertTrue(failure.exception.retryable)

    def test_checkpoint_baseline_is_not_new_download_or_disk_growth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "job"
            index = Path(directory) / "indexes"
            root.mkdir()
            index.mkdir()
            digest = "a" * 64
            checkpoint = index / f"hprof-{digest}.sqlite3"
            checkpoint.write_bytes(b"old-checkpoint")
            budget = WorkspaceBudget(
                root,
                max_bytes=6,
                min_free_bytes=0,
                index_dir=index,
                index_digest=digest,
            )
            (root / "raw").write_bytes(b"1234")
            self.assertEqual(budget.check()["total_bytes"], 4)
            checkpoint.write_bytes(b"old-checkpoint-new")
            with self.assertRaises(AnalysisError) as failure:
                budget.check()
            self.assertEqual(failure.exception.code, "ANALYSIS_DISK_LIMIT")

    def test_symlink_is_rejected_without_reading_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "job"
            root.mkdir()
            outside = Path(directory) / "outside"
            outside.write_bytes(b"outside")
            budget = WorkspaceBudget(root, max_bytes=100, min_free_bytes=0)
            try:
                (root / "link").symlink_to(outside)
            except OSError as error:
                self.skipTest(f"OS disallows fixture symlink: {error.errno}")
            with self.assertRaises(AnalysisError) as failure:
                budget.check()
            self.assertEqual(failure.exception.code, "ANALYSIS_UNSAFE_PATH")
            self.assertEqual(outside.read_bytes(), b"outside")

    @unittest.skipUnless(os.name == "posix", "POSIX special file fixture")
    def test_fifo_is_rejected_without_opening_it(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = WorkspaceBudget(directory, max_bytes=100, min_free_bytes=0)
            os.mkfifo(Path(directory) / "pipe")
            with self.assertRaises(AnalysisError) as failure:
                budget.check()
            self.assertEqual(failure.exception.code, "ANALYSIS_UNSAFE_PATH")
