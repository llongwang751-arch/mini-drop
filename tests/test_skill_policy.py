from types import SimpleNamespace

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight import service
from server.app.drop_insight.schemas import CreateDiagnosisRequestV2, RunPlannerRequest
from server.app.drop_insight.service import (
    _apply_active_planner_skill,
    create_diagnosis,
)
from server.app.models import (
    DiagnosticSkillModel,
    DropInsightEventModel,
    DropInsightSessionModel,
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


def test_skill_policy_defaults_to_auto_and_is_persisted() -> None:
    diagnosis = create_diagnosis(
        CreateDiagnosisRequestV2(query="Python CPU hotspot investigation")
    )

    assert diagnosis.skill_policy == "AUTO"
    assert diagnosis.to_dict()["skill_policy"] == "AUTO"


def test_disabled_skill_policy_is_persisted() -> None:
    diagnosis = create_diagnosis(
        CreateDiagnosisRequestV2(
            query="Python CPU hotspot investigation",
            skill_policy="DISABLED",
        )
    )

    assert diagnosis.skill_policy == "DISABLED"
    assert diagnosis.to_dict()["skill_policy"] == "DISABLED"


def test_disabled_policy_bypasses_skill_retrieval(monkeypatch) -> None:
    calls = []

    def fake_apply(*args, **kwargs):
        calls.append((args, kwargs))
        return {"applied": True, "skill_id": "published-skill"}

    monkeypatch.setattr(
        "server.app.drop_insight.skill_evolution.apply_active_skill", fake_apply
    )
    monkeypatch.setattr(service, "_record_skill_route_decision", lambda *_a, **_k: None)
    plan = {"category": "PYTHON_RUNTIME", "tool_name": "start_pyspy_profile"}

    disabled = _apply_active_planner_skill(
        SimpleNamespace(skill_policy="DISABLED"), "diag-control", plan, {}
    )
    enabled = _apply_active_planner_skill(
        SimpleNamespace(skill_policy="AUTO"), "diag-treatment", plan, {}
    )

    assert disabled is None
    assert enabled == {"applied": True, "skill_id": "published-skill"}
    assert [call[0][0] for call in calls] == ["diag-treatment"]
    assert calls[0][1] == {
        "round_index": None,
        "phase": "INITIAL_PLAN",
        "attempted_tools": None,
        "available_tools": None,
        "reuse_existing": True,
        "return_decision": True,
    }


def test_skill_round_metadata_and_full_instructions_are_forwarded(monkeypatch) -> None:
    observed = {}
    instructions = {
        "name": "python-runtime-diagnosis",
        "body": "## 证据要求\n本次必须重新取证。",
        "sections": {"证据要求": "本次必须重新取证。"},
        "content_sha256": "b" * 64,
    }

    def fake_apply(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return {
            "applied": True,
            "state": "REUSED",
            "skill_id": "skill-python-v1",
            "selected_tool": "start_continuous_profile",
            "skill_instructions": instructions,
        }

    monkeypatch.setattr(
        "server.app.drop_insight.skill_evolution.apply_active_skill",
        fake_apply,
    )
    monkeypatch.setattr(
        service,
        "_record_skill_route_decision",
        lambda diagnosis_id, decision, **kwargs: observed.update(
            {
                "event_diagnosis_id": diagnosis_id,
                "event_decision": decision,
                "event_kwargs": kwargs,
            }
        ),
    )
    plan = {
        "category": "INSUFFICIENT_EVIDENCE_REPLAN",
        "tool_name": "collect_sys_metrics",
    }

    result = _apply_active_planner_skill(
        SimpleNamespace(skill_policy="AUTO"),
        "insight-round-skill",
        plan,
        {"environment": "demo"},
        round_index=3,
        phase="INSUFFICIENT_EVIDENCE_REPLAN",
        attempted_tools={"collect_sys_metrics", "start_pyspy_profile"},
        available_tools=["start_continuous_profile"],
        reuse_existing=True,
    )

    assert result is not None
    assert result["skill_instructions"] == instructions
    assert observed["kwargs"] == {
        "round_index": 3,
        "phase": "INSUFFICIENT_EVIDENCE_REPLAN",
        "attempted_tools": {"collect_sys_metrics", "start_pyspy_profile"},
        "available_tools": ["start_continuous_profile"],
        "reuse_existing": True,
        "return_decision": True,
    }
    assert observed["event_kwargs"] == {
        "round_index": 3,
        "phase": "INSUFFICIENT_EVIDENCE_REPLAN",
    }


def test_skill_route_events_are_compact_and_idempotent() -> None:
    diagnosis = create_diagnosis(
        CreateDiagnosisRequestV2(query="Python 服务 CPU 异常")
    )
    decision = {
        "applied": True,
        "state": "ACTIVATED",
        "skill_id": "skill-python-v1",
        "skill_category": "PYTHON_RUNTIME",
        "requested_category": "CPU_HOTSPOT",
        "version": 1,
        "selected_tool": "start_pyspy_profile",
        "skill_instructions": {
            "name": "python-runtime-diagnosis",
            "body": "这个完整正文不能复制进事件",
            "source_path": "skills/python-runtime-diagnosis/SKILL.md",
            "content_sha256": "c" * 64,
            "loaded_sections": ["适用范围", "证据要求"],
            "load_mode": "FULL_REPOSITORY_SKILL_MD",
        },
    }

    for _ in range(2):
        service._record_skill_route_decision(
            diagnosis.id,
            decision,
            round_index=1,
            phase="INITIAL_PLAN",
        )

    with new_session() as session:
        events = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis.id,
                DropInsightEventModel.event_type == "skill.route_activated",
            )
            .all()
        )
        assert len(events) == 1
        payload = events[0].payload_json
        assert payload["skill_name"] == "python-runtime-diagnosis"
        assert payload["baseline_category"] == "CPU_HOTSPOT"
        assert payload["selected_category"] == "PYTHON_RUNTIME"
        assert payload["knowledge_is_evidence"] is False
        assert payload["load_mode"] == "FULL_REPOSITORY_SKILL_MD"
        assert "body" not in payload


