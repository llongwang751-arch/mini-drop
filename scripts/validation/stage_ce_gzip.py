#!/usr/bin/env python3
"""One new stage-only imported fixture and exact-byte S3 metadata regression.

Only safe identifiers, hashes, sizes and boolean results reach stdout. This
script never accepts an existing task, arbitrary bucket, key or service URL.
"""

import argparse
import gzip
import hashlib
import io
import json
import logging
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

API_ORIGIN = "http://web:8080"
PUBLIC_ORIGIN = "http://127.0.0.1:18080"
S3_ENDPOINT = "storage-backend:9000"
BUCKET = "drop-artifacts"
PREFIX = "IMPORTED_STAGE_CE_GZIP_"
MAX_OBJECT = 65536
MAX_API = 1024 * 1024
SYSTEM = (
    "Content-Type",
    "Content-Encoding",
    "Content-Language",
    "Content-Disposition",
    "Cache-Control",
)


class Failure(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Failure(code)


def require_fixed_stage_scope():
    # The business S3 gateway deliberately rejects admin CopyObject. Only this
    # one-time stage management container may reach the private backend.
    require(
        (API_ORIGIN, PUBLIC_ORIGIN, S3_ENDPOINT, BUCKET)
        == (
            "http://web:8080",
            "http://127.0.0.1:18080",
            "storage-backend:9000",
            "drop-artifacts",
        ),
        "FIXED_STAGE_SCOPE_REQUIRED",
    )


def positive_id(value):
    require(
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]{0,18}", value)
        and int(value) <= 2**63 - 1,
        "IDENTIFIER_INVALID",
    )
    return value


def header(metadata, name, default=None):
    return next(
        (value for key, value in metadata.items() if key.lower() == name.lower()),
        default,
    )


def user_metadata(metadata):
    return {
        key.lower(): value
        for key, value in metadata.items()
        if key.lower().startswith("x-amz-meta-")
    }


def replacement_metadata(metadata):
    require(header(metadata, "Expires") is None, "UNSUPPORTED_SYSTEM_METADATA")
    result = {
        name: value for name in SYSTEM if (value := header(metadata, name)) is not None
    }
    result["Content-Encoding"] = "gzip"
    result.update(user_metadata(metadata))
    return result


def select_artifact(task, task_id, run_id):
    require(
        isinstance(task, dict)
        and task.get("id") == task_id
        and task.get("name") == PREFIX + run_id
        and task.get("kind") == "IMPORT_COLLAPSED"
        and task.get("params", {}).get("source") == "import"
        and task.get("analysis_status") == "SUCCESS"
        and task.get("collection_status") == "COLLECTED",
        "FIXTURE_IDENTITY_MISMATCH",
    )
    attempts = task.get("attempts", [])
    require(isinstance(attempts, list) and 0 < len(attempts) <= 3, "ATTEMPT_INVALID")
    for attempt in attempts:
        positive_id(attempt.get("id"))
        require(
            type(attempt.get("attempt")) is int and 0 < attempt["attempt"] <= 3,
            "ATTEMPT_INVALID",
        )
    latest = max(attempts, key=lambda value: value["attempt"])
    # The tool creates one import and never retries it. Refuse an externally
    # retried/replaced collection rather than touching uncertain objects.
    require(
        latest["attempt"] == 1 and latest.get("status") == "COLLECTED",
        "FIXTURE_ATTEMPT_REPLACED",
    )
    artifacts = task.get("artifacts", [])
    require(
        isinstance(artifacts, list) and len(artifacts) <= 32, "ARTIFACT_LIST_INVALID"
    )
    selected = [
        a
        for a in artifacts
        if a.get("task_id") == task_id
        and a.get("attempt_id") == latest["id"]
        and a.get("kind") == "RESULT"
        and a.get("name") == "flamegraph.json.gz"
        and a.get("status") == "ready"
        and a.get("download_allowed") is True
    ]
    require(selected, "CURRENT_RESULT_MISSING")
    for artifact in selected:
        positive_id(artifact.get("id"))
        require(
            type(artifact.get("size")) is int
            and 0 < artifact["size"] <= MAX_OBJECT
            and isinstance(artifact.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]),
            "RESULT_METADATA_INVALID",
        )
        if artifact.get("expires_at"):
            require(
                datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00"))
                > datetime.now(timezone.utc),
                "RESULT_EXPIRED",
            )
    return max(selected, key=lambda value: int(value["id"])), latest


