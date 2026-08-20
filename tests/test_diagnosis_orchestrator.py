"""AI 集群诊断会话、探针审批、预算和证据链测试。"""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.diagnosis import orchestrator as orchestrator_module
from server.app.diagnosis.orchestrator import _pressure_flags
from server.app.diagnosis.actions import collect_action
from server.app.diagnosis.domain_analyzers import analyze_observations, assess_cluster, cluster_finding
from server.app.diagnosis.probe_registry import choose_probe_ids
from server.app.diagnosis.report_verifier import evidence_integrity_hash, verify_report
from server.app.diagnosis.schemas import ApprovalRequest
from server.app.main import app, repo
from server.app.main import diagnosis_orchestrator
from server.app.models import Base
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import Actor, TaskStatus


def test_cpu_probe_selection_uses_target_runtime_profiler() -> None:
    assert choose_probe_ids("cpu_saturation", "java")[-1] == "java_cpu_profile"
    assert choose_probe_ids("cpu_saturation", "python")[-1] == "python_cpu_profile"
    assert choose_probe_ids("cpu_saturation", "go")[-1] == "go_cpu_profile"
    assert choose_probe_ids("cpu_saturation", "native")[-1] == "process_cpu_profile"


def test_cpu_finding_distinguishes_captured_profile_from_missing_profile() -> None:
    findings = analyze_observations([{
        "task_id": "task-java",
        "collector_type": "java_async",
        "target": {"instance_id": "service-a-1"},
        "facts": {"process_cpu_core_usage": 1.4},
        "top_function": {"name": "", "percent": 0},
        "profile_available": True,
        "profile_artifacts": ["java_flamegraph_html"],
        "evidence_refs": ["ev-java-profile"],
    }])

    cpu = next(item for item in findings if item["category"] == "cpu")
    assert cpu["finding_type"] == "process_cpu_pressure_with_profile"
    assert cpu["facts"]["profile_artifacts"] == ["java_flamegraph_html"]
    assert cpu["missing_evidence"] == ["结构化 Profile TopN"]


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    monkeypatch.delenv("MINI_DROP_ALLOWED_SERVICES", raising=False)
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    repo._task_queues.clear()
    repo.agent_metrics.clear()
    repo.register_agent(
        "a1", "host-1", "10.0.0.1",
        capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
    )
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _payload(query: str = "服务 service-a CPU 飙高，请定位原因") -> dict:
    return {
        "query": query,
        "context": {
            "service_id": "service-a",
            "environment": "production",
            "instances": [{
                "service_id": "service-a",
                "instance_id": "service-a-1",
                "host_id": "host-1",
                "agent_id": "a1",
                "pid": 1234,
                "environment": "production",
            }],
        },
        "budget_profile": "production_safe",
    }


def test_historical_request_never_creates_current_task(client: TestClient):
    payload = _payload("请分析 2020 年的 service-a 故障")
    payload["context"]["time_range"] = {
        "start": "2020-01-01T00:00:00Z",
        "end": "2020-01-01T00:30:00Z",
        "source": "user_expression",
    }
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert data["normalized_intent"]["diagnosis_mode"] == "HISTORICAL"
    assert data["status"] == "INSUFFICIENT_EVIDENCE"
    assert data["child_task_ids"] == []
    assert data["probes"] == []


def test_live_effective_window_includes_bounded_collection_period(client: TestClient):
    data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    requested_end = datetime.fromisoformat(data["requested_time_range"]["end"].replace("Z", "+00:00"))
    effective_end = datetime.fromisoformat(data["effective_time_range"]["end"].replace("Z", "+00:00"))
    assert data["effective_time_range"]["source"] == "live_collection_window"
    assert effective_end > requested_end


def test_non_blocking_model_note_does_not_stop_resolved_scope(
    client: TestClient,
    monkeypatch,
):
    from server.app.diagnosis.intent import _fallback_intent

    def parsed_with_note(request):
        intent = _fallback_intent(request)
        intent.ambiguities = ["未提供时间范围，已使用服务器默认窗口"]
        return intent

    monkeypatch.setattr(orchestrator_module, "parse_diagnosis_intent", parsed_with_note)
    data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]

    assert data["target_scope"]["scope_completeness"] == "complete"
    assert data["status"] == "COLLECTING"
    assert data["child_task_ids"]


def test_missing_target_anchor_does_not_expand_to_other_service(client: TestClient):
    payload = _payload()
    payload["context"]["instances"][0]["service_id"] = "service-b"
    payload["context"]["instances"][0]["instance_id"] = "service-b-1"
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert data["status"] == "NEEDS_SCOPE_CONFIRMATION"
    assert data["target_scope"]["scope_completeness"] == "unresolved"
    assert data["child_task_ids"] == []


def test_worker_does_not_advance_or_spam_human_gate_sessions(client: TestClient):
    from sqlalchemy import func, select

    from server.app.database import new_session
    from server.app.models import DiagnosisEventModel, DiagnosisSessionModel

    payload = _payload()
    payload["context"]["instances"][0]["service_id"] = "service-b"
    payload["context"]["instances"][0]["instance_id"] = "service-b-1"
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    diagnosis_id = data["diagnosis_id"]

    with new_session() as session:
        before = session.scalar(select(func.count()).select_from(DiagnosisEventModel))
        before_session = session.get(DiagnosisSessionModel, diagnosis_id)
        before_version = before_session.row_version
        before_updated_at = before_session.updated_at
    diagnosis_orchestrator.advance_active()
    diagnosis_orchestrator.advance_active()
    with new_session() as session:
        after = session.scalar(select(func.count()).select_from(DiagnosisEventModel))
        after_session = session.get(DiagnosisSessionModel, diagnosis_id)
        failures = session.scalar(
            select(func.count()).select_from(DiagnosisEventModel).where(
                DiagnosisEventModel.diagnosis_id == diagnosis_id,
                DiagnosisEventModel.event_type == "advance_failed",
            )
        )

    assert after == before
    assert after_session.row_version == before_version
    assert after_session.updated_at == before_updated_at
    assert failures == 0


