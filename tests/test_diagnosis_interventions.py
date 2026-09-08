from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.exploration_tree import get_live_exploration_tree
from server.app.drop_insight.schemas import (
    ClarifyDiagnosisRequest,
    DiagnosticTimeRange,
    InterveneDiagnosisRequest,
)
from server.app.drop_insight.service import (
    _diagnostic_time_ranges_equivalent,
    clarify_diagnosis,
    intervene_diagnosis,
    list_diagnosis_interventions,
)
from server.app.models import (
    DropInsightEventModel,
    DropInsightHypothesisModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
)
from server.app.sql_repository import SqlRepository
from server.app.state_machine import now_utc


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _session_model(
    diagnosis_id: str,
    *,
    status: str,
    target: dict | None = None,
    requested_range: dict | None = None,
) -> DropInsightSessionModel:
    timestamp = now_utc()
    requested_range = requested_range or {}
    return DropInsightSessionModel(
        id=diagnosis_id,
        query="诊断订单服务 CPU 升高，并排除 I/O 等待",
        target_json=target or {},
        time_range_json=requested_range,
        requested_time_range_json=requested_range,
        effective_time_range_json={},
        mode="ASSISTED",
        skill_policy="AUTO",
        budget_json={
            "max_duration_seconds": 300,
            "max_tool_calls": 12,
            "max_diagnosis_rounds": 6,
            "max_concurrent_tasks": 3,
            "max_hosts": 5,
            "max_artifact_bytes": 524_288_000,
            "max_risk_level": "R2",
        },
        status=status,
        version=1,
        clarification_questions_json=[],
        created_at=timestamp,
        updated_at=timestamp,
    )


def _created_event(diagnosis_id: str) -> DropInsightEventModel:
    return DropInsightEventModel(
        id=f"event-created-{diagnosis_id}",
        diagnosis_id=diagnosis_id,
        sequence=1,
        event_type="diagnosis.created",
        actor="USER",
        payload_json={"status": "NEEDS_CLARIFICATION"},
        effect_key=f"created:{diagnosis_id}",
        occurred_at=now_utc(),
    )


def test_repeated_minute_precision_time_range_is_idempotent_but_a_real_move_is_rejected():
    diagnosis_id = "insight-range-idempotence"
    established = {
        "start": "2026-09-05T19:43:27+08:00",
        "end": "2026-09-05T19:48:41+08:00",
        "timezone": "Asia/Shanghai",
    }
    session = new_session()
    session.add(
        _session_model(
            diagnosis_id,
            status="NEEDS_CLARIFICATION",
            target={"service": "order-service", "environment": "demo"},
            requested_range=established,
        )
    )
    session.add(_created_event(diagnosis_id))
    session.commit()
    session.close()

    repeated = ClarifyDiagnosisRequest(
        expected_version=1,
        time_range=DiagnosticTimeRange(
            start=datetime.fromisoformat("2026-09-05T19:43:00+08:00"),
            end=datetime.fromisoformat("2026-09-05T19:48:00+08:00"),
            timezone="Asia/Shanghai",
        ),
    )
    result = clarify_diagnosis(diagnosis_id, repeated)

    assert result is not None
    assert result["requested_time_range"] == established
    assert result["time_range"] == established

    moved = ClarifyDiagnosisRequest(
        expected_version=result["version"],
        time_range=DiagnosticTimeRange(
            start=datetime.fromisoformat("2026-09-05T19:44:00+08:00"),
            end=datetime.fromisoformat("2026-09-05T19:49:00+08:00"),
            timezone="Asia/Shanghai",
        ),
    )
    with pytest.raises(ValueError, match="time range is immutable"):
        clarify_diagnosis(diagnosis_id, moved)

    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        assert diagnosis.requested_time_range_json == established
        assert diagnosis.version == result["version"]
        assert (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .count()
            == 2
        )
    finally:
        session.close()


