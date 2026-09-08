from __future__ import annotations

import copy
import math

import pytest

from server.app.drop_insight.lats import (
    FrozenReplayObservationProvider,
    LATSConfig,
    build_search_projection,
    execution_semantics,
    hypothesis_path,
    order_progressive_frontier,
    prepare_candidates,
    reflection_from_outcome,
    replay_search_events,
    reward_from_outcome,
    run_frozen_replay_lats,
    select_puct_candidate,
    termination_decision,
)


def _event(event_type: str, payload: dict) -> dict:
    return {"event_type": event_type, "payload_json": payload}


def test_mode_string_does_not_self_certify_full_lats() -> None:
    replay_without_provider = execution_semantics("REPLAY")
    assert replay_without_provider["execution_mode"] == "BUDGETED_LATS"
    assert replay_without_provider["environment_semantics"] == "REPLAY_PROVIDER_REQUIRED"
    assert replay_without_provider["strict_environment_reversibility"] is False

    frozen = execution_semantics("REPLAY", frozen_observations=True)
    assert frozen["execution_mode"] == "FULL_LATS"
    assert frozen["environment_semantics"] == "FROZEN_REPLAY"
    assert frozen["strict_environment_reversibility"] is True

    live = execution_semantics("AUTONOMOUS")
    assert live["execution_mode"] == "BUDGETED_LATS"
    assert live["rollout_semantics"] == "REAL_TOOL_SINGLE_STEP_NO_ROLLBACK"


def test_top_k_expansion_keeps_open_world_sentinel_and_honest_values() -> None:
    raw = [
        {
            "statement": "candidate A",
            "expected": ["a"],
            "falsification": ["not a"],
            "estimated_value": 0.8,
            # This untrusted field must not masquerade as server SC.
            "self_consistency": 1.0,
        },
        {"statement": "candidate B", "expected": ["b"], "falsification": ["not b"]},
        {"statement": "candidate C", "expected": ["c"], "falsification": ["not c"]},
        {"statement": "candidate D", "expected": ["d"], "falsification": ["not d"]},
        {
            "statement": "其他/未知原因仍需开放探索（OTHER/UNKNOWN）",
            "expected": ["new fact"],
            "falsification": ["known branch wins"],
        },
    ]
    candidates = prepare_candidates(raw, top_k=2, default_tool="collect_sys_metrics")
    assert [item["statement"] for item in candidates[:2]] == ["candidate A", "candidate B"]
    assert candidates[-1]["is_open_world_sentinel"] is True
    assert len(candidates) == 3
    assert sum(item["prior"] for item in candidates) == pytest.approx(1.0, abs=2e-6)
    assert candidates[0]["self_consistency"] is None
    assert candidates[0]["value_source"] == "LM_ONLY_SC_UNAVAILABLE"
    assert candidates[0]["initial_value"] == 0.8
    assert candidates[1]["lm_value"] is None
    assert candidates[1]["value_source"] == "DETERMINISTIC_FALLBACK"


def test_server_computed_self_consistency_uses_documented_value_blend() -> None:
    candidate = prepare_candidates(
        [
            {
                "statement": "sampled candidate",
                "expected": ["x"],
                "falsification": ["not x"],
                "estimated_value": 0.8,
                "server_self_consistency": 0.2,
            }
        ],
        top_k=1,
        value_lambda=0.25,
    )[0]
    assert candidate["value_source"] == "LM_PLUS_SELF_CONSISTENCY"
    assert candidate["initial_value"] == pytest.approx(0.35)
    assert candidate["value_formula"] == "lambda*LM(s)+(1-lambda)*SC(s)"


def test_deduplication_does_not_invent_self_consistency_from_duplicate_text() -> None:
    candidates = prepare_candidates(
        [
            {"statement": "same candidate", "estimated_value": 0.7},
            {"statement": "  SAME   candidate ", "estimated_value": 0.7},
        ],
        top_k=3,
    )
    assert len(candidates) == 1
    assert candidates[0]["self_consistency"] is None
    assert candidates[0]["value_source"] == "LM_ONLY_SC_UNAVAILABLE"


