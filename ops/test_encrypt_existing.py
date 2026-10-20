"""离线迁移回归；下方所有对象内容均为合成夹具。"""

from contextlib import nullcontext
from dataclasses import replace
import hashlib
import gzip
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from minio import Minio
import urllib3

from encrypt_existing import (
    Evidence,
    Limits,
    MigrationFailure,
    ReadBudget,
    S3Storage,
    Verified,
    migrate,
    migrate_object,
)


BODY = b"synthetic imported private fixture\x00" * 10
DIGEST = hashlib.sha256(BODY).hexdigest()


class Repository:
    def __init__(self, evidence=None, active=False):
        self.evidence = evidence or {}
        self.active = active
        self.checks = 0

    def quiescent(self, apply):
        if apply:
            self.assert_quiescent()
        return nullcontext()

    def assert_quiescent(self):
        self.checks += 1
        if self.active:
            raise MigrationFailure("ACTIVE_WRITERS")

    def expected(self, key):
        return self.evidence.get(key)

    def keys(self, page_size, max_rows):
        yield from self.evidence


class Storage:
    def __init__(self, algorithm="absent"):
        self.items = {
            "private/object": Verified(
                len(BODY),
                DIGEST,
                "etag-before",
                "application/x-private",
                {"sha256": DIGEST, "label": "synthetic fixture"},
                algorithm,
            )
        }
        self.copies = []
        self.versioned = False
        self.failure = None
        self.mutate = None

    def require_unversioned(self):
        if self.versioned:
            raise MigrationFailure("VERSIONED_BUCKET_UNSUPPORTED")

    def keys(self, page_size, max_pages):
        yield from self.items

    def exists(self, key):
        return key in self.items

    def verify(self, key, evidence, budget, max_object_bytes):
        if self.failure:
            raise self.failure
        item = self.items[key]
        budget.consume(item.size)
        if item.size > max_object_bytes:
            raise MigrationFailure("OBJECT_LIMIT")
        if evidence and (item.size, item.sha256) != (evidence.size, evidence.sha256):
            raise MigrationFailure("AUTHORITATIVE_DIGEST_MISMATCH")
        return item

    def copy(self, key, before):
        if self.mutate:
            self.mutate(self, key)
        if self.items[key].etag != before.etag:
            raise MigrationFailure("SOURCE_CHANGED")
        self.copies.append((key, before))
        self.items[key] = replace(before, etag="etag-after", algorithm="AES256")
        return "etag-after"


