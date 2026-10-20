"""PostgreSQL job ownership and atomic publication boundaries.

Every write that can publish results is fenced by owner, attempt, expiry and
the task's current collection attempt. Uploads use attempt-specific object keys
so a stale worker cannot overwrite objects owned by its successor.
"""

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import __version__, telemetry
from .errors import AnalysisError, LeaseLost
from .partial import validate_partial


@dataclass(frozen=True)
class Job:
    id: int
    task_id: int
    attempt_id: int
    attempt: int
    max_attempts: int
    worker_id: str
    kind: str
    params: dict[str, Any]
    collection_attempt: int
    pipeline: str


class Repository:
    def __init__(
        self, dsn: str, worker_id: str, lease_seconds: int = 60, queue: str = "all"
    ):
        self.dsn = dsn
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.queue = queue

    def _connect(self):
        return psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row)

    def ready(self) -> bool:
        with self._connect() as connection:
            connection.execute("SELECT 1 FROM analysis_jobs LIMIT 1")
        return True

    def queue_stats(self):
        with self._connect() as connection:
            return connection.execute(
                "SELECT status,count(*) AS count FROM analysis_jobs GROUP BY status"
            ).fetchall()

    @staticmethod
    def _event(
        connection, task_id: int, attempt_id: int, event_type: str, payload: dict
    ):
        connection.execute(
            "INSERT INTO task_events(task_id,attempt_id,type,source,payload) VALUES(%s,%s,%s,'analyzer',%s)",
            (task_id, attempt_id, event_type, Jsonb(payload)),
        )

    def claim(self) -> Job | None:
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout = '10s'")
            # A crashed final attempt becomes a clear dead letter, not an
            # immortal running job. SKIP LOCKED permits concurrent workers.
            expired = connection.execute("""
                SELECT j.id,j.task_id,j.attempt_id FROM analysis_jobs j
                JOIN tasks t ON t.id=j.task_id
                JOIN task_attempts a ON a.id=j.attempt_id
                WHERE j.status='running' AND j.lease_expires_at < now()
                  AND j.attempt >= j.max_attempts AND t.collection_status='COLLECTED'
                  AND t.analysis_status IN ('PENDING','RUNNING','RETRY')
                  AND NOT EXISTS (SELECT 1 FROM task_attempts newer WHERE newer.task_id=t.id AND newer.attempt>a.attempt)
                ORDER BY j.created_at FOR UPDATE OF j,t SKIP LOCKED LIMIT 1
                """).fetchone()
            if expired:
                connection.execute(
                    """UPDATE analysis_jobs SET status='failed',lease_owner=NULL,lease_expires_at=NULL,
                    error_code='ANALYSIS_RETRY_EXHAUSTED',error_message='Final analysis attempt lease expired',updated_at=now() WHERE id=%s""",
                    (expired["id"],),
                )
                connection.execute(
                    """UPDATE tasks SET analysis_status='FAILED',error_code='ANALYSIS_RETRY_EXHAUSTED',
                    error_message='Final analysis attempt lease expired',updated_at=now(),version=version+1 WHERE id=%s""",
                    (expired["task_id"],),
                )
                self._event(
                    connection,
                    expired["task_id"],
                    expired["attempt_id"],
                    "ANALYSIS_FAILED",
                    {
                        "analysis_job_id": str(expired["id"]),
                        "error_code": "ANALYSIS_RETRY_EXHAUSTED",
                        "dead_letter": True,
                    },
                )

            row = connection.execute(
                """
                SELECT j.*,t.kind,t.params,a.attempt AS collection_attempt,
                    EXTRACT(EPOCH FROM(clock_timestamp()-j.available_at)) AS claim_latency_seconds
                FROM analysis_jobs j JOIN tasks t ON t.id=j.task_id JOIN task_attempts a ON a.id=j.attempt_id
                WHERE ((j.status IN ('pending','retry') AND j.available_at<=now())
                    OR (j.status='running' AND j.lease_expires_at < now()))
                  AND j.attempt<j.max_attempts AND t.collection_status='COLLECTED'
                  AND t.analysis_status IN ('PENDING','RUNNING','RETRY')
                  AND (%s='all' OR (%s='high_memory') = (t.kind IN ('GO_HEAP','JAVA_HEAP','PYTHON_MEMORY','GPERF_HEAP','JEPROF_HEAP')))
                  AND NOT EXISTS (SELECT 1 FROM task_attempts newer WHERE newer.task_id=t.id AND newer.attempt>a.attempt)
                ORDER BY j.priority DESC,j.created_at,j.id FOR UPDATE OF j,t SKIP LOCKED LIMIT 1
                """,
                (self.queue, self.queue),
            ).fetchone()
            if not row:
                return None
            was_expired = row["status"] == "running"
            telemetry.count("jobs_claimed_total", kind=row["kind"])
            telemetry.observe(
                "claim_latency_seconds",
                max(0, float(row["claim_latency_seconds"])),
                kind=row["kind"],
            )
            if was_expired:
                telemetry.count("expired_lease_recoveries_total", kind=row["kind"])
            connection.execute(
                """UPDATE analysis_jobs SET status='running',attempt=attempt+1,lease_owner=%s,
                lease_expires_at=now()+%s*interval '1 second',analyzer_version=%s,
                result=NULL,error_code=NULL,error_message=NULL,updated_at=now() WHERE id=%s""",
                (self.worker_id, self.lease_seconds, __version__, row["id"]),
            )
            connection.execute(
                """UPDATE tasks SET analysis_status='RUNNING',error_code=NULL,error_message=NULL,
                updated_at=now(),version=version+1 WHERE id=%s""",
                (row["task_id"],),
            )
            self._event(
                connection,
                row["task_id"],
                row["attempt_id"],
                "ANALYSIS_STARTED",
                {
                    "analysis_job_id": str(row["id"]),
                    "analysis_attempt": row["attempt"] + 1,
                    "analyzer_version": __version__,
                    "recovered_expired_lease": was_expired,
                },
            )
            return Job(
                id=row["id"],
                task_id=row["task_id"],
                attempt_id=row["attempt_id"],
                attempt=row["attempt"] + 1,
                max_attempts=row["max_attempts"],
                worker_id=self.worker_id,
                kind=row["kind"],
                params=row["params"],
                collection_attempt=row["collection_attempt"],
                pipeline=row["pipeline"],
            )

    def renew(self, job: Job) -> bool:
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout = '10s'")
            cursor = connection.execute(
                """UPDATE analysis_jobs j SET lease_expires_at=clock_timestamp()+%s*interval '1 second',updated_at=now()
                WHERE j.id=%s AND j.status='running' AND j.lease_owner=%s AND j.attempt=%s AND j.lease_expires_at>clock_timestamp()
                AND EXISTS (SELECT 1 FROM tasks t JOIN task_attempts a ON a.id=j.attempt_id
                    WHERE t.id=j.task_id AND t.collection_status='COLLECTED' AND t.analysis_status='RUNNING'
                    AND NOT EXISTS (SELECT 1 FROM task_attempts newer WHERE newer.task_id=t.id AND newer.attempt>a.attempt))""",
                (self.lease_seconds, job.id, job.worker_id, job.attempt),
            )
            return cursor.rowcount == 1

    def input_artifact(self, job: Job) -> dict:
        with self._connect() as connection:
            artifact = connection.execute(
                """SELECT artifact.*,to_jsonb(attempt)->'runner_metadata' AS runner_metadata
                FROM artifacts artifact JOIN task_attempts attempt ON attempt.id=artifact.attempt_id
                WHERE artifact.task_id=%s AND artifact.attempt_id=%s
                AND artifact.kind='RAW' AND artifact.status='ready' ORDER BY artifact.id DESC LIMIT 1""",
                (job.task_id, job.attempt_id),
            ).fetchone()
        if not artifact:
            raise AnalysisError(
                "RAW_ARTIFACT_MISSING",
                "No ready raw artifact is attached to this collection attempt",
            )
        return artifact

    def auxiliary_artifacts(self, job: Job) -> list[dict]:
        with self._connect() as connection:
            return connection.execute(
                """SELECT * FROM artifacts WHERE task_id=%s AND attempt_id=%s
                AND kind='BINARY' AND status='ready' ORDER BY id DESC LIMIT 1""",
                (job.task_id, job.attempt_id),
            ).fetchall()

    def rule_settings(self):
        with self._connect() as connection:
            if (
                connection.execute(
                    "SELECT to_regclass('diagnostic_rules') AS name"
                ).fetchone()["name"]
                is None
            ):
                return []
            return connection.execute(
                "SELECT key,enabled,severity,min_samples,threshold,config,version FROM diagnostic_rules ORDER BY key LIMIT 100"
            ).fetchall()

    @staticmethod
    def _lock_owned(connection, job: Job):
        task = connection.execute(
            """SELECT t.id FROM tasks t
            WHERE t.id=%s AND t.collection_status='COLLECTED' AND t.analysis_status='RUNNING'
              AND (SELECT a.id FROM task_attempts a WHERE a.task_id=t.id ORDER BY a.attempt DESC LIMIT 1)=%s
            FOR UPDATE""",
            (job.task_id, job.attempt_id),
        ).fetchone()
        owned = (
            connection.execute(
                """SELECT id FROM analysis_jobs WHERE id=%s AND status='running'
            AND lease_owner=%s AND attempt=%s AND lease_expires_at>clock_timestamp() FOR UPDATE""",
                (job.id, job.worker_id, job.attempt),
            ).fetchone()
            if task
            else None
        )
        if not owned:
            raise LeaseLost()

    def progress(self, job: Job, stage: str):
        from .progress import HPROF_STAGES

        if stage not in HPROF_STAGES:
            raise AnalysisError("INVALID_PROGRESS", "Unregistered analysis stage")
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout = '5s'")
            self._lock_owned(connection, job)
            self._event(
                connection,
                job.task_id,
                job.attempt_id,
                "ANALYSIS_PROGRESS",
                {
                    "analysis_job_id": str(job.id),
                    "analysis_attempt": job.attempt,
                    "stage": stage,
                    "stage_index": HPROF_STAGES.index(stage) + 1,
                    "stage_count": len(HPROF_STAGES),
                },
            )
            # A slow insert/trigger must not publish after lease expiry either.
            owned = connection.execute(
                "SELECT 1 FROM analysis_jobs WHERE id=%s AND lease_expires_at>clock_timestamp()",
                (job.id,),
            ).fetchone()
            if not owned:
                raise LeaseLost()

    def save_partial(self, job: Job, result: dict, artifact: dict):
        # Reject caller-controlled JSON before it can cross the SQL boundary.
        if not validate_partial(result) or any(
            result[key] != expected
            for key, expected in (
                ("task_id", str(job.task_id)),
                ("attempt_id", str(job.attempt_id)),
                ("analysis_job_id", str(job.id)),
                ("analysis_run", job.attempt),
                ("kind", job.kind),
                ("input_artifact_id", str(artifact.get("id"))),
                ("input_digest", artifact.get("sha256")),
            )
        ):
            raise AnalysisError(
                "INVALID_PARTIAL_RESULT", "Invalid partial analysis checkpoint"
            )
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout = '5s'")
            self._lock_owned(connection, job)
            current = connection.execute(
                """SELECT id,sha256 FROM artifacts WHERE task_id=%s AND attempt_id=%s
                AND kind='RAW' AND status='ready' ORDER BY id DESC LIMIT 1 FOR SHARE""",
                (job.task_id, job.attempt_id),
            ).fetchone()
            if (
                not current
                or str(current["id"]) != result["input_artifact_id"]
                or current["sha256"] != result["input_digest"]
            ):
                raise LeaseLost()
            saved = connection.execute(
                """UPDATE analysis_jobs SET result=%s,updated_at=now() WHERE id=%s AND status='running'
                AND lease_owner=%s AND attempt=%s AND lease_expires_at>clock_timestamp()""",
                (Jsonb(result), job.id, job.worker_id, job.attempt),
            )
            if saved.rowcount != 1:
                raise LeaseLost()
            self._event(
                connection,
                job.task_id,
                job.attempt_id,
                "ANALYSIS_PARTIAL_SAVED",
                {
                    "analysis_job_id": str(job.id),
                    "analysis_attempt": job.attempt,
                    "input_digest": result["input_digest"],
                    "total_samples": result["summary"]["total_samples"],
                },
            )
            # UPDATE predicates run before BEFORE triggers. Check again after
            # both writes so an expired slow transaction rolls back entirely.
            if not connection.execute(
                """SELECT 1 FROM analysis_jobs WHERE id=%s AND status='running' AND lease_owner=%s
                AND attempt=%s AND lease_expires_at>clock_timestamp()""",
                (job.id, job.worker_id, job.attempt),
            ).fetchone():
                raise LeaseLost()

    def complete(self, job: Job, result: dict, artifacts: list[dict]):
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout = '15s'")
            self._lock_owned(connection, job)
            artifact_columns = {
                row["column_name"]
                for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='artifacts'"
                )
            }
            suggestion_columns = {
                row["column_name"]
                for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='suggestions'"
                )
            }
            for artifact in artifacts:
                values = (
                    job.task_id,
                    job.attempt_id,
                    artifact["kind"],
                    artifact["name"],
                    artifact["object_key"],
                    artifact["size"],
                    artifact["sha256"],
                    artifact["content_type"],
                )
                if "format" in artifact_columns:
                    row = connection.execute(
                        """INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type,format,metadata)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (
                            *values,
                            artifact.get("format", "json"),
                            Jsonb(artifact.get("metadata", {})),
                        ),
                    ).fetchone()
                else:
                    row = connection.execute(
                        """INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        values,
                    ).fetchone()
                self._event(
                    connection,
                    job.task_id,
                    job.attempt_id,
                    "RESULT_ARTIFACT_UPLOADED",
                    {
                        "artifact_id": str(row["id"]),
                        "name": artifact["name"],
                        "size": artifact["size"],
                    },
                )
            for suggestion in result["suggestions"]:
                values = (
                    job.task_id,
                    suggestion["engine"],
                    suggestion["version"],
                    suggestion["severity"],
                    suggestion["title"],
                    suggestion["content"],
                    Jsonb(suggestion["evidence"]),
                    suggestion["confidence"],
                )
                if "attempt_id" in suggestion_columns:
                    connection.execute(
                        """INSERT INTO suggestions(task_id,engine,version,severity,title,content,evidence,confidence,attempt_id,input_digest)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            *values,
                            job.attempt_id,
                            result.get("metadata", {}).get("input_digest", ""),
                        ),
                    )
                else:
                    connection.execute(
                        """INSERT INTO suggestions(task_id,engine,version,severity,title,content,evidence,confidence)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                        values,
                    )
            committed = connection.execute(
                """UPDATE analysis_jobs SET status='success',result=%s,lease_owner=NULL,lease_expires_at=NULL,
                error_code=NULL,error_message=NULL,updated_at=now()
                WHERE id=%s AND lease_owner=%s AND attempt=%s AND lease_expires_at>clock_timestamp()""",
                (Jsonb(result), job.id, job.worker_id, job.attempt),
            )
            if committed.rowcount != 1:
                raise LeaseLost()
            connection.execute(
                """UPDATE tasks SET analysis_status='SUCCESS',error_code=NULL,error_message=NULL,
                updated_at=now(),version=version+1 WHERE id=%s""",
                (job.task_id,),
            )
            self._event(
                connection,
                job.task_id,
                job.attempt_id,
                "ANALYSIS_SUCCEEDED",
                {
                    "analysis_job_id": str(job.id),
                    "total_samples": result["total_samples"],
                    "analyzer_version": __version__,
                },
            )

    def fail(self, job: Job, error: AnalysisError):
        retry = error.retryable and job.attempt < job.max_attempts
        delay_seconds = min(60, 2**job.attempt)
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout = '10s'")
            self._lock_owned(connection, job)
            failed = connection.execute(
                """UPDATE analysis_jobs SET status=%s,lease_owner=NULL,lease_expires_at=NULL,
                error_code=%s,error_message=%s,available_at=now()+%s*interval '1 second',updated_at=now()
                WHERE id=%s AND lease_owner=%s AND attempt=%s AND lease_expires_at>clock_timestamp()""",
                (
                    "retry" if retry else "failed",
                    error.code,
                    str(error)[:512],
                    delay_seconds,
                    job.id,
                    job.worker_id,
                    job.attempt,
                ),
            )
            if failed.rowcount != 1:
                raise LeaseLost()
            connection.execute(
                """UPDATE tasks SET analysis_status=%s,error_code=%s,error_message=%s,
                updated_at=now(),version=version+1 WHERE id=%s""",
                (
                    "RETRY" if retry else "FAILED",
                    error.code,
                    str(error)[:512],
                    job.task_id,
                ),
            )
            self._event(
                connection,
                job.task_id,
                job.attempt_id,
                "ANALYSIS_RETRY" if retry else "ANALYSIS_FAILED",
                {
                    "analysis_job_id": str(job.id),
                    "error_code": error.code,
                    "analysis_attempt": job.attempt,
                    "retryable": retry,
                    "dead_letter": not retry,
                    "retry_delay_seconds": delay_seconds if retry else None,
                },
            )