def test_zero_probe_budget_ends_explicitly(client: TestClient):
    payload = _payload()
    payload["budget"] = {"max_total_probe_cpu_seconds": 0}
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert data["status"] == "BUDGET_EXHAUSTED"
    assert data["child_task_ids"] == []


def test_pure_ebpf_latency_produces_io_finding():
    findings = analyze_observations([{
        "task_id": "t1",
        "target": {"instance_id": "i1"},
        "facts": {},
        "pressure": {"block_latency_high": True},
        "evidence_refs": ["ev1"],
    }])
    assert "io_wait_high" in {item["finding_type"] for item in findings}


def test_process_cpu_delta_detects_hot_process_on_non_saturated_host():
    pressure = _pressure_flags({
        "avg_cpu_user_pct": 25,
        "avg_cpu_sys_pct": 5,
        "process_cpu_core_usage": 1.2,
    }, {})
    assert pressure["cpu"] is True
    observations = [{
        "task_id": "t-target",
        "target": {"service_id": "service-a", "instance_id": "service-a-1"},
        "facts": {"avg_cpu_user_pct": 10, "process_cpu_core_usage": 0.1},
        "top_function": {"name": "", "percent": 0},
        "pressure": {name: False for name in pressure},
        "evidence_refs": ["ev-target"],
    }, {
        "task_id": "t-process",
        "target": {"service_id": "service-b", "instance_id": "service-b-1"},
        "facts": {"avg_cpu_user_pct": 25, "process_cpu_core_usage": 1.2},
        "top_function": {"name": "", "percent": 0},
        "pressure": pressure,
        "evidence_refs": ["ev-process"],
    }]
    findings = analyze_observations(observations)
    assert {item["finding_type"] for item in findings} == {"process_cpu_pressure"}
    assessment = assess_cluster({
        "target_service": "service-a",
        "downstream_service_ids": ["service-b"],
        "same_host_instance_ids": [],
    }, observations)
    assert assessment["domain_cause"]["subtype"] == "process_cpu_pressure"


def test_verifier_rejects_downstream_claim_without_evidence():
    result = verify_report({
        "cluster_assessment": {
            "classification": "downstream_dependency",
            "evidence_refs": [],
        },
        "actions": [],
    }, [], {"instances": []})
    assert result["status"] == "failed"
    assert any("缺少 Evidence" in issue for issue in result["issues"])


def test_root_location_and_domain_cause_are_independent():
    scope = {"target_service": "a", "downstream_service_ids": ["b"], "same_host_instance_ids": []}
    observations = [
        {"target": {"service_id": "a", "instance_id": "a1"}, "facts": {},
         "pressure": {}, "evidence_refs": ["ev-a"]},
        {"target": {"service_id": "b", "instance_id": "b1"},
         "facts": {"packet_loss_pct": 3}, "pressure": {"network": True}, "evidence_refs": ["ev-b"]},
    ]
    result = assess_cluster(scope, observations)
    assert result["root_location"]["type"] == "downstream"
    assert result["domain_cause"]["type"] == "network"
    assert "linux.cpu.process_pressure" not in cluster_finding(result)["knowledge_ids"]


def test_cpu_intent_prevents_incidental_memory_pressure_from_stealing_domain():
    scope = {"target_service": "a", "downstream_service_ids": [], "same_host_instance_ids": []}
    observations = [{
        "target": {"service_id": "a", "instance_id": "a1"},
        "facts": {"process_cpu_core_usage": 1.4, "vmrss_mb": 2200},
        "top_function": {"name": "compute_hotspot", "percent": 58},
        "pressure": {"cpu": True, "memory": True},
        "evidence_refs": ["ev-cpu", "ev-rss"],
    }]

    result = assess_cluster(scope, observations, symptom="cpu_saturation")

    assert result["domain_cause"]["type"] == "cpu"
    assert result["domain_cause"]["subtype"] == "process_cpu_pressure"


def test_memory_intent_still_prefers_memory_when_cpu_is_also_busy():
    scope = {"target_service": "a", "downstream_service_ids": [], "same_host_instance_ids": []}
    observations = [{
        "target": {"service_id": "a", "instance_id": "a1"},
        "facts": {"process_cpu_core_usage": 1.1, "vmrss_mb": 2600, "vmrss_trend": "increasing"},
        "top_function": {"name": "allocator", "percent": 45},
        "pressure": {"cpu": True, "memory": True},
        "evidence_refs": ["ev-cpu", "ev-memory"],
    }]

    result = assess_cluster(scope, observations, symptom="memory_pressure")

    assert result["domain_cause"]["type"] == "memory"


def test_verifier_detects_rendered_command_tampering():
    action = collect_action(
        action_id="a", title="collect", collector_type="sys_metrics",
        target={"agent_id": "a1", "pid": 1234}, duration_sec=15, sample_rate=11,
        comment="test", risk_level="R1", evidence_refs=[], confidence_level="中",
    )
    action["rendered_command"] += " --duration 99"
    result = verify_report({"actions": [action]}, [], {"instances": [{"agent_id": "a1", "pid": 1234}]})
    assert result["status"] == "failed"
    assert any("preview" in issue for issue in result["issues"])


