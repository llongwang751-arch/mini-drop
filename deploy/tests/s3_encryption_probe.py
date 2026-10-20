"""Private SSE-S3 fixture; transport or unrelated S3 errors always fail closed."""

import argparse
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import sys

import urllib3
from minio import Minio
from minio.error import S3Error
from minio.sseconfig import SSEConfig, Rule

ENDPOINT = "encrypted-storage:9000"
BUCKET = "encryption-fixture"
OBJECT = "raw/private-profile.bin"
LEGACY_OBJECT = "raw/legacy-private-profile.bin"
ENCODED_OBJECT = "raw/legacy-encoded-private-profile.gz"
ENCODED_METADATA = {
    "Content-Encoding": "gzip",
    "Content-Language": "zh-CN",
    "Content-Disposition": "attachment; filename=imported-fixture.gz",
    "Cache-Control": "private, max-age=60",
}
PAYLOAD_BYTES = 2 * 1024 * 1024
PHASES = ("legacy-write", "encrypt-legacy", "write", "wrong-key", "restored-key")
# Some MinIO versions wrap a KMS authentication failure in InternalError.
# A generic InternalError without a specific cryptographic reason is rejected.
DECRYPTION_CODES = frozenset(
    {
        "InternalError",
        "XMinioKMSInternalError",
        "XMinioKMSDecryptionFailed",
        "KMS.InternalException",
        "XMinioObjectTampered",
        # Exact RELEASE.2025-07-23T15-54-02Z internal/kms/errors.go.
        "kms:InvalidCiphertextException",
        "kms:DecryptionFailed",
    }
)
DECRYPTION_REASONS = (
    "failed to decrypt",
    "decryption failed",
    "ciphertext is not authentic",
    "invalid ciphertext",
    "cipher: message authentication failed",
    "sio: data is not authentic",
    "sio: tag mismatch",
    "the requested object was modified and may be compromised",
)


class ProbeFailure(Exception):
    """Only fixed, non-sensitive diagnostic codes reach the CLI."""

    def __init__(self, code, *, algorithms=None, details=None):
        super().__init__(code)
        self.algorithms = algorithms or {}
        self.details = details or {}


def close_response(response):
    try:
        response.close()
    finally:
        response.release_conn()


def header(headers, name):
    return next(
        (value for key, value in headers.items() if key.lower() == name.lower()), None
    )


def encryption_algorithm(headers):
    value = header(headers, "x-amz-server-side-encryption")
    # Raw server values are never returned or logged.
    return value if value in {"AES256", "aws:kms"} else "absent"


def require_ready(client, http, *, bucket_exists=True):
    response = http.request(
        "GET",
        f"http://{ENDPOINT}/minio/health/ready",
        preload_content=False,
        redirect=False,
    )
    try:
        if response.status != 200 or len(response.read(1025)) > 1024:
            raise ProbeFailure("STORAGE_NOT_READY")
    finally:
        close_response(response)
    # This is a separate authenticated S3 operation, not merely a live HTTP port.
    if bucket_exists and not client.bucket_exists(BUCKET):
        raise ProbeFailure("FIXTURE_BUCKET_MISSING")


def encoded_payload(payload):
    return gzip.compress(payload[:2048] * 16, mtime=0)


def verify_roundtrip(
    client, key, payload, *, expected_algorithm="AES256", expected_metadata=None
):
    stat = client.stat_object(BUCKET, key)
    stat_algorithm = encryption_algorithm(stat.metadata or {})
    response = client.get_object(BUCKET, key)
    try:
        actual = response.read(len(payload) + 1, decode_content=False)
        encryption = encryption_algorithm(response.headers)
        metadata_sha = header(response.headers, "x-amz-meta-sha256")
        if response.status != 200:
            raise ProbeFailure("UNEXPECTED_OBJECT_STATUS")
        for name, value in (expected_metadata or {}).items():
            if (
                header(response.headers, name) != value
                or header(stat.metadata or {}, name) != value
            ):
                raise ProbeFailure("SYSTEM_METADATA_NOT_PRESERVED")
    finally:
        close_response(response)
    if actual != payload:
        raise ProbeFailure("PAYLOAD_MISMATCH")
    if encryption != expected_algorithm or stat_algorithm != expected_algorithm:
        raise ProbeFailure(
            "AUTOMATIC_SSE_S3_NOT_REPORTED",
            algorithms={"get_algorithm": encryption, "stat_algorithm": stat_algorithm},
        )
    digest = hashlib.sha256(actual).hexdigest()
    if metadata_sha != digest:
        raise ProbeFailure("SHA256_METADATA_MISMATCH")
    return {
        "get_algorithm": encryption,
        "stat_algorithm": stat_algorithm,
        "size": len(actual),
        "sha256": digest,
        "plaintext_roundtrip": True,
        "system_metadata_preserved": expected_metadata is not None,
    }