def test_candidate_deduplication_ignores_round_label_and_unknown_aliases() -> None:
    candidates = prepare_candidates(
        [
            {
                "statement": "第 2 轮：目标进程可能存在原生调用栈热点",
                "expected": ["出现热点"],
                "falsification": ["没有热点"],
            },
            {
                "statement": "第 3 轮：目标进程可能存在原生调用栈热点",
                "expected": ["重复热点"],
                "falsification": ["没有热点"],
            },
            {
                "statement": "其他未知原因（OTHER/UNKNOWN）",
                "expected": ["存在未知观测"],
                "falsification": ["已知分支解释全部观测"],
            },
            {
                "statement": "未知原因",
                "expected": ["仍有未知观测"],
                "falsification": ["全部已解释"],
            },
        ],
        top_k=3,
    )

    assert len(candidates) == 2
    assert candidates[0]["statement"].startswith("第 2 轮")
    assert sum(item["is_open_world_sentinel"] for item in candidates) == 1
    assert candidates[-1]["candidate_key"] == "candidate:other-unknown"


def test_canonical_uct_prioritizes_unvisited_child_without_prior_bonus() -> None:
    candidates = [
        {"node_id": "hypothesis:visited", "rank": 0, "prior": 0.99, "initial_value": 1.0},
        {"node_id": "hypothesis:new", "rank": 1, "prior": 0.01, "initial_value": -1.0},
    ]
    selection = select_puct_candidate(
        candidates,
        {
            "hypothesis:visited": {"visits": 4, "value_sum": 4.0},
            "hypothesis:new": {"visits": 0, "value_sum": 0.0},
        },
        parent_visits=4,
        selection_policy="UCT",
    )
    assert selection is not None
    assert selection["node_id"] == "hypothesis:new"
    assert selection["selection_policy"] == "UCT"
    selected_score = next(
        item for item in selection["scores"] if item["node_id"] == "hypothesis:new"
    )
    assert selected_score["components"]["prior_bonus"] == 0.0
    assert selected_score["components"]["unvisited"] is True


def test_canonical_uct_uses_log_parent_visits_without_plus_one() -> None:
    selection = select_puct_candidate(
        [{"node_id": "hypothesis:a", "rank": 0, "prior": 1.0}],
        {"hypothesis:a": {"visits": 2, "value_sum": 1.0}},
        parent_visits=10,
        exploration_constant=2 ** 0.5,
        selection_policy="UCT",
    )
    assert selection is not None
    expected_exploration = math.sqrt(2.0 * math.log(10) / 2)
    assert selection["components"]["exploration"] == pytest.approx(
        expected_exploration, abs=1e-6
    )
    assert selection["score"] == pytest.approx(0.5 + expected_exploration, abs=1e-6)


def test_progressive_frontier_revisits_old_unvisited_sibling_before_widening() -> None:
    frontier = order_progressive_frontier(
        [{"node_id": "hypothesis:old-sibling", "rank": 2, "prior": 0.1}],
        [{"node_id": "hypothesis:new-child", "rank": 0, "prior": 0.9}],
    )
    selection = select_puct_candidate(
        frontier,
        parent_visits=1,
        selection_policy="UCT",
    )
    assert selection is not None
    assert selection["node_id"] == "hypothesis:old-sibling"
    assert frontier[0]["generation_rank"] == 2


def test_puct_is_explicitly_an_extension() -> None:
    selection = select_puct_candidate(
        [
            {"node_id": "hypothesis:a", "rank": 0, "prior": 0.1, "initial_value": 0.0},
            {"node_id": "hypothesis:b", "rank": 1, "prior": 0.9, "initial_value": 0.0},
        ],
        parent_visits=10,
        selection_policy="PUCT",
    )
    assert selection is not None
    assert selection["node_id"] == "hypothesis:b"
    assert "LATS-PUCT" in selection["reason"]
    assert selection["components"]["prior_bonus"] > 0


