"""Read-only PostgreSQL lock diagnostics collector.

The DSN is operator-provided through ``MINI_DROP_DATABASE_DIAGNOSTIC_URL``;
it is never accepted from an AI tool call. SQL text, bind values, usernames
and relation names are deliberately excluded from the artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import psycopg

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


_LOCK_QUERY = """
SELECT
    a.pid,
    COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() - a.query_start)) * 1000, 0),
    COALESCE(a.wait_event_type, ''),
    COALESCE(a.wait_event, ''),
    pg_blocking_pids(a.pid),
    md5(COALESCE(a.query, ''))
FROM pg_stat_activity AS a
WHERE cardinality(pg_blocking_pids(a.pid)) > 0
   OR a.wait_event_type = 'Lock'
ORDER BY a.pid
LIMIT 100
"""


class DatabaseLockCollector:
    OUTPUT_BASE = "/tmp/mini-drop"

    def __init__(self, connector: Callable[..., Any] | None = None) -> None:
        self._connector = connector or psycopg.connect

    def collect(self, task: CollectorTask) -> CollectorResult:
        if not self._is_postgres_process(task.target_pid):
            return CollectorResult(
                ok=False,
                reason=f"目标 PID {task.target_pid} 不是 PostgreSQL 进程",
            )
        dsn = os.getenv("MINI_DROP_DATABASE_DIAGNOSTIC_URL", "").strip()
        if not dsn:
            return CollectorResult(
                ok=False,
                reason="未配置 MINI_DROP_DATABASE_DIAGNOSTIC_URL，数据库锁采集能力不可用",
            )
        dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
        duration = max(3, min(int(task.duration_sec), 60))
        snapshots: list[dict[str, Any]] = []
        unique_blockers: set[int] = set()
        max_wait_ms = 0.0
        max_waiting = 0
        server_fingerprint = ""
        try:
            with self._connector(
                dsn,
                autocommit=True,
                connect_timeout=3,
            ) as connection:
                connection.execute("SET default_transaction_read_only = on")
                connection.execute("SET statement_timeout = '2000ms'")
                connection.execute("SET application_name = 'mini-drop-lock-diagnostics'")
                try:
                    system_id = str(
                        connection.execute(
                            "SELECT system_identifier::text FROM pg_control_system()"
                        ).fetchone()[0]
                    )
                    server_fingerprint = hashlib.sha256(
                        system_id.encode("utf-8")
                    ).hexdigest()[:16]
                except Exception:
                    # The lock views remain useful when pg_control_system is
                    # restricted by a managed PostgreSQL provider.
                    server_fingerprint = "unavailable"

                deadline = time.monotonic() + duration
                while time.monotonic() < deadline:
                    rows = connection.execute(_LOCK_QUERY).fetchall()
                    waiting: list[dict[str, Any]] = []
                    for row in rows:
                        blockers = sorted(
                            {int(value) for value in (row[4] or []) if int(value) > 0}
                        )
                        unique_blockers.update(blockers)
                        wait_ms = max(0.0, float(row[1] or 0.0))
                        max_wait_ms = max(max_wait_ms, wait_ms)
                        waiting.append({
                            "blocked_pid": int(row[0]),
                            "blocking_pids": blockers,
                            "wait_ms": round(wait_ms, 3),
                            "wait_event_type": str(row[2] or ""),
                            "wait_event": str(row[3] or ""),
                            "query_fingerprint": str(row[5] or ""),
                        })
                    max_waiting = max(max_waiting, len(waiting))
                    snapshots.append({
                        "timestamp": time.time(),
                        "waiting_sessions": waiting,
                    })
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(1.0, remaining))
        except Exception as exc:
            return CollectorResult(
                ok=False,
                reason=f"数据库锁只读采集失败: {type(exc).__name__}",
            )

        metadata = {
            "schema_version": "database_lock.v1",
            "sample_count": len(snapshots),
            "lock_wait_count": max_waiting,
            "blocker_count": len(unique_blockers),
            "max_wait_ms": round(max_wait_ms, 3),
            "lock_wait_ms": round(max_wait_ms, 3),
            "server_fingerprint": server_fingerprint,
            "waiting_sessions": max(
                (item["waiting_sessions"] for item in snapshots),
                key=len,
                default=[],
            ),
            "redaction": "sql_text,user,database,relation_and_bind_values_omitted",
        }
        output = {
            "task_id": task.id,
            "target_pid": task.target_pid,
            **metadata,
            "snapshots": snapshots,
        }
        output_dir = Path(self.OUTPUT_BASE) / task.id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "database_locks.json"
        output_path.write_text(
            json.dumps(output, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return CollectorResult(
            ok=True,
            reason=(
                f"数据库锁采集完成: {len(snapshots)} 个快照，"
                f"最多 {max_waiting} 个等待会话"
            ),
            artifacts=[{
                "artifact_type": "database_locks_json",
                "filename": output_path.name,
                "local_path": str(output_path),
                "content_type": "application/json",
                "size_bytes": output_path.stat().st_size,
                "metadata": metadata,
            }],
        )

    @staticmethod
    def _is_postgres_process(pid: int) -> bool:
        try:
            comm = Path(f"/proc/{pid}/comm").read_text(
                encoding="utf-8", errors="replace"
            ).strip().casefold()
        except OSError:
            return False
        return "postgres" in comm
