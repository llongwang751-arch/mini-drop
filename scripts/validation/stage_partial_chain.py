#!/usr/bin/env python3
"""Stage-only real partial checkpoint/final-commit fault acceptance manager.

No Docker/SSH, arbitrary endpoint/task/key, runtime patch or synthetic DB result.
Only fixed imported data, one new task, its own DDL and four exact orphan keys.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import stat
import sys
import time
import uuid
from http.cookies import SimpleCookie
from pathlib import Path

DIRECTORY = Path("/run/drop-validation-partial")
SECRETS = Path("/run/secrets")
API_ORIGIN = "http://web:8080"
PUBLIC_ORIGIN = "http://127.0.0.1:18080"
ANALYZER_IMAGE = "sha256:b9e6b659e0494e5002ed5f49a43812a73c3eb7b966d050b2e325b5990e9af136"
S3_ENDPOINT = "minio:9000"
BUCKET = "drop-artifacts"
PREFIX = "IMPORTED_STAGE_PARTIAL_"
RAW = b"synthetic_stage_partial;main;hot 80\nsynthetic_stage_partial;main;cold 20\n"
RAW_SHA = hashlib.sha256(RAW).hexdigest()
OUTPUTS = ("flamegraph.json.gz", "top.json", "report.json", "manifest.json")
IDS = ("task_id", "attempt_id", "analysis_job_id", "input_artifact_id")
MAXIMUM = 65536


class Failure(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Failure(code)


def positive(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value) and int(value) <= 2**63 - 1


def account_identity(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "PARTIAL_JSON_REJECTED")
        value[key] = item
    return value


def decode(payload, maximum=MAXIMUM):
    require(0 < len(payload) <= maximum, "PARTIAL_JSON_REJECTED")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
        require(isinstance(value, dict), "PARTIAL_JSON_REJECTED")
        return value
    except (ValueError, TypeError, RecursionError):
        raise Failure("PARTIAL_JSON_REJECTED") from None


def read_file(path, maximum, *, owner=True):
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= maximum
                and (not owner or (info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600)), "PARTIAL_PRIVATE_FILE_REJECTED")
        require(not path.is_symlink(), "PARTIAL_PRIVATE_FILE_REJECTED")
        payload = os.read(descriptor, maximum + 1)
        require(len(payload) == info.st_size, "PARTIAL_PRIVATE_FILE_REJECTED")
        return payload
    except Failure:
        raise
    except (OSError, ValueError):
        raise Failure("PARTIAL_PRIVATE_FILE_REJECTED") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_scope(run_id, *, directory=DIRECTORY, owner=True):
    require(re.fullmatch(r"[0-9a-f]{32}", run_id), "PARTIAL_RUN_REJECTED")
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and (not owner or (info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)), "PARTIAL_SCOPE_REJECTED")
    value = decode(read_file(directory / "scope.json", 4096, owner=owner), 4096)
    require(set(value) == {"schema_version", "project", "run_id", "public_origin", "api_origin", "analyzer_image", "postgres_container_id"}
            and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["project"] == "dropfullstage" and value["run_id"] == run_id
            and value["public_origin"] == PUBLIC_ORIGIN and value["api_origin"] == API_ORIGIN
            and value["analyzer_image"] == ANALYZER_IMAGE
            and isinstance(value["postgres_container_id"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["postgres_container_id"]), "PARTIAL_SCOPE_REJECTED")
    return value


def secret(name):
    require(name in {"admin_username", "admin_password", "migration_database_url", "s3_access_key", "s3_secret_key"}, "PARTIAL_SECRET_REJECTED")
    value = read_file(SECRETS / name, 1024).decode("utf-8").rstrip("\r\n")
    require(value and not any(ord(char) < 32 or ord(char) == 127 for char in value), "PARTIAL_SECRET_REJECTED")
    return value


class State:
    def __init__(self, run_id, directory=DIRECTORY, *, owner=True):
        self.run_id, self.directory, self.owner = run_id, directory, owner
        self.path = directory / "state.json"

    def load(self):
        value = decode(read_file(self.path, 16384, owner=self.owner), 16384)
        require(value.get("schema_version") == 1 and value.get("run_id") == self.run_id, "PARTIAL_STATE_REJECTED")
        return value

    def reserve(self):
        value = {"schema_version": 1, "run_id": self.run_id, "phase": "reserved", "objects": {}, "orphans": []}
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
        return value

    def save(self, value):
        original = self.path.lstat()
        self.load()
        temporary = self.directory / (".state-" + uuid.uuid4().hex)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
            current = self.path.lstat()
            require((current.st_dev, current.st_ino) == (original.st_dev, original.st_ino), "PARTIAL_STATE_IDENTITY_CHANGED")
            os.replace(temporary, self.path)
            if sys.platform == "linux":
                parent = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
        finally:
            if temporary.exists():
                temporary.unlink()


class API:
    def __init__(self, pool):
        self.pool, self.cookie, self.csrf = pool, "", ""

    def request(self, method, path, data=None, *, expected=200, key=None):
        require(re.fullmatch(r"/api/v1/(auth/login|tasks|tasks/[1-9][0-9]{0,18}(/(partial-result|result))?)", path), "PARTIAL_API_PATH_REJECTED")
        headers = {"Origin": PUBLIC_ORIGIN, "Accept": "application/json", "Accept-Encoding": "identity"}
        if self.cookie:
            headers["Cookie"] = "drop_session=" + self.cookie
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if key:
            headers["Idempotency-Key"] = key
        if data is not None:
            headers["Content-Type"] = "application/json"
        response = self.pool.request(method, API_ORIGIN + path, body=canonical(data) if data is not None else None,
                                     headers=headers, redirect=False, preload_content=False)
        try:
            require(response.status == expected, "PARTIAL_API_STATUS_FAILED")
            maximum = 17408 if path.endswith("/partial-result") else MAXIMUM
            value = decode(response.read(maximum + 1, decode_content=False), maximum)
            if expected == 409:
                require(isinstance(value.get("error"), dict), "PARTIAL_API_ENVELOPE_FAILED")
                return value
            require(value.get("error") is None and isinstance(value.get("data"), dict), "PARTIAL_API_ENVELOPE_FAILED")
            if path == "/api/v1/auth/login":
                cookies = SimpleCookie(); cookies.load(response.headers.get("Set-Cookie", ""))
                self.cookie = cookies["drop_session"].value if "drop_session" in cookies else ""
                self.csrf = value["data"].get("csrf_token", "")
                require(isinstance(self.csrf, str) and re.fullmatch(r"[0-9a-f]{64}", self.csrf)
                        and re.fullmatch(r"[0-9a-f]{64}", self.cookie), "PARTIAL_SESSION_REJECTED")
            return value["data"]
        finally:
            response.close(); response.release_conn()


def names(run_id):
    require(re.fullmatch(r"[0-9a-f]{32}", run_id), "PARTIAL_RUN_REJECTED")
    return {kind: "drop_partial_" + kind + "_" + run_id for kind in ("trigger", "function", "sequence")}


def keys(binding):
    require(all(positive(binding.get(name)) for name in IDS), "PARTIAL_BINDING_REJECTED")
    prefix = f"results/tasks/{binding['task_id']}/attempts/1/analysis/{binding['analysis_job_id']}/runs/1/"
    return [prefix + name for name in OUTPUTS]


def validate_row(row, state, *, prepared=False):
    require(isinstance(row, dict) and row.get("name") == PREFIX + state["run_id"]
            and str(row.get("creator_id")) == state["creator_id"] and row.get("kind") == "IMPORT_COLLAPSED"
            and row.get("collection_status") == "COLLECTED" and row.get("collection_attempt") == 1
            and row.get("input_digest") == RAW_SHA and row.get("input_size") == len(RAW)
            and all(str(row.get(name)) == state[name] for name in IDS), "PARTIAL_CURRENT_BINDING_REJECTED")
    if prepared:
        require(row.get("analysis_status") == "PENDING" and row.get("job_status") == "pending"
                and row.get("analysis_run") == 0 and row.get("lease_owner") is None
                and row.get("lease_expires_at") is None and row.get("result") is None, "PARTIAL_JOB_ALREADY_CLAIMED")
    else:
        require(row.get("analysis_status") == "FAILED" and row.get("job_status") == "failed"
                and row.get("analysis_run") == 1 and row.get("max_attempts") == 1
                and row.get("lease_owner") is None and row.get("lease_expires_at") is None,
                "PARTIAL_NOT_TERMINAL_FAILED")


class Database:
    def __init__(self, dsn):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row
        values = conninfo_to_dict(dsn)
        require(values.get("host") == "postgres" and values.get("dbname") == "drop" and values.get("user") == "drop"
                and values.get("port", "5432") == "5432" and not any(key in values for key in ("hostaddr", "service", "options")), "PARTIAL_DATABASE_SCOPE_REJECTED")
        self.driver, self.sql, self.rows, self.dsn = psycopg, sql, dict_row, dsn

    def connect(self):
        return self.driver.connect(self.dsn, connect_timeout=3, row_factory=self.rows,
                                   application_name="drop_stage_partial_validation",
                                   options="-c search_path=public -c statement_timeout=5000 -c lock_timeout=3000")

    def idle(self):
        with self.connect() as connection:
            require(connection.execute("SELECT count(*) AS n FROM analysis_jobs WHERE status IN ('pending','running','retry')").fetchone()["n"] == 0, "PARTIAL_STAGE_QUEUE_NOT_IDLE")

    def row(self, connection, task_id, *, lock=False):
        rows = connection.execute("""SELECT t.id AS task_id,t.name,t.creator_id,t.kind,t.collection_status,t.analysis_status,
            a.id AS attempt_id,a.attempt AS collection_attempt,j.id AS analysis_job_id,j.status AS job_status,
            j.attempt AS analysis_run,j.max_attempts,j.lease_owner,j.lease_expires_at,j.result,
            r.id AS input_artifact_id,r.sha256 AS input_digest,r.size AS input_size
            FROM tasks t JOIN LATERAL(SELECT * FROM task_attempts WHERE task_id=t.id ORDER BY attempt DESC LIMIT 1)a ON true
            JOIN analysis_jobs j ON j.task_id=t.id AND j.attempt_id=a.id
            JOIN LATERAL(SELECT * FROM artifacts WHERE task_id=t.id AND attempt_id=a.id AND kind='RAW' AND status='ready' ORDER BY id DESC LIMIT 1)r ON true
            WHERE t.id=%s""" + (" FOR UPDATE OF t,j" if lock else ""), (int(task_id),)).fetchall()
        require(len(rows) == 1, "PARTIAL_CURRENT_BINDING_REJECTED")
        return rows[0]

    def snapshot(self, task_id):
        with self.connect() as connection:
            return self.row(connection, task_id)

    def fingerprints(self, connection, run_id):
        result = {}
        for kind, name in names(run_id).items():
            if kind == "trigger":
                query = "SELECT oid,pg_get_triggerdef(oid,true) AS definition,tgrelid::text AS parent,tgfoid::text AS function,tgenabled FROM pg_trigger WHERE tgname=%s AND tgrelid='public.artifacts'::regclass AND NOT tgisinternal"
            elif kind == "function":
                query = "SELECT p.oid,pg_get_functiondef(p.oid) AS definition,pg_get_userbyid(p.proowner) AS owner,p.proacl::text AS acl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname=%s"
            else:
                query = "SELECT c.oid,pg_get_userbyid(c.relowner) AS owner,c.relacl::text AS acl,s.seqtypid,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_sequence s ON s.seqrelid=c.oid WHERE n.nspname='public' AND c.relname=%s AND c.relkind='S'"
            rows = connection.execute(query, (name,)).fetchall()
            require(len(rows) <= 1, "PARTIAL_DDL_IDENTITY_REJECTED")
            if rows:
                result[kind] = {"oid": rows[0]["oid"], "sha256": sha(canonical(rows[0]))}
        return result

    def install(self, state, journal):
        sql = self.sql
        n = names(state["run_id"])
        q = lambda kind: sql.Identifier("public", n[kind])
        with self.connect() as connection:
            row = self.row(connection, state["task_id"], lock=True)
            validate_row(row, state, prepared=True)
            require(not self.fingerprints(connection, state["run_id"]), "PARTIAL_DDL_ALREADY_EXISTS")
            # Also reject collisions with another relation or trigger table.
            for kind, name in n.items():
                if kind == "function":
                    continue
                exists = connection.execute("SELECT to_regclass(%s) AS relation", ("public." + name,)).fetchone()["relation"]
                require(exists is None, "PARTIAL_DDL_ALREADY_EXISTS")
            changed = connection.execute("UPDATE analysis_jobs SET max_attempts=1 WHERE id=%s AND task_id=%s AND status='pending' AND attempt=0 AND result IS NULL AND lease_owner IS NULL RETURNING id", (int(state["analysis_job_id"]), int(state["task_id"]))).fetchone()
            require(changed is not None, "PARTIAL_JOB_ALREADY_CLAIMED")
            connection.execute(sql.SQL("CREATE SEQUENCE {} AS bigint MINVALUE 1 MAXVALUE 2 START 1 CACHE 1 NO CYCLE").format(q("sequence")))
            connection.execute(sql.SQL("GRANT USAGE,SELECT ON SEQUENCE {} TO drop_app").format(q("sequence")))
            body = sql.SQL("""BEGIN IF NEW.task_id={} AND NEW.attempt_id={} AND NEW.kind IN ('RESULT','MANIFEST')
                AND NEW.object_key IN ({}) AND EXISTS(SELECT 1 FROM public.analysis_jobs WHERE id={} AND task_id={}
                AND attempt_id={} AND attempt=1 AND status='running' AND max_attempts=1 AND lease_owner IS NOT NULL
                AND lease_expires_at>pg_catalog.clock_timestamp()) THEN
                PERFORM pg_catalog.nextval({}::regclass);
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='IMPORTED_STAGE_PARTIAL_FINAL_COMMIT_FAILURE';
                END IF; RETURN NEW; END""").format(
                    sql.Literal(int(state["task_id"])), sql.Literal(int(state["attempt_id"])),
                    sql.SQL(",").join(sql.Literal(key) for key in keys(state)),
                    sql.Literal(int(state["analysis_job_id"])), sql.Literal(int(state["task_id"])),
                    sql.Literal(int(state["attempt_id"])), sql.Literal("public." + n["sequence"]),
                )
            connection.execute(sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS {}").format(q("function"), sql.Literal(body.as_string(connection))))
            connection.execute(sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON public.artifacts FOR EACH ROW WHEN (NEW.task_id={} AND NEW.attempt_id={} AND NEW.kind IN ('RESULT','MANIFEST')) EXECUTE FUNCTION {}()").format(
                sql.Identifier(n["trigger"]), sql.Literal(int(state["task_id"])), sql.Literal(int(state["attempt_id"])), q("function")))
            state.update(phase="prepared", objects=self.fingerprints(connection, state["run_id"]))
            require(set(state["objects"]) == {"trigger", "function", "sequence"}, "PARTIAL_DDL_IDENTITY_REJECTED")
            journal.save(state)  # durable ownership before CREATE transaction commits

    def facts(self, state):
        with self.connect() as connection:
            row = self.row(connection, state["task_id"])
            validate_row(row, state)
            require(self.fingerprints(connection, state["run_id"]) == state["objects"], "PARTIAL_DDL_IDENTITY_REJECTED")
            sequence = connection.execute(self.sql.SQL("SELECT last_value,is_called FROM {}").format(self.sql.Identifier("public", names(state["run_id"])["sequence"]))).fetchone()
            require(sequence["is_called"] is True and sequence["last_value"] == 1, "PARTIAL_FAULT_NOT_OBSERVED")
            events = connection.execute("SELECT sequence,type,source,payload FROM task_events WHERE task_id=%s AND attempt_id=%s AND type IN ('ANALYSIS_PARTIAL_SAVED','ANALYSIS_FAILED') ORDER BY sequence LIMIT 10", (int(state["task_id"]), int(state["attempt_id"]))).fetchall()
            require(len(events) == 2 and [item["type"] for item in events] == ["ANALYSIS_PARTIAL_SAVED", "ANALYSIS_FAILED"]
                    and all(item["source"] == "analyzer" and item["payload"].get("analysis_job_id") == state["analysis_job_id"] and item["payload"].get("analysis_attempt") == 1 for item in events), "PARTIAL_EVENT_CHAIN_REJECTED")
            count = connection.execute("SELECT count(*) AS n FROM artifacts WHERE task_id=%s AND attempt_id=%s AND kind IN ('RESULT','MANIFEST')", (int(state["task_id"]), int(state["attempt_id"]))).fetchone()["n"]
            require(count == 0, "PARTIAL_RESULT_METADATA_PUBLISHED")
            return row

    def referenced(self, key):
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM artifacts WHERE object_key=%s LIMIT 1", (key,)).fetchone() is not None

    def cleanup(self, state):
        with self.connect() as connection:
            actual = self.fingerprints(connection, state["run_id"])
            expected = state.get("objects", {})
            require(all(kind in expected and value == expected[kind] for kind, value in actual.items()), "PARTIAL_DDL_IDENTITY_REJECTED")
            n = names(state["run_id"])
            # No CASCADE; all identities are checked before any drop.
            if "trigger" in actual:
                connection.execute(self.sql.SQL("DROP TRIGGER {} ON public.artifacts").format(self.sql.Identifier(n["trigger"])))
            if "function" in actual:
                connection.execute(self.sql.SQL("DROP FUNCTION {}()").format(self.sql.Identifier("public", n["function"])))
            if "sequence" in actual:
                connection.execute(self.sql.SQL("DROP SEQUENCE {}").format(self.sql.Identifier("public", n["sequence"])))


class Storage:
    def __init__(self, pool):
        from minio import Minio
        self.client = Minio(S3_ENDPOINT, access_key=secret("s3_access_key"), secret_key=secret("s3_secret_key"), secure=False, region="us-east-1", http_client=pool)

    def inspect(self, key, *, absent=False):
        from minio.error import S3Error
        try:
            value = self.client.stat_object(BUCKET, key)
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchObject"}:
                return None
            raise Failure("PARTIAL_OBJECT_STAT_FAILED") from None
        require(not absent, "PARTIAL_OBJECT_DELETE_FAILED")
        digest = next((v for k, v in value.metadata.items() if k.lower() == "x-amz-meta-sha256"), None)
        require(type(value.size) is int and 0 < value.size <= MAXIMUM and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "PARTIAL_OBJECT_METADATA_REJECTED")
        response = self.client.get_object(BUCKET, key)
        try:
            body = response.read(MAXIMUM + 1, decode_content=False)
            require(len(body) == value.size and sha(body) == digest, "PARTIAL_OBJECT_BYTES_REJECTED")
        finally:
            response.close(); response.release_conn()
        return {"key": key, "size": value.size, "sha256": digest, "etag": value.etag}

    def remove(self, record):
        require(self.inspect(record["key"]) == record, "PARTIAL_OBJECT_IDENTITY_CHANGED")
        self.client.remove_object(BUCKET, record["key"])
        self.inspect(record["key"], absent=True)


def login(api):
    account = api.request("POST", "/api/v1/auth/login", {"username": secret("admin_username"), "password": secret("admin_password")})
    user = account.get("user", {})
    require(user.get("role") in {"admin", "platform_admin"} and account_identity(user.get("id")), "PARTIAL_STAGE_ADMIN_REQUIRED")
    return user["id"]


def prepare(api, database, journal, run_id):
    state = journal.reserve()
    database.idle()
    state["creator_id"] = login(api)
    task = api.request("POST", "/api/v1/tasks", {"name": PREFIX + run_id, "kind": "IMPORT_COLLAPSED", "parameters": {}, "collapsed_text": RAW.decode()}, expected=201, key="stage-partial-" + run_id)
    require(positive(task.get("id")), "PARTIAL_TASK_REJECTED")
    state["task_id"] = task["id"]
    journal.save(state)
    row = database.snapshot(state["task_id"])
    state.update({name: str(row[name]) for name in IDS})
    state["input_digest"] = RAW_SHA
    journal.save(state)
    validate_row(row, state, prepared=True)
    database.install(state, journal)
    return {"success": True, "phase": "prepared", "run_id": run_id,
            **{name: state[name] for name in IDS}, "input_digest": RAW_SHA}


def clean_orphans(database, storage, journal, state):
    allowed = keys(state)
    records = state.get("orphans", [])
    require(isinstance(records, list) and len(records) <= 4 and all(isinstance(record, dict) and record.get("key") in allowed for record in records)
            and len({record["key"] for record in records}) == len(records), "PARTIAL_OBJECT_SCOPE_REJECTED")
    if not records:
        records = [record for key in allowed if (record := storage.inspect(key)) is not None]
        state["orphans"] = records
        journal.save(state)  # durable exact list before any destructive action
    require(all(not database.referenced(record["key"]) for record in records), "PARTIAL_OBJECT_REFERENCED")
    for record in records:
        require(not database.referenced(record["key"]), "PARTIAL_OBJECT_REFERENCED")
        current = storage.inspect(record["key"])
        if current is not None:
            require(current == record, "PARTIAL_OBJECT_IDENTITY_CHANGED")
            storage.remove(record)
    return len(records)


def verify(api, database, storage, journal, validate_partial, *, clock=time.monotonic, sleep=time.sleep):
    state = journal.load()
    require(state.get("phase") in {"prepared", "verified"}, "PARTIAL_STATE_REJECTED")
    require(login(api) == state["creator_id"], "PARTIAL_OWNER_CHANGED")
    deadline = clock() + 180
    while True:
        task = api.request("GET", "/api/v1/tasks/" + state["task_id"])
        require(task.get("id") == state["task_id"] and task.get("name") == PREFIX + state["run_id"] and task.get("creator_id") == state["creator_id"], "PARTIAL_OWNER_CHANGED")
        require(task.get("collection_status") == "COLLECTED" and task.get("analysis_status") != "SUCCESS", "PARTIAL_DID_NOT_FAIL")
        if task.get("analysis_status") == "FAILED":
            break
        require(clock() < deadline, "PARTIAL_ANALYSIS_DEADLINE")
        sleep(1)
    row = database.facts(state)
    identity = task.get("analysis_identity")
    require(identity == {"analysis_job_id": state["analysis_job_id"], "analysis_run": 1,
                         "input_artifact_id": state["input_artifact_id"], "input_digest": RAW_SHA}, "PARTIAL_API_BINDING_REJECTED")
    value = api.request("GET", "/api/v1/tasks/" + state["task_id"] + "/partial-result")
    require(validate_partial(value) and value == row["result"] and value.get("input_digest") == RAW_SHA
            and value.get("analysis_run") == 1 and value.get("summary", {}).get("source") == "import"
            and value["summary"]["total_samples"] == 100 and value["summary"]["unique_stacks"] == 2,
            "PARTIAL_API_CHECKPOINT_REJECTED")
    api.request("GET", "/api/v1/tasks/" + state["task_id"] + "/result", expected=409)
    count = clean_orphans(database, storage, journal, state)
    require(1 <= count <= 4, "PARTIAL_UPLOADS_NOT_OBSERVED")
    state["phase"] = "verified"; journal.save(state)
    return {"schema_version": 1, "success": True, "phase": "verified", "run_id": state["run_id"],
            **{name: state[name] for name in IDS}, "analysis_run": 1, "input_digest": RAW_SHA,
            "partial_sha256": sha(canonical(value)), "summary": value["summary"],
            "checkpoint_event_before_failure": True, "terminal_failed_no_lease": True,
            "no_published_result_artifacts": True, "orphan_objects_deleted": True,
            "orphan_object_count": count, "injected_commit_failure_observed": True, "fault_trigger_calls": 1}


def cleanup(database, storage, journal, run_id):
    if not journal.path.exists():
        state = {"run_id": run_id, "objects": {}}
    else:
        state = journal.load()
    # Orphan deletion requires the real final-failure fence; otherwise retain
    # unknown/in-flight bytes. DDL cleanup still always runs in the finally.
    try:
        if all(name in state for name in IDS):
            row = database.snapshot(state["task_id"])
            if row.get("job_status") == "failed" and row.get("analysis_run") == 1:
                validate_row(row, state)
                clean_orphans(database, storage, journal, state)
    finally:
        database.cleanup(state)
    return {"success": True, "phase": "cleanup", "run_id": run_id,
            "trigger_removed": True, "function_removed": True, "sequence_removed": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "verify", "cleanup"), required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    require(sys.platform == "linux" and os.geteuid() == 0, "PARTIAL_MANAGER_HOST_REJECTED")
    read_scope(args.run_id)  # before any credential, SQL or HTTP access
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    import urllib3
    pool = urllib3.PoolManager(timeout=urllib3.Timeout(connect=3, read=10), retries=False)
    database = Database(secret("migration_database_url"))
    journal = State(args.run_id)
    if args.phase == "prepare":
        result = prepare(API(pool), database, journal, args.run_id)
    elif args.phase == "verify":
        sys.path.insert(0, "/app")
        from drop_analyzer.partial import validate_partial
        result = verify(API(pool), database, Storage(pool), journal, validate_partial)
    else:
        result = cleanup(database, Storage(pool), journal, args.run_id)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Failure as error:
        code = str(error)
        if not re.fullmatch(r"PARTIAL_[A-Z0-9_]{1,80}", code):
            code = "PARTIAL_MANAGER_FAILED"
        print(json.dumps({"success": False, "error_code": code}))
        raise SystemExit(1) from None
    except Exception as error:  # noqa: BLE001 -- no native message/body/URL/DSN
        import traceback
        sdk_code = getattr(error, "code", None)
        sql_state = getattr(error, "sqlstate", None)
        print(json.dumps({"success": False, "error_code": "PARTIAL_MANAGER_FAILED",
                          "exception_type": type(error).__name__,
                          "sdk_code": sdk_code if isinstance(sdk_code, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", sdk_code) else None,
                          "sql_state": sql_state if isinstance(sql_state, str) and re.fullmatch(r"[A-Z0-9]{5}", sql_state) else None,
                          "fixed_script_lines": [frame.lineno for frame in traceback.extract_tb(error.__traceback__) if frame.filename == __file__]}))
        raise SystemExit(1) from None