def test_verifier_recomputes_full_evidence_hash():
    evidence = {
        "evidence_id": "ev1", "source_type": "derived_artifact", "source_system": "agent",
        "evidence_role": "incident", "target": {"agent_id": "a1", "pid": 1234},
        "event_time_range": {}, "ingestion_time": datetime.now(timezone.utc),
        "query_or_probe": "sys_metrics", "raw_artifact_ref": None, "derived_artifact_ref": "x",
        "derivation_version": "v2", "observed_value": {"cpu": 1}, "baseline_value": {},
        "anomaly_score": {}, "data_quality": {"domains": ["host"]}, "claim_links": [],
    }
    evidence["integrity_hash"] = evidence_integrity_hash(evidence)
    evidence["observed_value"]["cpu"] = 2
    result = verify_report(
        {"root_location": {"type": "self", "target_ref": "i1", "evidence_refs": ["ev1"]}, "actions": []},
        [evidence], {"instances": [{"agent_id": "a1", "pid": 1234}]},
    )
    assert any("Hash" in issue for issue in result["issues"])


def test_five_instances_are_covered_in_bounded_batches(client: TestClient):
    payload = _payload("service-a 延迟升高，覆盖全部实例")
    payload["budget"] = {"max_parallel_probes": 2, "max_medium_risk_probes": 0}
    for index in range(2, 6):
        agent_id = f"a{index}"
        host_id = f"host-{index}"
        repo.register_agent(agent_id, host_id, f"10.0.0.{index}", capabilities=["sys_metrics"])
        payload["context"]["instances"].append({
            "service_id": "service-a", "instance_id": f"service-a-{index}",
            "host_id": host_id, "agent_id": agent_id, "pid": 1200 + index,
            "environment": "production",
        })
    detail = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert len(detail["coverage"]) == 5
    observed_active = []
    while detail["status"] not in {"COMPLETED", "PARTIAL_COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED"}:
        active = [
            task for task in repo.tasks.values()
            if task.request_params.get("options", {}).get("diagnosis_id") == detail["diagnosis_id"]
            and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING}
        ]
        observed_active.append(len(active))
        assert len(active) <= 2
        for task in active:
            _finish_sys_metrics_task(task.id, _normal_summary())
        detail = client.get(f"/api/v1/diagnoses/{detail['diagnosis_id']}").json()["data"]
    assert max(observed_active) == 2
    assert {item["status"] for item in detail["coverage"]} == {"COMPLETED"}


def test_concurrent_approval_creates_one_task(client: TestClient):
    detail = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    task_id = detail["child_task_ids"][0]
    repo.transition_task(task_id, TaskStatus.RUNNING, "accepted", Actor.SERVER)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    repo.transition_task(task_id, TaskStatus.DONE, "no artifact", Actor.ANALYZER)
    waiting = client.get(f"/api/v1/diagnoses/{detail['diagnosis_id']}").json()["data"]
    r2 = next(item for item in waiting["probes"] if item["risk_level"] == "R2")
    request = ApprovalRequest(step_id=r2["step_id"], decision="approve", approver_id="operator")

    def approve_once():
        try:
            return diagnosis_orchestrator.approve(detail["diagnosis_id"], request)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: approve_once(), range(2)))
    matching = [
        task for task in repo.tasks.values()
        if task.request_params.get("options", {}).get("diagnosis_step_id") == r2["step_id"]
    ]
    assert len(matching) == 1


def test_concurrent_advance_writes_one_conclusion(client: TestClient):
    detail = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    _finish_sys_metrics_task(detail["child_task_ids"][0], _normal_summary())
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: diagnosis_orchestrator.advance(detail["diagnosis_id"]), range(2)))
    stored = diagnosis_orchestrator.store.get_session(detail["diagnosis_id"])
    assert stored is not None
    assert len(stored["conclusion_versions"]) == 1


def test_remote_artifact_falls_back_to_object_storage_when_agent_path_is_missing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        orchestrator_module.storage,
        "read_object_bytes",
        lambda bucket, key: b'{"avg_cpu_user_pct": 88.0}',
    )

    value = diagnosis_orchestrator._read_artifact_json({
        "artifact_type": "sys_metrics",
        "bucket": "mini-drop",
        "object_key": "tasks/task-remote/sys_metrics.json",
        "local_path": str(tmp_path / "worker-only" / "sys_metrics.json"),
    })

    assert value == {"avg_cpu_user_pct": 88.0}


def test_runtime_flamegraph_is_imported_as_semantic_profile_evidence():
    artifacts = diagnosis_orchestrator._structured_artifacts([{
        "artifact_type": "java_flamegraph_html",
        "content_type": "text/html",
        "object_key": "tasks/task-java/java_flamegraph.html",
        "size_bytes": 57094,
        "metadata": {"schema_version": "java_async.v1", "event": "cpu"},
    }])

    assert len(artifacts) == 1
    artifact_type, value, _ = artifacts[0]
    assert artifact_type == "java_flamegraph_html"
    assert value["benchmark_evidence_tags"] == [
        "profile_hot_function", "target_cpu_profile",
    ]
    assert value["metadata"]["schema_version"] == "java_async.v1"


