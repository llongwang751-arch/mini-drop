"""备份元数据策略回归，所有 HTTP 头值均为导入夹具。"""

import importlib.util
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "backup_metadata.py"
spec = importlib.util.spec_from_file_location("backup_metadata", path)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class BackupMetadataTests(unittest.TestCase):
    def test_round_trip_preserves_only_object_metadata(self):
        headers = {
            "X-Amz-Meta-Sha256": "a" * 64,
            "x-amz-meta-imported": "fixture",
            "Content-Encoding": "gzip",
            "Content-Disposition": 'attachment; filename="fixture.folded"',
            "Cache-Control": "private, max-age=0",
            "Content-Language": "zh-CN",
            "Content-Type": "text/plain",
            "ETag": '"opaque"',
            "Authorization": "fixture-secret-do-not-copy",
            "X-Amz-Server-Side-Encryption": "AES256",
            "X-Amz-Server-Side-Encryption-Customer-Key": "fixture-key-do-not-copy",
        }
        expected = {
            key.lower(): value
            for key, value in headers.items()
            if key.lower().startswith("x-amz-meta-")
            or key.lower() in policy.OBJECT_HEADERS
        }
        result = policy.capture_metadata(headers)
        self.assertEqual(result, expected)
        self.assertEqual(policy.validate_metadata(result), expected)
        policy.verify_metadata(result, headers)

    def test_old_backups_without_metadata_remain_valid(self):
        self.assertEqual(policy.validate_metadata(None), {})
        policy.verify_metadata(
            {}, {"ETag": "opaque", "content-type": "application/octet-stream"}
        )

    def test_restore_rejects_non_metadata_headers(self):
        for key in (
            "authorization",
            "x-amz-copy-source",
            "x-amz-server-side-encryption",
            "x-amz-meta-",
            "content-type",
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy.validate_metadata({key: "imported-fixture"})

    def test_malformed_or_injected_metadata_is_rejected(self):
        for value in (
            {"x-amz-meta-ok": "bad\r\nInjected: fixture"},
            {"x-amz-meta-ok": "bad\x00value"},
            {"x-amz-meta-bad key": "value"},
            {"X-Amz-Meta-Ok": "a", "x-amz-meta-ok": "b"},
            {"x-amz-meta-ok": ["value"]},
            ["not-a-map"],
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.validate_metadata(value)

    def test_metadata_is_bounded(self):
        for value in (
            {"x-amz-meta-large": "x" * 8192},
            {f"x-amz-meta-{i}": "v" for i in range(129)},
        ):
            with self.subTest(size=len(value)), self.assertRaises(ValueError):
                policy.validate_metadata(value)

    def test_restore_detects_lost_or_changed_metadata(self):
        for actual in ({}, {"x-amz-meta-sha256": "different"}):
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                policy.verify_metadata({"x-amz-meta-sha256": "expected"}, actual)


if __name__ == "__main__":
    unittest.main()
