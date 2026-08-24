"""PostgreSQL lock collector security and artifact contract tests."""

from __future__ import annotations

import json
from unittest import mock

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.database_lock import DatabaseLockCollector


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]


class _Connection:
    def __init__(self, lock_rows):
        self.lock_rows = lock_rows
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "pg_control_system" in sql:
            return _Result([("cluster-system-id",)])
        if "pg_stat_activity" in sql:
            return _Result(self.lock_rows)
        return _Result([])


def _task() -> CollectorTask:
    return CollectorTask(
        id="db-lock-001",
        collector_type="database_lock",
        target_pid=123,
        sample_rate=1,
        duration_sec=3,
    )


def test_rejects_non_postgres_process():
    collector = DatabaseLockCollector()
    with mock.patch.object(collector, "_is_postgres_process", return_value=False):
        result = collector.collect(_task())
    assert result.ok is False
    assert "不是 PostgreSQL" in result.reason


def test_requires_operator_configured_dsn(monkeypatch):
    monkeypatch.delenv("MINI_DROP_DATABASE_DIAGNOSTIC_URL", raising=False)
    collector = DatabaseLockCollector()
    with mock.patch.object(collector, "_is_postgres_process", return_value=True):
        result = collector.collect(_task())
    assert result.ok is False
    assert "未配置" in result.reason


def test_collects_read_only_redacted_lock_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "MINI_DROP_DATABASE_DIAGNOSTIC_URL",
        "postgresql+psycopg://observer:secret@db/mini_drop",
    )
    connection = _Connection([
        (321, 1250.0, "Lock", "transactionid", [654], "query-md5"),
    ])
    connector = mock.Mock(return_value=connection)
    collector = DatabaseLockCollector(connector=connector)
    collector.OUTPUT_BASE = str(tmp_path)

    with mock.patch.object(collector, "_is_postgres_process", return_value=True), \
         mock.patch("time.monotonic", side_effect=[0.0, 0.0, 3.0, 3.0]), \
         mock.patch("time.sleep"):
        result = collector.collect(_task())

    assert result.ok is True
    connector.assert_called_once_with(
        "postgresql://observer:secret@db/mini_drop",
        autocommit=True,
        connect_timeout=3,
    )
    assert "SET default_transaction_read_only = on" in connection.statements
    artifact = result.artifacts[0]
    assert artifact["artifact_type"] == "database_locks_json"
    payload = json.loads((tmp_path / "db-lock-001" / "database_locks.json").read_text())
    assert payload["lock_wait_count"] == 1
    assert payload["blocker_count"] == 1
    assert payload["blocking_edge_count"] == 1
    assert payload["waiting_sessions"][0]["blocking_pids"] == [654]
    serialized = json.dumps(payload)
    assert "secret" not in serialized
    assert "observer" not in serialized