def test_time_range_equivalence_compares_instants_across_offsets():
    assert _diagnostic_time_ranges_equivalent(
        {
            "start": "2026-09-05T11:43:00Z",
            "end": "2026-09-05T11:48:00Z",
            "timezone": "UTC",
        },
        {
            "start": "2026-09-05T19:43:27+08:00",
            "end": "2026-09-05T19:48:41+08:00",
            "timezone": "Asia/Shanghai",
        },
    )
    assert not _diagnostic_time_ranges_equivalent(
        {
            "start": "2026-09-05T11:42:00Z",
            "end": "2026-09-05T11:48:00Z",
            "timezone": "UTC",
        },
        {
            "start": "2026-09-05T19:43:27+08:00",
            "end": "2026-09-05T19:48:41+08:00",
            "timezone": "Asia/Shanghai",
        },
    )


def test_user_intervention_reopens_insufficient_evidence_and_grows_tree_idempotently(
    monkeypatch,
):
    monkeypatch.setattr(
        "server.app.drop_insight.service.propose_hypothesis_plan",
        lambda **_kwargs: None,
    )
    repo = SqlRepository()
    repo.register_agent(
        "agent-intervention",
        "worker-intervention",
        "10.0.0.18",
        capabilities=[
            "sys_metrics",
            "perf_cpu",
            "ebpf_io",
            "pyspy",
            "java_async",
            "memory_smaps",
            "go_pprof",
            "continuous_perf",
        ],
    )
    snapshot = repo.record_process_candidate_snapshot(
        "agent-intervention",
        {
            "generation": 1,
            "boot_id": "boot-intervention",
            "observed_at_unix_ms": 1,
            "complete": True,
            "truncated": False,
            "error": "",
            "candidates": [
                {
                    "pid": 4242,
                    "process_start_ticks": 101,
                    "pid_namespace_inode": 202,
                    "namespace_pid": 4242,
                    "executable_identity": "sha256:intervention-demo",
                    "comm": "order-service",
                    "cgroup": "/demo/order-service",
                    "service_hint": "order-service",
                    "instance_hint": "demo",
                    "collector_capabilities": ["sys_metrics", "perf_cpu", "ebpf_io"],
                }
            ],
        },
        received_at=now_utc(),
    )
    binding = snapshot.candidates[0].binding().to_dict()
    diagnosis_id = "insight-human-intervention"
    timestamp = now_utc()
    session = new_session()
    session.add(
        _session_model(
            diagnosis_id,
            status="INSUFFICIENT_EVIDENCE",
            target={
                "service": "order-service",
                "environment": "demo",
                "agent_id": "agent-intervention",
                "pid": 4242,
                "process_binding": binding,
            },
        )
    )
    session.add(_created_event(diagnosis_id))
    session.add(
        DropInsightHypothesisModel(
            id="hypothesis-original",
            diagnosis_id=diagnosis_id,
            statement="CPU 热点可能导致订单服务延迟",
            expected_observations_json=["perf 栈集中"],
            falsification_criteria_json=["CPU 栈没有主导热点"],
            status="INCONCLUSIVE",
            source="MODEL",
            round_index=1,
            parent_hypothesis_id=None,
            generation_reason="首轮证据不足",
            effect_key="initial:hypothesis",
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    request = InterveneDiagnosisRequest(
        expected_version=1,
        action="CHANGE_DIRECTION",
        message="CPU 火焰图没有热点，请改为优先排查磁盘 I/O 等待。",
        hypothesis_id="hypothesis-original",
        idempotency_key="web-turn-0001",
    )
    first = intervene_diagnosis(diagnosis_id, request, created_by="user:demo")

    assert first is not None
    assert first["action"] == "CHANGE_DIRECTION"
    assert first["status"] == "HYPOTHESIZING"
    assert first["round_index"] == 2
    assert first["revision_hypothesis_id"]
    assert first["tool_call_id"]

    session = new_session()
    try:
        parent = session.get(DropInsightHypothesisModel, "hypothesis-original")
        revision = session.get(
            DropInsightHypothesisModel,
            first["revision_hypothesis_id"],
        )
        tool_call = session.get(DropInsightToolCallModel, first["tool_call_id"])
        assert parent.status == "DEPRIORITIZED"
        assert revision.parent_hypothesis_id == parent.id
        assert revision.round_index == 2
        assert tool_call.hypothesis_id == revision.id
        assert tool_call.tool_name == "start_ebpf_io_profile"
        assert tool_call.status == "PENDING_APPROVAL"
        counts_before_retry = (
            session.query(DropInsightHypothesisModel).count(),
            session.query(DropInsightToolCallModel).count(),
            session.query(DropInsightEventModel).count(),
        )
    finally:
        session.close()

    tree = get_live_exploration_tree(diagnosis_id)
    assert tree is not None
    assert tree["version"] == tree["revision"]
    assert tree["stats"]["human_interventions"] == 1
    intervention_node = next(
        item
        for item in tree["nodes"]
        if item["id"] == f"intervention:{first['intervention_id']}"
    )
    revision_node = next(
        item
        for item in tree["nodes"]
        if item["id"] == f"hypothesis:{first['revision_hypothesis_id']}"
    )
    assert intervention_node["parent_id"] == "hypothesis:hypothesis-original"
    assert revision_node["parent_id"] == intervention_node["id"]

    # A browser retry may carry the old optimistic version.  The idempotency
    # key must win before the version check and must not open another round.
    replay = intervene_diagnosis(diagnosis_id, request, created_by="user:demo")
    assert replay["intervention_id"] == first["intervention_id"]
    assert replay["revision_hypothesis_id"] == first["revision_hypothesis_id"]
    assert replay["tool_call_id"] == first["tool_call_id"]
    assert len(list_diagnosis_interventions(diagnosis_id)) == 1

    session = new_session()
    try:
        assert counts_before_retry == (
            session.query(DropInsightHypothesisModel).count(),
            session.query(DropInsightToolCallModel).count(),
            session.query(DropInsightEventModel).count(),
        )
    finally:
        session.close()


def test_rpc_dispatch_validates_and_forwards_intervention(monkeypatch):
    from server.app import diagnostic_ai_rpc

    observed = {}

    def fake_intervene(diagnosis_id, payload, *, created_by):
        observed.update(
            diagnosis_id=diagnosis_id,
            payload=payload,
            created_by=created_by,
        )
        return {"intervention_id": "intervention-rpc"}

    monkeypatch.setattr(diagnostic_ai_rpc, "intervene_diagnosis", fake_intervene)
    result = diagnostic_ai_rpc.dispatch(
        "POST",
        "/diagnoses/insight-rpc/interventions",
        "",
        json.dumps(
            {
                "action": "CONTINUE_INVESTIGATION",
                "message": "补充同一时间窗的系统基线后继续一轮",
                "idempotency_key": "rpc-turn-0001",
            }
        ),
        "user:rpc",
    )

    assert result.status == 200
    assert result.body["data"] == {"intervention_id": "intervention-rpc"}
    assert observed["diagnosis_id"] == "insight-rpc"
    assert observed["payload"].action == "CONTINUE_INVESTIGATION"
    assert observed["created_by"] == "user:rpc"


def test_rpc_dispatch_exposes_bounded_fault_plaza_controls(monkeypatch):
    from server.app import diagnostic_ai_rpc

    calls = []
    monkeypatch.setattr(
        diagnostic_ai_rpc,
        "get_fault_plaza",
        lambda: {"status": "READY", "scenarios": []},
    )
    monkeypatch.setattr(
        diagnostic_ai_rpc,
        "start_fault_scenario",
        lambda scenario_id, duration: calls.append(("start", scenario_id, duration))
        or {"status": "RUNNING"},
    )
    monkeypatch.setattr(
        diagnostic_ai_rpc,
        "stop_fault_scenario",
        lambda scenario_id: calls.append(("stop", scenario_id, None))
        or {"status": "STOPPED"},
    )

    listed = diagnostic_ai_rpc.dispatch(
        "GET", "/showcases/fault-plaza", "", "", "user:demo"
    )
    started = diagnostic_ai_rpc.dispatch(
        "POST",
        "/showcases/fault-plaza/cpu-hotspot/start",
        "",
        '{"duration_seconds":45}',
        "user:demo",
    )
    stopped = diagnostic_ai_rpc.dispatch(
        "POST",
        "/showcases/fault-plaza/cpu-hotspot/stop",
        "",
        "",
        "user:demo",
    )

    assert listed.body["data"]["status"] == "READY"
    assert started.body["data"]["status"] == "RUNNING"
    assert stopped.body["data"]["status"] == "STOPPED"
    assert calls == [
        ("start", "cpu-hotspot", 45),
        ("stop", "cpu-hotspot", None),
    ]
