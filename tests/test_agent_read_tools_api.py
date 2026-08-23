from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from server.app.agent_runtime.read_tools import canonical_json
from server.app.database import _get_engine, init_db, reset_engine
from server.app.diagnosis.store import DiagnosisStore
from server.app.main import app
from server.app.models import Base


TOKEN = "agent-read-tool-contract-token"
DIAGNOSIS_ID = "diag-read-tools"
OTHER_DIAGNOSIS_ID = "diag-read-tools-other"
EVIDENCE_ID = "ev-read-safe"


@pytest.fixture(autouse=True)
def _read_tool_database(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'read-tools.db'}")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("MINIO_AUTO_CREATE_BUCKET", "0")
    monkeypatch.setenv("MINI_DROP_EMBED_GRPC", "0")
    monkeypatch.setenv("MINI_DROP_EMBED_MAINTENANCE", "0")
    monkeypatch.setenv("MINI_DROP_OUTBOX_DISPATCH_ENABLED", "0")
    reset_engine()
    init_db()
    _seed_data()
    yield
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _headers(token: str = TOKEN) -> dict[str, str]:
    return {"X-Internal-Token": token}


def _post(client: TestClient, path: str, body: dict) -> dict:
    response = client.post(path, headers=_headers(), json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    return payload["data"]


def _create_diagnosis(store: DiagnosisStore, diagnosis_id: str, **values) -> None:
    store.create_session({
        "diagnosis_id": diagnosis_id,
        "case_id": f"case-{diagnosis_id}",
        "creator_id": "read-tool-test",
        "raw_query": "诊断服务内存持续增长",
        "normalized_intent": values.get("normalized_intent", {}),
        "target_scope": values.get("target_scope", {}),
        "requested_time_range": {"start": "2026-08-23T00:00:00Z", "end": "2026-08-23T00:05:00Z"},
        "effective_time_range": {"start": "2026-08-23T00:00:30Z", "end": "2026-08-23T00:04:30Z"},
        "status": values.get("status", "ANALYZING"),
        "policy_profile": "read-only",
        "hypothesis_graph": values.get("hypothesis_graph", {}),
        "conclusion_versions": values.get("conclusion_versions", []),
        "model_version": "none",
        "planner_version": "test",
        "deadline_at": datetime.now(timezone.utc),
    })


def _seed_data() -> None:
    store = DiagnosisStore()
    _create_diagnosis(
        store,
        DIAGNOSIS_ID,
        target_scope={
            "target_service": "checkout",
            "environment": "test",
            "same_host_instance_ids": None,
            "downstream_service_ids": None,
            "instances": None,
            "excluded_targets": None,
        },
        hypothesis_graph={
            "hypotheses": [
                {
                    "hypothesis_id": "hyp-memory",
                    "type": "RESOURCE",
                    "description": "process memory leak",
                    "status": "SUPPORTED",
                    "supporting_evidence_refs": [EVIDENCE_ID, "ev-ineligible"],
                    "contradicting_evidence_refs": [],
                    "missing_evidence_requirements": [],
                    "next_probe_candidates": [],
                },
                {
                    "hypothesis_id": "hyp-network",
                    "type": "DEPENDENCY",
                    "description": "network degradation",
                    "status": "SUPPORTED",
                    "supporting_evidence_refs": ["ev-ineligible"],
                    "contradicting_evidence_refs": [],
                    "missing_evidence_requirements": [],
                    "next_probe_candidates": ["collect network latency"],
                },
            ],
            "unexplained_evidence_refs": [],
        },
    )
    _create_diagnosis(store, OTHER_DIAGNOSIS_ID)
    store.add_probe({
        "step_id": "probe-step-safe",
        "diagnosis_id": DIAGNOSIS_ID,
        "probe_id": "sys-metrics",
        "target": {
            "service_id": "checkout",
            "instance_id": "checkout-1",
            "host_id": "host-1",
            "environment": "test",
            "runtime": "python",
            "agent_id": "private-agent",
            "pid": 4217,
            "target_pid": 4217,
            "object_key": "private/object",
        },
        "parameters": {"authorization": "private"},
        "reason": "verify memory trend",
        "risk_level": "R1",
        "status": "SUCCEEDED",
        "evidence_purpose": "VERIFY",
    })
    for evidence_id, diagnosis_id, trusted in (
        (EVIDENCE_ID, DIAGNOSIS_ID, True),
        ("ev-ineligible", DIAGNOSIS_ID, False),
        ("ev-other", OTHER_DIAGNOSIS_ID, True),
    ):
        store.add_evidence({
            "evidence_id": evidence_id,
            "diagnosis_id": diagnosis_id,
            "source_type": "metric",
            "source_system": "sys-metrics",
            "evidence_role": "incident",
            "target": {
                "service_id": "checkout",
                "instance_id": "checkout-1",
                "host_id": "host-1",
                "environment": "test",
                "runtime": "python",
                "agent_id": "private-agent",
                "pid": 4217,
                "target_pid": 4217,
                "container_id": "private-container",
                "object_key": "private/object",
            },
            "event_time_range": {
                "start": "2026-08-23T00:01:00Z",
                "end": "2026-08-23T00:02:00Z",
                "source": "collector",
                "sampling_period_seconds": 60,
                "clock_skew_estimate_ms": 3,
                "private_window": "secret-window",
            },
            "query_or_probe": "bounded sys metrics",
            "raw_artifact_ref": "s3://private/raw",
            "derived_artifact_ref": "s3://private/derived",
            "observed_value": {
                "status": "SUCCEEDED",
                "collector_type": "sys_metrics",
                "task_id": "private-task",
                "agent_id": "private-agent",
                "target_pid": 4217,
                "status_reason": "private status",
                "keys": ["private"],
                "summary": {
                    "vmrss_mb": 512.5,
                    "network_latency_p95_ms": 17,
                    "pid": 4217,
                    "password": "private-password",
                    "path": "/private/path",
                },
                "top_items": [
                    {"name": "worker-a", "percent": 81.5, "pid": 4217, "token": "private-token"}
                ],
                "fact_domains": {
                    "schema_version": "v2",
                    "process": {
                        "pid": 4217,
                        "start_time_ticks": 99,
                        "memory": {"rss_bytes": 536870912, "private_path": "/private"},
                        "cpu": {"normalized_core_usage": 0.82, "command": "private"},
                    },
                },
            },
            "baseline_value": {"private_baseline": "must-not-leak"},
            "anomaly_score": {"private_score": 0.99},
            "data_quality": {
                "completeness": "COMPLETE",
                "domains": ["cpu", "memory"],
                "size_bytes": 2048,
                "sample_count": 60,
                "sampling_window_seconds": 60,
                "quality_reasons": ["stable clock"],
                "reviewer_id": "private-reviewer",
                "object_key": "private/object",
            },
            "integrity_hash": f"sha256:{evidence_id:0<64}"[:71],
            "claim_links": [{"private_oracle": "must-not-leak"}],
        })
        if trusted:
            store.review_evidence(
                diagnosis_id=diagnosis_id,
                evidence_id=evidence_id,
                lifecycle_status="ACTIVE",
                trust_status="TRUSTED",
                reviewer_id="private-reviewer",
            )


def _database_snapshot() -> dict[str, list[str]]:
    with _get_engine().connect() as connection:
        return {
            table.name: sorted(
                canonical_json(dict(row._mapping))
                for row in connection.execute(select(table))
            )
            for table in Base.metadata.sorted_tables
        }


def _all_success_requests() -> list[tuple[str, dict]]:
    return [
        ("/internal/agent/tools/diagnosis-snapshot", {"tool": "get_diagnosis_snapshot", "diagnosis_id": DIAGNOSIS_ID}),
        ("/internal/agent/tools/list-diagnosis-evidence", {"tool": "list_diagnosis_evidence", "diagnosis_id": DIAGNOSIS_ID, "filters": {}}),
        ("/internal/agent/tools/get-evidence-projection", {"tool": "get_evidence_projection", "diagnosis_id": DIAGNOSIS_ID, "evidence_ids": [EVIDENCE_ID]}),
        ("/internal/agent/tools/compare-evidence", {"tool": "compare_evidence", "diagnosis_id": DIAGNOSIS_ID, "evidence_ids": [EVIDENCE_ID, EVIDENCE_ID + "-second"]}),
        ("/internal/agent/tools/get-evidence-gaps", {"tool": "get_evidence_gaps", "diagnosis_id": DIAGNOSIS_ID}),
        ("/internal/agent/tools/evaluate-hypotheses", {"tool": "evaluate_hypotheses", "diagnosis_id": DIAGNOSIS_ID}),
    ]


def test_routes_require_internal_token_and_strict_tool_schema(client: TestClient, monkeypatch) -> None:
    path = "/internal/agent/tools/diagnosis-snapshot"
    body = {"tool": "get_diagnosis_snapshot", "diagnosis_id": DIAGNOSIS_ID}
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, headers=_headers("wrong"), json=body).status_code == 401
    monkeypatch.delenv("MINI_DROP_PI_INTERNAL_TOKEN")
    assert client.post(path, headers=_headers(), json=body).status_code == 503
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", TOKEN)
    assert client.post(path, headers=_headers(), json={**body, "unexpected": True}).status_code == 422
    assert client.post(path, headers=_headers(), json={**body, "tool": "get_evidence_gaps"}).status_code == 422