def test_unknown_keyword_route_can_be_corrected_by_skill_before_clarification(
    monkeypatch,
) -> None:
    diagnosis = SimpleNamespace(
        id="insight-skill-corrects-unknown",
        query="订单处理表现异常，请定位真正原因",
        target_json={"service": "orders", "environment": "demo"},
        budget_json={"lats_top_k": 3},
        skill_policy="AUTO",
    )
    captured = {}

    monkeypatch.setattr(service, "get_diagnosis", lambda _id: diagnosis)
    monkeypatch.setattr(service, "list_tool_calls", lambda _id: [])
    monkeypatch.setattr(service, "list_hypotheses", lambda _id: [])
    monkeypatch.setattr(service, "_validated_target_binding", lambda *_args: object())
    monkeypatch.setattr(
        service,
        "_planner_tool_arguments",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        service,
        "_available_planner_tools",
        lambda *_args: ["collect_sys_metrics"],
    )
    monkeypatch.setattr(
        service,
        "_apply_active_planner_skill",
        lambda *_args, **_kwargs: {
            "applied": True,
            "state": "ACTIVATED",
            "skill_id": "skill-queue-v1",
            "skill_category": "QUEUE_CONGESTION",
            "category_correction": {
                "from": "UNKNOWN",
                "to": "QUEUE_CONGESTION",
            },
            "match_score": 0.88,
            "selected_tool": "collect_sys_metrics",
            "skill_instructions": {"body": "完整 Skill 正文"},
        },
    )
    monkeypatch.setattr(
        service,
        "_record_planner_knowledge_retrieval",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(service, "_successful_tool_route_priors", lambda: [])

    class PlanningObserved(RuntimeError):
        pass

    def capture_plan(**kwargs):
        captured.update(kwargs)
        raise PlanningObserved

    monkeypatch.setattr(service, "propose_hypothesis_plan", capture_plan)

    with pytest.raises(PlanningObserved):
        service.run_diagnosis_planner(
            diagnosis.id,
            RunPlannerRequest(),
        )

    assert captured["category"] == "QUEUE_CONGESTION"
    assert captured["rule_plan"]["category"] == "QUEUE_CONGESTION"
    assert captured["active_skill"]["skill_id"] == "skill-queue-v1"


@pytest.mark.parametrize(
    ("query", "expected_category", "expected_tool"),
    [
        (
            "诊断 demo 环境中的 python-hotspot 服务，先确认 CPU 异常，"
            "再定位源码级热点函数，最后用系统指标排除 I/O 和宿主机争抢；"
            "至少经过两类独立证据再下结论。",
            "PYTHON_RUNTIME",
            "start_pyspy_profile",
        ),
        (
            "诊断 demo 环境中的 python-hotspot 服务写入变慢，"
            "先用系统指标确认 I/O 方向，再采集块设备延迟，"
            "随后寻找 CPU 热点反证。",
            "IO_LATENCY",
            "start_ebpf_io_profile",
        ),
        (
            "诊断 demo 环境中进程名为 java 的 Java 服务端到端延迟升高。"
            "先检查本实例资源，再观察等待线程路径，随后对比下游响应与"
            "本地计算反证，最后验证停止故障后的恢复。",
            "DOWNSTREAM_DEPENDENCY",
            "collect_sys_metrics",
        ),
        (
            "诊断 demo 环境中的 go-hotspot 服务出现网络等待和下游慢响应。"
            "先检查端点 CPU 与系统指标，再查看 Go 阻塞栈，最后寻找本地计算"
            "热点反证并验证恢复窗口，不要只凭一次超时判断网络故障。",
            "NETWORK_DEGRADATION",
            "collect_sys_metrics",
        ),
        (
            "诊断 demo 环境中的 cpp-hotspot 服务请求延迟升高。"
            "第一轮比较 CPU、运行队列与请求延迟，第二轮用 perf/持续采样"
            "识别 socket 等待路径，第三轮排除锁竞争、同步文件 I/O 和纯计算"
            "热点，并验证关闭下游延迟后的恢复。",
            "DOWNSTREAM_DEPENDENCY",
            "collect_sys_metrics",
        ),
        (
            "诊断 demo 环境中进程名为 java 的 Java 服务文件写入变慢。"
            "第一轮检查系统 I/O 与进程写入趋势，第二轮用 eBPF 和 JVM 栈"
            "定位 FileChannel.force 路径，第三轮寻找 GC、锁竞争、CPU 热点"
            "和下游等待反证。",
            "IO_LATENCY",
            "start_ebpf_io_profile",
        ),
        (
            "诊断 demo 环境中的 cpp-hotspot 服务 CPU 持续升高。"
            "先做系统初筛，再用 perf 定位计算热点，最后寻找锁竞争、I/O "
            "等待和同机争抢反证。",
            "CPU_HOTSPOT",
            "start_perf_profile",
        ),
        (
            "诊断 demo 环境中的 cpp-hotspot 服务吞吐下降。"
            "先用系统指标区分 CPU 计算和阻塞等待，再用 perf 定位 "
            "mutex/futex 路径，最后寻找 I/O 或纯计算热点反证。",
            "LOCK_CONTENTION",
            "collect_sys_metrics",
        ),
    ],
)
def test_planner_routes_from_positive_symptom_not_counter_clause(
    monkeypatch,
    query,
    expected_category,
    expected_tool,
) -> None:
    diagnosis = SimpleNamespace(
        id=f"insight-intent-{expected_category.casefold()}",
        query=query,
        target_json={"service": "python-hotspot", "environment": "demo"},
        budget_json={"lats_top_k": 3},
        skill_policy="DISABLED",
    )
    captured = {}

    monkeypatch.setattr(service, "get_diagnosis", lambda _id: diagnosis)
    monkeypatch.setattr(service, "list_tool_calls", lambda _id: [])
    monkeypatch.setattr(service, "list_hypotheses", lambda _id: [])
    monkeypatch.setattr(service, "_validated_target_binding", lambda *_args: object())
    monkeypatch.setattr(
        service,
        "_planner_tool_arguments",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        service,
        "_available_planner_tools",
        lambda *_args: [
            "collect_sys_metrics",
            "start_ebpf_io_profile",
            "start_pyspy_profile",
            "start_perf_profile",
        ],
    )
    monkeypatch.setattr(service, "_apply_active_planner_skill", lambda *_a, **_k: None)
    monkeypatch.setattr(
        service,
        "_record_planner_knowledge_retrieval",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(service, "_successful_tool_route_priors", lambda: [])

    class PlanningObserved(RuntimeError):
        pass

    def capture_plan(**kwargs):
        captured.update(kwargs)
        raise PlanningObserved

    monkeypatch.setattr(service, "propose_hypothesis_plan", capture_plan)

    with pytest.raises(PlanningObserved):
        service.run_diagnosis_planner(diagnosis.id, RunPlannerRequest())

    assert captured["category"] == expected_category
    assert captured["rule_plan"]["category"] == expected_category
    assert captured["rule_plan"]["tool_name"] == expected_tool


def test_auto_executes_published_skill_first_tool_while_disabled_keeps_model_route(
    monkeypatch,
) -> None:
    repo = SqlRepository()
    agent_id = "agent-skill-ab-first-tool"
    repo.register_agent(
        agent_id,
        "skill-ab-host",
        "127.0.0.1",
        capabilities=["sys_metrics", "perf_cpu"],
    )
    snapshot = repo.record_process_candidate_snapshot(
        agent_id,
        {
            "generation": 1,
            "boot_id": "boot-skill-ab",
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
                    "executable_identity": "sha256:generic-cpu-demo",
                    "comm": "orders",
                    "cgroup": "/demo/orders",
                    "service_hint": "orders",
                    "instance_hint": "demo",
                    "collector_capabilities": ["sys_metrics", "perf_cpu"],
                }
            ],
        },
        received_at=now_utc(),
    )
    binding = snapshot.candidates[0].binding().to_dict()
    query = "检查 orders 服务的 CPU 热点"
    timestamp = now_utc()
    target = {
        "service": "orders",
        "environment": "demo",
        "agent_id": agent_id,
        "pid": 4242,
        "collector_capabilities": ["sys_metrics", "perf_cpu"],
        "process_binding": binding,
    }
    budget = {
        "max_duration_seconds": 300,
        "max_tool_calls": 12,
        "max_diagnosis_rounds": 4,
        "max_concurrent_tasks": 3,
        "max_hosts": 5,
        "max_artifact_bytes": 524_288_000,
        "max_risk_level": "R2",
        "lats_top_k": 3,
    }
    with new_session() as session:
        session.add(
            DiagnosticSkillModel(
                id="skill-cpu-system-baseline-v1",
                family_key="test:cpu-system-baseline",
                category="CPU_HOTSPOT",
                version=1,
                status="ACTIVE",
                source_diagnosis_ids_json=[],
                trigger_json={
                    "environment": "demo",
                    "service": "orders",
                    "source_query": query,
                    "query_terms": ["cpu", "热点", "orders"],
                },
                strategy_json={"probe_order": ["collect_sys_metrics"]},
                gate_metrics_json={"eligible": True},
                created_by="system:test",
                created_at=timestamp,
                updated_at=timestamp,
                published_at=timestamp,
            )
        )
        for diagnosis_id, skill_policy in (
            ("insight-skill-auto", "AUTO"),
            ("insight-skill-disabled", "DISABLED"),
        ):
            session.add(
                DropInsightSessionModel(
                    id=diagnosis_id,
                    query=query,
                    target_json=target,
                    time_range_json={},
                    requested_time_range_json={},
                    effective_time_range_json={},
                    mode="AUTONOMOUS",
                    skill_policy=skill_policy,
                    budget_json=budget,
                    status="UNDERSTANDING",
                    version=1,
                    clarification_questions_json=[],
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        session.commit()

    proposal_calls = []

    def fake_proposal(**kwargs):
        proposal_calls.append(
            {
                "diagnosis_id": kwargs["diagnosis_id"],
                "active_skill": kwargs["active_skill"],
            }
        )
        return {
            "tool_name": "start_perf_profile",
            "reasoning_summary": "模型仍然生成并排序首轮候选假设。",
            "hypotheses": [
                {
                    "statement": "业务计算路径可能存在稳定的 CPU 热点",
                    "expected_observations": ["原生调用栈样本集中在少数函数"],
                    "falsification_criteria": ["采样结果没有稳定热点"],
                    "rationale": "模型把原生热点排在首位。",
                    "prior_probability": 0.9,
                    "estimated_value": 0.9,
                }
            ],
        }

    monkeypatch.setattr(service, "propose_hypothesis_plan", fake_proposal)
    monkeypatch.setattr(
        service,
        "_record_planner_knowledge_retrieval",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(service, "_successful_tool_route_priors", lambda: [])
    monkeypatch.setattr(
        service,
        "_record_lats_expansion_and_selection",
        lambda _diagnosis_id, candidates, **_kwargs: {
            "node_id": candidates[0]["node_id"]
        },
    )
    monkeypatch.setattr(
        service,
        "_record_lats_action_dispatched",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "_issue_task_upload_authorizations",
        lambda *_args, **_kwargs: None,
    )

    auto = service.run_diagnosis_planner(
        "insight-skill-auto", RunPlannerRequest()
    )
    disabled = service.run_diagnosis_planner(
        "insight-skill-disabled", RunPlannerRequest()
    )

    assert auto is not None and disabled is not None
    assert auto["skill_activation"]["selected_tool"] == "collect_sys_metrics"
    assert auto["tool_call"]["tool_name"] == "collect_sys_metrics"
    assert auto["tool_call"]["status"] == "TASK_CREATED"
    assert disabled["skill_activation"] is None
    assert disabled["tool_call"]["tool_name"] == "start_perf_profile"
    assert disabled["tool_call"]["status"] == "TASK_CREATED"
    assert [item["active_skill"] is not None for item in proposal_calls] == [
        True,
        False,
    ]

    with new_session() as session:
        auto_task = session.get(TaskModel, auto["tool_call"]["task_id"])
        disabled_task = session.get(TaskModel, disabled["tool_call"]["task_id"])
        assert auto_task is not None and auto_task.collector_type == "sys_metrics"
        assert disabled_task is not None and disabled_task.collector_type == "perf_cpu"
