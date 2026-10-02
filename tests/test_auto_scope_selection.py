from types import SimpleNamespace

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.schemas import PreviewToolCallRequest, CreateDiagnosisRequestV2
from server.app.drop_insight import service
from server.app.drop_insight.service import (
    _auto_scope_service_filter,
    _diagnosis_runtime_family,
    _is_database_query,
    _query_mentions_go_runtime,
    _runtime_compatible_tools,
    _runtime_tool_is_compatible,
    _select_auto_scope_candidate,
)
from server.app.models import (
    DropInsightSessionModel,
    DropInsightToolCallModel,
    TaskModel,
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


def _candidate(binding_id: str, *, process: str, capabilities: list[str]) -> dict:
    return {
        "binding_id": binding_id,
        "service": "mini-drop-agent",
        "environment": "production",
        "instance": binding_id,
        "process": process,
        "collector_capabilities": capabilities,
        "eligible": True,
    }


def test_background_scope_respects_explicit_manual_selection(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "_auto_resolve_diagnosis_scope", lambda *args: calls.append(args))
    diagnosis = service.create_diagnosis(CreateDiagnosisRequestV2(
        query="知识库 Python 查询慢", mode="AUTONOMOUS", auto_scope=False))
    assert diagnosis.status == "NEEDS_CLARIFICATION"
    assert service.resolve_diagnosis_scope_autonomously(diagnosis.id) is False
    assert calls == []
    assert service.get_diagnosis(diagnosis.id).target_json.get("process_binding") is None


def test_background_scope_retries_when_user_enabled_discovery(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "_auto_resolve_diagnosis_scope", lambda *args: calls.append(args))
    diagnosis = service.create_diagnosis(CreateDiagnosisRequestV2(
        query="知识库 Python 查询慢", mode="AUTONOMOUS", auto_scope=True))
    assert len(calls) == 1
    service.resolve_diagnosis_scope_autonomously(diagnosis.id)
    assert len(calls) == 2


def test_ambiguous_autonomous_scope_uses_capability_aware_safe_fallback() -> None:
    discovery = {
        "candidates": [
            _candidate("binding-b", process="java", capabilities=["java_async"]),
            _candidate("binding-a", process="python", capabilities=["pyspy", "sys_metrics"]),
        ]
    }

    selected = _select_auto_scope_candidate("检查 Python 服务 CPU", discovery)

    assert selected is not None
    assert selected["binding_id"] == "binding-a"


def test_autonomous_scope_never_selects_an_ineligible_candidate() -> None:
    discovery = {
        "candidates": [
            {**_candidate("binding-ineligible", process="python", capabilities=["pyspy"]), "eligible": False},
            _candidate("binding-safe", process="generic", capabilities=["sys_metrics"]),
        ]
    }

    selected = _select_auto_scope_candidate("检查 Python 服务 CPU", discovery)

    assert selected is not None
    assert selected["binding_id"] == "binding-safe"


def test_explicit_java_process_name_binds_before_model_ranking(monkeypatch) -> None:
    discovery = {
        "candidates": [
            _candidate("binding-control", process="mini-drop-native-control", capabilities=["java_async"]),
            _candidate("binding-java", process="java", capabilities=["java_async", "sys_metrics"]),
        ]
    }

    def must_not_call_model(**_kwargs):
        raise AssertionError("unique explicit process name must not be overridden by the model")

    monkeypatch.setattr(
        "server.app.drop_insight.diagnosis_agent.select_scope_with_diagnosis_agent",
        must_not_call_model,
    )

    selected = _select_auto_scope_candidate(
        "诊断 demo 环境中进程名为 java 的 Java 服务出现 GC 和延迟抖动",
        discovery,
        diagnosis_id="diagnosis-java-scope",
    )

    assert selected is not None
    assert selected["binding_id"] == "binding-java"


def test_duplicate_explicit_process_name_requires_clarification(monkeypatch):
    monkeypatch.setattr("server.app.ai_provider.get_ai_settings", lambda: SimpleNamespace(nlp_enabled=True, api_key="test-only"))
    calls = []
    monkeypatch.setattr("server.app.drop_insight.diagnosis_agent.select_scope_with_diagnosis_agent",
                        lambda **kwargs: calls.append(kwargs) or kwargs["candidates"][0])
    rows = [_candidate(name, process="java", capabilities=["java_async"])
            for name in ["control", "other"]]
    assert _select_auto_scope_candidate("诊断进程名为 java 的延迟", {"candidates": rows},
                                        diagnosis_id="ambiguous-java") is None
    assert calls == []


def _seed_java_host(agent, pid, *, age=0, count=1, generation=1):
    from datetime import timedelta
    repo = SqlRepository()
    repo.register_agent(agent, agent, "127.0.0.1", capabilities=["sys_metrics", "java_async"])
    repo.record_process_candidate_snapshot(agent, {
        "generation": generation, "boot_id": "boot-" + agent, "observed_at_unix_ms": 1,
        "complete": True, "truncated": False, "error": "",
        "candidates": [dict(pid=pid+i, process_start_ticks=101+i,
            pid_namespace_inode=202, namespace_pid=10+i, executable_identity="/usr/bin/java",
            comm="java", cgroup="/lab", service_hint="", instance_hint="lab",
            collector_capabilities=["sys_metrics", "java_async"]) for i in range(count)],
    }, received_at=now_utc() - timedelta(seconds=age))


@pytest.mark.parametrize("auto_scope", [False, True])
def test_requested_agent_narrows_discovery_and_automatic_binding(auto_scope):
    _seed_java_host("requested-host", 500)
    _seed_java_host("other-host", 600)
    diagnosis = service.create_diagnosis(CreateDiagnosisRequestV2(
        query="诊断进程名为 java 的延迟", mode="AUTONOMOUS", auto_scope=auto_scope,
        target={"agent_id": "requested-host"}))
    if auto_scope:
        assert diagnosis.target_json["process_binding"]["agent_id"] == "requested-host"
        assert diagnosis.target_json["pid"] == 500
    else:
        discovery = service.discover_target_candidates(diagnosis.id)
        assert discovery["status"] == "READY"
        assert len(discovery["candidates"]) == 1


@pytest.mark.parametrize("mode", ["absent", "stale", "ambiguous"])
def test_unavailable_requested_instance_never_falls_back_to_other_host(mode):
    _seed_java_host("other-host", 600)
    if mode != "absent":
        _seed_java_host("requested-host", 500, age=600 if mode == "stale" else 0,
                        count=2 if mode == "ambiguous" else 1)
    diagnosis = service.create_diagnosis(CreateDiagnosisRequestV2(
        query="诊断进程名为 java 的延迟", mode="AUTONOMOUS", auto_scope=True,
        target={"agent_id": "requested-host"}))
    assert diagnosis.status == "NEEDS_CLARIFICATION"
    assert not diagnosis.target_json.get("process_binding")
    service.resolve_diagnosis_scope_autonomously(diagnosis.id)
    assert not service.get_diagnosis(diagnosis.id).target_json.get("process_binding")


def test_discovery_cannot_override_requested_agent():
    _seed_java_host("requested-host", 500)
    _seed_java_host("other-host", 600)
    diagnosis = service.create_diagnosis(CreateDiagnosisRequestV2(
        query="诊断进程名为 java 的延迟", target={"agent_id": "requested-host"}))
    with pytest.raises(ValueError, match="agent"):
        service.discover_target_candidates(diagnosis.id, agent_id="other-host")


def test_requested_agent_survives_snapshot_replacement_during_binding(monkeypatch):
    _seed_java_host("requested-host", 500)
    _seed_java_host("other-host", 600)
    clarify = service.clarify_diagnosis
    calls = []

    def replace_snapshot_once(diagnosis_id, payload, **kwargs):
        calls.append(payload.target.binding_id)
        if len(calls) == 1:
            _seed_java_host("requested-host", 501, generation=2)
        return clarify(diagnosis_id, payload, **kwargs)

    monkeypatch.setattr(service, "clarify_diagnosis", replace_snapshot_once)
    diagnosis = service.create_diagnosis(CreateDiagnosisRequestV2(
        query="诊断进程名为 java 的延迟", mode="AUTONOMOUS", auto_scope=True,
        target={"agent_id": "requested-host"}))
    assert len(calls) == 2 and calls[0] != calls[1]
    assert diagnosis.target_json["process_binding"]["agent_id"] == "requested-host"
    assert diagnosis.target_json["process_binding"]["snapshot_generation"] == 2
    assert diagnosis.target_json["pid"] == 501


def test_go_target_cannot_dispatch_python_or_jvm_profilers() -> None:
    diagnosis = SimpleNamespace(
        target_json={
            "service": "go-hotspot",
            "process_binding": {"executable_identity": "go-hotspot"},
        }
    )

    assert _diagnosis_runtime_family(diagnosis) == "GO"
    assert _runtime_compatible_tools(
        diagnosis,
        [
            "collect_sys_metrics",
            "start_pyspy_profile",
            "start_jvm_profile",
            "collect_go_profile",
            "start_perf_profile",
        ],
    ) == ["collect_sys_metrics", "collect_go_profile", "start_perf_profile"]


def test_fault_plaza_go_service_name_selects_the_go_runtime_route() -> None:
    assert _query_mentions_go_runtime(
        "诊断 demo 环境中的 go-hotspot 服务 CPU 持续升高"
    )


def test_java_runtime_probe_is_not_starved_by_generic_replan_registry_order():
    from server.app.drop_insight.service import _runtime_preferred_tools
    diagnosis = SimpleNamespace(target_json={
        "process_binding": {"executable_identity": "java"},
    })
    tools = ["start_perf_profile", "start_continuous_profile", "start_ebpf_io_profile", "start_jvm_profile"]
    assert _runtime_preferred_tools(diagnosis, tools) == [
        "start_jvm_profile", "start_perf_profile", "start_continuous_profile", "start_ebpf_io_profile",
    ]
    assert _runtime_preferred_tools(diagnosis, tools[:2]) == tools[:2]


def test_named_service_followed_by_symptom_cannot_select_diagnosis_worker(monkeypatch):
    discovery = {"candidates": [
        _candidate("worker", process="python", capabilities=["pyspy", "sys_metrics"]),
        _candidate("target", process="python-hotspot", capabilities=["pyspy", "sys_metrics"]),
    ]}
    calls = []
    def must_not_rank(**kwargs):
        calls.append(kwargs)
        raise AssertionError("explicit eligible target must bind before model ranking")
    monkeypatch.setattr("server.app.ai_provider.get_ai_settings", lambda: SimpleNamespace(nlp_enabled=True, api_key="test-only"))
    monkeypatch.setattr("server.app.drop_insight.diagnosis_agent.select_scope_with_diagnosis_agent", must_not_rank)
    selected = _select_auto_scope_candidate(
        "诊断 demo 环境中的 python-hotspot 入口请求被拒绝且吞吐下降",
        discovery, diagnosis_id="strict-load-saturation")
    assert selected["binding_id"] == "target"
    assert calls == []
    assert _auto_scope_service_filter(
        "诊断 demo 环境中的 python-hotspot 入口请求被拒绝且吞吐下降"
    ) == "python-hotspot"


def test_absent_explicit_process_does_not_select_only_unrelated_candidate():
    discovery = {"candidates": [_candidate("worker", process="python", capabilities=["sys_metrics"])]}
    assert _select_auto_scope_candidate("检查进程名为 java 的服务", discovery) is None


def test_java_process_query_does_not_create_an_invalid_service_filter() -> None:
    assert (
        _auto_scope_service_filter(
            "诊断 demo 环境中进程名为 java 的 Java 服务出现 GC 和延迟抖动"
        )
        is None
    )


def test_java_method_name_is_not_mistaken_for_a_service_filter() -> None:
    assert (
        _auto_scope_service_filter(
            "诊断 demo 环境中进程名为 java 的 Java 服务文件写入变慢，"
            "用 eBPF 和 JVM 栈定位 FileChannel.force 路径"
        )
        is None
    )


def test_explicit_hyphenated_service_name_remains_a_service_filter() -> None:
    assert (
        _auto_scope_service_filter("诊断 demo 环境中的 go-hotspot 服务 CPU 持续升高")
        == "go-hotspot"
    )


def test_lock_wait_only_selects_database_when_database_is_explicit() -> None:
    assert not _is_database_query("检查 Java GC，并排除锁等待和纯 CPU 热点")
    assert _is_database_query("检查 MySQL 锁等待")


def test_go_runtime_matcher_is_case_insensitive() -> None:
    assert _query_mentions_go_runtime("Investigate GO-HOTSPOT with PPROF")


def test_authoritative_executable_runtime_wins_over_service_label() -> None:
    diagnosis = SimpleNamespace(
        query="检查服务",
        target_json={
            "service": "python-api",
            "process_binding": {"executable_identity": "/usr/local/bin/go-hotspot"},
        },
    )

    assert _diagnosis_runtime_family(diagnosis) == "GO"
    assert not _runtime_tool_is_compatible(diagnosis, "start_pyspy_profile")
    assert not _runtime_tool_is_compatible(diagnosis, "start_jvm_profile")
    assert _runtime_tool_is_compatible(diagnosis, "collect_go_profile")


def test_prose_does_not_authorize_runtime_attach_for_unknown_binary() -> None:
    diagnosis = SimpleNamespace(
        query="请用 Golang pprof 检查 orders 的 CPU",
        target_json={
            "service": "orders",
            "process_binding": {"executable_identity": "/srv/orders"},
        },
    )

    assert _diagnosis_runtime_family(diagnosis) is None
    assert not _runtime_tool_is_compatible(diagnosis, "collect_go_profile")


def _seed_bound_go_diagnosis() -> tuple[str, str, int]:
    diagnosis_id = "insight-go-runtime-gate"
    agent_id = "agent-go-runtime-gate"
    pid = 4242
    repo = SqlRepository()
    repo.register_agent(
        agent_id,
        "go-runtime-host",
        "127.0.0.1",
        capabilities=["sys_metrics", "go_pprof", "pyspy", "java_async"],
    )
    snapshot = repo.record_process_candidate_snapshot(
        agent_id,
        {
            "generation": 1,
            "boot_id": "boot-go-runtime-gate",
            "observed_at_unix_ms": 1,
            "complete": True,
            "truncated": False,
            "error": "",
            "candidates": [
                {
                    "pid": pid,
                    "process_start_ticks": 101,
                    "pid_namespace_inode": 202,
                    "namespace_pid": pid,
                    "executable_identity": "/usr/local/bin/go-hotspot",
                    "comm": "go-hotspot",
                    "cgroup": "/demo/go-hotspot",
                    "service_hint": "python-api",
                    "instance_hint": "demo",
                    "collector_capabilities": ["sys_metrics", "go_pprof"],
                }
            ],
        },
        received_at=now_utc(),
    )
    binding = snapshot.candidates[0].binding().to_dict()
    timestamp = now_utc()
    with new_session() as session:
        session.add(
            DropInsightSessionModel(
                id=diagnosis_id,
                query="检查当前服务的 CPU 热点",
                target_json={
                    "service": "python-api",
                    "environment": "demo",
                    "agent_id": agent_id,
                    "pid": pid,
                    "process_binding": binding,
                },
                time_range_json={},
                requested_time_range_json={},
                effective_time_range_json={},
                mode="AUTONOMOUS",
                skill_policy="AUTO",
                budget_json={
                    "max_duration_seconds": 300,
                    "max_tool_calls": 12,
                    "max_concurrent_tasks": 3,
                    "max_hosts": 5,
                    "max_artifact_bytes": 524_288_000,
                    "max_risk_level": "R2",
                },
                status="PLANNING",
                version=1,
                clarification_questions_json=[],
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.commit()
    return diagnosis_id, agent_id, pid


def test_preview_runtime_gate_denies_python_probe_for_bound_go_process_without_task() -> None:
    diagnosis_id, agent_id, pid = _seed_bound_go_diagnosis()

    decision = service.preview_tool_call(
        diagnosis_id,
        PreviewToolCallRequest(
            tool_name="start_pyspy_profile",
            arguments={
                "agent_id": agent_id,
                "pid": pid,
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        ),
    )

    assert decision is not None and decision["decision"] == "DENY"
    assert decision["checks"] == [
        {"name": "RUNTIME_COMPATIBILITY", "result": "FAIL"}
    ]
    with new_session() as session:
        assert session.query(TaskModel).count() == 0


def test_execute_runtime_gate_rejects_legacy_approved_python_probe_before_task() -> None:
    diagnosis_id, agent_id, pid = _seed_bound_go_diagnosis()
    timestamp = now_utc()
    tool_call_id = "toolcall-legacy-python-for-go"
    with new_session() as session:
        session.add(
            DropInsightToolCallModel(
                id=tool_call_id,
                diagnosis_id=diagnosis_id,
                hypothesis_id=None,
                tool_name="start_pyspy_profile",
                arguments_json={
                    "agent_id": agent_id,
                    "pid": pid,
                    "duration_seconds": 15,
                    "sample_rate": 99,
                },
                policy_decision="ALLOW",
                policy_checks_json=[],
                policy_reason="模拟旧版本已经批准但尚未执行的调用",
                status="APPROVED",
                result_json={},
                budget_reservation_json={
                    "duration_seconds": 15,
                    "artifact_bytes": 16 * 1024 * 1024,
                    "agent_id": agent_id,
                    "concurrent_tasks": 1,
                },
                budget_reservation_status="RESERVED",
                effect_key="legacy:runtime-mismatch",
                requested_by="system:test",
                created_at=timestamp,
            )
        )
        session.commit()

    result = service._execute_approved_tool_call(tool_call_id)

    assert result.status == "FAILED"
    assert result.task_id is None
    assert result.result_json["error"] == "runtime_incompatible_tool"
    assert result.budget_reservation_status == "RELEASED"
    with new_session() as session:
        assert session.query(TaskModel).count() == 0
