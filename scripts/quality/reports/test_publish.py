"""导入合成收据测试：校验状态、未知指标、来源和不可变发布，不代表真实测试执行。"""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import publish


def fixture():
    return {
        "schema_version": 1,
        "id": "fixture-run-001",
        "title": "明确导入的合成记录",
        "category": "system",
        "suite": "fixture-suite",
        "status": "passed",
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:01Z",
        "environment": "linux_isolated",
        "source_commit": None,
        "source_sha256": "a" * 64,
        "provenance": "imported_fixture",
        "fixture_scope": "合成摘要，不代表真实测试",
        "counts": {"passed": 1, "failed": 0, "skipped": 0, "total": 1},
        "metrics": [],
        "checks": [],
        "limits": ["仅验证发布边界"],
    }


def encoded(row):
    return (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()


class PublishTests(unittest.TestCase):
    def test_existing_oversized_record_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            row = fixture()
            data = encoded(row)
            (directory / (row["id"] + ".json")).write_bytes(
                b" " * (publish.MAX_BYTES + 1)
            )
            with self.assertRaises(ValueError):
                publish.publish(data, directory, hashlib.sha256(data).hexdigest())

    def test_integer_and_failed_check_contract(self):
        for value in [None, 1.0, True]:
            row = fixture()
            row["counts"]["failed"] = value
            with self.assertRaises((ValueError, publish.jsonschema.ValidationError)):
                publish.validate(encoded(row))
        row = fixture()
        row["schema_version"] = 1.0
        with self.assertRaises(ValueError):
            publish.validate(encoded(row))
        row = fixture()
        row["checks"] = [
            {
                "id": "failed-check",
                "label": "必须保留失败",
                "status": "failed",
                "detail": "",
            }
        ]
        with self.assertRaises(ValueError):
            publish.validate(encoded(row))

    def test_rejects_skipped_as_success_and_bad_denominator(self):
        for counts in [
            {"passed": 1, "failed": 0, "skipped": 1, "total": 1},
            {"passed": 0, "failed": 0, "skipped": 1, "total": 1},
        ]:
            row = fixture()
            row["counts"] = counts
            with self.assertRaises(ValueError):
                publish.validate(encoded(row))

    def test_null_human_metric_stays_unknown(self):
        row = fixture()
        row["metrics"] = [
            {
                "id": "human-accuracy",
                "label": "人工准确率",
                "value": None,
                "unit": "ratio",
            }
        ]
        self.assertIsNone(publish.validate(encoded(row))["metrics"][0]["value"])

    def test_duplicate_and_private_text_rejected(self):
        data = encoded(fixture())
        with self.assertRaises(ValueError):
            publish.validate(b'{"id":"duplicate-run",' + data[1:])
        row = fixture()
        row["limits"] = ["https://fixture.invalid/presigned"]
        with self.assertRaises(ValueError):
            publish.validate(encoded(row))

    def test_immutable_publish_and_source_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "records"
            data = encoded(fixture())
            sha = hashlib.sha256(data).hexdigest()
            publish.publish(data, out, sha)
            publish.publish(data, out, sha)
            row = fixture()
            row["title"] = "覆盖旧失败不可接受"
            changed = encoded(row)
            with self.assertRaises(ValueError):
                publish.publish(changed, out, hashlib.sha256(changed).hexdigest())
            with self.assertRaises(ValueError):
                publish.publish(data, out, "0" * 64)
            self.assertEqual((out / (row["id"] + ".json")).read_bytes(), data)

    def test_status_and_repeated_metric(self):
        row = fixture()
        row["status"] = "failed"
        with self.assertRaises(ValueError):
            publish.validate(encoded(row))
        row = fixture()
        metric = {"id": "test-metric", "label": "测试", "value": 1, "unit": "count"}
        row["metrics"] = [metric, copy.deepcopy(metric)]
        with self.assertRaises(ValueError):
            publish.validate(encoded(row))


if __name__ == "__main__":
    unittest.main()