def test_projection_is_closed_canonical_utf8_and_diagnosis_fenced(client: TestClient) -> None:
    data = _post(client, "/internal/agent/tools/get-evidence-projection", {
        "tool": "get_evidence_projection",
        "diagnosis_id": DIAGNOSIS_ID,
        "evidence_ids": [EVIDENCE_ID],
    })
    projection = data["projection"]
    item = projection["items"][0]
    serialized = canonical_json(projection)
    assert data["projection_bytes"] == len(serialized.encode("utf-8"))
    assert data["projection_hash"].startswith("sha256:")
    assert item["target"] == {
        "service_id": "checkout",
        "instance_id": "checkout-1",
        "host_id": "host-1",
        "environment": "test",
        "runtime": "python",
    }
    assert item["observed_value"]["summary"] == {
        "vmrss_mb": 512.5,
        "network_latency_p95_ms": 17,
    }
    assert item["observed_value"]["top_items"] == [{"name": "worker-a", "percent": 81.5}]
    assert item["observed_value"]["fact_domains"]["process"] == {
        "memory": {"rss_bytes": 536870912},
        "cpu": {"normalized_core_usage": 0.82},
    }
    assert item["baseline_value"] == {}
    assert item["anomaly_score"] == {}
    assert item["claim_links"] == []
    rendered = canonical_json(data)
    for forbidden in (
        "private-agent", "private-container", "private-task", "private-password",
        "private-token", "private-reviewer", "private_oracle", "s3://private",
        "/private/path", "object_key", "target_pid", "start_time_ticks",
    ):
        assert forbidden not in rendered

    cross = client.post(
        "/internal/agent/tools/get-evidence-projection",
        headers=_headers(),
        json={
            "tool": "get_evidence_projection",
            "diagnosis_id": DIAGNOSIS_ID,
            "evidence_ids": ["ev-other"],
        },
    )
    ineligible = client.post(
        "/internal/agent/tools/get-evidence-projection",
        headers=_headers(),
        json={
            "tool": "get_evidence_projection",
            "diagnosis_id": DIAGNOSIS_ID,
            "evidence_ids": ["ev-ineligible"],
        },
    )
    assert cross.status_code == 404
    assert ineligible.status_code == 404
    assert cross.json()["detail"] == ineligible.json()["detail"]


