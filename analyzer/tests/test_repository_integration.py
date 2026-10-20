"""使用真实 PostgreSQL 的隔离测试，必须显式配置 TEST_DATABASE_URL 才运行。

测试创建并清理自己的 UUID schema，不清空或修改连接默认 schema 中的应用表。
"""

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from drop_analyzer.config import Config
from drop_analyzer.errors import AnalysisError, LeaseLost
from drop_analyzer.folded import analyze_folded
from drop_analyzer.partial import build_partial
from drop_analyzer.repository import Repository
from drop_analyzer.result import make_result
from drop_analyzer.storage import ObjectStore
from drop_analyzer.worker import process_job
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row


@unittest.skipUnless(
    os.getenv("TEST_DATABASE_URL"),
    "Set TEST_DATABASE_URL for real PostgreSQL isolation tests",
)
class RepositoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "analyzer_test_" + uuid4().hex
        cls.base_dsn = os.environ["TEST_DATABASE_URL"]
        with psycopg.connect(cls.base_dsn) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {};").format(sql.Identifier(cls.schema))
            )
        cls.dsn = make_conninfo(cls.base_dsn, options=f"-csearch_path={cls.schema}")
        with psycopg.connect(cls.dsn) as connection:
            migrations = Path(__file__).resolve().parents[2] / "db" / "migrations"
            for migration in sorted(migrations.glob("*.sql")):
                connection.execute(migration.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.base_dsn) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema))
            )

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def setUp(self):
        with self.connect() as connection:
            connection.execute("TRUNCATE users CASCADE")
            connection.execute(
                "INSERT INTO users(id,username,display_name,role) VALUES('test-user','test','Test','admin')"
            )
        self.a, self.b = Repository(self.dsn, "a", 60), Repository(self.dsn, "b", 60)

    def seed(self, *, max_attempts=3):
        with self.connect() as connection:
            task_id = connection.execute("""INSERT INTO tasks(name,kind,creator_id,request_id,collection_status)
                VALUES('Profile','IMPORT_COLLAPSED','test-user','test-request','COLLECTED') RETURNING id""").fetchone()[
                "id"
            ]
            attempt_id = connection.execute(
                """INSERT INTO task_attempts(task_id,raw_object_key,status)
                VALUES(%s,'raw/test','COLLECTED') RETURNING id""",
                (task_id,),
            ).fetchone()["id"]
            job_id = connection.execute(
                "INSERT INTO analysis_jobs(task_id,attempt_id,max_attempts) VALUES(%s,%s,%s) RETURNING id",
                (task_id, attempt_id, max_attempts),
            ).fetchone()["id"]
        return task_id, attempt_id, job_id

    @staticmethod
    def result():
        return make_result(analyze_folded(b"root;malloc 100"), "IMPORT_COLLAPSED", {})

    @staticmethod
    def artifacts(key="results/test"):
        return [
            {
                "name": "top.json",
                "kind": "RESULT",
                "object_key": key,
                "size": 2,
                "sha256": "a" * 64,
                "content_type": "application/json",
            }
        ]

    def test_skip_locked_and_exclusive_claim(self):
        _, _, first = self.seed()
        _, _, second = self.seed()
        with self.connect() as blocker:
            blocker.execute(
                "SELECT id FROM analysis_jobs WHERE id=%s FOR UPDATE", (first,)
            )
            job = self.b.claim()
            self.assertEqual(job.id, second)
        first_job = self.a.claim()
        self.assertEqual(first_job.id, first)
        self.assertIsNone(self.b.claim())

    def test_small_and_high_memory_workers_claim_only_their_queue(self):
        heavy_task, _, heavy_job = self.seed()
        _, _, small_job = self.seed()
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET kind='JAVA_HEAP' WHERE id=%s", (heavy_task,)
            )
            connection.execute(
                "UPDATE analysis_jobs SET pipeline='hprof_v1' WHERE id=%s", (heavy_job,)
            )
        small = Repository(self.dsn, "small", queue="small")
        high = Repository(self.dsn, "high", queue="high_memory")
        self.assertEqual(small.claim().id, small_job)
        self.assertIsNone(small.claim())
        self.assertEqual(high.claim().id, heavy_job)
        self.assertIsNone(high.claim())

    def test_rule_configuration_is_loaded_and_controls_evidence(self):
        from drop_analyzer.rules import suggestions

        with self.connect() as connection:
            connection.execute(
                "INSERT INTO diagnostic_rules(key,enabled,min_samples,threshold,version) VALUES('allocation',false,10,5,7)"
            )
        self.assertEqual(
            suggestions(
                analyze_folded(b"root;malloc 100")["top"], self.a.rule_settings()
            ),
            [],
        )
        with self.connect() as connection:
            connection.execute(
                "UPDATE diagnostic_rules SET enabled=true WHERE key='allocation'"
            )
        findings = suggestions(
            analyze_folded(b"root;malloc 100")["top"], self.a.rule_settings()
        )
        self.assertEqual(findings[0]["rule_version"], "7")
        self.assertEqual(findings[0]["evidence"]["threshold"], 0.05)

    def test_expired_lease_recovers_and_late_publish_is_rejected(self):
        self.seed()
        stale = self.a.claim()
        with self.connect() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                (stale.id,),
            )
        self.assertFalse(self.a.renew(stale))
        current = self.b.claim()
        self.assertEqual((current.id, current.attempt), (stale.id, 2))
        with self.assertRaises(LeaseLost):
            self.a.complete(stale, self.result(), self.artifacts())
        self.b.complete(current, self.result(), self.artifacts())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status,result FROM analysis_jobs WHERE id=%s", (current.id,)
            ).fetchone()
            self.assertEqual(row["status"], "success")
            self.assertEqual(row["result"]["total_samples"], 100)
            self.assertEqual(
                connection.execute("SELECT count(*) AS n FROM suggestions").fetchone()[
                    "n"
                ],
                1,
            )

    def test_progress_events_are_fenced_by_owner_attempt_expiry_and_cancellation(self):
        task_id, _, _ = self.seed()
        old = self.a.claim()
        self.a.progress(old, "parse")
        with self.connect() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                (old.id,),
            )
        current = self.b.claim()
        with self.assertRaises(LeaseLost):
            self.a.progress(old, "index")
        self.b.progress(current, "index")
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET collection_status='CANCELED',analysis_status='CANCELED' WHERE id=%s",
                (task_id,),
            )
        with self.assertRaises(LeaseLost):
            self.b.progress(current, "graph")
        with self.connect() as connection:
            events = connection.execute(
                "SELECT payload FROM task_events WHERE type='ANALYSIS_PROGRESS' ORDER BY sequence"
            ).fetchall()
        self.assertEqual(
            [event["payload"]["stage"] for event in events], ["parse", "index"]
        )
        self.assertEqual(
            [event["payload"]["analysis_attempt"] for event in events], [1, 2]
        )

    def test_progress_event_expiry_during_insert_rolls_back(self):
        self.seed()
        job = self.a.claim()
        with self.connect() as connection:
            connection.execute("""CREATE FUNCTION delay_progress_insert() RETURNS trigger LANGUAGE plpgsql AS
                $$ BEGIN IF NEW.type='ANALYSIS_PROGRESS' THEN PERFORM pg_sleep(0.5); END IF; RETURN NEW; END $$""")
            connection.execute(
                "CREATE TRIGGER slow_progress BEFORE INSERT ON task_events FOR EACH ROW EXECUTE FUNCTION delay_progress_insert()"
            )
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at=clock_timestamp()+interval '0.25 seconds'"
            )
        try:
            with self.assertRaises(LeaseLost):
                self.a.progress(job, "parse")
            with self.connect() as connection:
                count = connection.execute(
                    "SELECT count(*) AS n FROM task_events WHERE type='ANALYSIS_PROGRESS'"
                ).fetchone()["n"]
                self.assertEqual(count, 0)
        finally:
            with self.connect() as connection:
                connection.execute("DROP TRIGGER slow_progress ON task_events")
                connection.execute("DROP FUNCTION delay_progress_insert()")

    def test_same_worker_id_still_fenced_by_attempt(self):
        self.seed()
        old = self.a.claim()
        with self.connect() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                (old.id,),
            )
        current = self.a.claim()
        with self.assertRaises(LeaseLost):
            self.a.complete(old, self.result(), self.artifacts())
        self.a.complete(current, self.result(), self.artifacts())

    def test_cancellation_and_new_collection_attempt_fence_old_job(self):
        task_id, _, _ = self.seed()
        old = self.a.claim()
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET collection_status='CANCELED',analysis_status='CANCELED' WHERE id=%s",
                (task_id,),
            )
        self.assertFalse(self.a.renew(old))
        with self.assertRaises(LeaseLost):
            self.a.complete(old, self.result(), self.artifacts())
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET collection_status='COLLECTED',analysis_status='RUNNING' WHERE id=%s",
                (task_id,),
            )
            connection.execute(
                "INSERT INTO task_attempts(task_id,attempt,raw_object_key) VALUES(%s,2,'raw/new')",
                (task_id,),
            )
        self.assertFalse(self.a.renew(old))
        with self.assertRaises(LeaseLost):
            self.a.fail(old, AnalysisError("INVALID_INPUT", "Invalid input"))

    def test_retry_backoff_then_dead_letter(self):
        self.seed(max_attempts=2)
        first = self.a.claim()
        self.a.fail(
            first, AnalysisError("STORAGE_ERROR", "Temporary failure", retryable=True)
        )
        self.assertIsNone(self.a.claim())
        with self.connect() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET available_at=now()-interval '1 second'"
            )
        second = self.a.claim()
        self.a.fail(
            second, AnalysisError("STORAGE_ERROR", "Temporary failure", retryable=True)
        )
        self.assertIsNone(self.a.claim())
        with self.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT status FROM analysis_jobs").fetchone()[
                    "status"
                ],
                "failed",
            )
            event = connection.execute(
                "SELECT payload FROM task_events WHERE type='ANALYSIS_FAILED'"
            ).fetchone()["payload"]
            self.assertTrue(event["dead_letter"])

    def test_permanent_corruption_is_not_retried(self):
        self.seed()
        job = self.a.claim()
        self.a.fail(job, AnalysisError("ARTIFACT_INTEGRITY_ERROR", "Digest mismatch"))
        self.assertIsNone(self.a.claim())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status,attempt FROM analysis_jobs"
            ).fetchone()
            self.assertEqual((row["status"], row["attempt"]), ("failed", 1))

    def test_last_attempt_crash_is_dead_lettered(self):
        self.seed(max_attempts=1)
        job = self.a.claim()
        with self.connect() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at=now()-interval '1 second'"
            )
        self.assertIsNone(self.b.claim())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status,error_code FROM analysis_jobs WHERE id=%s", (job.id,)
            ).fetchone()
            self.assertEqual(
                (row["status"], row["error_code"]),
                ("failed", "ANALYSIS_RETRY_EXHAUSTED"),
            )

    def partial_fixture(self, *, max_attempts=3):
        task_id, attempt_id, _ = self.seed(max_attempts=max_attempts)
        raw = b"imported_root;imported_work 5"
        with self.connect() as connection:
            artifact = connection.execute(
                """INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type)
                VALUES(%s,%s,'RAW','imported.folded',%s,%s,%s,'text/plain') RETURNING *""",
                (
                    task_id,
                    attempt_id,
                    f"raw/imported-{task_id}",
                    len(raw),
                    hashlib.sha256(raw).hexdigest(),
                ),
            ).fetchone()
        job = self.a.claim()
        parsed = analyze_folded(raw)
        parsed["metadata"]["input_digest"] = artifact["sha256"]
        snapshot = build_partial(parsed, job, artifact)
        self.assertIsNotNone(snapshot)
        return job, artifact, snapshot, parsed

    def test_partial_checkpoint_and_terminal_failure_preserve_only_current_parsed_summary(
        self,
    ):
        job, artifact, snapshot, _ = self.partial_fixture(max_attempts=1)
        self.a.save_partial(job, snapshot, artifact)
        self.a.fail(
            job, AnalysisError("STORAGE_ERROR", "Upload unavailable", retryable=True)
        )
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status,result FROM analysis_jobs WHERE id=%s", (job.id,)
            ).fetchone()
            self.assertEqual((row["status"], row["result"]), ("failed", snapshot))
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) AS n FROM artifacts WHERE kind<>'RAW'"
                ).fetchone()["n"],
                0,
            )
            event = connection.execute(
                "SELECT payload FROM task_events WHERE type='ANALYSIS_PARTIAL_SAVED'"
            ).fetchone()["payload"]
            self.assertEqual(event["analysis_attempt"], job.attempt)
            self.assertNotIn("imported_work", json.dumps(event))
            self.assertNotIn("top_functions", event)

    def test_partial_is_cleared_when_retry_is_claimed_and_old_same_owner_run_cannot_write(
        self,
    ):
        job, artifact, snapshot, _ = self.partial_fixture()
        self.a.save_partial(job, snapshot, artifact)
        self.a.fail(
            job, AnalysisError("STORAGE_ERROR", "Upload unavailable", retryable=True)
        )
        with self.connect() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET available_at=now()-interval '1 second' WHERE id=%s",
                (job.id,),
            )
        current = self.a.claim()
        self.assertEqual(current.worker_id, job.worker_id)
        self.assertEqual(current.attempt, job.attempt + 1)
        with self.assertRaises(LeaseLost):
            self.a.save_partial(job, snapshot, artifact)
        with self.connect() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT result FROM analysis_jobs WHERE id=%s", (job.id,)
                ).fetchone()["result"]
            )

    def test_partial_expired_owner_cannot_publish_after_recovery(self):
        job, artifact, snapshot, _ = self.partial_fixture()
        with self.assertRaises(LeaseLost):
            self.b.save_partial(
                replace(job, worker_id="not-the-owner"), snapshot, artifact
            )
        self.a.save_partial(job, snapshot, artifact)
        with self.connect() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                (job.id,),
            )
        with self.assertRaises(LeaseLost):
            self.a.save_partial(job, snapshot, artifact)
        current = self.b.claim()
        self.assertEqual(current.attempt, job.attempt + 1)
        with self.assertRaises(LeaseLost):
            self.a.save_partial(job, snapshot, artifact)
        with self.connect() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT result FROM analysis_jobs WHERE id=%s", (job.id,)
                ).fetchone()["result"]
            )

    def test_partial_cancellation_fences_checkpoint_writes(self):
        job, artifact, snapshot, _ = self.partial_fixture()
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET analysis_status='FAILED' WHERE id=%s", (job.task_id,)
            )
        with self.assertRaises(LeaseLost):
            self.a.save_partial(job, snapshot, artifact)

    def test_partial_new_collection_attempt_fences_checkpoint_writes(self):
        job, artifact, snapshot, _ = self.partial_fixture()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO task_attempts(task_id,attempt,raw_object_key,status) VALUES(%s,2,'raw/imported-next-attempt','COLLECTED')",
                (job.task_id,),
            )
        with self.assertRaises(LeaseLost):
            self.a.save_partial(job, snapshot, artifact)

    def test_partial_rejects_changed_raw_digest_and_snapshot_identity_without_publishing(
        self,
    ):
        job, artifact, snapshot, _ = self.partial_fixture()
        for field, value in (
            ("task_id", "999"),
            ("analysis_run", job.attempt + 1),
            ("input_digest", "f" * 64),
            ("sections", {}),
        ):
            with self.subTest(field=field), self.assertRaises(AnalysisError):
                self.a.save_partial(job, {**snapshot, field: value}, artifact)
        with self.connect() as connection:
            connection.execute(
                "UPDATE artifacts SET sha256=%s WHERE id=%s", ("e" * 64, artifact["id"])
            )
        with self.assertRaises(LeaseLost):
            self.a.save_partial(job, snapshot, artifact)
        with self.connect() as connection:
            connection.execute(
                "UPDATE artifacts SET sha256=%s WHERE id=%s",
                (artifact["sha256"], artifact["id"]),
            )
            connection.execute(
                """INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type)
                VALUES(%s,%s,'RAW','new-imported.folded','raw/new-imported-current',1,%s,'text/plain')""",
                (job.task_id, job.attempt_id, artifact["sha256"]),
            )
        with self.assertRaises(LeaseLost):
            self.a.save_partial(job, snapshot, artifact)
        with self.connect() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT result FROM analysis_jobs WHERE id=%s", (job.id,)
                ).fetchone()["result"]
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) AS n FROM task_events WHERE type='ANALYSIS_PARTIAL_SAVED'"
                ).fetchone()["n"],
                0,
            )

    def test_partial_lease_expiry_during_update_rolls_back_checkpoint_and_event(self):
        job, artifact, snapshot, _ = self.partial_fixture()
        with self.connect() as connection:
            connection.execute("""CREATE FUNCTION delay_partial_update() RETURNS trigger LANGUAGE plpgsql AS
                $$ BEGIN PERFORM pg_sleep(0.5); RETURN NEW; END $$""")
            connection.execute(
                "CREATE TRIGGER slow_partial BEFORE UPDATE OF result ON analysis_jobs FOR EACH ROW EXECUTE FUNCTION delay_partial_update()"
            )
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at=clock_timestamp()+interval '0.25 seconds' WHERE id=%s",
                (job.id,),
            )
        try:
            with self.assertRaises(LeaseLost):
                self.a.save_partial(job, snapshot, artifact)
            with self.connect() as connection:
                self.assertIsNone(
                    connection.execute(
                        "SELECT result FROM analysis_jobs WHERE id=%s", (job.id,)
                    ).fetchone()["result"]
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) AS n FROM task_events WHERE type='ANALYSIS_PARTIAL_SAVED'"
                    ).fetchone()["n"],
                    0,
                )
        finally:
            with self.connect() as connection:
                connection.execute("DROP TRIGGER slow_partial ON analysis_jobs")
                connection.execute("DROP FUNCTION delay_partial_update()")

    def test_success_replaces_partial_and_publishes_complete_result_atomically(self):
        job, artifact, snapshot, parsed = self.partial_fixture()
        self.a.save_partial(job, snapshot, artifact)
        result = make_result(parsed, job.kind, job.params)
        self.a.complete(job, result, self.artifacts())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status,result FROM analysis_jobs WHERE id=%s", (job.id,)
            ).fetchone()
            self.assertEqual((row["status"], row["result"]), ("success", result))
            self.assertNotIn("completeness", row["result"])
        with self.assertRaises(LeaseLost):
            self.a.save_partial(job, snapshot, artifact)

    def test_final_crash_dead_letter_retains_verified_partial_without_new_stale_write(
        self,
    ):
        job, artifact, snapshot, _ = self.partial_fixture(max_attempts=1)
        self.a.save_partial(job, snapshot, artifact)
        with self.connect() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                (job.id,),
            )
        self.assertIsNone(self.b.claim())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status,result FROM analysis_jobs WHERE id=%s", (job.id,)
            ).fetchone()
            self.assertEqual((row["status"], row["result"]), ("failed", snapshot))
        with self.assertRaises(LeaseLost):
            self.a.save_partial(job, snapshot, artifact)

    def test_result_metadata_and_status_commit_atomically(self):
        self.seed()
        job = self.a.claim()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.a.complete(job, self.result(), self.artifacts() + self.artifacts())
        with self.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT status FROM analysis_jobs").fetchone()[
                    "status"
                ],
                "running",
            )
            self.assertEqual(
                connection.execute("SELECT count(*) AS n FROM artifacts").fetchone()[
                    "n"
                ],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) AS n FROM suggestions").fetchone()[
                    "n"
                ],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT analysis_status FROM tasks").fetchone()[
                    "analysis_status"
                ],
                "RUNNING",
            )

    def test_lease_expiry_during_publish_rolls_back_artifacts(self):
        self.seed()
        job = self.a.claim()
        with self.connect() as connection:
            connection.execute("""CREATE FUNCTION delay_artifact_insert() RETURNS trigger LANGUAGE plpgsql AS
                $$ BEGIN PERFORM pg_sleep(0.5); RETURN NEW; END $$""")
            connection.execute(
                "CREATE TRIGGER slow_artifact BEFORE INSERT ON artifacts FOR EACH ROW EXECUTE FUNCTION delay_artifact_insert()"
            )
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at=clock_timestamp()+interval '0.25 seconds'"
            )
        try:
            with self.assertRaises(LeaseLost):
                self.a.complete(job, self.result(), self.artifacts())
            with self.connect() as connection:
                self.assertEqual(
                    connection.execute("SELECT status FROM analysis_jobs").fetchone()[
                        "status"
                    ],
                    "running",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) AS n FROM artifacts"
                    ).fetchone()["n"],
                    0,
                )
        finally:
            with self.connect() as connection:
                connection.execute("DROP TRIGGER slow_artifact ON artifacts")
                connection.execute("DROP FUNCTION delay_artifact_insert()")

    @unittest.skipUnless(
        os.getenv("TEST_S3_ENDPOINT"),
        "Set TEST_S3_ENDPOINT for actual MinIO pipeline round trip",
    )
    def test_minio_raw_to_result_round_trip(self):
        task_id, attempt_id, _ = self.seed()
        with tempfile.TemporaryDirectory() as root:
            config = Config(
                database_url=self.dsn,
                s3_endpoint=os.environ["TEST_S3_ENDPOINT"],
                s3_access_key=os.environ["TEST_S3_ACCESS_KEY"],
                s3_secret_key=os.environ["TEST_S3_SECRET_KEY"],
                s3_bucket=self.schema.replace("_", "-"),
                work_dir=root,
            )
            storage = ObjectStore(config)
            for attempt in range(20):
                try:
                    storage.client.make_bucket(config.s3_bucket)
                    break
                except Exception:
                    if attempt == 19:
                        raise
                    time.sleep(0.5)
            try:
                raw = b"process;malloc 80\nprocess;work 20\n"
                # 导入的合成编码 RAW 经过真实 S3 传输，验证在 pipeline 解压前
                # 仍保留存储的 gzip 字节。
                encoded = gzip.compress(raw, mtime=0)
                key = "raw/imported-profile.folded.gz"
                digest = hashlib.sha256(encoded).hexdigest()
                storage.client.put_object(
                    config.s3_bucket,
                    key,
                    io.BytesIO(encoded),
                    len(encoded),
                    content_type="text/x-collapsed+gzip",
                    metadata={"sha256": digest, "Content-Encoding": "gzip"},
                )
                with self.connect() as connection:
                    connection.execute(
                        """INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type)
                        VALUES(%s,%s,'RAW','profile.folded.gz',%s,%s,%s,'text/x-collapsed+gzip')""",
                        (
                            task_id,
                            attempt_id,
                            key,
                            len(encoded),
                            digest,
                        ),
                    )
                job = self.a.claim()
                process_job(job, self.a, storage, config)
                with self.connect() as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT status FROM analysis_jobs"
                        ).fetchone()["status"],
                        "success",
                    )
                    artifacts = connection.execute(
                        "SELECT * FROM artifacts WHERE kind<>'RAW'"
                    ).fetchall()
                self.assertEqual(len(artifacts), 4)
                downloaded = {}
                for artifact in artifacts:
                    response = storage.client.get_object(
                        config.s3_bucket, artifact["object_key"]
                    )
                    try:
                        body = response.read()
                    finally:
                        response.close()
                        response.release_conn()
                    self.assertEqual(
                        hashlib.sha256(body).hexdigest(), artifact["sha256"]
                    )
                    downloaded[artifact["name"]] = body
                self.assertEqual(
                    json.loads(gzip.decompress(downloaded["flamegraph.json.gz"]))[
                        "value"
                    ],
                    100,
                )
                self.assertEqual(
                    json.loads(downloaded["report.json"])["summary"]["source"], "import"
                )
                # 失败分支仍从真实 S3 下载导入 RAW，并通过真实 PostgreSQL 提交检查点；
                # 只对后续结果写入显式注入故障。
                failed_task, failed_attempt, _ = self.seed(max_attempts=1)
                failed_key = "raw/imported-partial-profile.folded.gz"
                storage.client.put_object(
                    config.s3_bucket,
                    failed_key,
                    io.BytesIO(encoded),
                    len(encoded),
                    content_type="text/x-collapsed+gzip",
                    metadata={"sha256": digest, "Content-Encoding": "gzip"},
                )
                with self.connect() as connection:
                    raw_id = connection.execute(
                        """INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type)
                        VALUES(%s,%s,'RAW','imported-partial.folded.gz',%s,%s,%s,'text/x-collapsed+gzip') RETURNING id""",
                        (failed_task, failed_attempt, failed_key, len(encoded), digest),
                    ).fetchone()["id"]
                failed_job = self.a.claim()
                with (
                    patch.object(
                        storage,
                        "upload",
                        side_effect=AnalysisError(
                            "STORAGE_ERROR",
                            "Injected result write failure",
                            retryable=True,
                        ),
                    ),
                    self.assertRaises(AnalysisError) as failure,
                ):
                    process_job(failed_job, self.a, storage, config)
                self.a.fail(failed_job, failure.exception)
                with self.connect() as connection:
                    failed = connection.execute(
                        "SELECT status,result FROM analysis_jobs WHERE id=%s",
                        (failed_job.id,),
                    ).fetchone()
                    self.assertEqual(failed["status"], "failed")
                    partial = failed["result"]
                    self.assertEqual(
                        (
                            partial["completeness"],
                            partial["input_artifact_id"],
                            partial["input_digest"],
                        ),
                        ("partial", str(raw_id), digest),
                    )
                    self.assertEqual(partial["summary"]["total_samples"], 100)
                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) AS n FROM artifacts WHERE task_id=%s AND kind<>'RAW'",
                            (failed_task,),
                        ).fetchone()["n"],
                        0,
                    )
            finally:
                for item in storage.client.list_objects(
                    config.s3_bucket, recursive=True
                ):
                    storage.client.remove_object(config.s3_bucket, item.object_name)
                storage.client.remove_bucket(config.s3_bucket)

    @unittest.skipUnless(
        os.getenv("TEST_S3_ENDPOINT")
        and (
            Path(__file__).parent / "fixtures" / "collected" / "manifest.json"
        ).exists(),
        "Actual tool fixtures and isolated MinIO are required",
    )
    def test_all_native_kinds_through_actual_storage_and_transaction(self):
        from drop_analyzer.registry import KIND_PIPELINES

        fixtures = Path(__file__).parent / "fixtures" / "collected"
        names = {
            "PERF_CPU": "perf.folded",
            "PROC_CPU": "proc.folded",
            "JAVA_CPU": "java.folded",
            "PYTHON_CPU": "python.folded",
            "GO_CPU": "go-cpu.pprof",
            "GO_HEAP": "go-heap.pprof",
            "JAVA_HEAP": "java-heap.hprof",
            "PYTHON_MEMORY": "python-memory.bin",
            "IO_TRACE": "io.jsonl",
            "MEMORY_TRACE": "memory.jsonl",
            "GPERF_CPU": "gperf-cpu.profile",
            "GPERF_HEAP": "gperf-heap.profile",
            "JEPROF_HEAP": "jemalloc-heap.profile",
            "BOLT_PROFILE": "bolt.fdata",
            "SCRIPT": "script.json",
            "RESOURCE_SNAPSHOT": "resources.json",
        }
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                self.dsn,
                os.environ["TEST_S3_ENDPOINT"],
                os.environ["TEST_S3_ACCESS_KEY"],
                os.environ["TEST_S3_SECRET_KEY"],
                s3_bucket=self.schema.replace("_", "-"),
                work_dir=directory,
            )
            storage = ObjectStore(config)
            storage.client.make_bucket(config.s3_bucket)
            try:
                for kind, pipeline in KIND_PIPELINES.items():
                    with self.subTest(kind=kind):
                        path = (
                            fixtures / names[kind]
                            if kind != "IMPORT_COLLAPSED"
                            else fixtures.parent / "golden.folded"
                        )
                        self.assertTrue(
                            path.exists(), f"Actual fixture missing: {kind}"
                        )
                        task, attempt, job_id = self.seed()
                        raw = path.read_bytes()
                        uploaded = storage.upload(
                            f"raw/{task}/{path.name}",
                            raw,
                            "text/plain"
                            if pipeline.formats[0] == "folded"
                            else "application/octet-stream",
                        )
                        with self.connect() as connection:
                            connection.execute(
                                "UPDATE tasks SET kind=%s WHERE id=%s", (kind, task)
                            )
                            connection.execute(
                                "UPDATE analysis_jobs SET pipeline=%s WHERE id=%s",
                                (pipeline.pipeline, job_id),
                            )
                            connection.execute(
                                """INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type,format,metadata)
                                VALUES(%s,%s,'RAW',%s,%s,%s,%s,%s,%s,'{"provenance":"imported isolated real-tool fixture"}')""",
                                (
                                    task,
                                    attempt,
                                    path.name,
                                    uploaded["object_key"],
                                    len(raw),
                                    uploaded["sha256"],
                                    uploaded["content_type"],
                                    pipeline.formats[0],
                                ),
                            )
                            binary_name = {
                                "GPERF_CPU": "native-target.elf",
                                "GPERF_HEAP": "native-target.elf",
                                "JEPROF_HEAP": "jemalloc-target.elf",
                                "BOLT_PROFILE": "bolt-target.elf",
                            }.get(kind)
                            if binary_name:
                                binary = (fixtures / binary_name).read_bytes()
                                stored = storage.upload(
                                    f"raw/{task}/target.elf",
                                    binary,
                                    "application/x-elf",
                                )
                                connection.execute(
                                    """INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type,format)
                                    VALUES(%s,%s,'BINARY','target.elf',%s,%s,%s,'application/x-elf','elf')""",
                                    (
                                        task,
                                        attempt,
                                        stored["object_key"],
                                        len(binary),
                                        stored["sha256"],
                                    ),
                                )
                        job = self.a.claim()
                        self.assertEqual(job.task_id, task)
                        result = process_job(job, self.a, storage, config)
                        self.assertEqual(result["kind"], kind)
                        self.assertEqual(
                            result["metadata"]["input_digest"], uploaded["sha256"]
                        )
                        with self.connect() as connection:
                            state = connection.execute(
                                "SELECT status,result FROM analysis_jobs WHERE id=%s",
                                (job_id,),
                            ).fetchone()
                            self.assertEqual(state["status"], "success")
                            self.assertEqual(state["result"], result)
                            if kind == "JAVA_HEAP":
                                progress = connection.execute(
                                    "SELECT payload FROM task_events WHERE task_id=%s AND type='ANALYSIS_PROGRESS' ORDER BY sequence",
                                    (task,),
                                ).fetchall()
                                self.assertEqual(
                                    [event["payload"]["stage"] for event in progress],
                                    ["parse", "index", "graph", "dominator", "emit"],
                                )
                            outputs = connection.execute(
                                "SELECT * FROM artifacts WHERE task_id=%s AND kind IN ('RESULT','MANIFEST')",
                                (task,),
                            ).fetchall()
                        self.assertEqual(
                            len(outputs), 5 if kind == "BOLT_PROFILE" else 4
                        )
                        for artifact in outputs:
                            response = storage.client.get_object(
                                config.s3_bucket, artifact["object_key"]
                            )
                            try:
                                body = response.read()
                            finally:
                                response.close()
                                response.release_conn()
                            self.assertEqual(
                                hashlib.sha256(body).hexdigest(), artifact["sha256"]
                            )
                            if artifact["name"] == "manifest.json":
                                manifest = json.loads(body)
                                self.assertEqual(
                                    manifest["source"], result["summary"]["source"]
                                )
                                self.assertEqual(
                                    manifest["input_format"], pipeline.formats[0]
                                )
            finally:
                for item in storage.client.list_objects(
                    config.s3_bucket, recursive=True
                ):
                    storage.client.remove_object(config.s3_bucket, item.object_name)
                storage.client.remove_bucket(config.s3_bucket)

    @unittest.skipUnless(
        os.getenv("TEST_S3_ENDPOINT"),
        "Set TEST_S3_ENDPOINT for actual worker crash recovery",
    )
    def test_worker_sigkill_and_automatic_lease_recovery(self):
        task_id, attempt_id, _ = self.seed()
        with tempfile.TemporaryDirectory() as root:
            config = Config(
                database_url=self.dsn,
                s3_endpoint=os.environ["TEST_S3_ENDPOINT"],
                s3_access_key=os.environ["TEST_S3_ACCESS_KEY"],
                s3_secret_key=os.environ["TEST_S3_SECRET_KEY"],
                s3_bucket=self.schema.replace("_", "-"),
                work_dir=root,
            )
            storage = ObjectStore(config)
            storage.client.make_bucket(config.s3_bucket)
            workers = []
            try:
                raw = b"process;malloc 100\n"
                stored = storage.upload("raw/crash.folded", raw, "text/plain")
                with self.connect() as connection:
                    connection.execute(
                        """INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type)
                        VALUES(%s,%s,'RAW','profile.folded',%s,%s,%s,'text/plain')""",
                        (
                            task_id,
                            attempt_id,
                            stored["object_key"],
                            stored["size"],
                            stored["sha256"],
                        ),
                    )

                def start_worker(name, *, stall_download=False):
                    overrides = {
                        "DATABASE_URL": self.dsn,
                        "S3_ENDPOINT": config.s3_endpoint,
                        "S3_ACCESS_KEY": config.s3_access_key,
                        "S3_SECRET_KEY": config.s3_secret_key,
                        "S3_BUCKET": config.s3_bucket,
                        "WORKER_ID": name,
                        "LEASE_SECONDS": "15",
                        "RENEW_SECONDS": "1",
                        "POLL_SECONDS": "0.1",
                        "WORK_DIR": str(Path(root) / name),
                        "HEALTH_FILE": str(Path(root) / f"{name}.health.json"),
                    }
                    environment = dict(os.environ)
                    for key in overrides:
                        environment.pop(f"{key}_FILE", None)
                    environment.update(overrides)
                    script = "from drop_analyzer.config import Config; from drop_analyzer.worker import run; "
                    if stall_download:
                        # 仅供测试的故障注入：子进程完成真实数据库领取后暂挂，
                        # 同时保持租约续期，用于区分处理中断与租约失效。
                        script += "from drop_analyzer.storage import ObjectStore; import time; ObjectStore.download=lambda *a,**k: time.sleep(60); "
                    script += "run(Config.from_env())"
                    process = subprocess.Popen(
                        [sys.executable, "-c", script],
                        env=environment,
                        cwd=Path(__file__).resolve().parents[1],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    workers.append(process)
                    return process

                def wait_for_status(status, timeout):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        with self.connect() as connection:
                            row = connection.execute(
                                "SELECT status,attempt FROM analysis_jobs WHERE task_id=%s",
                                (task_id,),
                            ).fetchone()
                        if row["status"] == status:
                            return row
                        if workers[-1].poll() is not None:
                            self.fail(
                                f"Worker exited before {status}: code={workers[-1].returncode}, database_status={row['status']}"
                            )
                        time.sleep(0.1)
                    self.fail(f"Worker did not reach {status} within {timeout} seconds")

                first = start_worker("crashed-worker", stall_download=True)
                # 共享宿主 CPU 配额下，冷启动解释器与 SDK 导入可能超过五秒；
                # 启动仍需有界，但不能与测试主动制造的租约恢复期限混为一谈。
                wait_for_status("running", 15)
                first.kill()
                first.wait(timeout=5)
                with self.connect() as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT status FROM analysis_jobs WHERE task_id=%s",
                            (task_id,),
                        ).fetchone()["status"],
                        "running",
                    )
                start_worker("recovery-worker")
                recovered = wait_for_status("success", 40)
                self.assertEqual(recovered["attempt"], 2)
                with self.connect() as connection:
                    event = connection.execute(
                        """SELECT payload FROM task_events WHERE task_id=%s
                        AND type='ANALYSIS_STARTED' ORDER BY sequence DESC LIMIT 1""",
                        (task_id,),
                    ).fetchone()["payload"]
                    self.assertTrue(event["recovered_expired_lease"])
                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) AS n FROM artifacts WHERE task_id=%s AND kind<>'RAW'",
                            (task_id,),
                        ).fetchone()["n"],
                        4,
                    )
            finally:
                for process in workers:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                for item in storage.client.list_objects(
                    config.s3_bucket, recursive=True
                ):
                    storage.client.remove_object(config.s3_bucket, item.object_name)
                storage.client.remove_bucket(config.s3_bucket)


if __name__ == "__main__":
    unittest.main()
