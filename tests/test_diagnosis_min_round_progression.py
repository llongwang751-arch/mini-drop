from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight import service
from server.app.models import (
    AgentModel,
    DropInsightEventModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
)


NOW = datetime(2026, 9, 6, 6, tzinfo=timezone.utc)
DIAGNOSIS_ID = "insight-min-round-progression"
AGENT_ID = "agent-min-round-progression"


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _hypothesis(
    hypothesis_id: str,
    *,
    statement: str,
    round_index: int,
    parent_hypothesis_id: str | None,
    created_offset: int,
    status: str = "OPEN",
) -> DropInsightHypothesisModel:
    created_at = NOW + timedelta(seconds=created_offset)
    return DropInsightHypothesisModel(
        id=hypothesis_id,
        diagnosis_id=DIAGNOSIS_ID,
        statement=statement,
        expected_observations_json=["本轮真实探针产生可区分的观察"],
        falsification_criteria_json=["本轮观察与候选原因不一致"],
        status=status,
        source="MODEL",
        round_index=round_index,
        parent_hypothesis_id=parent_hypothesis_id,
        generation_reason="最少报告轮次回归",
        effect_key=f"hypothesis:{hypothesis_id}",
        created_at=created_at,
        updated_at=created_at,
    )


def _report(
    report_id: str,
    *,
    hypothesis_id: str,
    created_offset: int,
    effects_status: str,
) -> DropInsightReportModel:
    return DropInsightReportModel(
        id=report_id,
        diagnosis_id=DIAGNOSIS_ID,
        hypothesis_id=hypothesis_id,
        conclusion="当前轮次的真实证据支持候选原因。",
        confidence=900,
        evidence_refs_json=[f"evidence:{report_id}"],
        counter_evidence_refs_json=[f"control:{report_id}"],
        assumptions_json=[],
        limitations_json=[],
        next_actions_json=[],
        claims_json=[],
        verification_json={"status": "VERIFIED", "coverage_ratio": 1.0},
        effects_status=effects_status,
        effects_fencing_token=1 if effects_status == "APPLIED" else 0,
        effects_applied_at=(
            NOW + timedelta(seconds=created_offset)
            if effects_status == "APPLIED"
            else None
        ),
        created_at=NOW + timedelta(seconds=created_offset),
    )


def _completed_tool_call(
    tool_call_id: str,
    *,
    hypothesis_id: str,
    tool_name: str,
    created_offset: int,
) -> DropInsightToolCallModel:
    return DropInsightToolCallModel(
        id=tool_call_id,
        diagnosis_id=DIAGNOSIS_ID,
        hypothesis_id=hypothesis_id,
        tool_name=tool_name,
        arguments_json={"agent_id": AGENT_ID, "pid": 4242, "duration_seconds": 15},
        policy_decision="ALLOW",
        policy_checks_json=[],
        policy_reason="回归测试中的既有真实探针",
        status="COMPLETED",
        result_json={},
        budget_reservation_json={},
        budget_settlement_json={},
        budget_reservation_status="SETTLED",
        terminal_processing_status="REPORT_EFFECTS_DONE",
        effect_key=f"tool-call:{tool_call_id}",
        requested_by="system:test-fixture",
        created_at=NOW + timedelta(seconds=created_offset),
        executed_at=NOW + timedelta(seconds=created_offset),
    )