def test_reward_and_reflection_obey_evidence_gate() -> None:
    verified = reward_from_outcome(
        verification_status="VERIFIED",
        confidence=850,
        support_count=2,
        counter_count=0,
    )
    assert verified["reward"] > 0
    assert reflection_from_outcome(verified)["decision"] == "STOP_VERIFIED"

    failed = reward_from_outcome(
        verification_status=None,
        confidence=0,
        support_count=0,
        counter_count=0,
        tool_status="FAILED",
    )
    assert failed["outcome"] == "TOOL_FAILURE"
    assert failed["reward"] < 0
    assert reflection_from_outcome(failed)["decision"] == "SWITCH_OR_STOP"

    partial = reward_from_outcome(
        verification_status="PARTIAL_WITHOUT_COUNTER",
        confidence=700,
        support_count=1,
        counter_count=0,
    )
    assert reflection_from_outcome(partial)["decision"] == "PROGRESSIVE_WIDEN"

    blocked = reward_from_outcome(
        verification_status=None,
        confidence=0,
        support_count=0,
        counter_count=0,
        tool_status="REJECTED",
    )
    assert blocked["outcome"] == "POLICY_BLOCKED"
    assert blocked["reward"] < 0
    assert reflection_from_outcome(blocked)["decision"] == "SWITCH_DIRECTION"


def test_event_replay_recovers_metrics_and_best_path_by_value_not_last_path() -> None:
    events = [
        _event(
            "lats.search_started",
            {
                "semantics": execution_semantics("AUTONOMOUS"),
                "config": {"selection_policy": "UCT", "max_iterations": 4, "max_tool_calls": 4},
            },
        ),
        _event(
            "lats.candidates_expanded",
            {
                "candidates": [
                    {"node_id": "hypothesis:a", "prior": 0.5, "initial_value": 0.2, "depth": 0},
                    {"node_id": "hypothesis:b", "prior": 0.5, "initial_value": 0.2, "depth": 0},
                ]
            },
        ),
        _event(
            "lats.candidates_evaluated",
            {
                "evaluations": [
                    {
                        "node_id": "hypothesis:a",
                        "initial_value": 0.2,
                        "lm_value": None,
                        "self_consistency": None,
                        "heuristic_value": 0.2,
                        "value_source": "DETERMINISTIC_FALLBACK",
                    }
                ]
            },
        ),
        _event("lats.node_selected", {"node_id": "hypothesis:a", "iteration": 1, "score": 1}),
        _event("lats.simulation_started", {"node_id": "hypothesis:a"}),
        _event("lats.action_dispatched", {"node_id": "hypothesis:a", "tool_call_id": "tool-a"}),
        _event("lats.observation_recorded", {"node_id": "hypothesis:a", "summary": "real a"}),
        _event("lats.reflection_recorded", {"node_id": "hypothesis:a", "summary": "keep", "decision": "CONTINUE"}),
        _event("lats.backpropagated", {"path_node_ids": ["hypothesis:a"], "reward": 0.8}),
        _event("lats.node_selected", {"node_id": "hypothesis:b", "iteration": 2, "score": 1}),
        _event("lats.backpropagated", {"path_node_ids": ["hypothesis:b"], "reward": -0.8}),
        _event("lats.node_pruned", {"node_id": "hypothesis:b", "reason": "counter"}),
    ]
    state = replay_search_events(events)
    assert state["phase"] == "BACKPROPAGATION"
    assert state["iteration"] == 2
    assert state["latest_path_node_ids"] == ["hypothesis:b"]
    assert state["best_path_node_ids"] == ["hypothesis:a"]
    assert state["node_metrics"]["hypothesis:a"]["visits"] == 1
    assert state["node_metrics"]["hypothesis:a"]["mean_value"] == 0.8
    assert state["node_metrics"]["hypothesis:b"]["pruned"] is True


