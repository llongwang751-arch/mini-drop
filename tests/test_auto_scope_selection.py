from types import SimpleNamespace

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.schemas import PreviewToolCallRequest
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


def test_explicit_go_query_can_narrow_a_generically_named_binary() -> None:
    diagnosis = SimpleNamespace(
        query="请用 Golang pprof 检查 orders 的 CPU",
        target_json={
            "service": "orders",
            "process_binding": {"executable_identity": "/srv/orders"},
        },
    )

    assert _diagnosis_runtime_family(diagnosis) == "GO"


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