def test_existing_structured_evidence_uses_legal_transition_and_completes(client: TestClient):
    task_id = client.post("/api/tasks", json={
        "name": "reusable-sys-metrics",
        "agent_id": "a1",
        "target_pid": 1234,
        "collector_type": "sys_metrics",
        "duration_sec": 5,
    }).json()["data"]["task_id"]
    summary = _normal_summary()
    summary["avg_cpu_user_pct"] = 92.0
    _finish_sys_metrics_task(task_id, summary)

    response = client.post("/api/v1/diagnoses", json=_payload())

    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["status"] == "COMPLETED"
    assert len(detail["coverage"]) == 1
    assert detail["coverage"][0]["target"] == "service-a-1"
    assert detail["coverage"][0]["status"] == "COMPLETED"
    assert detail["coverage"][0]["task_id"] == task_id
    transitions = [(event["from_status"], event["to_status"]) for event in detail["events"]]
    assert ("ANALYZING_EXISTING_DATA", "ANALYZING") in transitions
    assert ("ANALYZING", "CONCLUDING") in transitions


def test_reusable_evidence_must_be_fresh_and_cover_every_target(client: TestClient):
    task_id = client.post("/api/tasks", json={
        "name": "reusable-sys-metrics",
        "agent_id": "a1",
        "target_pid": 1234,
        "collector_type": "sys_metrics",
        "duration_sec": 5,
    }).json()["data"]["task_id"]
    _finish_sys_metrics_task(task_id, _normal_summary())
    now = datetime.now(timezone.utc)

    complete_scope = {
        "instances": [{"agent_id": "a1", "pid": 1234}],
    }
    assert diagnosis_orchestrator._find_reusable_tasks(
        complete_scope, now - timedelta(minutes=30), now,
    ) == [task_id]
    assert diagnosis_orchestrator._find_reusable_tasks(
        complete_scope, now - timedelta(minutes=30), now + timedelta(minutes=5),
    ) == []

    partial_scope = {
        "instances": [
            {"agent_id": "a1", "pid": 1234},
            {"agent_id": "a2", "pid": 5678},
        ],
    }
    assert diagnosis_orchestrator._find_reusable_tasks(
        partial_scope, now - timedelta(minutes=30), now,
    ) == []


def test_diagnosis_waits_for_all_active_target_tasks(client: TestClient):
    repo.register_agent(
        "a2", "host-2", "10.0.0.2",
        capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
    )
    payload = _payload()
    payload["budget"] = {"max_medium_risk_probes": 0}
    payload["context"]["instances"].append({
        "service_id": "service-b",
        "instance_id": "service-b-1",
        "host_id": "host-2",
        "agent_id": "a2",
        "pid": 5678,
        "environment": "production",
    })
    payload["context"]["dependencies"] = [{
        "source_service": "service-a",
        "target_service": "service-b",
        "relation": "CALLS",
    }]
    created = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    diagnosis_id = created["diagnosis_id"]
    task_ids = [
        item["task_id"]
        for item in created["probes"]
        if item["probe_id"] == "host_process_metrics"
    ]
    assert len(task_ids) == 2

    _finish_sys_metrics_task(task_ids[0], _normal_summary())
    collecting = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    assert collecting["status"] == "COLLECTING"
    run_node = next(item for item in collecting["pipeline_nodes"] if item["node_name"] == "run_probes")
    assert run_node["status"] == "RUNNING"
    assert run_node["metrics"]["terminal_task_count"] == 1

    _finish_sys_metrics_task(task_ids[1], _normal_summary())
    completed = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    assert completed["status"] == "INSUFFICIENT_EVIDENCE"
    assert len(completed["evidence"]) == 4


def _finish_sys_metrics_task(task_id: str, summary: dict):
    repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    artifact_ids = repo.add_artifacts(task_id, [{
        "artifact_type": "sys_metrics",
        "object_key": f"tasks/{task_id}/sys_metrics.json",
        "metadata": {
            "data": {
                "sample_count": 10,
                "summary": summary,
            },
        },
    }])
    for artifact_id in artifact_ids:
        repo.mark_artifact_integrity(artifact_id, "VERIFIED", "test fixture verified")
    repo.transition_task(task_id, TaskStatus.DONE, "analysis complete", Actor.ANALYZER)


def _normal_summary() -> dict:
    return {
        "avg_cpu_user_pct": 18.0,
        "avg_cpu_sys_pct": 4.0,
        "avg_cpu_iowait_pct": 1.0,
        "load1m": 0.8,
        "thread_count": 20,
        "thread_trend": "stable",
        "fd_count": 20,
        "fd_trend": "stable",
        "fd_max": 25,
        "vmrss_mb": 200,
        "vmrss_mb_max": 220,
        "ctx_nonvoluntary_rate": 10,
        "net_rx_kbps": 10,
        "net_tx_kbps": 10,
    }


