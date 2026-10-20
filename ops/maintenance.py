import json
import hmac
import os
import stat
import re
import math
import sys
from pathlib import Path
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from minio import Minio
from minio.error import S3Error
from minio.commonconfig import ENABLED, Filter
from minio.lifecycleconfig import (
    LifecycleConfig,
    Rule,
    Expiration,
    AbortIncompleteMultipartUpload,
)
from reconcile import reconcile

from ops_runtime import Config, ConfigurationError, VERSION, emit


def log(message, **fields):
    emit("drop-maintenance", VERSION, message, stream=sys.stderr, **fields)


class SQLRepository:
    def __init__(self, conn):
        self.conn = conn

    def expired(self, limit):
        return self.conn.execute(
            "SELECT id,object_key FROM artifacts a WHERE status IN ('ready','deleting') AND expires_at<=now() AND NOT EXISTS(SELECT 1 FROM analysis_jobs j WHERE j.task_id=a.task_id AND j.status IN ('pending','running','retry')) ORDER BY expires_at LIMIT %s",
            (limit,),
        ).fetchall()

    def candidates(self, limit):
        # Stable cursor state persists in metadata so large catalogues do not starve later rows.
        result = self.conn.execute(
            "SELECT id,object_key,size,sha256 FROM artifacts WHERE status='ready' AND created_at<now()-interval '5 minutes' ORDER BY COALESCE((metadata->>'last_reconciled_at')::timestamptz,created_at),id LIMIT %s",
            (limit,),
        ).fetchall()
        return result

    def mark_checked(self, item):
        self.conn.execute(
            "UPDATE artifacts SET metadata=metadata||jsonb_build_object('last_reconciled_at',now()) WHERE id=%s",
            (item["id"],),
        )

    def object_cursor(self):
        row = self.conn.execute(
            "SELECT value FROM storage_maintenance_state WHERE key='objects_cursor'"
        ).fetchone()
        return row["value"].get("cursor", "") if row else ""

    def save_object_cursor(self, cursor):
        self.conn.execute(
            "INSERT INTO storage_maintenance_state(key,value) VALUES('objects_cursor',%s::jsonb) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=now()",
            (json.dumps({"cursor": cursor}),),
        )

    def referenced(self, key):
        return self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM artifacts WHERE object_key=%s UNION ALL SELECT 1 FROM task_attempts WHERE raw_object_key=%s UNION ALL SELECT 1 FROM analysis_jobs j WHERE j.status IN ('pending','running','retry') AND (%s LIKE 'results/tasks/'||j.task_id||'/%%' OR %s LIKE 'result/'||j.task_id||'/%%')) AS yes",
            (key, key, key, key),
        ).fetchone()["yes"]

    def set_status(self, item, state):
        self.conn.execute(
            "UPDATE artifacts SET status=%s,download_allowed=false WHERE id=%s",
            (state, item["id"]),
        )

    def audit(self, action, identifier):
        self.conn.execute(
            "INSERT INTO audit_logs(action,resource_type,resource_id,request_id,detail) VALUES(%s,'artifact',%s,'maintenance',%s::jsonb)",
            (action, identifier, json.dumps({"source": "maintenance"})),
        )


