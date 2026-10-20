import hashlib
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from drop_analyzer.config import Config
from drop_analyzer.errors import AnalysisError, LeaseLost
from drop_analyzer.health import healthy
from drop_analyzer.repository import Job
from drop_analyzer.worker import LeaseGuard, cleanup_stale_workdirs, process_job


class FakeRepository:
    def __init__(self, raw):
        self.raw, self.completed, self.valid = raw, None, True
        self.partials = []

    def input_artifact(self, job):
        return {
            "id": 40,
            "object_key": "raw/profile.folded",
            "size": len(self.raw),
            "sha256": hashlib.sha256(self.raw).hexdigest(),
            "content_type": "text/plain",
        }

    def renew(self, job):
        return self.valid

    def complete(self, job, result, artifacts):
        if not self.valid:
            raise LeaseLost()
        self.completed = (result, artifacts)

    def save_partial(self, job, result, artifact):
        if not self.valid:
            raise LeaseLost()
        self.partials.append(result)


class FakeStorage:
    def __init__(self, raw, fail_on_upload=0, before_upload=None):
        self.raw, self.uploads, self.fail_on_upload, self.before_upload = (
            raw,
            [],
            fail_on_upload,
            before_upload,
        )

    def download(self, key, destination, **kwargs):
        destination.write_bytes(self.raw)
        return self.raw

    def upload(self, key, body, content_type):
        if self.before_upload:
            self.before_upload()
        if len(self.uploads) + 1 == self.fail_on_upload:
            raise AnalysisError("STORAGE_ERROR", "Upload failed", retryable=True)
        self.uploads.append((key, body))
        return {
            "object_key": key,
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "content_type": content_type,
        }


def job():
    return Job(
        id=1,
        task_id=2,
        attempt_id=3,
        attempt=2,
        max_attempts=3,
        worker_id="worker-1",
        kind="PROC_CPU",
        params={"duration_seconds": 5},
        collection_attempt=1,
        pipeline="folded_v1",
    )


