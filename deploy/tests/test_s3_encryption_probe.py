"""Offline private-fixture regressions; no MinIO/network access is performed."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from minio.error import S3Error
import urllib3

import s3_encryption_probe as probe


PAYLOAD = b"synthetic imported fixture\x00" * (probe.PAYLOAD_BYTES // 27 + 1)
PAYLOAD = PAYLOAD[: probe.PAYLOAD_BYTES]


class Response:
    def __init__(self, body=b"", headers=None, status=200):
        self.body = io.BytesIO(body)
        self.headers, self.status = headers or {}, status
        self.closed = self.released = False

    def read(self, amount, *, decode_content=None):
        return self.body.read(amount)

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class Client:
    def __init__(self, algorithm="AES256", failure=None):
        self.algorithm, self.failure = algorithm, failure
        self.objects = {
            probe.OBJECT: PAYLOAD,
            probe.LEGACY_OBJECT: PAYLOAD,
            probe.ENCODED_OBJECT: probe.encoded_payload(PAYLOAD),
        }
        self.metadata = {probe.ENCODED_OBJECT: probe.ENCODED_METADATA.copy()}
        self.puts, self.configured = [], False

    def bucket_exists(self, bucket):
        return True

    def make_bucket(self, bucket):
        pass

    def set_bucket_encryption(self, bucket, config):
        self.algorithm, self.configured = config.rule.sse_algorithm, True

    def get_bucket_encryption(self, bucket):
        return SimpleNamespace(rule=SimpleNamespace(sse_algorithm=self.algorithm))

    def headers(self, key):
        return {
            **self.metadata.get(key, {}),
            "x-amz-server-side-encryption": self.algorithm,
            "x-amz-meta-sha256": hashlib.sha256(self.objects[key]).hexdigest(),
        }

    def stat_object(self, bucket, key):
        return SimpleNamespace(metadata=self.headers(key))

    def get_object(self, bucket, key):
        if self.failure and key in {
            probe.OBJECT,
            probe.LEGACY_OBJECT,
            probe.ENCODED_OBJECT,
        }:
            raise self.failure
        return Response(self.objects[key], self.headers(key))

    def put_object(self, bucket, key, stream, length, **kwargs):
        self.puts.append(kwargs)
        self.objects[key] = stream.read()
        self.metadata[key] = kwargs.get("metadata", {}).copy()


def http():
    return SimpleNamespace(
        request=Mock(side_effect=lambda *a, **kw: Response(b"ready"))
    )


def s3error(code="InternalError", message="failed to decrypt object key", status=500):
    return S3Error(SimpleNamespace(status=status), code, message, None, None, None)


class ProbeTests(unittest.TestCase):
    def test_encoded_roundtrip_checks_exact_bytes_and_system_metadata(self):
        client = Client()
        encoded = probe.encoded_payload(PAYLOAD)
        client.get_object = Mock(
            return_value=urllib3.response.HTTPResponse(
                body=io.BytesIO(encoded),
                headers=client.headers(probe.ENCODED_OBJECT),
                status=200,
                preload_content=False,
            )
        )
        result = probe.verify_roundtrip(
            client,
            probe.ENCODED_OBJECT,
            encoded,
            expected_metadata=probe.ENCODED_METADATA,
        )
        self.assertTrue(result["system_metadata_preserved"])
        self.assertEqual(result["sha256"], hashlib.sha256(encoded).hexdigest())

    def test_encoded_roundtrip_rejects_missing_system_metadata(self):
        client = Client()
        client.metadata = {}
        with self.assertRaisesRegex(
            probe.ProbeFailure, "SYSTEM_METADATA_NOT_PRESERVED"
        ):
            probe.verify_roundtrip(
                client,
                probe.ENCODED_OBJECT,
                probe.encoded_payload(PAYLOAD),
                expected_metadata=probe.ENCODED_METADATA,
            )

    def test_fixture_is_real_two_mib_payload_and_automatic_put_has_no_sse_arg(self):
        self.assertEqual(len(PAYLOAD), 2 * 1024**2)
        client = Client()
        result = probe.run_phase("write", client, http(), PAYLOAD)
        self.assertTrue(result["plaintext_roundtrip"])
        self.assertTrue(client.configured)
        self.assertEqual(
            client.puts[0]["metadata"]["sha256"], hashlib.sha256(PAYLOAD).hexdigest()
        )
        self.assertNotIn("sse", client.puts[0])

    def test_legacy_phase_proves_absent_encryption_before_restart(self):
        client = Client("absent")
        result = probe.run_phase("legacy-write", client, http(), PAYLOAD)
        self.assertTrue(result["legacy_plaintext_confirmed"])
        self.assertEqual(result["get_algorithm"], "absent")
        self.assertFalse(client.configured)

    def test_kms_default_is_not_mislabeled_as_sse_s3(self):
        with self.assertRaises(probe.ProbeFailure) as raised:
            probe.verify_roundtrip(Client("aws:kms"), probe.OBJECT, PAYLOAD)
        self.assertEqual(
            raised.exception.algorithms,
            {"get_algorithm": "aws:kms", "stat_algorithm": "aws:kms"},
        )

    def test_unknown_header_is_sanitized(self):
        self.assertEqual(
            probe.encryption_algorithm(
                {"x-amz-server-side-encryption": "SECRET diagnostic"}
            ),
            "absent",
        )

    def test_wrong_key_requires_specific_error_for_all_historical_objects(self):
        result = probe.run_phase(
            "wrong-key", Client(failure=s3error()), http(), PAYLOAD
        )
        self.assertTrue(result["decryption_refused"])
        self.assertTrue(result["current_key_roundtrip"])
        self.assertEqual(result["refused_object_count"], 3)
        self.assertTrue(result["decryption_reason_matched"])
        self.assertEqual(len(result["refusals"]), 3)

    def test_exact_minio_release_builtin_kms_error_is_accepted(self):
        # Imported API shape from RELEASE.2025-07-23T15-54-02Z
        # internal/kms/errors.go ErrDecrypt; no server/network is used here.
        error = s3error(
            code="kms:InvalidCiphertextException",
            message="failed to decrypt ciphertext",
            status=400,
        )
        result = probe.run_phase("wrong-key", Client(failure=error), http(), PAYLOAD)
        self.assertTrue(result["decryption_refused"])
        self.assertEqual(result["s3_error_code"], "kms:InvalidCiphertextException")

    def test_timeout_does_not_count_as_decryption_refusal(self):
        with self.assertRaises(TimeoutError):
            probe.run_phase(
                "wrong-key", Client(failure=TimeoutError()), http(), PAYLOAD
            )

    def test_generic_auth_missing_and_service_errors_fail_closed(self):
        cases = [
            s3error(message="Internal server error"),
            s3error(code="AccessDenied", status=403),
            s3error(code="NoSuchKey", status=404),
            s3error(code="SlowDown", status=503),
        ]
        for error in cases:
            with self.subTest(code=error.code):
                with self.assertRaisesRegex(
                    probe.ProbeFailure, "UNEXPECTED_S3_ERROR"
                ) as raised:
                    probe.run_phase("wrong-key", Client(failure=error), http(), PAYLOAD)
                self.assertEqual(raised.exception.details["s3_error_code"], error.code)

    def test_unhealthy_service_after_error_does_not_publish_success(self):
        pool = http()
        pool.request.side_effect = [Response(b"ready"), Response(status=503)]
        with self.assertRaisesRegex(probe.ProbeFailure, "STORAGE_NOT_READY"):
            probe.run_phase("wrong-key", Client(failure=s3error()), pool, PAYLOAD)

    def test_missing_current_key_functionality_does_not_publish_refusal(self):
        client = Client(failure=s3error())
        client.put_object = Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            probe.run_phase("wrong-key", client, http(), PAYLOAD)

    def test_cli_unexpected_failure_output_has_no_exception_secret(self):
        client = Client(failure=TimeoutError("SECRET profile and credentials"))
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(probe, "Minio", return_value=client),
            patch.object(probe.urllib3, "PoolManager", return_value=http()),
            patch.object(probe.Path, "read_text", return_value="fixture-secret"),
            patch.object(probe.Path, "open", return_value=io.BytesIO(PAYLOAD)),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = probe.main(["wrong-key"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            json.loads(stderr.getvalue())["error"], "UNEXPECTED_PROBE_FAILURE"
        )
        self.assertNotIn("SECRET", stderr.getvalue())
        self.assertNotIn("fixture-secret", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