class MigrationTests(unittest.TestCase):
    def test_default_is_dry_run_and_does_not_copy(self):
        store = Storage()
        result = migrate(store, Repository())
        self.assertTrue(result["scan_complete"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["would_encrypt"], 1)
        self.assertEqual(result["baseline_only"], 1)
        self.assertEqual(store.copies, [])

    def test_apply_needs_explicit_writers_stopped_confirmation(self):
        store = Storage()
        result = migrate(store, Repository(), apply=True)
        self.assertEqual(result["error"], "WRITERS_STOPPED_CONFIRMATION_REQUIRED")
        self.assertEqual(store.copies, [])

    def test_active_collection_or_analysis_refuses_all_writes(self):
        store = Storage()
        result = migrate(
            store, Repository(active=True), apply=True, confirm_writers_stopped=True
        )
        self.assertEqual(result["error"], "ACTIVE_WRITERS")
        self.assertFalse(result["complete"])
        self.assertEqual(store.copies, [])

    def test_apply_and_rerun_are_idempotent_and_verify_each_time(self):
        store = Storage()
        repo = Repository({"private/object": Evidence(len(BODY), DIGEST)})
        first = migrate(store, repo, apply=True, confirm_writers_stopped=True)
        second = migrate(store, repo, apply=True, confirm_writers_stopped=True)
        self.assertTrue(first["complete"])
        self.assertTrue(second["complete"])
        self.assertEqual(first["encrypted"], 1)
        self.assertEqual(second["already_encrypted"], 1)
        self.assertEqual(len(store.copies), 1)
        self.assertEqual(second["bytes_read"], len(BODY))
        self.assertGreaterEqual(repo.checks, 5)

    def test_preserves_sha_size_content_type_and_user_metadata(self):
        store = Storage()
        before = store.items["private/object"]
        result = migrate(store, Repository(), apply=True, confirm_writers_stopped=True)
        after = store.items["private/object"]
        self.assertTrue(result["complete"])
        self.assertEqual(
            (before.size, before.sha256, before.content_type, before.metadata),
            (after.size, after.sha256, after.content_type, after.metadata),
        )

    def test_concurrent_etag_change_refuses_copy(self):
        store = Storage()
        store.mutate = lambda s, k: s.items.__setitem__(
            k, replace(s.items[k], etag="changed")
        )
        result = migrate(store, Repository(), apply=True, confirm_writers_stopped=True)
        self.assertFalse(result["complete"])
        self.assertEqual(result["error"], "SOURCE_CHANGED")
        self.assertEqual(store.copies, [])

    def test_existing_encrypted_objects_still_require_integrity(self):
        store = Storage("AES256")
        repo = Repository({"private/object": Evidence(len(BODY), "0" * 64)})
        result = migrate(store, repo, apply=True, confirm_writers_stopped=True)
        self.assertEqual(result["error"], "AUTHORITATIVE_DIGEST_MISMATCH")
        self.assertFalse(result["complete"])

    def test_network_failure_is_not_success(self):
        store = Storage()
        store.failure = TimeoutError(
            "private object and credential must never be logged"
        )
        result = migrate(store, Repository(), apply=True, confirm_writers_stopped=True)
        self.assertEqual(result["error"], "STORAGE_OR_DATABASE_FAILURE")
        self.assertNotIn("private", str(result))
        self.assertFalse(result["complete"])

    def test_postcopy_wrong_encryption_algorithm_fails_closed(self):
        store = Storage()
        original_copy = store.copy

        def bad_copy(key, before):
            result = original_copy(key, before)
            store.items[key] = replace(store.items[key], algorithm="aws:kms")
            return result

        store.copy = bad_copy
        result = migrate(store, Repository(), apply=True, confirm_writers_stopped=True)
        self.assertEqual(result["error"], "SSE_S3_NOT_VERIFIED")
        self.assertEqual(result["encrypted"], 0)
        self.assertFalse(result["complete"])

    def test_postcopy_plaintext_change_is_not_reported_encrypted(self):
        store = Storage()
        original_copy = store.copy

        def bad_copy(key, before):
            result = original_copy(key, before)
            store.items[key] = replace(store.items[key], sha256="f" * 64)
            return result

        store.copy = bad_copy
        result = migrate(store, Repository(), apply=True, confirm_writers_stopped=True)
        self.assertEqual(result["error"], "AUTHORITATIVE_DIGEST_MISMATCH")
        self.assertEqual(result["encrypted"], 0)

    def test_missing_database_objects_are_counted_without_claiming_integrity(self):
        repo = Repository({"missing": Evidence(1, "0" * 64)})
        result = migrate(
            Storage("AES256"), repo, apply=True, confirm_writers_stopped=True
        )
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["database_missing"], 1)
        self.assertFalse(result["complete"])
        self.assertTrue(result["bucket_encryption_verified"])
        self.assertFalse(result["artifact_integrity_complete"])

    def test_versioned_bucket_refuses_incomplete_history_migration(self):
        store = Storage()
        store.versioned = True
        result = migrate(store, Repository(), apply=True, confirm_writers_stopped=True)
        self.assertEqual(result["error"], "VERSIONED_BUCKET_UNSUPPORTED")

    def test_object_and_total_read_budgets_are_bounded(self):
        for limits, error in [
            (Limits(max_object_bytes=1), "OBJECT_LIMIT"),
            (Limits(max_bytes=len(BODY)), "READ_BUDGET_EXCEEDED"),
        ]:
            with self.subTest(error=error):
                result = migrate(
                    Storage(),
                    Repository(),
                    apply=True,
                    confirm_writers_stopped=True,
                    limits=limits,
                )
                self.assertFalse(result["complete"])
                self.assertEqual(result["error"], error)

    def test_object_scan_limit_reports_incomplete_and_rerun_is_safe(self):
        store = Storage()
        store.items["private/second"] = store.items["private/object"]
        result = migrate(store, Repository(), limits=Limits(max_objects=1))
        self.assertEqual(result["error"], "OBJECT_COUNT_LIMIT")
        self.assertFalse(result["scan_complete"])

    def test_standalone_object_verifies_pre_and_post_copy(self):
        store = Storage()
        budget = ReadBudget(10 * len(BODY))
        mode, baseline = migrate_object(
            store,
            "private/object",
            None,
            apply=True,
            budget=budget,
            max_object_bytes=10000,
            assert_quiescent=lambda: None,
        )
        self.assertEqual((mode, baseline), ("encrypted", True))
        self.assertEqual(budget.used, 2 * len(BODY))

    def test_postcopy_metadata_loss_refuses_success(self):
        store = Storage()
        original_copy = store.copy

        def bad_copy(key, before):
            tag = original_copy(key, before)
            store.items[key] = replace(store.items[key], metadata={})
            return tag

        store.copy = bad_copy
        result = migrate(store, Repository(), apply=True, confirm_writers_stopped=True)
        self.assertEqual(result["error"], "OBJECT_METADATA_CHANGED")
        self.assertFalse(result["bucket_encryption_verified"])

    def test_postcopy_system_metadata_loss_refuses_success(self):
        store = Storage()
        store.items["private/object"] = replace(
            store.items["private/object"], system_metadata={"Content-Encoding": "gzip"}
        )
        original_copy = store.copy

        def bad_copy(key, before):
            tag = original_copy(key, before)
            store.items[key] = replace(store.items[key], system_metadata={})
            return tag

        store.copy = bad_copy
        result = migrate(store, Repository(), apply=True, confirm_writers_stopped=True)
        self.assertEqual(result["error"], "OBJECT_METADATA_CHANGED")
        self.assertFalse(result["bucket_encryption_verified"])

    def test_postcopy_concurrent_replace_refuses_success(self):
        store = Storage()
        original_copy = store.copy

        def bad_copy(key, before):
            tag = original_copy(key, before)
            store.items[key] = replace(store.items[key], etag="concurrent-update")
            return tag

        store.copy = bad_copy
        result = migrate(store, Repository(), apply=True, confirm_writers_stopped=True)
        self.assertEqual(result["error"], "SOURCE_CHANGED")
        self.assertFalse(result["complete"])