class S3Storage:
    def __init__(self, client, bucket):
        self.client = client
        self.bucket = bucket

    def remove(self, key):
        self.client.remove_object(self.bucket, key)

    def stat(self, key):
        try:
            return self.client.stat_object(self.bucket, key).size
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
                return None
            raise

    def chunks(self, key):
        response = self.client.get_object(self.bucket, key)
        try:
            yield from response.stream(1024 * 1024, decode_content=False)
        finally:
            response.close()
            response.release_conn()

    def list(self, after=""):
        for obj in self.client.list_objects(
            self.bucket, recursive=True, start_after=after or None
        ):
            yield obj.object_name, obj.last_modified

    def multipart(self):
        # Isolate the pinned minio-py 7.2.20 signed multipart adapter here.
        marker = upload_marker = None
        for _ in range(100):
            result = self.client._list_multipart_uploads(
                self.bucket,
                key_marker=marker,
                upload_id_marker=upload_marker,
                max_uploads=100,
            )
            for item in result.uploads:
                yield item.object_name, item.upload_id, item.initiated_time
            if not result.is_truncated or not result.uploads:
                return
            next_marker = result.next_key_marker or result.uploads[-1].object_name
            next_upload = result.next_upload_id_marker or result.uploads[-1].upload_id
            if (next_marker, next_upload) == (marker, upload_marker):
                raise RuntimeError("Multipart cursor did not advance")
            marker, upload_marker = next_marker, next_upload

    def abort(self, key, upload_id):
        self.client._abort_multipart_upload(self.bucket, key, upload_id)


def configure_lifecycle(client, bucket):
    rules = [
        Rule(
            ENABLED,
            rule_filter=Filter(prefix=prefix),
            rule_id="drop-" + prefix.rstrip("/"),
            expiration=Expiration(days=days),
        )
        for prefix, days in [
            ("raw/", 14),
            ("intermediate/", 3),
            ("result/", 90),
            ("results/", 90),
            ("logs/", 14),
            ("manifest/", 90),
            ("binary/", 90),
        ]
    ]
    rules.append(
        Rule(
            ENABLED,
            rule_filter=Filter(prefix=""),
            rule_id="drop-incomplete",
            abort_incomplete_multipart_upload=AbortIncompleteMultipartUpload(
                days_after_initiation=1
            ),
        )
    )
    try:
        client.set_bucket_lifecycle(bucket, LifecycleConfig(rules))
    except S3Error as exc:
        if exc.code not in ("InvalidArgument", "InvalidRequest", "NotImplemented"):
            raise
        # The community backend rejects AbortIncompleteMultipartUpload XML.
        # Keep expiration rules; the bounded reconciler explicitly lists and aborts old uploads.
        client.set_bucket_lifecycle(bucket, LifecycleConfig(rules[:-1]))
        log(
            "multipart_lifecycle_adapter", stage="startup", mode="explicit_signed_abort"
        )


state = {
    "last_success": 0,
    "errors": 0,
    "expired": 0,
    "missing": 0,
    "quarantined": 0,
    "checked": 0,
    "orphans": 0,
    "multipart_aborted": 0,
}


