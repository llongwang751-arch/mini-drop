"""Regression checks for the private S3 gateway; only UUID fixture buckets.

Run inside the configured analyzer service so credentials stay in mounted
Secret files. The production artifact bucket is never queried or changed.
"""

import hashlib
import io
import unittest
from urllib.parse import urlencode
from uuid import uuid4

from minio import Minio
import urllib3

from drop_analyzer.config import Config


class StorageGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config.from_env()
        cls.bucket = "drop-gateway-test-" + uuid4().hex
        cls.http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=15), retries=False)
        cls.client = Minio(cls.config.s3_endpoint, access_key=cls.config.s3_access_key,
                           secret_key=cls.config.s3_secret_key, secure=cls.config.s3_secure,
                           http_client=cls.http)
        cls.base = ("https://" if cls.config.s3_secure else "http://") + cls.config.s3_endpoint
        cls.client.make_bucket(cls.bucket)

    @classmethod
    def tearDownClass(cls):
        # Only remove the exact UUID fixture bucket created by this process.
        for item in cls.client.list_objects(cls.bucket, recursive=True):
            cls.client.remove_object(cls.bucket, item.object_name)
        cls.client.remove_bucket(cls.bucket)
        cls.http.clear()

    def get_bytes(self, name):
        response = self.client.get_object(self.bucket, name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def test_signed_put_get_head_metadata(self):
        body = b"gateway-fixture;leaf 23\n"
        digest = hashlib.sha256(body).hexdigest()
        self.client.put_object(self.bucket, "normal.folded", io.BytesIO(body), len(body),
                               content_type="text/plain", metadata={"sha256": digest})
        self.assertEqual(self.get_bytes("normal.folded"), body)
        metadata = self.client.stat_object(self.bucket, "normal.folded")
        self.assertEqual(metadata.size, len(body))
        self.assertEqual(metadata.metadata.get("x-amz-meta-sha256"), digest)
        signed = self.client.presigned_get_object(self.bucket, "normal.folded")
        response = self.http.request("GET", signed)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, body)

    def test_presigned_put_and_get(self):
        body = b"gateway-fixture;presigned 17\n"
        url = self.client.presigned_put_object(self.bucket, "presigned.folded")
        response = self.http.request("PUT", url, body=body, headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.get_bytes("presigned.folded"), body)

    def test_unsigned_trailer_is_rejected_before_write(self):
        for value in ("STREAMING-UNSIGNED-PAYLOAD-TRAILER", "streaming-unsigned-payload-trailer"):
            with self.subTest(value=value):
                response = self.http.request("PUT", f"{self.base}/{self.bucket}/blocked-trailer",
                                             body=b"fixture", headers={"X-Amz-Content-Sha256": value})
                self.assertEqual(response.status, 403)
                self.assertIn("text/html", response.headers.get("Content-Type", ""))
        self.assertFalse(any(item.object_name == "blocked-trailer" for item in self.client.list_objects(self.bucket)))

    def test_sts_query_action_is_rejected(self):
        for action in ("AssumeRole", "AssumeRoleWithWebIdentity", "AssumeRoleWithLDAPIdentity"):
            with self.subTest(action=action):
                response = self.http.request("POST", f"{self.base}/?{urlencode({'Action': action, 'Version': '2011-06-15'})}", body=b"")
                self.assertEqual(response.status, 403)
                self.assertIn("text/html", response.headers.get("Content-Type", ""))

    def test_sts_form_action_is_rejected(self):
        response = self.http.request("POST", f"{self.base}/", body=urlencode({"Action": "AssumeRole", "Version": "2011-06-15"}).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(response.status, 403)
        self.assertIn("text/html", response.headers.get("Content-Type", ""))

    def test_management_endpoints_are_rejected(self):
        for path in ("/minio/admin/v3/info", "/minio/admin/v3/list-users", "/minio/v2/metrics/cluster"):
            with self.subTest(path=path):
                response = self.http.request("GET", self.base + path)
                self.assertEqual(response.status, 403)
                self.assertIn("text/html", response.headers.get("Content-Type", ""))

    def test_replication_flag_is_rejected(self):
        signed = self.client.presigned_put_object(self.bucket, "blocked-replication")
        response = self.http.request("PUT", signed, body=b"fixture",
                                     headers={"X-Minio-Source-Replication-Request": "true"})
        self.assertEqual(response.status, 403)
        self.assertFalse(any(item.object_name == "blocked-replication" for item in self.client.list_objects(self.bucket)))

    def test_replication_encryption_headers_cannot_corrupt_fixture(self):
        body = b"gateway-fixture;replication-header-regression 31\n"
        signed = self.client.presigned_put_object(self.bucket, "encryption-fixture.folded")
        headers = {"Content-Type": "text/plain",
                   "X-Minio-Replication-Server-Side-Encryption-Sealed-Key": "ZmFrZS1maXh0dXJlLWtleQ==",
                   "X-Minio-Replication-Server-Side-Encryption-Seal-Algorithm": "DAREv2-HMAC-SHA256",
                   "X-Minio-Replication-Server-Side-Encryption-Iv": "ZmFrZS1maXh0dXJlLWl2"}
        response = self.http.request("PUT", signed, body=body, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.get_bytes("encryption-fixture.folded"), body)
        metadata = self.client.stat_object(self.bucket, "encryption-fixture.folded")
        self.assertFalse(any("sealed-key" in key.lower() or "replication" in key.lower() for key in metadata.metadata))


if __name__ == "__main__":
    unittest.main(verbosity=2)