def test_projection_omits_historical_non_lats_sessions() -> None:
    assert build_search_projection(
        mode="AUTONOMOUS",
        budget={},
        status="COMPLETED",
        events=[_event("hypothesis.created", {"hypothesis_id": "old"})],
        tool_calls_used=1,
    ) is None


def test_projection_reports_algorithm_budget_and_live_semantics() -> None:
    projection = build_search_projection(
        mode="AUTONOMOUS",
        budget={"max_lats_iterations": 3, "max_tool_calls": 4},
        status="HYPOTHESIZING",
        events=[
            _event(
                "lats.search_started",
                {
                    "semantics": execution_semantics("AUTONOMOUS"),
                    "config": {
                        "selection_policy": "UCT",
                        "max_iterations": 3,
                        "max_tool_calls": 4,
                    },
                },
            ),
            _event("lats.node_selected", {"node_id": "hypothesis:a", "iteration": 1}),
        ],
        tool_calls_used=1,
    )
    assert projection is not None
    assert projection["algorithm"] == "LATS-UCT"
    assert projection["execution_mode"] == "BUDGETED_LATS"
    assert projection["budget"]["remaining_iterations"] == 2
    assert projection["termination"]["reason"] == "AWAITING_OBSERVATION"


def test_budget_termination_and_cycle_safe_path() -> None:
    decision = termination_decision(
        verification_status=None,
        iterations_used=2,
        tool_calls_used=1,
        config=LATSConfig(max_iterations=2),
        eligible_children=3,
    )
    assert decision["reason"] == "BUDGET_EXHAUSTED"
    assert hypothesis_path("b", {"b": "a", "a": "b"}) == ["hypothesis:a", "hypothesis:b"]


def test_partial_support_continues_until_cross_validation_or_coverage_exhaustion() -> None:
    running = termination_decision(
        verification_status="PARTIAL_WITHOUT_COUNTER",
        iterations_used=1,
        tool_calls_used=1,
        config=LATSConfig(max_iterations=4, max_tool_calls=4),
        eligible_children=2,
    )
    exhausted = termination_decision(
        verification_status="PARTIAL_WITHOUT_COUNTER",
        iterations_used=1,
        tool_calls_used=1,
        config=LATSConfig(max_iterations=4, max_tool_calls=4),
        eligible_children=0,
    )

    assert running == {
        "stopped": False,
        "reason": "CROSS_VALIDATION_REQUIRED",
        "detail": "已有支持证据，但仍可在预算内切换独立证据域交叉验证。",
    }
    assert exhausted["stopped"] is True
    assert exhausted["reason"] == "VERIFIED_WITH_LIMITATION"


def test_pruned_falsified_branch_is_never_selected_even_with_high_q() -> None:
    selection = select_puct_candidate(
        [
            {
                "node_id": "hypothesis:falsified",
                "rank": 0,
                "prior": 0.9,
                "initial_value": 1.0,
            },
            {
                "node_id": "hypothesis:eligible",
                "rank": 1,
                "prior": 0.1,
                "initial_value": -0.2,
            },
        ],
        {
            "hypothesis:falsified": {
                "visits": 20,
                "value_sum": 20.0,
                "pruned": True,
            },
            "hypothesis:eligible": {"visits": 1, "value_sum": -0.2},
        },
        parent_visits=21,
        selection_policy="UCT",
    )
    assert selection is not None
    assert selection["node_id"] == "hypothesis:eligible"
    assert {
        score["node_id"] for score in selection["scores"]
    } == {"hypothesis:eligible"}