class WorkerTests(unittest.TestCase):
    def test_combined_download_budget_is_checked_before_any_download(self):
        raw = b"root;leaf 1"
        repository, storage = FakeRepository(raw), FakeStorage(raw)
        repository.auxiliary_artifacts = lambda job: [{"size": len(raw)}]
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(storage, "download", wraps=storage.download) as download,
        ):
            with self.assertRaises(AnalysisError) as failure:
                process_job(
                    job(),
                    repository,
                    storage,
                    Config(
                        "x",
                        "x",
                        "x",
                        "x",
                        work_dir=root,
                        analysis_download_bytes=len(raw),
                    ),
                )
            self.assertEqual(failure.exception.code, "INPUT_LIMIT_EXCEEDED")
            download.assert_not_called()
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertIsNone(repository.completed)
        self.assertEqual(storage.uploads, [])

    def test_low_disk_space_never_downloads_or_publishes_and_cleans_workdir(self):
        raw = b"root;leaf 1"
        repository, storage = FakeRepository(raw), FakeStorage(raw)
        with (
            tempfile.TemporaryDirectory() as root,
            patch(
                "drop_analyzer.disk.shutil.disk_usage",
                return_value=SimpleNamespace(free=0),
            ),
            patch.object(storage, "download", wraps=storage.download) as download,
        ):
            with self.assertRaises(AnalysisError) as failure:
                process_job(
                    job(),
                    repository,
                    storage,
                    Config("x", "x", "x", "x", work_dir=root),
                )
            self.assertEqual(failure.exception.code, "ANALYSIS_DISK_SPACE")
            download.assert_not_called()
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertIsNone(repository.completed)

    def test_task_byte_budget_is_enforced_before_analysis_and_publication(self):
        raw = b"process;thread 42"
        repository, storage = FakeRepository(raw), FakeStorage(raw)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(AnalysisError) as failure:
                process_job(
                    replace(job(), params={"budget": {"max_file_bytes": 1}}),
                    repository,
                    storage,
                    Config("x", "x", "x", "x", work_dir=root),
                )
            self.assertEqual(failure.exception.code, "INPUT_LIMIT_EXCEEDED")
            self.assertIsNone(repository.completed)

    def test_upload_all_outputs_before_commit_and_cleanup(self):
        raw = b"process;thread 42"
        repository, storage = FakeRepository(raw), FakeStorage(raw)
        with tempfile.TemporaryDirectory() as root:
            config = Config("unused", "unused", "unused", "unused", work_dir=root)
            process_job(job(), repository, storage, config)
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertEqual(len(repository.completed[1]), 4)
        self.assertTrue(all("/runs/2/" in key for key, _ in storage.uploads))
        report = json.loads(
            next(body for key, body in storage.uploads if key.endswith("report.json"))
        )
        self.assertEqual(report["summary"]["unit"], "cpu_ticks")
        self.assertEqual(report["metadata"]["unit"], "cpu_ticks")
        self.assertEqual(report["suggestions"], [])

    def test_storage_failure_never_publishes_success(self):
        raw = b"root;leaf 5"
        repository, storage = FakeRepository(raw), FakeStorage(raw, fail_on_upload=3)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(AnalysisError):
                process_job(
                    job(),
                    repository,
                    storage,
                    Config("x", "x", "x", "x", work_dir=root),
                )
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertIsNone(repository.completed)
        self.assertEqual(len(repository.partials), 1)
        self.assertEqual(repository.partials[0]["completeness"], "partial")
        self.assertEqual(repository.partials[0]["summary"]["total_samples"], 5)

    def test_partial_is_saved_before_diagnostic_rule_failure(self):
        repository, storage = (
            FakeRepository(b"root;work 5"),
            FakeStorage(b"root;work 5"),
        )
        repository.rule_settings = lambda: (_ for _ in ()).throw(
            AnalysisError("RULES_UNAVAILABLE", "Rules unavailable")
        )
        with tempfile.TemporaryDirectory() as root, self.assertRaises(AnalysisError):
            process_job(
                job(), repository, storage, Config("x", "x", "x", "x", work_dir=root)
            )
        self.assertEqual(len(repository.partials), 1)
        self.assertEqual(storage.uploads, [])
        self.assertIsNone(repository.completed)

    def test_parser_failure_never_publishes_partial(self):
        repository, storage = FakeRepository(b"not folded"), FakeStorage(b"not folded")
        with tempfile.TemporaryDirectory() as root, self.assertRaises(AnalysisError):
            process_job(
                job(), repository, storage, Config("x", "x", "x", "x", work_dir=root)
            )
        self.assertEqual(repository.partials, [])
        self.assertEqual(storage.uploads, [])

    def test_invalid_checkpoint_does_not_change_complete_success_semantics(self):
        repository, storage = (
            FakeRepository(b"root;work 5"),
            FakeStorage(b"root;work 5"),
        )
        with (
            tempfile.TemporaryDirectory() as root,
            patch("drop_analyzer.worker.build_partial", return_value=None),
        ):
            process_job(
                job(), repository, storage, Config("x", "x", "x", "x", work_dir=root)
            )
        self.assertEqual(repository.partials, [])
        self.assertIsNotNone(repository.completed)
        self.assertNotIn("completeness", repository.completed[0])

    def test_checkpoint_lease_loss_stops_before_result_upload(self):
        repository, storage = (
            FakeRepository(b"root;work 5"),
            FakeStorage(b"root;work 5"),
        )
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(repository, "save_partial", side_effect=LeaseLost()),
            self.assertRaises(LeaseLost),
        ):
            process_job(
                job(), repository, storage, Config("x", "x", "x", "x", work_dir=root)
            )
        self.assertEqual(storage.uploads, [])
        self.assertIsNone(repository.completed)

    def test_stale_owner_cannot_commit_after_upload(self):
        raw = b"root;leaf 5"
        repository = FakeRepository(raw)
        storage = FakeStorage(
            raw, before_upload=lambda: setattr(repository, "valid", False)
        )
        with tempfile.TemporaryDirectory() as root, self.assertRaises(LeaseLost):
            process_job(
                job(),
                repository,
                storage,
                Config("x", "x", "x", "x", work_dir=root),
            )
        self.assertIsNone(repository.completed)

    def test_lease_guard_detects_lost_ownership(self):
        repository = FakeRepository(b"root 1")
        repository.valid = False
        with LeaseGuard(repository, job(), 0.01) as lease:
            self.assertTrue(lease.lost.wait(timeout=1))
            with self.assertRaises(LeaseLost):
                lease.check()

    def test_scavenger_and_health(self):
        with tempfile.TemporaryDirectory() as root:
            stale = Path(root) / "job-123-abcdef"
            stale.mkdir()
            (stale / "profile").write_bytes(b"data")
            unrelated = Path(root) / "important"
            unrelated.mkdir()
            os.utime(stale, (0, 0))
            cleanup_stale_workdirs(root)
            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.exists())
            health = Path(root) / "health.json"
            health.write_text(json.dumps({"updated_at": time.time(), "ready": True}))
            self.assertTrue(healthy(str(health)))
            health.write_text(json.dumps({"updated_at": 1, "ready": True}))
            self.assertFalse(healthy(str(health)))


if __name__ == "__main__":
    unittest.main()