def write_automatic(client, key, payload, *, system_metadata=None):
    # Deliberately omit sse/SSE request headers: this proves server-side automatic
    # encryption rather than silently enabling it from the test client.
    client.put_object(
        BUCKET,
        key,
        io.BytesIO(payload),
        len(payload),
        content_type="application/octet-stream",
        metadata={
            "sha256": hashlib.sha256(payload).hexdigest(),
            **(system_metadata or {}),
        },
    )


def configure_aes256_default(client):
    client.set_bucket_encryption(BUCKET, SSEConfig(Rule.new_sse_s3_rule()))
    if client.get_bucket_encryption(BUCKET).rule.sse_algorithm != "AES256":
        raise ProbeFailure("BUCKET_AES256_DEFAULT_NOT_CONFIRMED")


def expected_decryption_error(error):
    status = getattr(error.response, "status", None)
    reason = (error.message or "").lower()
    # The built-in KMS uses distinct API codes and exact HTTP statuses. These
    # are passed through by cmd/api-errors.go, rather than XMinioKMS codes.
    if error.code == "kms:InvalidCiphertextException" and status != 400:
        return False
    if error.code == "kms:DecryptionFailed" and status != 500:
        return False
    return (
        status in {400, 403, 500}
        and error.code in DECRYPTION_CODES
        and any(marker in reason for marker in DECRYPTION_REASONS)
    )


def safe_error_details(error):
    codes = DECRYPTION_CODES | {
        "AccessDenied",
        "NoSuchKey",
        "SlowDown",
        "kms:KeyNotFound",
        "kms:NotAuthorized",
    }
    status = getattr(error.response, "status", None)
    return {
        "s3_error_code": error.code if error.code in codes else "other",
        "s3_error_status": status
        if isinstance(status, int) and 100 <= status <= 599
        else None,
        "decryption_reason_matched": any(
            marker in (error.message or "").lower() for marker in DECRYPTION_REASONS
        ),
    }


