from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.service import (
    _autonomous_round_limit,
    _counter_evidence_pivot_from_envelopes,
    _create_and_select_lats_round,
    _deterministic_exploration_candidates,
    _merge_replan_candidates,
    _primary_intent_query,
    _record_lats_action_dispatched,
    _record_lats_report_outcome,
    _record_lats_tool_failure,
    _replan_from_counter_evidence,
)
from server.app.models import (
    DropInsightEventModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _create_session(identifier: str = "insight-lats-action") -> None:
    timestamp = datetime.now(timezone.utc)
    session = new_session()
    session.add(
        DropInsightSessionModel(
            id=identifier,
            query="diagnose a CPU hotspot",
            target_json={},
            time_range_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            mode="AUTONOMOUS",
            skill_policy="AUTO",
            budget_json={},
            status="HYPOTHESIZING",
            version=1,
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.flush()
    session.add(
        DropInsightEventModel(
            id="event-lats-started",
            diagnosis_id=identifier,
            sequence=1,
            event_type="lats.search_started",
            actor="SYSTEM",
            payload_json={
                "algorithm": "LATS-UCT",
                "semantics": {"execution_mode": "BUDGETED_LATS"},
            },
            effect_key=f"diagnosis:{identifier}:lats:started",
            occurred_at=timestamp,
        )
    )
    session.commit()
    session.close()


def _tool_call(*, status: str, task_id: str | None) -> DropInsightToolCallModel:
    return DropInsightToolCallModel(
        id="tool-lats-action",
        diagnosis_id="insight-lats-action",
        hypothesis_id="hypothesis-lats-action",
        tool_name="start_perf_profile",
        arguments_json={},
        policy_decision="REQUIRE_APPROVAL" if status == "PENDING_APPROVAL" else "ALLOW",
        policy_checks_json=[],
        policy_reason="bounded registered diagnostic probe",
        status=status,
        task_id=task_id,
        result_json={},
        budget_reservation_json={},
        budget_settlement_json={},
        budget_reservation_status="RESERVED",
        terminal_processing_status="NONE",
        effect_key="tool:lats-action",
        requested_by="agent",
        created_at=datetime.now(timezone.utc),
    )


def _event_types() -> list[str]:
    session = new_session()
    try:
        return [
            event.event_type
            for event in session.query(DropInsightEventModel)
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        ]
    finally:
        session.close()


def _event_payload(event_type: str) -> dict:
    session = new_session()
    try:
        event = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.event_type == event_type)
            .one()
        )
        return dict(event.payload_json or {})
    finally:
        session.close()


def _create_hypothesis_and_report(*, report_id: str = "report-counter") -> None:
    timestamp = datetime.now(timezone.utc)
    session = new_session()
    session.add(
        DropInsightHypothesisModel(
            id="hypothesis-lats-action",
            diagnosis_id="insight-lats-action",
            statement="CPU is saturated by one hot function",
            expected_observations_json=["samples concentrate on one stack"],
            falsification_criteria_json=["trusted samples are uniformly distributed"],
            status="OPEN",
            source="MODEL",
            round_index=1,
            parent_hypothesis_id=None,
            generation_reason="initial expansion",
            effect_key="hypothesis:lats-action",
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.flush()
    session.add(
        DropInsightReportModel(
            id=report_id,
            diagnosis_id="insight-lats-action",
            hypothesis_id="hypothesis-lats-action",
            conclusion="trusted counter evidence falsifies the branch",
            confidence=800,
            evidence_refs_json=[],
            counter_evidence_refs_json=["evidence-counter-1"],
            assumptions_json=[],
            limitations_json=[],
            next_actions_json=[],
            claims_json=[],
            verification_json={"status": "INSUFFICIENT_EVIDENCE"},
            effects_status="PENDING",
            effects_fencing_token=0,
            created_at=timestamp,
        )
    )
    session.commit()
    session.close()


def test_pending_approval_is_a_proposal_not_a_simulated_or_dispatched_action() -> None:
    _create_session()
    pending = _tool_call(status="PENDING_APPROVAL", task_id=None)

    _record_lats_action_dispatched(
        "insight-lats-action",
        "hypothesis-lats-action",
        pending,
        effect_prefix="acceptance",
    )
    assert _event_types() == [
        "lats.search_started",
        "lats.action_proposed",
        "lats.awaiting_approval",
    ]

    approved = _tool_call(status="TASK_CREATED", task_id="task-approved-1")
    approved.policy_decision = "ALLOW"
    _record_lats_action_dispatched(
        "insight-lats-action",
        "hypothesis-lats-action",
        approved,
        effect_prefix="acceptance",
    )
    assert _event_types() == [
        "lats.search_started",
        "lats.action_proposed",
        "lats.awaiting_approval",
        "lats.simulation_started",
        "lats.action_dispatched",
    ]

    # Retrying after a worker restart must not duplicate durable phase events.
    _record_lats_action_dispatched(
        "insight-lats-action",
        "hypothesis-lats-action",
        approved,
        effect_prefix="acceptance",
    )
    assert _event_types() == [
        "lats.search_started",
        "lats.action_proposed",
        "lats.awaiting_approval",
        "lats.simulation_started",
        "lats.action_dispatched",
    ]


def test_approved_action_only_enters_simulation_after_a_task_exists() -> None:
    _create_session()

    allowed_without_task = _tool_call(status="APPROVED", task_id=None)
    _record_lats_action_dispatched(
        "insight-lats-action",
        "hypothesis-lats-action",
        allowed_without_task,
        effect_prefix="acceptance",
    )
    assert _event_types() == ["lats.search_started", "lats.action_proposed"]

    dispatched = _tool_call(status="TASK_CREATED", task_id="task-real-1")
    _record_lats_action_dispatched(
        "insight-lats-action",
        "hypothesis-lats-action",
        dispatched,
        effect_prefix="acceptance",
    )
    assert _event_types() == [
        "lats.search_started",
        "lats.action_proposed",
        "lats.simulation_started",
        "lats.action_dispatched",
    ]


def test_policy_rejection_backpropagates_and_switches_instead_of_stopping(
    monkeypatch,
) -> None:
    _create_session()
    _create_hypothesis_and_report()
    replans = []
    monkeypatch.setattr(
        "server.app.drop_insight.service._replan_after_insufficient_evidence",
        lambda *args, **kwargs: replans.append((args, kwargs)),
    )

    rejected = _tool_call(status="REJECTED", task_id=None)
    _record_lats_action_dispatched(
        "insight-lats-action",
        "hypothesis-lats-action",
        rejected,
        effect_prefix="acceptance",
    )

    assert _event_types() == [
        "lats.search_started",
        "lats.action_proposed",
        "lats.action_blocked",
        "lats.observation_recorded",
        "lats.reflection_recorded",
        "lats.backpropagated",
    ]
    assert "lats.search_terminated" not in _event_types()
    assert _event_payload("lats.observation_recorded")[
        "failure_is_counter_evidence"
    ] is False
    assert _event_payload("lats.reflection_recorded")["decision"] == (
        "SWITCH_DIRECTION"
    )
    assert _event_payload("lats.backpropagated")["outcome"] == "POLICY_BLOCKED"
    assert len(replans) == 1


def test_rule_fallback_expands_fresh_chinese_candidates_across_evidence_domains() -> None:
    previous = [
        DropInsightHypothesisModel(
            statement=(
                "第 1 轮：Python 运行时可能存在 GIL 竞争、用户态热点或同步阻塞路径"
            )
        )
    ]
    candidates = _deterministic_exploration_candidates(
        ["start_pyspy_profile", "start_ebpf_io_profile", "collect_memory_profile"],
        round_index=2,
        reason="可信反证后切换证据域",
        prior_hypotheses=previous,
    )
    merged = _merge_replan_candidates(
        [],
        candidates,
        top_k=3,
        prior_hypotheses=previous,
    )

    assert [item["recommended_tool"] for item in merged] == [
        "start_ebpf_io_profile",
        "collect_memory_profile",
    ]
    assert {item["evidence_domain"] for item in merged} == {
        "KERNEL_IO",
        "PROCESS_MEMORY",
    }
    assert all(item["statement"].startswith("第 2 轮：") for item in merged)
    assert _autonomous_round_limit({"max_diagnosis_rounds": 6}) == 4


def test_primary_intent_ignores_explicit_counter_check_clauses() -> None:
    source_query = (
        "诊断 demo 环境中的 python-hotspot 服务，先确认 CPU 异常，"
        "再定位源码级热点函数，最后用系统指标排除 I/O 和宿主机争抢；"
        "至少经过两类独立证据再下结论。"
    )
    io_query = (
        "诊断 demo 环境中的 python-hotspot 服务写入变慢，"
        "先用系统指标确认 I/O 方向，再采集块设备延迟，"
        "随后寻找 CPU 热点反证。"
    )

    source_intent = _primary_intent_query(source_query)
    io_intent = _primary_intent_query(io_query)

    assert "源码级热点函数" in source_intent
    assert "i/o" not in source_intent.casefold()
    assert "i/o" in io_intent.casefold()
    assert "cpu 热点反证" not in io_intent.casefold()


def test_lats_frontier_never_pairs_stale_io_hypothesis_with_pyspy() -> None:
    diagnosis_id = "insight-frontier-domain"
    _create_session(diagnosis_id)
    timestamp = datetime.now(timezone.utc)
    with new_session() as session:
        session.add_all(
            [
                DropInsightHypothesisModel(
                    id="hyp-parent",
                    diagnosis_id=diagnosis_id,
                    statement="系统基线不足以区分根因",
                    expected_observations_json=["需要专项证据"],
                    falsification_criteria_json=["专项证据没有区分力"],
                    status="INCONCLUSIVE",
                    source="DETERMINISTIC_RULE",
                    round_index=1,
                    parent_hypothesis_id=None,
                    generation_reason="initial",
                    effect_key="hyp-parent-effect",
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                DropInsightHypothesisModel(
                    id="hyp-old-io",
                    diagnosis_id=diagnosis_id,
                    statement="目标进程同步写盘路径过重",
                    expected_observations_json=["写系统调用集中"],
                    falsification_criteria_json=["I/O 平稳"],
                    status="OPEN",
                    source="DETERMINISTIC_RULE",
                    round_index=1,
                    parent_hypothesis_id=None,
                    generation_reason="initial",
                    effect_key="hyp-old-io-effect",
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                DropInsightHypothesisModel(
                    id="hyp-old-python",
                    diagnosis_id=diagnosis_id,
                    statement="Python 运行时存在源码热点函数",
                    expected_observations_json=["py-spy 样本集中"],
                    falsification_criteria_json=["Python 样本分散"],
                    status="OPEN",
                    source="DETERMINISTIC_RULE",
                    round_index=1,
                    parent_hypothesis_id=None,
                    generation_reason="initial",
                    effect_key="hyp-old-python-effect",
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                DropInsightEventModel(
                    id="event-historical-frontier",
                    diagnosis_id=diagnosis_id,
                    sequence=2,
                    event_type="lats.candidates_expanded",
                    actor="AGENT",
                    payload_json={
                        "candidates": [
                            {
                                "node_id": "hypothesis:hyp-old-io",
                                "candidate_key": "io",
                                "recommended_tool": "start_ebpf_io_profile",
                                "prior": 0.8,
                                "initial_value": 0.8,
                                "rank": 0,
                            },
                            {
                                "node_id": "hypothesis:hyp-old-python",
                                "candidate_key": "python",
                                "recommended_tool": "start_pyspy_profile",
                                "prior": 0.2,
                                "initial_value": 0.5,
                                "rank": 1,
                            },
                        ]
                    },
                    effect_key="historical-frontier",
                    occurred_at=timestamp,
                ),
            ]
        )
        session.commit()

    selected, selected_tool, _ = _create_and_select_lats_round(
        diagnosis_id,
        [],
        source="AUTONOMOUS_RULE_FALLBACK",
        round_index=2,
        execution_round_index=2,
        parent_hypothesis_id="hyp-parent",
        generation_reason="switch evidence domain",
        default_tool="start_pyspy_profile",
        allowed_tools=["start_pyspy_profile"],
        phase="INSUFFICIENT_EVIDENCE_EXPANSION",
        effect_prefix="frontier-domain",
    )

    assert selected is not None
    assert selected.id == "hyp-old-python"
    assert selected_tool == "start_pyspy_profile"


def test_counter_evidence_replan_excludes_attempted_tool_and_changes_domain(
    monkeypatch,
) -> None:
    parent = SimpleNamespace(
        id="hypothesis-refuted",
        round_index=1,
        statement="Python 热点是唯一根因",
        to_dict=lambda: {"statement": "Python 热点是唯一根因"},
    )
    diagnosis = SimpleNamespace(
        id="insight-counter-switch",
        query="诊断 Python 服务延迟",
        target_json={},
        budget_json={"max_diagnosis_rounds": 6, "lats_top_k": 3},
    )
    captured = {}
    monkeypatch.setattr(
        "server.app.drop_insight.service.get_diagnosis", lambda _id: diagnosis
    )
    monkeypatch.setattr(
        "server.app.drop_insight.service.list_hypotheses", lambda _id: [parent]
    )
    monkeypatch.setattr(
        "server.app.drop_insight.service.list_tool_calls",
        lambda _id: [SimpleNamespace(tool_name="start_pyspy_profile")],
    )
    monkeypatch.setattr(
        "server.app.drop_insight.service._tool_call_by_effect_key",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "server.app.drop_insight.service._current_target_binding",
        lambda _diagnosis: object(),
    )
    monkeypatch.setattr(
        "server.app.drop_insight.service._available_planner_tools",
        lambda *_args: [
            "start_pyspy_profile",
            "start_ebpf_io_profile",
            "collect_memory_profile",
        ],
    )
    monkeypatch.setattr(
        "server.app.drop_insight.service._record_planner_knowledge_retrieval",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "server.app.drop_insight.service._successful_tool_route_priors",
        lambda: [],
    )
    monkeypatch.setattr(
        "server.app.drop_insight.service.propose_hypothesis_plan",
        lambda **_kwargs: None,
    )

    def capture_round(_diagnosis_id, raw_candidates, **kwargs):
        captured["candidates"] = raw_candidates
        captured["allowed_tools"] = kwargs["allowed_tools"]
        return None, None, None

    monkeypatch.setattr(
        "server.app.drop_insight.service._create_and_select_lats_round",
        capture_round,
    )

    assert (
        _replan_from_counter_evidence(
            "insight-counter-switch",
            "hypothesis-refuted",
            "report-refuted",
        )
        is None
    )
    assert captured["allowed_tools"] == [
        "start_ebpf_io_profile",
        "collect_memory_profile",
    ]
    assert [item["recommended_tool"] for item in captured["candidates"]] == [
        "start_ebpf_io_profile",
        "collect_memory_profile",
    ]
    assert all("第 2 轮" in item["statement"] for item in captured["candidates"])


def test_counter_predicate_promotes_user_hotspot_to_continuous_independent_repeat() -> None:
    pivot = _counter_evidence_pivot_from_envelopes(
        [
            {
                "evidence_id": "ev-perf-counter",
                "envelope": {
                    "observation": {
                        "metadata": {
                            "hypothesis_predicate": {
                                "outcome": "COUNTER",
                                "reason": (
                                    "a strong non-lock user-space hotspot exists and "
                                    "no lock-related symbol was sampled"
                                ),
                                "metrics": {
                                    "dominant_function": "cpp_cpu_hot_function",
                                    "dominant_percent": 100.0,
                                },
                            }
                        }
                    }
                },
            }
        ],
        ["collect_memory_profile", "start_continuous_profile"],
    )

    assert pivot is not None
    assert pivot["tool_name"] == "start_continuous_profile"
    assert pivot["trigger_evidence_id"] == "ev-perf-counter"
    assert "cpp_cpu_hot_function" in pivot["expected"][0]


def test_counter_predicate_does_not_pivot_without_independent_probe() -> None:
    assert _counter_evidence_pivot_from_envelopes(
        [
            {
                "envelope": {
                    "observation": {
                        "metadata": {
                            "hypothesis_predicate": {
                                "outcome": "COUNTER",
                                "reason": "a strong non-lock user-space hotspot exists",
                                "metrics": {
                                    "dominant_function": "hot",
                                    "dominant_percent": 99,
                                },
                            }
                        }
                    }
                }
            }
        ],
        ["collect_memory_profile"],
    ) is None


def test_tool_failure_backpropagates_but_never_masquerades_as_counter_evidence() -> None:
    _create_session()
    _record_lats_tool_failure(
        "insight-lats-action",
        "hypothesis-lats-action",
        "tool-failed-1",
        "task-failed-1",
        "FAILED",
        "collector returned no artifact",
    )
    assert _event_types() == [
        "lats.search_started",
        "lats.observation_recorded",
        "lats.reflection_recorded",
        "lats.backpropagated",
    ]
    observation = _event_payload("lats.observation_recorded")
    reward = _event_payload("lats.backpropagated")
    assert observation["failure_is_counter_evidence"] is False
    assert observation["evidence_ids"] == []
    assert reward["outcome"] == "TOOL_FAILURE"
    assert reward["reward"] < 0
    assert "lats.node_pruned" not in _event_types()

    # Terminal processing can be retried after a worker crash without a
    # second visit/reward being folded into the tree.
    _record_lats_tool_failure(
        "insight-lats-action",
        "hypothesis-lats-action",
        "tool-failed-1",
        "task-failed-1",
        "FAILED",
        "collector returned no artifact",
    )
    assert _event_types().count("lats.backpropagated") == 1


def test_trusted_counter_only_report_prunes_the_falsified_branch_once() -> None:
    _create_session()
    _create_hypothesis_and_report()

    outcome = _record_lats_report_outcome("report-counter")
    assert outcome is not None
    assert outcome["outcome"] == "FALSIFIED"
    assert outcome["reflection"]["decision"] == "BACKTRACK"
    assert _event_types() == [
        "lats.search_started",
        "lats.observation_recorded",
        "lats.reflection_recorded",
        "lats.backpropagated",
        "lats.node_pruned",
    ]
    assert _event_payload("lats.node_pruned")["node_id"] == (
        "hypothesis:hypothesis-lats-action"
    )

    repeated = _record_lats_report_outcome("report-counter")
    assert repeated is not None
    assert _event_types().count("lats.node_pruned") == 1
    assert _event_types().count("lats.backpropagated") == 1