def external_monitoring_metrics(path):
    """Optional host counters must never break the primary metrics endpoint."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return b""
            data = stream.read(4097)
        if len(data) > 4096:
            return b""
        lines = []
        for line in data.decode("ascii").splitlines():
            match = re.fullmatch(r"drop_(backup_last_success|backup_restore_duration_seconds|public_certificate_expiry_seconds) ((?:0|[1-9][0-9]*)(?:\.[0-9]+)?)", line)
            if match and math.isfinite(float(match.group(2))):
                lines.append(line)
        return ("\n".join(lines) + ("\n" if lines else "")).encode("ascii")
    except FileNotFoundError:
        return b""
    except (OSError, UnicodeError, ValueError) as error:
        log("monitoring_status_unavailable", level="warning", stage="metrics",
            error_code="MONITORING_STATUS_UNAVAILABLE", error_type=type(error).__name__)
        return b""


class Metrics(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/alerts":
            self.send_error(404)
            return
        expected = self.server.config["ALERT_WEBHOOK_TOKEN"]
        if not expected or not hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + expected
        ):
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(413)
            return
        if size <= 0 or size > 65536:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(size))
            alerts = payload.get("alerts", [])
            if not isinstance(alerts, list) or len(alerts) > 100:
                raise ValueError("Invalid alert batch")
            with psycopg.connect(
                self.server.config["DATABASE_URL"], autocommit=True
            ) as conn:
                for alert in alerts:
                    labels = alert.get("labels", {})
                    safe = {
                        key: str(labels[key])[:150]
                        for key in ("alertname", "severity", "job", "instance")
                        if key in labels
                    }
                    safe["status"] = (
                        "resolved" if alert.get("status") == "resolved" else "firing"
                    )
                    conn.execute(
                        "INSERT INTO audit_logs(action,resource_type,resource_id,request_id,detail) VALUES('MONITORING_ALERT','monitoring',%s,'alertmanager',%s::jsonb)",
                        (safe.get("alertname", "unknown"), json.dumps(safe)),
                    )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"accepted\n")
        except Exception:
            self.send_error(503)

    def do_GET(self):
        if self.path not in ("/metrics", "/healthz"):
            self.send_error(404)
            return
        if self.path == "/healthz":
            ok = time.time() - state["last_success"] < 900
            payload = b"ready\n" if ok else b"not ready\n"
            self.send_response(200 if ok else 503)
        else:
            payload = "".join(
                "drop_storage_reconcile_"
                + key
                + ("" if key == "last_success" else "_total")
                + " "
                + str(value)
                + "\n"
                for key, value in state.items()
            ).encode()
            fs = os.statvfs("/")
            payload += f"drop_host_disk_free_bytes {fs.f_bavail * fs.f_frsize}\ndrop_host_disk_size_bytes {fs.f_blocks * fs.f_frsize}\n".encode()
            payload += external_monitoring_metrics(self.server.config["MONITORING_STATUS_FILE"])
            self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


class SafeHTTPServer(ThreadingHTTPServer):
    def handle_error(self, *_):
        log(
            "http_handler_failed",
            level="error",
            stage="http",
            error_code="HTTP_HANDLER_FAILED",
        )


def main(argv=None):
    server = None
    server_started = False
    try:
        config = Config.load(argv)
        config.log_startup("drop-maintenance")
        endpoint = urlsplit(config["S3_ENDPOINT"])
        client = Minio(
            endpoint.netloc,
            access_key=config["S3_ACCESS_KEY"],
            secret_key=config["S3_SECRET_KEY"],
            secure=endpoint.scheme == "https",
        )
        bucket = config["S3_BUCKET"]
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        configure_lifecycle(client, bucket)
        server = SafeHTTPServer(
            (config["MAINTENANCE_METRICS_HOST"], config["MAINTENANCE_METRICS_PORT"]),
            Metrics,
        )
        server.config = config
        threading.Thread(target=server.serve_forever, daemon=True).start()
        server_started = True
        while not stop.is_set():
            started = time.monotonic()
            try:
                with psycopg.connect(
                    config["DATABASE_URL"], autocommit=True, row_factory=dict_row
                ) as conn:
                    locked = conn.execute(
                        "SELECT pg_try_advisory_lock(hashtextextended('drop-maintenance-v1',0)) AS yes"
                    ).fetchone()["yes"]
                    if locked:
                        outcome = reconcile(
                            SQLRepository(conn), S3Storage(client, bucket)
                        )
                        for key, value in outcome.items():
                            state[key] += value
                        state["last_success"] = time.time()
                        log(
                            "storage_reconciled",
                            stage="reconcile",
                            duration_ms=int((time.monotonic() - started) * 1000),
                            **outcome,
                        )
            except Exception as exc:
                state["errors"] += 1
                log(
                    "storage_reconcile_failed",
                    level="error",
                    stage="reconcile",
                    error_code="STORAGE_OR_DATABASE_FAILURE",
                    error_type=type(exc).__name__,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            stop.wait(config["MAINTENANCE_INTERVAL_SECONDS"])
        log("maintenance_stopped", stage="shutdown")
        return 0
    except ConfigurationError:
        log(
            "configuration_invalid",
            level="error",
            stage="startup",
            error_code="CONFIGURATION_INVALID",
        )
        return 1
    except Exception:
        log(
            "maintenance_startup_failed",
            level="error",
            stage="startup",
            error_code="STARTUP_FAILED",
        )
        return 1
    finally:
        if server is not None:
            if server_started:
                server.shutdown()
            server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
