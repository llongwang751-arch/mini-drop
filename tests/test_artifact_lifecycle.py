from __future__ import annotations

from datetime import datetime, timedelta, timezone

from server.app import storage
from server.app.artifact_lifecycle import (
    classify_family,
    list_expired_artifacts,
    reconcile_artifacts,
    retention_days,
)

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def _artifact(key, artifact_type="result", sha256=None, created_at=None):
    return {
        "bucket": "mini-drop",
        "object_key": key,
        "artifact_type": artifact_type,
        "size_bytes": 10,
        "sha256": sha256,
        "created_at": created_at or (NOW - timedelta(days=1)),
    }


def test_classify_family_and_retention():
    assert classify_family("raw") == "raw"
    assert classify_family("pprof_raw") == "raw"
    assert classify_family("flamegraph_json") == "result"
    assert classify_family("top_json") == "result"
    assert classify_family("memory_json") == "result"
    assert classify_family("some_log") == "log"
    assert classify_family("weird_type") == "intermediate"
    assert retention_days("result") == 90
    assert retention_days("raw") == 30


def test_reconcile_detects_missing_artifacts(monkeypatch):
    monkeypatch.setattr(storage, "list_objects", lambda *a, **k: [
        {"object_key": "tasks/t1/raw/perf.data", "size": 100, "last_modified": NOW - timedelta(days=1)},
        {"object_key": "tasks/t2/result/flamegraph.json", "size": 50, "last_modified": NOW},
    ])
    artifacts = [
        _artifact("tasks/t1/raw/perf.data", "raw"),
        _artifact("tasks/t2/result/flamegraph.json", "result"),
        _artifact("tasks/t3/raw/missing.pb.gz", "pprof_raw"),
    ]
    report = reconcile_artifacts(artifacts, "mini-drop", now=NOW)
    assert [item["object_key"] for item in report.missing_objects] == [
        "tasks/t3/raw/missing.pb.gz"
    ]
    assert report.orphan_objects == []


def test_reconcile_detects_orphan_objects(monkeypatch):
    monkeypatch.setattr(storage, "list_objects", lambda *a, **k: [
        {"object_key": "tasks/t1/result/flamegraph.json", "size": 50, "last_modified": NOW},
        {"object_key": "tasks/orphan/raw/orphan.data", "size": 999, "last_modified": NOW},
    ])
    artifacts = [_artifact("tasks/t1/result/flamegraph.json", "result")]
    report = reconcile_artifacts(artifacts, "mini-drop", now=NOW)
    assert [item["object_key"] for item in report.orphan_objects] == [
        "tasks/orphan/raw/orphan.data"
    ]
    assert report.missing_objects == []


def test_reconcile_flags_hash_mismatch(monkeypatch):
    monkeypatch.setattr(storage, "list_objects", lambda *a, **k: [
        {"object_key": "tasks/t1/raw/perf.data", "size": 100, "last_modified": NOW},
    ])
    monkeypatch.setattr(storage, "read_object_bytes", lambda *a, **k: b"corrupted")
    artifacts = [_artifact("tasks/t1/raw/perf.data", "raw", sha256="a" * 64)]
    report = reconcile_artifacts(artifacts, "mini-drop", verify_hashes=True, now=NOW)
    assert len(report.hash_mismatches) == 1
    assert report.hash_mismatches[0]["actual_sha256"] != "a" * 64


def test_reconcile_flags_expired_objects_by_retention(monkeypatch):
    old = NOW - timedelta(days=100)  # result retention is 90d -> expired
    monkeypatch.setattr(storage, "list_objects", lambda *a, **k: [
        {"object_key": "tasks/t1/result/old.json", "size": 10, "last_modified": old},
        {"object_key": "tasks/t2/result/new.json", "size": 10, "last_modified": NOW},
    ])
    artifacts = [
        _artifact("tasks/t1/result/old.json", "result", created_at=old),
        _artifact("tasks/t2/result/new.json", "result", created_at=NOW),
    ]
    report = reconcile_artifacts(artifacts, "mini-drop", now=NOW)
    assert [item["object_key"] for item in report.expirable_objects] == [
        "tasks/t1/result/old.json"
    ]


def test_list_expired_artifacts_uses_created_at():
    old = NOW - timedelta(days=100)
    artifacts = [
        _artifact("tasks/t1/result/old.json", "result", created_at=old),
        _artifact("tasks/t2/raw/new.data", "raw", created_at=NOW),
    ]
    expired = list_expired_artifacts(artifacts, now=NOW)
    assert [item["object_key"] for item in expired] == ["tasks/t1/result/old.json"]