def _completed_baseline_task(*, pid: int = 1234):
    task = repo.create_task(CreateTaskRequest(
        name="controlled pre-incident baseline",
        agent_id="a1",
        target_pid=pid,
        collector_type="sys_metrics",
        sample_rate=10,
        duration_sec=5,
    ))
    _finish_sys_metrics_task(task.id, _normal_summary())
    return repo.tasks[task.id]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def test_explicit_baseline_task_is_bound_as_immutable_baseline_snapshot(client: TestClient):
    baseline = _completed_baseline_task()
    incident_start = _aware(baseline.finished_at) + timedelta(seconds=1)
    payload = _payload()
    payload["context"]["time_range"] = {
        "start": incident_start.isoformat(),
        "end": (incident_start + timedelta(minutes=5)).isoformat(),
        "source": "request_context",
    }
    payload["baseline_task_ids"] = [baseline.id]

    response = client.post("/api/v1/diagnoses", json=payload)
    assert response.status_code == 200, response.json()
    detail = response.json()["data"]
    baseline_snapshots = [
        item for item in detail["evidence_snapshots"]
        if item["evidence_role"] == "baseline"
    ]
    assert len(baseline_snapshots) == 1
    assert baseline_snapshots[0]["task_id"] == baseline.id
    assert detail["baseline_snapshot_id"] == baseline_snapshots[0]["snapshot_id"]
    assert all(
        item["evidence_role"] == "baseline"
        for item in detail["evidence"]
        if item["derived_artifact_ref"] in {
            f"task:{baseline.id}",
            f"tasks/{baseline.id}/sys_metrics.json",
        }
    )


def test_baseline_task_from_another_target_is_rejected(client: TestClient):
    baseline = _completed_baseline_task(pid=9999)
    incident_start = _aware(baseline.finished_at) + timedelta(seconds=1)
    payload = _payload()
    payload["context"]["time_range"] = {
        "start": incident_start.isoformat(),
        "end": (incident_start + timedelta(minutes=5)).isoformat(),
        "source": "request_context",
    }
    payload["baseline_task_ids"] = [baseline.id]

    response = client.post("/api/v1/diagnoses", json=payload)
    assert response.status_code == 400, response.json()
    assert "目标与诊断范围不一致" in response.json()["detail"]


def test_task_inside_incident_window_cannot_be_claimed_as_baseline(client: TestClient):
    baseline = _completed_baseline_task()
    finished_at = _aware(baseline.finished_at)
    incident_start = finished_at - timedelta(seconds=1)
    payload = _payload()
    payload["context"]["time_range"] = {
        "start": incident_start.isoformat(),
        "end": (finished_at + timedelta(minutes=5)).isoformat(),
        "source": "request_context",
    }
    payload["baseline_task_ids"] = [baseline.id]

    response = client.post("/api/v1/diagnoses", json=payload)
    assert response.status_code == 400, response.json()
    assert "不在事故时间窗之前" in response.json()["detail"]


def test_baseline_binding_requires_explicit_incident_window(client: TestClient):
    baseline = _completed_baseline_task()
    payload = _payload()
    payload["baseline_task_ids"] = [baseline.id]

    response = client.post("/api/v1/diagnoses", json=payload)
    assert response.status_code == 400, response.json()
    assert "显式提供事故 time_range" in response.json()["detail"]


def test_stable_rss_and_fd_near_observed_max_are_not_pressure():
    summary = _normal_summary()
    summary.update({
        "vmrss_mb": 200,
        "vmrss_mb_max": 201,
        "fd_count": 20,
        "fd_max": 20,
    })
    flags = orchestrator_module._pressure_flags(summary, {})
    assert flags["memory"] is False
    assert flags["fd"] is False


def _sys_metric_probe_by_instance(data: dict) -> dict:
    return {
        item["target"]["instance_id"]: item
        for item in data["probes"]
        if item["probe_id"] == "host_process_metrics"
    }