def run_phase(phase, client, http, payload):
    if phase not in PHASES:
        raise ProbeFailure("INVALID_PHASE")
    if len(payload) != PAYLOAD_BYTES:
        raise ProbeFailure("INVALID_FIXTURE_SIZE")
    require_ready(client, http, bucket_exists=phase not in {"legacy-write", "write"})
    if phase == "legacy-write":
        client.make_bucket(BUCKET)
        write_automatic(client, LEGACY_OBJECT, payload)
        result = verify_roundtrip(
            client, LEGACY_OBJECT, payload, expected_algorithm="absent"
        )
        compressed = encoded_payload(payload)
        write_automatic(
            client, ENCODED_OBJECT, compressed, system_metadata=ENCODED_METADATA
        )
        encoded_result = verify_roundtrip(
            client,
            ENCODED_OBJECT,
            compressed,
            expected_algorithm="absent",
            expected_metadata=ENCODED_METADATA,
        )
        require_ready(client, http)
        return {
            "phase": phase,
            "legacy_plaintext_confirmed": True,
            "encoded_object": encoded_result,
            **result,
        }
    if phase == "encrypt-legacy":
        # This reuses the production migration primitive, not a fixture-only
        # alternative. The isolated fixture has no application writers or DB.
        from encrypt_existing import Evidence, ReadBudget, S3Storage, migrate_object

        configure_aes256_default(client)
        outcome, baseline_only = migrate_object(
            S3Storage(client, BUCKET),
            LEGACY_OBJECT,
            Evidence(len(payload), hashlib.sha256(payload).hexdigest()),
            apply=True,
            budget=ReadBudget(4 * len(payload)),
            max_object_bytes=PAYLOAD_BYTES,
            assert_quiescent=lambda: None,
        )
        if outcome != "encrypted" or baseline_only:
            raise ProbeFailure("LEGACY_MIGRATION_NOT_PERFORMED")
        result = verify_roundtrip(client, LEGACY_OBJECT, payload)
        compressed = encoded_payload(payload)
        encoded_outcome, encoded_baseline = migrate_object(
            S3Storage(client, BUCKET),
            ENCODED_OBJECT,
            Evidence(len(compressed), hashlib.sha256(compressed).hexdigest()),
            apply=True,
            budget=ReadBudget(4 * len(compressed)),
            max_object_bytes=PAYLOAD_BYTES,
            assert_quiescent=lambda: None,
        )
        if encoded_outcome != "encrypted" or encoded_baseline:
            raise ProbeFailure("ENCODED_LEGACY_MIGRATION_NOT_PERFORMED")
        encoded_result = verify_roundtrip(
            client, ENCODED_OBJECT, compressed, expected_metadata=ENCODED_METADATA
        )
        require_ready(client, http)
        return {
            "phase": phase,
            "legacy_same_key_copy_verified": True,
            "encoded_object": encoded_result,
            **result,
        }
    if phase == "write":
        if not client.bucket_exists(BUCKET):
            client.make_bucket(BUCKET)
        configure_aes256_default(client)
        write_automatic(client, OBJECT, payload)
    if phase != "wrong-key":
        result = verify_roundtrip(client, OBJECT, payload)
        verify_roundtrip(client, LEGACY_OBJECT, payload)
        encoded_result = verify_roundtrip(
            client,
            ENCODED_OBJECT,
            encoded_payload(payload),
            expected_metadata=ENCODED_METADATA,
        )
        require_ready(client, http)
        return {
            "phase": phase,
            "encoded_object": encoded_result,
            **result,
            "storage_ready_before": True,
            "storage_ready_after": True,
        }

    # A freshly encrypted object must work with the currently loaded key. This
    # excludes a generally broken KMS, access policy or encryption subsystem.
    canary = os.urandom(64)
    write_automatic(client, "control/current-master-key.bin", canary)
    verify_roundtrip(client, "control/current-master-key.bin", canary)
    refusal_count = 0
    refusals = []
    for key in (OBJECT, LEGACY_OBJECT, ENCODED_OBJECT):
        try:
            response = client.get_object(BUCKET, key)
            try:
                response.read(len(payload) + 1, decode_content=False)
            finally:
                close_response(response)
        except S3Error as error:
            if not expected_decryption_error(error):
                raise ProbeFailure(
                    "UNEXPECTED_S3_ERROR", details=safe_error_details(error)
                ) from None
            error_code, error_status = error.code, error.response.status
            refusals.append(safe_error_details(error))
            refusal_count += 1
        else:
            raise ProbeFailure("WRONG_KEY_WAS_NOT_REJECTED")
    # Network/timeout/SDK exceptions are deliberately not accepted above.
    require_ready(client, http)
    return {
        "phase": phase,
        "decryption_refused": True,
        "refused_object_count": refusal_count,
        "decryption_reason_matched": all(
            item["decryption_reason_matched"] for item in refusals
        ),
        "refusals": refusals,
        "s3_error_code": error_code,
        "s3_error_status": error_status,
        "current_key_roundtrip": True,
        "storage_ready_before": True,
        "storage_ready_after": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=PHASES)
    args = parser.parse_args(argv)
    try:
        http = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=3, read=8), retries=urllib3.Retry(total=0)
        )
        client = Minio(
            ENDPOINT,
            access_key=Path("/fixture/access").read_text(encoding="utf8").strip(),
            secret_key=Path("/fixture/password").read_text(encoding="utf8").strip(),
            secure=False,
            http_client=http,
        )
        with Path("/fixture/payload.bin").open("rb") as stream:
            payload = stream.read(PAYLOAD_BYTES + 1)
        result = run_phase(args.phase, client, http, payload)
    except ProbeFailure as error:
        print(
            json.dumps(
                {
                    "phase": args.phase,
                    "success": False,
                    "error": str(error),
                    **error.algorithms,
                    **error.details,
                }
            ),
            file=sys.stderr,
        )
        return 1
    except Exception:
        # Never print exception messages, request headers, credentials or raw
        # payloads. Unexpected failures must not become decryption evidence.
        print(
            json.dumps(
                {
                    "phase": args.phase,
                    "success": False,
                    "error": "UNEXPECTED_PROBE_FAILURE",
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"success": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