class S3AdapterTests(unittest.TestCase):
    def test_unsupported_expires_is_refused_before_copy(self):
        client = self.make_client()
        client.stat_object.return_value.metadata["Expires"] = (
            "Tue, 20 Oct 2026 00:00:00 GMT"
        )
        client.get_object.return_value.headers["Expires"] = (
            "Tue, 20 Oct 2026 00:00:00 GMT"
        )
        with self.assertRaisesRegex(MigrationFailure, "UNSUPPORTED_SYSTEM_METADATA"):
            S3Storage(client, "fixture-bucket").verify(
                "imported-unsupported-metadata", None, ReadBudget(10000), 10000
            )
        client.copy_object.assert_not_called()

    def test_encoded_object_integrity_uses_exact_stored_bytes(self):
        encoded = gzip.compress(BODY * 10, mtime=0)
        headers = {
            "ETag": '"opaque-etag"',
            "Content-Length": str(len(encoded)),
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "gzip",
        }
        client = Mock()
        client.stat_object.return_value = SimpleNamespace(
            size=len(encoded),
            etag="opaque-etag",
            content_type="application/octet-stream",
            metadata=headers,
        )
        client.get_object.return_value = urllib3.response.HTTPResponse(
            body=io.BytesIO(encoded), headers=headers, status=200, preload_content=False
        )
        item = S3Storage(client, "fixture-bucket").verify(
            "imported-encoded-fixture",
            Evidence(len(encoded), hashlib.sha256(encoded).hexdigest()),
            ReadBudget(10000),
            10000,
        )
        self.assertEqual(item.size, len(encoded))
        self.assertEqual(item.sha256, hashlib.sha256(encoded).hexdigest())

    def make_client(self):
        client = Mock()
        headers = {
            "ETag": '"opaque-etag"',
            "Content-Length": str(len(BODY)),
            "Content-Type": "application/octet-stream",
            "x-amz-meta-label": "fixture",
        }
        client.stat_object.return_value = SimpleNamespace(
            size=len(BODY),
            etag="opaque-etag",
            content_type="application/octet-stream",
            metadata=headers,
        )
        client.get_object.return_value = urllib3.response.HTTPResponse(
            body=io.BytesIO(BODY), headers=headers, status=200, preload_content=False
        )
        return client

    def test_streaming_get_uses_source_condition_and_full_digest(self):
        client = self.make_client()
        budget = ReadBudget(10000)
        item = S3Storage(client, "fixture-bucket").verify(
            "synthetic", Evidence(len(BODY), DIGEST), budget, 10000
        )
        self.assertEqual(item.sha256, DIGEST)
        self.assertEqual(budget.used, len(BODY))
        self.assertEqual(
            client.get_object.call_args.kwargs["request_headers"],
            {"If-Match": '"opaque-etag"'},
        )

    def test_get_different_etag_fails_before_hashing(self):
        client = self.make_client()
        client.get_object.return_value.headers["etag"] = '"changed"'
        with self.assertRaisesRegex(MigrationFailure, "SOURCE_CHANGED"):
            S3Storage(client, "fixture-bucket").verify(
                "synthetic", None, ReadBudget(1000), 1000
            )

    def test_body_truncation_or_overflow_fails(self):
        for body in [BODY[:-1], BODY + b"extra"]:
            with self.subTest(length=len(body)):
                client = self.make_client()
                headers = dict(client.get_object.return_value.headers)
                client.get_object.return_value = urllib3.response.HTTPResponse(
                    body=io.BytesIO(body),
                    headers=headers,
                    status=200,
                    preload_content=False,
                )
                with self.assertRaises(
                    (MigrationFailure, urllib3.exceptions.ProtocolError)
                ):
                    S3Storage(client, "fixture-bucket").verify(
                        "synthetic", None, ReadBudget(1000), 1000
                    )

    def test_budget_refuses_before_opening_body(self):
        client = self.make_client()
        with self.assertRaisesRegex(MigrationFailure, "READ_BUDGET_EXCEEDED"):
            S3Storage(client, "fixture-bucket").verify(
                "synthetic", None, ReadBudget(1), 1000
            )
        client.get_object.assert_not_called()

    def test_actual_sdk_generates_condition_and_unambiguous_metadata_headers(self):
        client = Minio(
            "private-fixture:9000", "fixture-access", "fixture-secret", secure=False
        )
        client.stat_object = Mock(return_value=SimpleNamespace(size=len(BODY)))
        client._execute = Mock(
            return_value=urllib3.response.HTTPResponse(
                body=b'<CopyObjectResult><ETag>"after"</ETag><LastModified>2026-10-17T00:00:00.000Z</LastModified></CopyObjectResult>',
                headers={},
                status=200,
            )
        )
        before = Verified(
            len(BODY),
            DIGEST,
            "opaque-etag",
            "application/x-profile",
            {"content-type": "user value", "x-amz-copy-source": "metadata value"},
            "absent",
            {
                "Content-Encoding": "gzip",
                "Cache-Control": "private, max-age=60",
                "Content-Language": "zh-CN",
                "Content-Disposition": "attachment; filename=fixture.gz",
            },
        )
        self.assertEqual(
            S3Storage(client, "fixture-bucket").copy(
                "private/imported profile", before
            ),
            "after",
        )
        headers = client._execute.call_args.kwargs["headers"]
        self.assertEqual(headers["x-amz-copy-source-if-match"], '"opaque-etag"')
        self.assertEqual(
            headers["x-amz-copy-source"], "/fixture-bucket/private/imported%20profile"
        )
        self.assertEqual(headers["x-amz-metadata-directive"], "REPLACE")
        self.assertEqual(headers["Content-Type"], "application/x-profile")
        self.assertEqual(headers["x-amz-meta-content-type"], "user value")
        self.assertEqual(headers["x-amz-meta-x-amz-copy-source"], "metadata value")
        for name, value in before.system_metadata.items():
            self.assertEqual(headers[name], value)
        self.assertNotIn("x-amz-server-side-encryption", headers)

    def test_listing_uses_bounded_signed_page_and_rejects_stuck_cursor(self):
        client = Mock()
        page = b"<ListBucketResult><Name>fixture-bucket</Name><IsTruncated>true</IsTruncated><NextContinuationToken>same</NextContinuationToken><Contents><Key>synthetic</Key><Size>1</Size></Contents></ListBucketResult>"
        client._execute.side_effect = lambda *args, **kwargs: (
            urllib3.response.HTTPResponse(body=io.BytesIO(page), preload_content=False)
        )
        with self.assertRaisesRegex(MigrationFailure, "LIST_CURSOR_DID_NOT_ADVANCE"):
            list(S3Storage(client, "fixture-bucket").keys(10, 3))
        self.assertEqual(client._execute.call_count, 2)
        self.assertEqual(
            client._execute.call_args.kwargs["query_params"]["max-keys"], "10"
        )


if __name__ == "__main__":
    unittest.main()