class TestDiagnosisSessionAPI:
    def test_missing_instance_mapping_requires_scope_confirmation(self, client: TestClient):
        response = client.post("/api/v1/diagnoses", json={
            "query": "服务 service-a 为什么变慢",
            "context": {"service_id": "service-a", "environment": "production"},
        })
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "NEEDS_SCOPE_CONFIRMATION"
        assert data["child_task_ids"] == []
        assert data["normalized_intent"]["ambiguities"] == ["service_instance_mapping"]
        assert data["latest_conclusion"]["diagnostic_commands"]
        assert all(cmd["auto_execute"] is False for cmd in data["latest_conclusion"]["diagnostic_commands"])

    def test_create_schedules_only_registered_low_risk_probe(self, client: TestClient):
        response = client.post("/api/v1/diagnoses", json=_payload())
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "COLLECTING"
        assert len(data["child_task_ids"]) == 1
        probes = {item["probe_id"]: item for item in data["probes"]}
        assert probes["host_process_metrics"]["status"] in {"SCHEDULED", "RUNNING"}
        assert "process_cpu_profile" not in probes
        assert data["coverage"][0]["status"] in {"SCHEDULED", "RUNNING"}

        task = repo.tasks[data["child_task_ids"][0]]
        assert task.collector_type == "sys_metrics"
        assert task.request_params["options"]["registered_probe"] is True
        assert task.request_params["options"]["diagnosis_step_id"].startswith("step_")

    def test_r2_probe_requires_explicit_single_execution_approval(self, client: TestClient):
        data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
        task_id = data["child_task_ids"][0]
        repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
        repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
        repo.transition_task(task_id, TaskStatus.DONE, "no structured output", Actor.ANALYZER)
        waiting = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        r2 = next(item for item in waiting["probes"] if item["risk_level"] == "R2")
        approved = client.post(
            f"/api/v1/diagnoses/{data['diagnosis_id']}/approvals",
            json={
                "step_id": r2["step_id"],
                "decision": "approve",
                "scope": "single_execution",
                "approver_id": "operator-1",
            },
        )
        assert approved.status_code == 200
        detail = approved.json()["data"]
        approved_probe = next(item for item in detail["probes"] if item["step_id"] == r2["step_id"])
        assert approved_probe["approved_by"] == "operator-1"
        assert approved_probe["task_id"]
        assert detail["budget_used"]["medium_risk_probes"] == 1
        assert repo.tasks[approved_probe["task_id"]].collector_type == "perf_cpu"

    def test_completed_probe_produces_evidence_linked_candidate(self, client: TestClient):
        payload = _payload()
        payload["evaluation_oracle"] = {
            "case_id": "cpu-hotspot-001",
            "expected_instance_id": "service-a-1",
            "expected_location_type": "self",
            "expected_domain_type": "cpu",
            "expected_classification": "self_code_or_process_pressure",
        }
        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        assert data["evaluation_oracle"]["case_id"] == "cpu-hotspot-001"
        assert "evaluation_oracle" not in data["normalized_intent"]
        task_id = data["child_task_ids"][0]
        repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
        repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
        repo.add_artifacts(task_id, [{
            "artifact_type": "sys_metrics",
            "object_key": f"tasks/{task_id}/sys_metrics.json",
            "metadata": {
                "data": {
                    "sample_count": 10,
                    "summary": {
                        "avg_cpu_user_pct": 92.0,
                        "avg_cpu_sys_pct": 5.0,
                        "avg_cpu_iowait_pct": 1.0,
                        "load1m": 8.0,
                        "thread_count": 20,
                        "thread_trend": "stable",
                        "fd_count": 20,
                        "fd_trend": "stable",
                        "fd_max": 25,
                        "vmrss_mb": 200,
                        "vmrss_mb_max": 210,
                        "ctx_nonvoluntary_rate": 10,
                        "net_rx_kbps": 10,
                        "net_tx_kbps": 10,
                    },
                },
            },
        }])
        repo.transition_task(task_id, TaskStatus.DONE, "analysis complete", Actor.ANALYZER)

        detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assert detail["status"] == "COMPLETED"
        assert detail["latest_conclusion"]["root_cause_candidates"]
        assert detail["latest_conclusion"]["cluster_assessment"]["evidence_refs"]
        assert detail["latest_conclusion"]["diagnostic_commands"]
        assert all(cmd["auto_execute"] is False for cmd in detail["latest_conclusion"]["diagnostic_commands"])
        candidate = detail["latest_conclusion"]["root_cause_candidates"][0]
        assert candidate["confidence_level"] in {"低", "中", "高"}
        assert candidate["evidence_refs"]
        evidence_ids = {item["evidence_id"] for item in detail["evidence"]}
        assert set(candidate["evidence_refs"]).issubset(evidence_ids)
        assert all(item["integrity_hash"].startswith("sha256:") for item in detail["evidence"])
        assert len(detail["evidence_snapshots"]) == 1
        snapshot = detail["evidence_snapshots"][0]
        assert snapshot["evidence_role"] == "incident"
        assert snapshot["task_id"] == task_id
        assert set(snapshot["evidence_refs"]) == evidence_ids
        assert snapshot["time_range"]["start"]
        assert snapshot["time_range"]["end"]
        assert snapshot["integrity_hash"].startswith("sha256:")
        assert all(item["status"] != "WAITING_APPROVAL" for item in detail["probes"])
        assert len(detail["pipeline_nodes"]) == 12
        assert detail["latest_conclusion"]["verification"]["status"] == "passed"
        assert detail["latest_conclusion"]["findings"]
        assert detail["latest_conclusion"]["knowledge_refs"]
        evaluation = detail["latest_conclusion"]["evaluation"]
        assert evaluation["case_id"] == "cpu-hotspot-001"
        assert evaluation["oracle_isolated"] is True
        assert evaluation["exact_match"] is True
        assert evaluation["score_pct"] == 100.0
        assert evaluation["matched_count"] == evaluation["specified_count"] == 4
        actions = detail["latest_conclusion"]["actions"]
        assert actions
        assert all(action["action_type"] in {"inspect", "collect", "manual_remediation"} for action in actions)
        assert all(action["rendered_command"] == action["command"] for action in actions)
        assert all(action["auto_execute"] is False for action in actions)
        recommendations = detail["latest_conclusion"]["recommendations"]
        assert {item["category"] for item in recommendations} == {
            "mitigation", "optimization", "validation",
        }
        assert all(item["detail"] for item in recommendations)
        assert all(set(item["evidence_refs"]).issubset(evidence_ids) for item in recommendations)
        graph = detail["hypothesis_graph"]
        assert graph["updated_at"]
        assert graph["edges"]
        assert any(item["status"] == "SUPPORTED" for item in graph["hypotheses"])
        assert all(len(item["history"]) >= 2 for item in graph["hypotheses"])
        assert all("evidence_score" in item for item in graph["hypotheses"])
        assert graph["open_world_state"] in {"EXPLORING", "EXPLAINED"}
        assert set(graph["unexplained_evidence_refs"]).issubset(evidence_ids)
        assert set(graph["new_hypothesis_request"]["evidence_refs"]).issubset(evidence_ids)

    def test_analysis_strategy_is_persisted_and_changes_probe_plan(self, client: TestClient):
        decision_tree = _payload()
        decision_tree["analysis_strategy"] = "DECISION_TREE"
        tree_detail = client.post("/api/v1/diagnoses", json=decision_tree).json()["data"]
        assert tree_detail["normalized_intent"]["analysis_strategy"] == "DECISION_TREE"
        assert tree_detail["planner_version"].endswith(":decision_tree")
        assert any(
            item["risk_level"] == "R2" and item["status"] == "WAITING_APPROVAL"
            for item in tree_detail["probes"]
        )

        exploratory = _payload()
        exploratory["analysis_strategy"] = "EXPLORATORY"
        exploratory_detail = client.post("/api/v1/diagnoses", json=exploratory).json()["data"]
        assert exploratory_detail["normalized_intent"]["analysis_strategy"] == "EXPLORATORY"
        assert {
            item["probe_id"] for item in exploratory_detail["probes"]
        } >= {"host_process_metrics", "process_memory_map"}

    def test_rejected_deep_probe_can_end_as_insufficient_evidence(self, client: TestClient):
        data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
        task_id = data["child_task_ids"][0]
        repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
        repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
        repo.transition_task(task_id, TaskStatus.DONE, "no structured output", Actor.ANALYZER)
        waiting = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assert waiting["status"] == "WAITING_APPROVAL"
        repeated = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}")
        assert repeated.status_code == 200
        assert repeated.json()["data"]["status"] == "WAITING_APPROVAL"
        r2 = next(item for item in waiting["probes"] if item["risk_level"] == "R2")

        rejected = client.post(
            f"/api/v1/diagnoses/{data['diagnosis_id']}/approvals",
            json={"step_id": r2["step_id"], "decision": "reject", "approver_id": "operator-1"},
        )
        assert rejected.status_code == 200
        detail = rejected.json()["data"]
        assert detail["status"] == "INSUFFICIENT_EVIDENCE"
        assert detail["latest_conclusion"]["confidence_level"] == "不可判断"

    def test_staging_falsification_round_requires_approval_and_recalculates(self, client: TestClient):
        payload = _payload()
        payload["budget_profile"] = "staging"
        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]

        first_task_id = data["child_task_ids"][0]
        cpu_hot = _normal_summary()
        cpu_hot.update({
            "avg_cpu_user_pct": 91.0,
            "avg_cpu_sys_pct": 5.0,
            "load1m": 10.0,
        })
        _finish_sys_metrics_task(first_task_id, cpu_hot)

        waiting = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assert waiting["status"] == "WAITING_APPROVAL"
        assert waiting["budget_used"]["analysis_rounds"] == 1
        assert len(waiting["conclusion_versions"]) == 1
        falsification = next(
            probe for probe in waiting["probes"]
            if probe["evidence_purpose"] == "FALSIFY"
        )
        assert falsification["probe_id"] == "process_cpu_profile"
        assert falsification["round_index"] == 2
        assert falsification["task_id"] is None

        approved = client.post(
            f"/api/v1/diagnoses/{data['diagnosis_id']}/approvals",
            json={
                "step_id": falsification["step_id"],
                "decision": "approve",
                "approver_id": "operator-1",
            },
        ).json()["data"]
        deep_task_id = next(
            probe["task_id"] for probe in approved["probes"]
            if probe["step_id"] == falsification["step_id"]
        )
        assert deep_task_id

        repo.transition_task(deep_task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
        repo.transition_task(deep_task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
        repo.transition_task(deep_task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
        repo.add_artifacts(deep_task_id, [{
            "artifact_type": "top_json",
            "object_key": f"tasks/{deep_task_id}/top.json",
            "metadata": {
                "data": [
                    {"name": "service_a.hot_loop", "samples": 900, "percent": 90.0},
                    {"name": "runtime.scheduler", "samples": 100, "percent": 10.0},
                ],
            },
        }])
        repo.transition_task(deep_task_id, TaskStatus.DONE, "analysis complete", Actor.ANALYZER)

        completed = client.get(
            f"/api/v1/diagnoses/{data['diagnosis_id']}"
        ).json()["data"]
        assert completed["status"] == "COMPLETED"
        assert completed["budget_used"]["analysis_rounds"] == 2
        assert completed["budget_used"]["falsification_probes"] == 1
        assert len(completed["conclusion_versions"]) == 2
        assert completed["latest_conclusion"]["cluster_assessment"]["classification"] == (
            "self_code_or_process_pressure"
        )
        event_types = [event["event_type"] for event in completed["events"]]
        assert "falsification_round_planned" in event_types
        assert event_types.count("diagnosis_round_completed") == 2
        assert "diagnosis_stop_condition_met" in event_types
        metrics = client.get("/api/metrics").text
        assert "mini_drop_ai_diagnosis_rounds_total" in metrics
        assert (
            'mini_drop_ai_diagnosis_stop_conditions_total'
            '{reason="max_diagnosis_rounds_reached"}'
        ) in metrics

    def test_unknown_fields_are_rejected(self, client: TestClient):
        payload = _payload()
        payload["context"]["shell"] = "rm -rf /"
        response = client.post("/api/v1/diagnoses", json=payload)
        assert response.status_code == 422

    def test_service_allowlist_is_enforced(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_ALLOWED_SERVICES", "service-b")
        response = client.post("/api/v1/diagnoses", json=_payload())
        assert response.status_code == 403

    def test_requested_budget_cannot_exceed_policy_profile(self, client: TestClient):
        payload = _payload()
        payload["budget"] = {
            "max_hosts": 20,
            "max_service_instances": 100,
            "max_topology_hops": 3,
            "max_duration_minutes": 60,
            "max_parallel_probes": 10,
            "max_artifact_size_mb": 4096,
            "max_model_calls": 30,
            "max_medium_risk_probes": 5,
            "max_total_probe_cpu_seconds": 3600,
        }
        detail = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        assert detail["resource_budget"]["max_hosts"] == 5
        assert detail["resource_budget"]["max_parallel_probes"] == 3
        assert detail["resource_budget"]["max_medium_risk_probes"] == 1
        assert detail["resource_budget"]["max_diagnosis_rounds"] == 1

    def test_probe_registry_exposes_no_shell_command(self, client: TestClient):
        probes = client.get("/api/v1/probes").json()["data"]
        assert probes
        assert all("command" not in probe for probe in probes)
        assert {probe["risk_level"] for probe in probes}.issubset({"R0", "R1", "R2", "R3"})

    def test_same_host_noisy_neighbor_assessment_uses_multiple_agents(self, client: TestClient):
        repo.register_agent(
            "a2", "host-1", "10.0.0.2",
            capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
        )
        payload = _payload("service-a 变慢，判断是不是被同宿主其他服务影响")
        payload["context"]["instances"].append({
            "service_id": "service-b",
            "instance_id": "service-b-1",
            "host_id": "host-1",
            "agent_id": "a2",
            "pid": 4321,
            "environment": "production",
        })

        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        probes = {
            item["target"]["instance_id"]: item
            for item in data["probes"]
            if item["probe_id"] == "host_process_metrics"
        }
        assert set(probes) == {"service-a-1", "service-b-1"}

        noisy_summary = _normal_summary()
        noisy_summary.update({
            "avg_cpu_user_pct": 86.0,
            "avg_cpu_sys_pct": 9.0,
            "avg_cpu_iowait_pct": 24.0,
            "load1m": 9.0,
        })
        _finish_sys_metrics_task(probes["service-a-1"]["task_id"], _normal_summary())
        _finish_sys_metrics_task(probes["service-b-1"]["task_id"], noisy_summary)

        detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assessment = detail["latest_conclusion"]["cluster_assessment"]
        assert detail["status"] == "COMPLETED"
        assert assessment["classification"] == "same_host_noisy_neighbor"
        assert assessment["confidence_level"] in {"中", "高"}
        assert len(assessment["compared_targets"]) == 2
        evidence_ids = {item["evidence_id"] for item in detail["evidence"]}
        assert set(assessment["evidence_refs"]).issubset(evidence_ids)
        commands = detail["latest_conclusion"]["diagnostic_commands"]
        assert any(cmd["risk_level"] == "R2" and cmd["requires_approval"] for cmd in commands)
        assert all(cmd["execution_policy"] == "human_review_required" for cmd in commands)
        assert all(cmd["approval_policy"] == "single_execution" for cmd in commands if cmd["risk_level"] == "R2")

    def test_shared_io_wait_prefers_host_contention_over_generic_neighbor(self, client: TestClient):
        repo.register_agent(
            "a2", "host-1", "10.0.0.2",
            capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
        )
        payload = _payload("service-a 变慢，检查同宿主 I/O 争抢")
        payload["context"]["instances"].append({
            "service_id": "service-b",
            "instance_id": "service-b-1",
            "host_id": "host-1",
            "agent_id": "a2",
            "pid": 4321,
            "environment": "production",
        })

        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        probes = _sys_metric_probe_by_instance(data)
        target_io = _normal_summary()
        target_io.update({"avg_cpu_iowait_pct": 28.0, "load1m": 6.0})
        neighbor_io = _normal_summary()
        neighbor_io.update({"avg_cpu_iowait_pct": 34.0, "load1m": 7.0})
        _finish_sys_metrics_task(probes["service-a-1"]["task_id"], target_io)
        _finish_sys_metrics_task(probes["service-b-1"]["task_id"], neighbor_io)

        detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assessment = detail["latest_conclusion"]["cluster_assessment"]
        assert detail["status"] == "COMPLETED"
        assert assessment["classification"] == "host_resource_contention"
        assert any(target["pressure"]["io_wait"] for target in assessment["compared_targets"])
        assert any(cmd["command_id"] == "cmd_io_latency" for cmd in detail["latest_conclusion"]["diagnostic_commands"])

    def test_downstream_pressure_is_reported_as_root_cause_node_not_first_alert(self, client: TestClient):
        repo.register_agent(
            "a2", "host-2", "10.0.0.2",
            capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
        )
        payload = _payload("service-a 延迟升高，逐层检查调用链真正根因")
        payload["context"]["instances"].append({
            "service_id": "service-b",
            "instance_id": "service-b-1",
            "host_id": "host-2",
            "agent_id": "a2",
            "pid": 4321,
            "environment": "production",
        })
        payload["context"]["dependencies"] = [{
            "source_service": "service-a",
            "target_service": "service-b",
            "relation": "CALLS",
            "confidence": "high",
            "source": "test_topology",
        }]

        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        probes = _sys_metric_probe_by_instance(data)
        downstream_hot = _normal_summary()
        downstream_hot.update({"avg_cpu_user_pct": 91.0, "avg_cpu_sys_pct": 6.0, "load1m": 12.0})
        _finish_sys_metrics_task(probes["service-a-1"]["task_id"], _normal_summary())
        _finish_sys_metrics_task(probes["service-b-1"]["task_id"], downstream_hot)

        detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assessment = detail["latest_conclusion"]["cluster_assessment"]
        assert detail["status"] == "COMPLETED"
        assert assessment["classification"] == "downstream_dependency"
        assert "service-b" in detail["target_scope"]["downstream_service_ids"]
        assert any(
            target["service_id"] == "service-b" and target["pressure"]["cpu"]
            for target in assessment["compared_targets"]
        )
        assert any(item["hypothesis"] == "same_host_noisy_neighbor" for item in assessment["ruled_out"])
        evidence_ids = {item["evidence_id"] for item in detail["evidence"]}
        assert set(assessment["evidence_refs"]).issubset(evidence_ids)