def object_key(signed_url, task_id, collection_attempt):
    require(
        isinstance(signed_url, str)
        and 0 < len(signed_url) <= 8192
        and not re.search(r"[\s\\]", signed_url),
        "GRANT_INVALID",
    )
    parsed = urlsplit(signed_url)
    require(
        parsed.scheme + "://" + parsed.netloc == PUBLIC_ORIGIN
        and not parsed.username
        and not parsed.password
        and not parsed.fragment,
        "GRANT_ORIGIN_INVALID",
    )
    expected = (
        rf"/objects/{BUCKET}/(results/tasks/{positive_id(task_id)}/attempts/{collection_attempt}/"
        r"analysis/[1-9][0-9]{0,18}/runs/[1-9][0-9]{0,18}/flamegraph\.json\.gz)"
    )
    matched = re.fullmatch(expected, parsed.path)
    require(matched is not None, "FIXTURE_OBJECT_SCOPE_INVALID")
    query = parse_qs(parsed.query)
    require(
        query.get("response-content-encoding") == ["identity"]
        and len(query.get("X-Amz-Signature", [])) == 1
        and re.fullmatch(r"[0-9a-f]{64}", query["X-Amz-Signature"][0]),
        "IDENTITY_OVERRIDE_MISSING",
    )
    return matched.group(1)


