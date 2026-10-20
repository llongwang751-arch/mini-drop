"""Pure imported-fixture boundaries; no HTTP, S3 or browser is started."""

import gzip
import hashlib
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from stage_ce_gzip import (
    Failure,
    main,
    object_key,
    replacement_metadata,
    require_fixed_stage_scope,
    run,
    select_artifact,
    verify_stored,
)


class StageEncodingBoundaryTests(unittest.TestCase):
    def sdk_modules(self, urllib3_version):
        return {
            "minio": SimpleNamespace(__version__="7.2.20", Minio=Mock()),
            "minio.commonconfig": SimpleNamespace(CopySource=Mock(), REPLACE="REPLACE"),
            "urllib3": SimpleNamespace(
                __version__=urllib3_version,
                PoolManager=Mock(),
                Timeout=Mock(),
                Retry=Mock(),
            ),
        }

    def test_old_sdk_is_rejected_before_clients_or_secret_reads(self):
        with (
            patch.dict(sys.modules, self.sdk_modules("2.7.0")),
            patch("stage_ce_gzip.API") as api,
            patch("stage_ce_gzip.secret_file") as secret,
            self.assertRaisesRegex(Failure, "^LOCKED_SDK_VERSION_REQUIRED$"),
        ):
            try:
                run(SimpleNamespace(), {})
            finally:
                api.assert_not_called()
                secret.assert_not_called()

    def test_patched_sdk_passes_version_guard_without_network_or_secrets(self):
        with (
            patch.dict(sys.modules, self.sdk_modules("2.8.0")),
            patch("stage_ce_gzip.API") as api,
            patch(
                "stage_ce_gzip.secret_file", side_effect=Failure("TEST_SDK_ACCEPTED")
            ) as secret,
            self.assertRaisesRegex(Failure, "^TEST_SDK_ACCEPTED$"),
        ):
            try:
                run(SimpleNamespace(admin_username_file="unused"), {})
            finally:
                api.assert_called_once()
                secret.assert_called_once_with("unused")

    def test_fixed_management_scope_is_checked_before_clients_or_secrets(self):
        require_fixed_stage_scope()
        for field, invalid in (
            ("S3_ENDPOINT", "minio:9000"),
            ("S3_ENDPOINT", "120.24.187.205:9000"),
            ("API_ORIGIN", "http://120.24.187.205"),
            ("PUBLIC_ORIGIN", "http://120.24.187.205"),
            ("BUCKET", "production-artifacts"),
        ):
            with (
                self.subTest(field=field, invalid=invalid),
                patch("stage_ce_gzip." + field, invalid),
                patch("stage_ce_gzip.API") as api,
                patch("stage_ce_gzip.secret_file") as secret,
                self.assertRaisesRegex(Failure, "^FIXED_STAGE_SCOPE_REQUIRED$"),
            ):
                try:
                    run(SimpleNamespace(), {})
                finally:
                    api.assert_not_called()
                    secret.assert_not_called()

    def task(self):
        return {
            "id": "7",
            "name": "IMPORTED_STAGE_CE_GZIP_" + "a" * 32,
            "kind": "IMPORT_COLLAPSED",
            "params": {"source": "import"},
            "collection_status": "COLLECTED",
            "analysis_status": "SUCCESS",
            "attempts": [{"id": "11", "attempt": 1, "status": "COLLECTED"}],
            "artifacts": [
                {
                    "id": "21",
                    "task_id": "7",
                    "attempt_id": "11",
                    "kind": "RESULT",
                    "name": "flamegraph.json.gz",
                    "status": "ready",
                    "download_allowed": True,
                    "size": 200,
                    "sha256": "b" * 64,
                }
            ],
        }

    def test_only_current_owned_fixture_result_is_selected(self):
        task = self.task()
        self.assertEqual(select_artifact(task, "7", "a" * 32)[0]["id"], "21")
        task["attempts"].append({"id": "12", "attempt": 2, "status": "COLLECTED"})
        with self.assertRaises(Failure):
            select_artifact(task, "7", "a" * 32)
        task = self.task()
        task["name"] = "another task"
        with self.assertRaises(Failure):
            select_artifact(task, "7", "a" * 32)

    def test_signed_path_cannot_select_another_task_bucket_or_origin(self):
        key = "results/tasks/7/attempts/1/analysis/3/runs/1/flamegraph.json.gz"
        base = "http://127.0.0.1:18080/objects/drop-artifacts/" + key
        query = "?response-content-encoding=identity&X-Amz-Signature=" + "c" * 64
        self.assertEqual(object_key(base + query, "7", 1), key)
        for invalid in (
            base.replace("tasks/7/", "tasks/8/"),
            base.replace("drop-artifacts", "production"),
            base.replace("127.0.0.1:18080", "120.24.187.205"),
            base.replace("flamegraph", "%2e%2e/flamegraph"),
        ):
            with self.assertRaises(Failure):
                object_key(invalid + query, "7", 1)
        with self.assertRaises(Failure):
            object_key(base + "?X-Amz-Signature=" + "c" * 64, "7", 1)

    def test_system_and_user_metadata_stay_distinct(self):
        metadata = replacement_metadata(
            {
                "Content-Type": "application/gzip",
                "Cache-Control": "private",
                "Content-Language": "en",
                "Content-Disposition": "attachment",
                "X-Amz-Meta-sha256": "b" * 64,
                "X-Amz-Meta-content-type": "fixture-user-value",
            }
        )
        self.assertEqual(metadata["Content-Encoding"], "gzip")
        self.assertEqual(metadata["Content-Type"], "application/gzip")
        self.assertEqual(metadata["x-amz-meta-content-type"], "fixture-user-value")
        self.assertEqual(metadata["Cache-Control"], "private")
        with self.assertRaises(Failure):
            replacement_metadata({"Expires": "fixture-expiry"})

    def test_stored_gzip_verification_keeps_encoded_bytes(self):
        raw = gzip.compress(
            b'{"name":"imported-stage-ce-gzip","value":100,"children":[]}'
        )
        sha = hashlib.sha256(raw).hexdigest()
        metadata = {
            "Content-Length": str(len(raw)),
            "Content-Type": "application/gzip",
            "Content-Encoding": "gzip",
            "X-Amz-Meta-sha256": sha,
        }

        class Response:
            status = 200
            headers = metadata
            closed = released = False

            def stream(self, size, *, decode_content):
                self.decode_content = decode_content
                yield raw

            def close(self):
                self.closed = True

            def release_conn(self):
                self.released = True

        response = Response()
        client = SimpleNamespace(
            stat_object=lambda *args: SimpleNamespace(
                size=len(raw), etag="f" * 32, metadata=metadata
            ),
            get_object=lambda *args, **kwargs: response,
        )
        verified = verify_stored(
            client, "controlled-new-fixture", {"size": len(raw), "sha256": sha}
        )
        self.assertEqual((verified.size, verified.sha256), (len(raw), sha))
        self.assertFalse(response.decode_content)
        self.assertTrue(response.closed and response.released)

    def test_transport_exception_never_reaches_public_proof(self):
        output = io.StringIO()
        with (
            patch(
                "stage_ce_gzip.run",
                side_effect=RuntimeError("https://fixture/?signature=private-fixture"),
            ),
            patch("stage_ce_gzip.logging.disable"),
            redirect_stdout(output),
        ):
            status = main(["--confirm-stage", "dropfullstage", "--run-id", "a" * 32])
        self.assertEqual(status, 1)
        self.assertEqual(
            json.loads(output.getvalue())["error_code"], "STAGE_VALIDATION_FAILED"
        )
        self.assertNotIn("private-fixture", output.getvalue())
        self.assertNotIn("https://", output.getvalue())


if __name__ == "__main__":
    unittest.main()