def test_complete_six_operation_trace_is_restart_recomputable() -> None:
    """Fold only durable facts; a fresh fold after restart must be identical."""

    events = [
        _event(
            "lats.search_started",
            {
                "semantics": execution_semantics("AUTONOMOUS"),
                "config": {"selection_policy": "UCT"},
            },
        ),
        _event(
            "lats.candidates_expanded",
            {
                "candidates": [
                    {
                        "node_id": "hypothesis:root",
                        "prior": 1.0,
                        "initial_value": 0.3,
                        "depth": 0,
                    }
                ]
            },
        ),
        _event(
            "lats.candidates_evaluated",
            {
                "evaluations": [
                    {
                        "node_id": "hypothesis:root",
                        "initial_value": 0.3,
                        "lm_value": 0.3,
                        "self_consistency": None,
                        "value_source": "LM_ONLY_SC_UNAVAILABLE",
                    }
                ]
            },
        ),
        _event(
            "lats.node_selected",
            {
                "node_id": "hypothesis:root",
                "iteration": 1,
                "score": 1_000_000.0,
                "reason": "unvisited",
            },
        ),
        _event("lats.simulation_started", {"node_id": "hypothesis:root"}),
        _event(
            "lats.action_dispatched",
            {
                "node_id": "hypothesis:root",
                "tool_call_id": "tool-1",
                "task_id": "task-1",
            },
        ),
        _event(
            "lats.observation_recorded",
            {
                "node_id": "hypothesis:root",
                "observation_id": "report-1",
                "observation_source": "REAL_TOOL_EVIDENCE_GATE",
                "summary": "one trusted observation",
            },
        ),
        _event(
            "lats.reflection_recorded",
            {
                "node_id": "hypothesis:root",
                "decision": "PROGRESSIVE_WIDEN",
                "summary": "collect a discriminating counter-check",
            },
        ),
        _event(
            "lats.backpropagated",
            {"path_node_ids": ["hypothesis:root"], "reward": 0.4},
        ),
    ]
    before_restart = replay_search_events(events)
    after_restart = replay_search_events(copy.deepcopy(events))
    assert after_restart == before_restart
    metric = after_restart["node_metrics"]["hypothesis:root"]
    assert after_restart["phase"] == "BACKPROPAGATION"
    assert after_restart["iteration"] == 1
    assert metric["tool_call_id"] == "tool-1"
    assert metric["task_id"] == "task-1"
    assert metric["observation_id"] == "report-1"
    assert metric["last_reflection"] == "collect a discriminating counter-check"
    assert metric["visits"] == 1
    assert metric["mean_value"] == pytest.approx(0.4)


def test_best_path_is_derived_from_highest_q_not_stale_termination_payload() -> None:
    """A persisted UI hint must not override the reward statistics."""

    events = [
        _event("lats.search_started", {"config": {"selection_policy": "UCT"}}),
        _event(
            "lats.candidates_expanded",
            {
                "candidates": [
                    {"node_id": "hypothesis:best", "prior": 0.5},
                    {"node_id": "hypothesis:last", "prior": 0.5},
                ]
            },
        ),
        _event(
            "lats.backpropagated",
            {"path_node_ids": ["hypothesis:best"], "reward": 0.9},
        ),
        _event(
            "lats.backpropagated",
            {"path_node_ids": ["hypothesis:last"], "reward": -0.7},
        ),
        _event(
            "lats.search_terminated",
            {
                "reason": "BUDGET_EXHAUSTED",
                "best_path_node_ids": ["hypothesis:last"],
            },
        ),
    ]
    state = replay_search_events(events)
    assert state["latest_path_node_ids"] == ["hypothesis:last"]
    assert state["best_path_node_ids"] == ["hypothesis:best"]


def test_equal_q_backprop_prefers_deep_terminal_leaf_over_ancestor() -> None:
    """One rollout updates every ancestor; the path must not collapse to its root."""

    events = [
        _event("lats.search_started", {"config": {"selection_policy": "UCT"}}),
        _event(
            "lats.candidates_expanded",
            {
                "candidates": [
                    {
                        "node_id": "hypothesis:z-root",
                        "parent_node_id": None,
                        "depth": 0,
                    },
                    {
                        "node_id": "hypothesis:a-leaf",
                        "parent_node_id": "hypothesis:z-root",
                        "depth": 1,
                    },
                ]
            },
        ),
        _event(
            "lats.backpropagated",
            {
                "path_node_ids": ["hypothesis:z-root", "hypothesis:a-leaf"],
                "reward": 0.9,
            },
        ),
        _event(
            "lats.search_terminated",
            {
                "reason": "VERIFIED",
                "best_path_node_ids": [
                    "hypothesis:z-root",
                    "hypothesis:a-leaf",
                ],
            },
        ),
    ]
    state = replay_search_events(events)
    assert state["best_path_node_ids"] == [
        "hypothesis:z-root",
        "hypothesis:a-leaf",
    ]


