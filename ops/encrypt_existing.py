"""Bounded, fail-closed SSE-S3 migration through authenticated S3 APIs only.

Dry-run is the default. Apply requires stopped writers and a database-held
maintenance fence. No object names, profile bytes or credentials are logged.
"""

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import hashlib
import json
import re
import sys
import time
from urllib.parse import urlsplit

from minio import Minio
from minio.commonconfig import CopySource, REPLACE
from minio.datatypes import parse_list_objects
from minio.error import S3Error
import psycopg
from psycopg.rows import dict_row
import urllib3


class MigrationFailure(Exception):
    """Only fixed codes from reviewed call sites may reach the report."""


@dataclass(frozen=True)
class Evidence:
    size: int
    sha256: str

    def __post_init__(self):
        if (
            type(self.size) is not int
            or self.size < 0
            or not isinstance(self.sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
        ):
            raise MigrationFailure("INVALID_DATABASE_EVIDENCE")


@dataclass(frozen=True)
class Verified:
    size: int
    sha256: str
    etag: str
    content_type: str
    metadata: dict
    algorithm: str
    system_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Limits:
    max_objects: int = 10000
    max_bytes: int = 2 * 1024**3
    max_object_bytes: int = 256 * 1024**2
    page_size: int = 100

    def __post_init__(self):
        for value, upper in (
            (self.max_objects, 1000000),
            (self.max_bytes, 1024**4),
            (self.max_object_bytes, 1024**3),
            (self.page_size, 1000),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise MigrationFailure("INVALID_LIMIT")


@dataclass
class ReadBudget:
    maximum: int
    used: int = 0

    def require(self, amount):
        if amount < 0 or self.used + amount > self.maximum:
            raise MigrationFailure("READ_BUDGET_EXCEEDED")

    def consume(self, amount):
        self.require(amount)
        self.used += amount


def header(headers, name, default=None):
    return next(
        (value for key, value in headers.items() if key.lower() == name.lower()),
        default,
    )


def algorithm(headers):
    value = header(headers, "x-amz-server-side-encryption")
    return value if value in {"AES256", "aws:kms"} else "absent"


def user_metadata(headers):
    return {
        key.lower()[11:]: value
        for key, value in headers.items()
        if key.lower().startswith("x-amz-meta-")
    }


def system_metadata(headers):
    # S3 REPLACE also replaces these HTTP representation/cache fields. Keep
    # them distinct from identically named x-amz-meta-* user metadata.
    # minio-py 7.2.20 would silently normalize Expires into user metadata.
    # Refuse before writing instead of claiming to preserve an unsupported
    # representation field. The application gateway never accepts this field.
    if header(headers, "Expires") is not None:
        raise MigrationFailure("UNSUPPORTED_SYSTEM_METADATA")
    return {
        name: value
        for name in (
            "Content-Encoding",
            "Content-Language",
            "Content-Disposition",
            "Cache-Control",
        )
        if (value := header(headers, name)) is not None
    }


def etag(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise MigrationFailure("INVALID_OBJECT_ETAG")
    result = value[1:-1] if value.startswith('"') and value.endswith('"') else value
    if not result or any(
        ord(char) < 33 or ord(char) > 126 or char == '"' for char in result
    ):
        raise MigrationFailure("INVALID_OBJECT_ETAG")
    return result


def close_response(response):
    try:
        response.close()
    finally:
        response.release_conn()


class S3Storage:
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    def require_unversioned(self):
        # Copying current versions cannot establish encryption of older versions.
        if self.client.get_bucket_versioning(self.bucket).status is not None:
            raise MigrationFailure("VERSIONED_BUCKET_UNSUPPORTED")
        config = self.client.get_bucket_encryption(self.bucket)
        if config.rule.sse_algorithm != "AES256":
            raise MigrationFailure("BUCKET_AES256_DEFAULT_REQUIRED")

    def keys(self, page_size, max_pages):
        # Explicitly bounded adapter for the pinned minio-py 7.2.20 SDK. Its
        # public list_objects does not expose a bounded page count or max-keys.
        token = None
        for _ in range(max_pages):
            query = {
                "list-type": "2",
                "max-keys": str(page_size),
                "encoding-type": "url",
            }
            if token is not None:
                query["continuation-token"] = token
            response = self.client._execute(
                "GET", self.bucket, query_params=query, preload_content=False
            )
            try:
                body = response.read(2 * 1024**2 + 1)
                if len(body) > 2 * 1024**2:
                    raise MigrationFailure("LIST_RESPONSE_LIMIT")
                parsed = urllib3.response.HTTPResponse(body=body, status=200)
                objects, truncated, next_token, _ = parse_list_objects(parsed)
            finally:
                close_response(response)
            if len(objects) > page_size:
                raise MigrationFailure("LIST_RESPONSE_LIMIT")
            for item in objects:
                # With no delimiter, a trailing-slash Contents entry is still
                # a real zero-byte object and remains in the migration scope.
                if not item.object_name or item.is_delete_marker:
                    raise MigrationFailure("INVALID_LIST_ENTRY")
                yield item.object_name
            if not truncated:
                return
            if not objects or not next_token or next_token == token:
                raise MigrationFailure("LIST_CURSOR_DID_NOT_ADVANCE")
            if len(next_token) > 16384:
                raise MigrationFailure("LIST_CURSOR_LIMIT")
            token = next_token
        raise MigrationFailure("LIST_PAGE_LIMIT")

    def exists(self, key):
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                return False
            raise

    def verify(self, key, evidence, budget, max_object_bytes):
        stat = self.client.stat_object(self.bucket, key)
        if type(stat.size) is not int or not 0 <= stat.size <= max_object_bytes:
            raise MigrationFailure("OBJECT_LIMIT")
        budget.require(stat.size)
        expected_etag = etag(stat.etag)
        response = self.client.get_object(
            self.bucket, key, request_headers={"If-Match": '"' + expected_etag + '"'}
        )
        digest, total = hashlib.sha256(), 0
        try:
            headers = response.headers
            if response.status != 200 or etag(header(headers, "etag")) != expected_etag:
                raise MigrationFailure("SOURCE_CHANGED")
            if str(header(headers, "content-length")) != str(stat.size):
                raise MigrationFailure("OBJECT_SIZE_MISMATCH")
            for chunk in response.stream(64 * 1024, decode_content=False):
                if len(chunk) > 64 * 1024 or total + len(chunk) > stat.size:
                    raise MigrationFailure("OBJECT_SIZE_MISMATCH")
                budget.consume(len(chunk))
                total += len(chunk)
                digest.update(chunk)
            actual = Verified(
                total,
                digest.hexdigest(),
                expected_etag,
                header(headers, "content-type", "application/octet-stream"),
                user_metadata(headers),
                algorithm(headers),
                system_metadata(headers),
            )
            if (
                total != stat.size
                or actual.content_type != stat.content_type
                or actual.metadata != user_metadata(stat.metadata or {})
                or actual.algorithm != algorithm(stat.metadata or {})
                or actual.system_metadata != system_metadata(stat.metadata or {})
            ):
                raise MigrationFailure("OBJECT_METADATA_CHANGED")
        finally:
            close_response(response)
        if evidence and (actual.size, actual.sha256) != (
            evidence.size,
            evidence.sha256,
        ):
            raise MigrationFailure("AUTHORITATIVE_DIGEST_MISMATCH")
        return actual

    def copy(self, key, before):
        # MetadataDirective.REPLACE permits a same-key copy. Encryption comes
        # from the already verified bucket AES256 default, with no client SSE.
        metadata = {"Content-Type": before.content_type, **before.system_metadata}
        # Explicit prefixes preserve user fields named e.g. content-type or
        # x-amz-copy-source; the SDK must not reinterpret them as control headers.
        metadata.update(
            {"x-amz-meta-" + key: value for key, value in before.metadata.items()}
        )
        result = self.client.copy_object(
            self.bucket,
            key,
            CopySource(self.bucket, key, match_etag='"' + before.etag + '"'),
            metadata=metadata,
            metadata_directive=REPLACE,
        )
        return etag(result.etag)


class SQLRepository:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def quiescent(self, apply):
        if not apply:
            with nullcontext():
                yield
            return
        # Same advisory key as the existing maintenance reconciler. The table
        # locks block DML from writers which were accidentally left running.
        self.connection.execute("SET LOCAL lock_timeout = '3s'")
        row = self.connection.execute(
            "SELECT pg_try_advisory_xact_lock("
            "hashtextextended('drop-maintenance-v1',0)) AS yes"
        ).fetchone()
        if not row["yes"]:
            raise MigrationFailure("MAINTENANCE_ALREADY_RUNNING")
        self.connection.execute(
            "LOCK TABLE tasks,task_attempts,analysis_jobs,artifacts IN SHARE MODE"
        )
        self.assert_quiescent()
        yield
        self.assert_quiescent()

    def assert_quiescent(self):
        row = self.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM tasks WHERE collection_status "
            "NOT IN ('COLLECTED','FAILED','CANCELED')) OR EXISTS("
            "SELECT 1 FROM analysis_jobs WHERE status IN ('pending','running','retry')) AS yes"
        ).fetchone()
        if row["yes"]:
            raise MigrationFailure("ACTIVE_WRITERS")

    def expected(self, key):
        row = self.connection.execute(
            "SELECT size,sha256 FROM artifacts WHERE object_key=%s", (key,)
        ).fetchone()
        # Expired/quarantined objects remain in scope; status is not filtered.
        return Evidence(row["size"], row["sha256"]) if row else None

    def keys(self, page_size, max_rows):
        with self.connection.cursor(name="encryption_artifact_keys") as cursor:
            cursor.execute("SELECT object_key FROM artifacts ORDER BY object_key")
            total = 0
            while rows := cursor.fetchmany(page_size):
                for row in rows:
                    total += 1
                    if total > max_rows:
                        raise MigrationFailure("DATABASE_SCAN_LIMIT")
                    yield row["object_key"]


def migrate_object(
    storage, key, evidence, *, apply, budget, max_object_bytes, assert_quiescent
):
    before = storage.verify(key, evidence, budget, max_object_bytes)
    baseline_only = evidence is None
    if before.algorithm == "AES256":
        return "already_encrypted", baseline_only
    if not apply:
        return "would_encrypt", baseline_only
    assert_quiescent()
    # Reserve enough read capacity before any mutation, including orphan data.
    budget.require(before.size)
    copied_etag = etag(storage.copy(key, before))
    after = storage.verify(
        key, Evidence(before.size, before.sha256), budget, max_object_bytes
    )
    if after.etag != copied_etag:
        raise MigrationFailure("SOURCE_CHANGED")
    if (after.content_type, after.metadata, after.system_metadata) != (
        before.content_type,
        before.metadata,
        before.system_metadata,
    ):
        raise MigrationFailure("OBJECT_METADATA_CHANGED")
    if after.algorithm != "AES256":
        raise MigrationFailure("SSE_S3_NOT_VERIFIED")
    assert_quiescent()
    return "encrypted", baseline_only


def migrate(
    storage, repository, *, apply=False, confirm_writers_stopped=False, limits=None
):
    limits = limits or Limits()
    budget = ReadBudget(limits.max_bytes)
    result = {
        "mode": "apply" if apply else "dry_run",
        "complete": False,
        "bucket_encryption_verified": False,
        "artifact_integrity_complete": False,
        "scan_complete": False,
        "checked": 0,
        "encrypted": 0,
        "already_encrypted": 0,
        "would_encrypt": 0,
        "baseline_only": 0,
        "database_missing": 0,
    }
    try:
        if apply and not confirm_writers_stopped:
            raise MigrationFailure("WRITERS_STOPPED_CONFIRMATION_REQUIRED")
        with repository.quiescent(apply):
            storage.require_unversioned()
            for key in storage.keys(
                limits.page_size, limits.max_objects // limits.page_size + 2
            ):
                if result["checked"] >= limits.max_objects:
                    raise MigrationFailure("OBJECT_COUNT_LIMIT")
                action, baseline = migrate_object(
                    storage,
                    key,
                    repository.expected(key),
                    apply=apply,
                    budget=budget,
                    max_object_bytes=limits.max_object_bytes,
                    assert_quiescent=repository.assert_quiescent,
                )
                result["checked"] += 1
                result[action] += 1
                result["baseline_only"] += int(baseline)
            for key in repository.keys(limits.page_size, limits.max_objects):
                if not storage.exists(key):
                    result["database_missing"] += 1
            if apply:
                repository.assert_quiescent()
            result["scan_complete"] = True
        result["bucket_encryption_verified"] = apply
        result["artifact_integrity_complete"] = result["database_missing"] == 0
        result["complete"] = apply and result["artifact_integrity_complete"]
    except MigrationFailure as error:
        result["complete"] = result["scan_complete"] = False
        result["error"] = str(error)
    except Exception:
        # SDK/database exceptions may contain signed request details or keys.
        result["complete"] = result["scan_complete"] = False
        result["error"] = "STORAGE_OR_DATABASE_FAILURE"
    result["bytes_read"] = budget.used
    return result


def main(argv=None):
    # Lazy import keeps the reviewed migration primitive usable as a standalone
    # read-only bind mount by the private storage fixture.
    from ops_runtime import Config, ConfigurationError, VERSION, emit

    started = time.monotonic()
    try:
        config = Config.load(argv, mode="encryption")
        config.log_startup("drop-encryption-migration")
        limits = Limits(
            config["ENCRYPTION_MAX_OBJECTS"],
            config["ENCRYPTION_MAX_BYTES"],
            config["ENCRYPTION_MAX_OBJECT_BYTES"],
            config["ENCRYPTION_PAGE_SIZE"],
        )
        endpoint = urlsplit(config["S3_ENDPOINT"])
        client = Minio(
            endpoint.netloc,
            access_key=config["S3_ACCESS_KEY"],
            secret_key=config["S3_SECRET_KEY"],
            secure=endpoint.scheme == "https",
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=3, read=30),
                retries=urllib3.Retry(total=0),
            ),
        )
        with psycopg.connect(
            config["DATABASE_URL"], row_factory=dict_row, connect_timeout=5
        ) as connection:
            result = migrate(
                S3Storage(client, config["S3_BUCKET"]),
                SQLRepository(connection),
                apply=config.apply,
                confirm_writers_stopped=config.confirm_writers_stopped,
                limits=limits,
            )
    except ConfigurationError as error:
        result = {"complete": False, "scan_complete": False, "error": str(error)}
    except MigrationFailure as error:
        result = {"complete": False, "scan_complete": False, "error": str(error)}
    except Exception:
        result = {
            "complete": False,
            "scan_complete": False,
            "error": "STORAGE_OR_DATABASE_FAILURE",
        }
    emit(
        "drop-encryption-migration",
        VERSION,
        "encryption_migration_finished",
        stage="migration",
        level="error" if result.get("error") else "info",
        error_code=result.get("error", ""),
        duration_ms=int((time.monotonic() - started) * 1000),
        stream=sys.stderr,
    )
    # This stdout document is a machine-readable CLI report, not a log stream.
    print(json.dumps(result, sort_keys=True))
    return (
        0
        if result.get("scan_complete")
        and not result.get("database_missing")
        and not result.get("error")
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