def secret_file(path, maximum=1024):
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(descriptor)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and 0 < info.st_size <= maximum,
            "SECRET_FILE_INVALID",
        )
        data = os.read(descriptor, maximum + 1)
        require(0 < len(data) <= maximum, "SECRET_FILE_INVALID")
        value = data.decode("utf8").rstrip("\r\n")
        require(
            value and not any(ord(char) < 32 or ord(char) == 127 for char in value),
            "SECRET_FILE_INVALID",
        )
        return value
    except Failure:
        raise
    except Exception:  # noqa: BLE001 - secret and OS errors must remain redacted.
        raise Failure("SECRET_FILE_INVALID") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class API:
    def __init__(self, pool):
        self.pool, self.cookie, self.csrf = pool, "", ""

    def request(self, method, path, data=None, expected=200, idempotency=None):
        require(
            re.fullmatch(
                r"/api/v1/(?:auth/login|tasks|tasks/[1-9][0-9]{0,18}(?:/artifacts/[1-9][0-9]{0,18}/download)?)",
                path,
            ),
            "API_PATH_INVALID",
        )
        headers = {
            "Origin": PUBLIC_ORIGIN,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        if self.cookie:
            headers["Cookie"] = "drop_session=" + self.cookie
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        response = self.pool.request(
            method,
            API_ORIGIN + path,
            body=body,
            headers=headers,
            redirect=False,
            preload_content=False,
        )
        try:
            require(response.status == expected, "STAGE_API_STATUS_FAILED")
            encoded = response.read(MAX_API + 1, decode_content=False)
            require(len(encoded) <= MAX_API, "STAGE_API_BODY_LIMIT")
            result = json.loads(encoded)
            require(
                isinstance(result, dict)
                and result.get("error") is None
                and isinstance(result.get("data"), dict),
                "STAGE_API_ENVELOPE_INVALID",
            )
            if path == "/api/v1/auth/login":
                cookie = SimpleCookie()
                cookie.load(response.headers.get("Set-Cookie", ""))
                self.cookie = (
                    cookie["drop_session"].value if "drop_session" in cookie else ""
                )
                self.csrf = result["data"].get("csrf_token", "")
                require(
                    re.fullmatch(r"[0-9a-f]{64}", self.cookie)
                    and isinstance(self.csrf, str)
                    and re.fullmatch(r"[0-9a-f]{64}", self.csrf),
                    "STAGE_SESSION_INVALID",
                )
            return result["data"]
        finally:
            response.close()
            response.release_conn()


@dataclass
class Stored:
    size: int
    sha256: str
    etag: str
    metadata: dict


def verify_stored(client, key, artifact):
    observed = client.stat_object(BUCKET, key)
    require(
        observed.size == artifact["size"] and 0 < observed.size <= MAX_OBJECT,
        "STORED_SIZE_MISMATCH",
    )
    require(
        isinstance(observed.etag, str)
        and re.fullmatch(r"[0-9a-fA-F-]{1,128}", observed.etag),
        "STORED_ETAG_INVALID",
    )
    response = client.get_object(
        BUCKET, key, request_headers={"If-Match": '"' + observed.etag + '"'}
    )
    chunks, length, digest = [], 0, hashlib.sha256()
    try:
        require(
            response.status == 200
            and str(header(response.headers, "Content-Length")) == str(observed.size),
            "STORED_RESPONSE_INVALID",
        )
        for chunk in response.stream(8192, decode_content=False):
            length += len(chunk)
            require(length <= observed.size and len(chunk) <= 8192, "STORED_READ_LIMIT")
            chunks.append(chunk)
            digest.update(chunk)
        require(
            length == observed.size and digest.hexdigest() == artifact["sha256"],
            "STORED_DIGEST_MISMATCH",
        )
        compressed = b"".join(chunks)
        require(compressed.startswith(b"\x1f\x8b"), "STORED_GZIP_MAGIC_INVALID")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            decoded = stream.read(MAX_OBJECT + 1)
        require(
            len(decoded) <= MAX_OBJECT
            and b"imported-stage-ce-gzip" in decoded
            and isinstance(json.loads(decoded), dict),
            "IMPORTED_FLAMEGRAPH_INVALID",
        )
        metadata = dict(response.headers)
        require(
            user_metadata(metadata) == user_metadata(observed.metadata or {}),
            "STORED_METADATA_CHANGED",
        )
        return Stored(length, digest.hexdigest(), observed.etag, metadata)
    finally:
        response.close()
        response.release_conn()


def run(args, proof):
    require_fixed_stage_scope()
    import minio
    import urllib3
    from minio import Minio
    from minio.commonconfig import REPLACE, CopySource

    require(
        minio.__version__ == "7.2.20" and urllib3.__version__ == "2.8.0",
        "LOCKED_SDK_VERSION_REQUIRED",
    )
    pool = urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=3, read=10), retries=urllib3.Retry(total=0)
    )
    api = API(pool)
    username, password = (
        secret_file(args.admin_username_file),
        secret_file(args.admin_password_file),
    )
    require(len(username) <= 64 and len(password) <= 72, "ADMIN_SECRET_LIMIT")
    account = api.request(
        "POST", "/api/v1/auth/login", {"username": username, "password": password}
    )
    require(
        account.get("user", {}).get("role") in {"admin", "platform_admin"},
        "STAGE_ADMIN_REQUIRED",
    )
    created = api.request(
        "POST",
        "/api/v1/tasks",
        {
            "name": PREFIX + args.run_id,
            "kind": "IMPORT_COLLAPSED",
            "parameters": {},
            "collapsed_text": "imported-stage-ce-gzip;main;work 80\nimported-stage-ce-gzip;main;allocation 20\n",
        },
        expected=201,
        idempotency="stage-ce-gzip-" + args.run_id,
    )
    task_id = positive_id(created.get("id"))
    proof["task_id"] = task_id
    deadline = time.monotonic() + 180
    while True:
        task = api.request("GET", "/api/v1/tasks/" + task_id)
        require(
            task.get("creator_id") == account["user"]["id"], "FIXTURE_OWNER_CHANGED"
        )
        require(
            task.get("analysis_status") != "FAILED"
            and task.get("collection_status") not in {"FAILED", "CANCELED"},
            "FIXTURE_ANALYSIS_FAILED",
        )
        if task.get("analysis_status") == "SUCCESS":
            break
        require(time.monotonic() < deadline, "FIXTURE_ANALYSIS_DEADLINE")
        time.sleep(2)
    artifact, attempt = select_artifact(task, task_id, args.run_id)
    proof.update(
        artifact_id=artifact["id"],
        attempt_id=attempt["id"],
        collection_attempt=attempt["attempt"],
        size=artifact["size"],
        sha256=artifact["sha256"],
    )
    grant = api.request(
        "GET", f"/api/v1/tasks/{task_id}/artifacts/{artifact['id']}/download"
    )
    key = object_key(grant.get("url"), task_id, attempt["attempt"])
    client = Minio(
        S3_ENDPOINT,
        access_key=secret_file(args.s3_access_key_file),
        secret_key=secret_file(args.s3_secret_key_file),
        secure=False,
        region="us-east-1",
        http_client=pool,
    )
    before = verify_stored(client, key, artifact)
    require(
        header(before.metadata, "Content-Encoding") in {None, ""},
        "FRESH_FIXTURE_ENCODING_UNEXPECTED",
    )
    metadata = replacement_metadata(before.metadata)
    client.copy_object(
        BUCKET,
        key,
        CopySource(BUCKET, key, match_etag='"' + before.etag + '"'),
        metadata=metadata,
        metadata_directive=REPLACE,
    )
    after = verify_stored(client, key, artifact)
    require(
        header(after.metadata, "Content-Encoding") == "gzip",
        "GZIP_SYSTEM_METADATA_NOT_APPLIED",
    )
    require(
        user_metadata(before.metadata) == user_metadata(after.metadata),
        "USER_METADATA_NOT_PRESERVED",
    )
    require(
        all(
            header(before.metadata, name) == header(after.metadata, name)
            for name in SYSTEM
            if name != "Content-Encoding"
        ),
        "SYSTEM_METADATA_NOT_PRESERVED",
    )
    require(
        header(before.metadata, "x-amz-server-side-encryption")
        == header(after.metadata, "x-amz-server-side-encryption"),
        "ENCRYPTION_METADATA_CHANGED",
    )
    current, latest = select_artifact(
        api.request("GET", "/api/v1/tasks/" + task_id), task_id, args.run_id
    )
    require(
        current["id"] == artifact["id"] and latest["id"] == attempt["id"],
        "CURRENT_RESULT_CHANGED",
    )
    proof.update(
        success=True,
        metadata_test={
            "content_encoding": "gzip",
            "stored_size_unchanged": before.size == after.size,
            "stored_sha256_unchanged": before.sha256 == after.sha256,
            "user_metadata_preserved": True,
            "other_system_metadata_preserved": True,
            "encryption_metadata_preserved": True,
        },
    )


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("OPTIONS_INVALID")


def main(argv=None):
    logging.disable(logging.CRITICAL)
    proof = {"schema_version": 1, "success": False}
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--confirm-stage", required=True, choices=["dropfullstage"])
        parser.add_argument("--run-id", required=True)
        for name, default in (
            ("admin-username", "admin_username"),
            ("admin-password", "admin_password"),
            ("s3-access-key", "s3_access_key"),
            ("s3-secret-key", "s3_secret_key"),
        ):
            parser.add_argument(
                "--" + name + "-file", default="/run/secrets/" + default
            )
        args = parser.parse_args(argv)
        require(re.fullmatch(r"[0-9a-f]{32}", args.run_id), "RUN_ID_INVALID")
        proof["run_id"] = args.run_id
        run(args, proof)
    except Failure as error:
        proof["error_code"] = str(error)
    except KeyboardInterrupt:
        proof["error_code"] = "VALIDATION_INTERRUPTED"
    except Exception:  # noqa: BLE001 - public proof must never include SDK errors.
        proof["error_code"] = "STAGE_VALIDATION_FAILED"
    print(json.dumps(proof, sort_keys=True))
    return 0 if proof["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