def test_snapshot_and_compare_reuse_closed_nested_projections(client: TestClient) -> None:
    snapshot = _post(client, "/internal/agent/tools/diagnosis-snapshot", {
        "tool": "get_diagnosis_snapshot",
        "diagnosis_id": DIAGNOSIS_ID,
    })["projection"]
    assert snapshot["target_scope"]["instances"] == []
    assert snapshot["target_scope"]["excluded_targets"] == []
    assert snapshot["target_scope"]["same_host_instance_ids"] == []
    assert snapshot["target_scope"]["downstream_service_ids"] == []
    assert snapshot["probes"][0]["target"] == {
        "service_id": "checkout",
        "instance_id": "checkout-1",
        "host_id": "host-1",
        "environment": "test",
        "runtime": "python",
    }
    rendered = canonical_json(snapshot)
    assert "private-agent" not in rendered
    assert "target_pid" not in rendered
    assert "object_key" not in rendered

    duplicate = client.post(
        "/internal/agent/tools/compare-evidence",
        headers=_headers(),
        json={
            "tool": "compare_evidence",
            "diagnosis_id": DIAGNOSIS_ID,
            "evidence_ids": [EVIDENCE_ID, EVIDENCE_ID],
        },
    )
    assert duplicate.status_code == 409


def test_projection_oversize_rejects_without_truncating_json(client: TestClient) -> None:
    response = client.post(
        "/internal/agent/tools/get-evidence-projection",
        headers=_headers(),
        json={
            "tool": "get_evidence_projection",
            "diagnosis_id": DIAGNOSIS_ID,
            "evidence_ids": [EVIDENCE_ID],
            "max_bytes": 1,
        },
    )
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["projection_bytes"] > detail["max_bytes"] == 1