def _seed_two_reported_rounds_with_an_old_sibling() -> None:
    first = _hypothesis(
        "hypothesis-round-1",
        statement="目标进程可能存在原生调用栈热点",
        round_index=1,
        parent_hypothesis_id=None,
        created_offset=1,
        status="SUPPORTED",
    )
    second = _hypothesis(
        "hypothesis-round-2-selected",
        statement="第 2 轮：异常可能来自主机资源基线或共享资源争抢，而非单一运行时路径",
        round_index=2,
        parent_hypothesis_id=first.id,
        created_offset=2,
        status="SUPPORTED",
    )
    # This sibling is deliberately semantically identical to the deterministic
    # round-3 I/O candidate once the display-only round label is removed.  It
    # has never received a Tool Call or Report.
    old_sibling = _hypothesis(
        "hypothesis-round-2-old-sibling",
        statement="第 2 轮：异常可能来自块设备延迟或 I/O 队列，而非用户态计算热点",
        round_index=2,
        parent_hypothesis_id=first.id,
        created_offset=3,
    )

    with new_session() as session:
        session.add(
            AgentModel(
                id=AGENT_ID,
                hostname="min-round-host",
                ip_addr="127.0.0.1",
                version="test",
                os_info="linux",
                capabilities=["sys_metrics", "perf_cpu", "ebpf_io"],
                status="ONLINE",
                last_heartbeat_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            DropInsightSessionModel(
                id=DIAGNOSIS_ID,
                query="诊断 CPU 热点并完成三轮真实交叉验证",
                target_json={},
                time_range_json={},
                requested_time_range_json={},
                effective_time_range_json={},
                mode="AUTONOMOUS",
                skill_policy="AUTO",
                budget_json={
                    "min_diagnosis_rounds": 3,
                    "max_diagnosis_rounds": 4,
                    "max_tool_calls": 12,
                    "max_lats_iterations": 6,
                    "lats_top_k": 3,
                },
                status="COLLECTING_EVIDENCE",
                version=1,
                clarification_questions_json=[],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add_all([first, second, old_sibling])
        session.add_all(
            [
                _completed_tool_call(
                    "tool-call-round-1",
                    hypothesis_id=first.id,
                    tool_name="start_perf_profile",
                    created_offset=4,
                ),
                _completed_tool_call(
                    "tool-call-round-2",
                    hypothesis_id=second.id,
                    tool_name="collect_sys_metrics",
                    created_offset=5,
                ),
                _report(
                    "report-round-1",
                    hypothesis_id=first.id,
                    created_offset=6,
                    effects_status="APPLIED",
                ),
                _report(
                    "report-round-2",
                    hypothesis_id=second.id,
                    created_offset=7,
                    effects_status="PENDING",
                ),
            ]
        )
        session.commit()

    with new_session() as session:
        service._append_event(
            session,
            DIAGNOSIS_ID,
            "lats.search_started",
            "SYSTEM",
            {"round_index": 1},
            NOW,
            effect_key=f"diagnosis:{DIAGNOSIS_ID}:lats:started",
        )
        service._append_event(
            session,
            DIAGNOSIS_ID,
            "lats.candidates_expanded",
            "AGENT",
            {
                "round_index": 2,
                "candidates": [
                    {
                        "node_id": f"hypothesis:{old_sibling.id}",
                        "candidate_key": "old-io-sibling",
                        "recommended_tool": "start_ebpf_io_profile",
                        "prior": 1.0,
                        "initial_value": 0.6,
                        "rank": 0,
                    }
                ],
            },
            NOW + timedelta(microseconds=1),
            effect_key=f"diagnosis:{DIAGNOSIS_ID}:lats:expanded:2",
        )
        for iteration, hypothesis_id in (
            (1, first.id),
            (2, second.id),
        ):
            service._append_event(
                session,
                DIAGNOSIS_ID,
                "lats.node_selected",
                "AGENT",
                {
                    "iteration": iteration,
                    "round_index": iteration,
                    "tree_depth": iteration,
                    "node_id": f"hypothesis:{hypothesis_id}",
                },
                NOW + timedelta(milliseconds=iteration),
                effect_key=f"diagnosis:{DIAGNOSIS_ID}:lats:selected:{iteration}",
            )
        session.commit()


def test_minimum_report_rounds_advance_to_round_three_despite_semantic_sibling(
    monkeypatch,
) -> None:
    _seed_two_reported_rounds_with_an_old_sibling()

    monkeypatch.setattr(
        service,
        "_current_target_binding",
        lambda _diagnosis: SimpleNamespace(agent_id=AGENT_ID),
    )
    monkeypatch.setattr(
        service,
        "_record_planner_knowledge_retrieval",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(service, "_successful_tool_route_priors", lambda: [])
    monkeypatch.setattr(service, "propose_hypothesis_plan", lambda **_kwargs: None)
    monkeypatch.setattr(
        service,
        "_planner_tool_arguments",
        lambda _tool_name, _target, **_kwargs: {
            "agent_id": AGENT_ID,
            "pid": 4242,
            "duration_seconds": 15,
        },
    )
    monkeypatch.setattr(
        service,
        "_evaluate_persisted_tool_policy",
        lambda *_args, **_kwargs: {
            "decision": "REQUIRE_APPROVAL",
            "checks": [],
            "reason": "保留持久化 Tool Call，但不在单元测试中启动采集任务",
        },
    )

    with new_session() as session:
        diagnosis = session.get(DropInsightSessionModel, DIAGNOSIS_ID)
        progress = service._diagnosis_round_contract(session, diagnosis)
        assert progress == {
            "minimum": 3,
            "observed": 2,
            "round_indexes": [1, 2],
            "satisfied": False,
        }

    service._apply_report_effects("report-round-2")

    with new_session() as session:
        coverage_exhausted = any(
            (
                (event.payload_json or {}).get("reason")
                or (event.payload_json or {}).get("termination_reason")
            )
            == "COVERAGE_EXHAUSTED"
            for event in session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == DIAGNOSIS_ID)
            .all()
            if event.event_type
            in {"lats.search_terminated", "diagnosis.insufficient_evidence_finalized"}
        )
        next_call = (
            session.query(DropInsightToolCallModel)
            .filter(
                DropInsightToolCallModel.diagnosis_id == DIAGNOSIS_ID,
                DropInsightToolCallModel.effect_key
                == "report:report-round-2:insufficient:tool_call",
            )
            .one_or_none()
        )
        selected_event = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == DIAGNOSIS_ID,
                DropInsightEventModel.event_type == "lats.node_selected",
                DropInsightEventModel.effect_key
                == "report:report-round-2:insufficient:lats:selected",
            )
            .one_or_none()
        )
        next_round = (
            int((selected_event.payload_json or {}).get("iteration") or 0)
            if selected_event is not None
            else None
        )

        # The tuple exposes both regressions: today it is (True, None); merely
        # removing the premature termination selects the old round-2 sibling.
        # Its immutable birth depth stays 2, while the durable selection event
        # must record the third real observation round.
        assert (coverage_exhausted, next_round) == (False, 3)

        next_hypothesis = (
            session.get(DropInsightHypothesisModel, next_call.hypothesis_id)
            if next_call is not None
            else None
        )
        assert next_hypothesis is not None
        assert next_hypothesis.id == "hypothesis-round-2-old-sibling"
        assert next_hypothesis.round_index == 2

        diagnosis = session.get(DropInsightSessionModel, DIAGNOSIS_ID)
        assert diagnosis.status != "INSUFFICIENT_EVIDENCE"


def test_reused_skill_tool_is_attached_to_its_matching_lats_hypothesis(
    monkeypatch,
) -> None:
    parent = _hypothesis(
        "hypothesis-java-baseline",
        statement="Java 服务需要先确认系统资源基线",
        round_index=1,
        parent_hypothesis_id=None,
        created_offset=1,
        status="INCONCLUSIVE",
    )
    unknown = _hypothesis(
        "hypothesis-java-unknown",
        statement="其他未知原因（OTHER/UNKNOWN）",
        round_index=1,
        parent_hypothesis_id=None,
        created_offset=2,
    )
    with new_session() as session:
        session.add(
            AgentModel(
                id=AGENT_ID,
                hostname="java-skill-host",
                ip_addr="127.0.0.1",
                version="test",
                os_info="linux",
                capabilities=["sys_metrics", "memory_smaps", "java_async", "perf_cpu"],
                status="ONLINE",
                last_heartbeat_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            DropInsightSessionModel(
                id=DIAGNOSIS_ID,
                query="Java Full GC 与分配压力诊断，并排除纯 CPU 热点",
                target_json={"service": "java-hotspot", "environment": "demo"},
                time_range_json={},
                requested_time_range_json={},
                effective_time_range_json={},
                mode="AUTONOMOUS",
                skill_policy="AUTO",
                budget_json={"lats_top_k": 3, "max_tool_calls": 12},
                status="COLLECTING_EVIDENCE",
                version=1,
                clarification_questions_json=[],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add_all([parent, unknown])
        session.add(
            _completed_tool_call(
                "tool-call-java-baseline",
                hypothesis_id=parent.id,
                tool_name="collect_sys_metrics",
                created_offset=3,
            )
        )
        session.commit()

    monkeypatch.setattr(
        service,
        "_current_target_binding",
        lambda _diagnosis: SimpleNamespace(agent_id=AGENT_ID),
    )
    monkeypatch.setattr(
        service,
        "_apply_active_planner_skill",
        lambda *_args, **_kwargs: {
            "applied": True,
            "state": "REUSED",
            "skill_id": "skill-java-gc",
            "skill_name": "gc-pressure-diagnosis",
            "selected_tool": "start_jvm_profile",
        },
    )
    monkeypatch.setattr(
        service,
        "_record_planner_knowledge_retrieval",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(service, "_successful_tool_route_priors", lambda: [])
    monkeypatch.setattr(service, "propose_hypothesis_plan", lambda **_kwargs: None)
    monkeypatch.setattr(
        service,
        "_planner_tool_arguments",
        lambda _tool_name, _target, **_kwargs: {
            "agent_id": AGENT_ID,
            "pid": 4242,
            "duration_seconds": 15,
            "event": "alloc",
        },
    )
    monkeypatch.setattr(
        service,
        "_evaluate_persisted_tool_policy",
        lambda *_args, **_kwargs: {
            "decision": "REQUIRE_APPROVAL",
            "checks": [],
            "reason": "单元测试只验证计划与节点绑定",
        },
    )

    revision = service._replan_after_insufficient_evidence(
        DIAGNOSIS_ID,
        parent.id,
        "report-java-baseline",
    )

    assert revision is not None
    assert "JVM" in revision.statement
    assert "GC" in revision.statement
    with new_session() as session:
        call = (
            session.query(DropInsightToolCallModel)
            .filter(
                DropInsightToolCallModel.diagnosis_id == DIAGNOSIS_ID,
                DropInsightToolCallModel.effect_key
                == "report:report-java-baseline:insufficient:tool_call",
            )
            .one()
        )
        assert call.tool_name == "start_jvm_profile"
        assert call.hypothesis_id == revision.id
        assert call.hypothesis_id != unknown.id