def test_strict_frozen_replay_resets_same_snapshot_for_sibling_rollouts() -> None:
    provider = FrozenReplayObservationProvider(
        snapshot_id="fault-plaza-snapshot-1",
        observations={
            "candidate A": {
                "summary": "A has no distinguishing evidence",
                "verification_status": "INSUFFICIENT_EVIDENCE",
                "confidence": 0.2,
                "support_count": 0,
                "counter_count": 0,
            },
            "candidate B": {
                "summary": "B has no distinguishing evidence",
                "verification_status": "INSUFFICIENT_EVIDENCE",
                "confidence": 0.3,
                "support_count": 0,
                "counter_count": 0,
            },
        },
    )
    result = run_frozen_replay_lats(
        [
            {"statement": "candidate A", "expected": ["a"], "falsification": ["not a"]},
            {"statement": "candidate B", "expected": ["b"], "falsification": ["not b"]},
        ],
        provider,
        config=LATSConfig(top_k=2, max_iterations=2, max_tool_calls=2),
    )

    simulations = [
        event["payload"]
        for event in result["events"]
        if event["event_type"] == "lats.simulation_started"
    ]
    assert result["rollout_count"] == 2
    assert provider.reset_count == 2
    assert {item["snapshot_id"] for item in simulations} == {
        "fault-plaza-snapshot-1"
    }
    assert {item["snapshot_digest"] for item in simulations} == {
        provider.snapshot_digest
    }
    assert [item["reset_proof"]["reset_count"] for item in simulations] == [1, 2]
    selected_leaf_ids = [item["node_id"] for item in simulations]
    assert len(set(selected_leaf_ids)) == 2

    search = result["search"]
    assert search["execution_mode"] == "FULL_LATS"
    assert search["environment_semantics"] == "FROZEN_REPLAY"
    assert search["strict_environment_reversibility"] is True
    assert search["termination"]["reason"] == "BUDGET_EXHAUSTED"
    assert all(
        search["node_metrics"][node_id]["visits"] == 1
        for node_id in selected_leaf_ids
    )
    assert all(
        search["node_metrics"][node_id]["last_reflection"]
        for node_id in selected_leaf_ids
    )


def test_strict_frozen_replay_expands_reflected_children_and_backpropagates_path() -> None:
    provider = FrozenReplayObservationProvider(
        snapshot_id="fault-plaza-snapshot-tree",
        observations={
            "root hypothesis": {
                "summary": "root observation asks for a deeper probe",
                "verification_status": "INSUFFICIENT_EVIDENCE",
                "confidence": 0.4,
                "support_count": 0,
                "counter_count": 0,
                "next_candidates": [
                    {
                        "statement": "child hypothesis",
                        "expected": ["frozen child evidence"],
                        "falsification": ["child evidence absent"],
                        "estimated_value": 0.9,
                    }
                ],
            },
            "child hypothesis": {
                "summary": "child is verified by frozen evidence",
                "verification_status": "VERIFIED",
                "confidence": 0.9,
                "support_count": 2,
                "counter_count": 0,
                "evidence_refs": ["evidence-1", "evidence-2"],
            },
        },
    )
    result = run_frozen_replay_lats(
        [
            {
                "statement": "root hypothesis",
                "expected": ["root evidence"],
                "falsification": ["root evidence absent"],
            }
        ],
        provider,
        config=LATSConfig(top_k=2, max_iterations=4, max_tool_calls=4),
    )

    assert result["rollout_count"] == 2
    assert result["search"]["termination"]["reason"] == "VERIFIED"
    path = result["search"]["best_path_node_ids"]
    assert len(path) == 2
    assert result["search"]["node_metrics"][path[0]]["visits"] == 2
    assert result["search"]["node_metrics"][path[1]]["visits"] == 1
    assert result["search"]["node_metrics"][path[1]]["reward"] > 0
    assert any(
        event["event_type"] == "lats.candidates_expanded"
        and event["payload"].get("parent_node_id") == path[0]
        and event["payload"].get("reflection_injected") is True
        for event in result["events"]
    )