def test_invalid_cursor_and_filter_validation_are_explicit(client: TestClient) -> None:
    invalid_cursor = client.post(
        "/internal/agent/tools/list-diagnosis-evidence",
        headers=_headers(),
        json={
            "tool": "list_diagnosis_evidence",
            "diagnosis_id": DIAGNOSIS_ID,
            "filters": {},
            "cursor": "not-in-filtered-result",
        },
    )
    invalid_filter = client.post(
        "/internal/agent/tools/list-diagnosis-evidence",
        headers=_headers(),
        json={
            "tool": "list_diagnosis_evidence",
            "diagnosis_id": DIAGNOSIS_ID,
            "filters": {"reviewer_id": "private-reviewer"},
        },
    )
    assert invalid_cursor.status_code == 409
    assert invalid_filter.status_code == 422


def test_hypothesis_support_degrades_when_only_ineligible_evidence_remains(client: TestClient) -> None:
    data = _post(client, "/internal/agent/tools/evaluate-hypotheses", {
        "tool": "evaluate_hypotheses",
        "diagnosis_id": DIAGNOSIS_ID,
    })["projection"]
    by_id = {item["hypothesis_id"]: item for item in data["hypotheses"]}
    assert by_id["hyp-memory"]["effective_status"] == "SUPPORTED"
    assert by_id["hyp-network"]["effective_status"] == "INCONCLUSIVE"
    assert data["graph_complete"] is False
    assert data["counts"]["INCONCLUSIVE"] == 1


def test_all_six_routes_leave_complete_database_unchanged(client: TestClient) -> None:
    store = DiagnosisStore()
    store.add_evidence({
        "evidence_id": EVIDENCE_ID + "-second",
        "diagnosis_id": DIAGNOSIS_ID,
        "source_type": "metric",
        "source_system": "sys-metrics",
        "query_or_probe": "second bounded metric",
        "integrity_hash": "sha256:" + "2" * 64,
        "observed_value": {"summary": {"vmrss_mb": 500}},
    })
    store.review_evidence(
        diagnosis_id=DIAGNOSIS_ID,
        evidence_id=EVIDENCE_ID + "-second",
        lifecycle_status="ACTIVE",
        trust_status="TRUSTED",
        reviewer_id="private-reviewer",
    )
    before = _database_snapshot()
    for path, body in _all_success_requests():
        response = client.post(path, headers=_headers(), json=body)
        assert response.status_code == 200, response.text
        assert _database_snapshot() == before
