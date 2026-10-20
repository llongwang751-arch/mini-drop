"""Real S3 metadata round-trip, using only a new UUID imported fixture bucket.

Run with the configured analyzer image and read-only mounts of this file and
deploy/backup_metadata.py. No production bucket is listed or modified.
"""

import gzip
import hashlib
import io
import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4

import urllib3
from minio import Minio

sys.path.insert(0, "/opt/drop-backup")
from backup_metadata import capture_metadata, validate_metadata, verify_metadata


def setting(name):
    file = os.getenv(name + "_FILE")
    return Path(file).read_text().strip() if file else os.environ[name]


class BackupMetadataS3Tests(unittest.TestCase):
    def test_compressed_bytes_and_metadata_round_trip(self):
        endpoint = setting("S3_ENDPOINT")
        secure = endpoint.startswith("https://")
        http = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=5, read=15), retries=False
        )
        client = Minio(
            endpoint.removeprefix("https://").removeprefix("http://"),
            access_key=setting("S3_ACCESS_KEY"),
            secret_key=setting("S3_SECRET_KEY"),
            secure=secure,
            http_client=http,
        )
        bucket = "drop-backup-test-" + uuid4().hex
        created = False
        try:
            if client.bucket_exists(bucket):
                raise RuntimeError("UUID backup fixture bucket already exists")
            client.make_bucket(bucket)
            created = True
            body = gzip.compress(b"imported-backup-fixture;leaf 23\n", mtime=0)
            metadata = {
                "sha256": hashlib.sha256(body).hexdigest(),
                "source": "imported-fixture",
                "Content-Encoding": "gzip",
                "Content-Language": "zh-CN",
                "Content-Disposition": 'attachment; filename="fixture.folded.gz"',
                "Cache-Control": "private, max-age=0",
            }
            client.put_object(
                bucket,
                "source",
                io.BytesIO(body),
                len(body),
                content_type="application/octet-stream",
                metadata=metadata,
            )
            original = client.stat_object(bucket, "source")
            saved = capture_metadata(original.metadata)
            response = client.get_object(bucket, "source")
            try:
                backed_up = b"".join(response.stream(1024, decode_content=False))
            finally:
                response.close()
                response.release_conn()
            self.assertEqual(backed_up, body)
            self.assertEqual(
                saved["x-amz-meta-sha256"], hashlib.sha256(backed_up).hexdigest()
            )
            client.put_object(
                bucket,
                "restored",
                io.BytesIO(backed_up),
                len(backed_up),
                content_type=original.content_type,
                metadata=validate_metadata(saved),
            )
            restored = client.stat_object(bucket, "restored")
            self.assertEqual(restored.content_type, original.content_type)
            verify_metadata(saved, restored.metadata)
            self.assertEqual(restored.size, len(body))
        finally:
            if created:
                for name in ("source", "restored"):
                    client.remove_object(bucket, name)
                client.remove_bucket(bucket)
            http.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)