def test_frozen_provider_defensively_copies_observations() -> None:
    original = {
        "candidate": {
            "verification_status": "INSUFFICIENT_EVIDENCE",
            "nested": {"value": 1},
        }
    }
    provider = FrozenReplayObservationProvider(
        snapshot_id="immutable-snapshot", observations=original
    )
    original["candidate"]["nested"]["value"] = 2
    first = provider.observe({"statement": "candidate"}, iteration=1)
    first["nested"]["value"] = 3
    second = provider.observe({"statement": "candidate"}, iteration=2)
    assert second["nested"]["value"] == 1


def test_frozen_replay_namespace_isolates_ids_but_not_snapshot_digest() -> None:
    observations = {
        "candidate": {
            "verification_status": "INSUFFICIENT_EVIDENCE",
            "support_count": 0,
            "counter_count": 0,
        }
    }
    first = run_frozen_replay_lats(
        [{"statement": "candidate"}],
        FrozenReplayObservationProvider(
            snapshot_id="shared-snapshot", observations=observations
        ),
        config=LATSConfig(max_iterations=1, max_tool_calls=1),
        node_namespace="diagnosis-one",
    )
    second = run_frozen_replay_lats(
        [{"statement": "candidate"}],
        FrozenReplayObservationProvider(
            snapshot_id="shared-snapshot", observations=observations
        ),
        config=LATSConfig(max_iterations=1, max_tool_calls=1),
        node_namespace="diagnosis-two",
    )
    assert first["snapshot"]["snapshot_digest"] == second["snapshot"]["snapshot_digest"]
    assert first["node_namespace"] == "diagnosis-one"
    assert second["node_namespace"] == "diagnosis-two"
    first_node_ids = set(first["search"]["node_metrics"])
    second_node_ids = set(second["search"]["node_metrics"])
    assert first_node_ids.isdisjoint(second_node_ids)
    assert {event["event_id"] for event in first["events"]}.isdisjoint(
        {event["event_id"] for event in second["events"]}
    )
    assert {event["effect_key"] for event in first["events"]}.isdisjoint(
        {event["effect_key"] for event in second["events"]}
    )


def test_frozen_replay_observation_key_disambiguates_equal_statements() -> None:
    provider = FrozenReplayObservationProvider(
        snapshot_id="stateful-text-snapshot",
        observations={
            "state-one": {"summary": "first state"},
            "same statement": {"summary": "fallback state"},
        },
    )
    observation = provider.observe(
        {
            "observation_key": "state-one",
            "statement": "same statement",
        },
        iteration=1,
    )
    assert observation["summary"] == "first state"


def test_frozen_replay_generates_unique_default_run_namespace() -> None:
    observations = {"candidate": {"summary": "frozen"}}
    config = LATSConfig(max_iterations=1, max_tool_calls=1)
    first = run_frozen_replay_lats(
        [{"statement": "candidate"}],
        FrozenReplayObservationProvider(snapshot_id="same", observations=observations),
        config=config,
    )
    second = run_frozen_replay_lats(
        [{"statement": "candidate"}],
        FrozenReplayObservationProvider(snapshot_id="same", observations=observations),
        config=config,
    )
    assert first["node_namespace"] != second["node_namespace"]
    assert first["snapshot"]["snapshot_digest"] == second["snapshot"]["snapshot_digest"]
