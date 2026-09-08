from datetime import datetime, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.exploration_tree import get_live_exploration_tree
from server.app.models import (
    DiagnosticSkillActivationModel,
    DiagnosticSkillModel,
    DropInsightEvidenceModel,
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


def _hypothesis(identifier: str, *, round_index: int, parent_id: str | None):
    timestamp = datetime.now(timezone.utc)
    return DropInsightHypothesisModel(
        id=identifier,
        diagnosis_id="insight-tree",
        statement=f"round {round_index} hypothesis",
        expected_observations_json=["collect discriminating evidence"],
        falsification_criteria_json=["trusted counter evidence"],
        status="OPEN",
        source="MODEL_REPLAN" if parent_id else "MODEL",
        round_index=round_index,
        parent_hypothesis_id=parent_id,
        generation_reason="evidence changed the next diagnostic direction",
        effect_key=f"hypothesis:{identifier}",
        created_at=timestamp,
        updated_at=timestamp,
    )


def _tool(identifier: str, hypothesis_id: str, tool_name: str):
    return DropInsightToolCallModel(
        id=identifier,
        diagnosis_id="insight-tree",
        hypothesis_id=hypothesis_id,
        tool_name=tool_name,
        arguments_json={},
        policy_decision="ALLOW",
        policy_checks_json=[],
        policy_reason="registered read-only probe",
        status="COMPLETED",
        result_json={},
        budget_reservation_json={},
        budget_settlement_json={},
        budget_reservation_status="SETTLED",
        terminal_processing_status="REPORT_EFFECTS_DONE",
        effect_key=f"tool:{identifier}",
        requested_by="planner",
        created_at=datetime.now(timezone.utc),
    )


def test_live_tree_revision_grows_when_evidence_replans_a_new_round():
    timestamp = datetime.now(timezone.utc)
    session = new_session()
    session.add(
        DropInsightSessionModel(
            id="insight-tree",
            query="service latency rose after deployment",
            target_json={},
            time_range_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            mode="AUTONOMOUS",
            budget_json={"max_diagnosis_rounds": 6},
            status="RUNNING",
            version=1,
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.add(_hypothesis("hypothesis-1", round_index=1, parent_id=None))
    session.add(_tool("tool-1", "hypothesis-1", "start_perf_profile"))
    session.add(
        DropInsightEventModel(
            id="event-1",
            diagnosis_id="insight-tree",
            sequence=1,
            event_type="planner.initialized",
            actor="SYSTEM",
            payload_json={"hypothesis_id": "hypothesis-1", "tool_call_id": "tool-1"},
            effect_key="event:1",
            occurred_at=timestamp,
        )
    )
    session.commit()
    session.close()

    first = get_live_exploration_tree("insight-tree")
    assert first is not None
    assert first["revision"] == 1
    assert first["stats"]["rounds"] == 1

    session = new_session()
    session.add(_hypothesis("hypothesis-2", round_index=2, parent_id="hypothesis-1"))
    session.add(_tool("tool-2", "hypothesis-2", "collect_sys_metrics"))
    session.add(
        DropInsightEventModel(
            id="event-2",
            diagnosis_id="insight-tree",
            sequence=2,
            event_type="planner.counter_replanned",
            actor="SYSTEM",
            payload_json={"hypothesis_id": "hypothesis-2", "tool_call_id": "tool-2"},
            effect_key="event:2",
            occurred_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    session.close()

    second = get_live_exploration_tree("insight-tree")
    assert second is not None
    assert second["revision"] == 2
    assert second["stats"]["rounds"] == 2
    assert second["stats"]["nodes"] > first["stats"]["nodes"]
    child = next(item for item in second["nodes"] if item["id"] == "hypothesis:hypothesis-2")
    assert child["parent_id"] == "hypothesis:hypothesis-1"
    assert second["switches"] == [
        {
            "from": "CPU",
            "to": "系统基线",
            "from_key": "CPU_HOTSPOT",
            "to_key": "SYSTEM_RESOURCE",
            "reason": "上一方向证据不足或被反证，转向新的取证域",
        }
    ]


def test_live_tree_projects_backtracked_sibling_into_actual_lats_iteration():
    """A sibling born at depth two can become the third executed round."""

    timestamp = datetime.now(timezone.utc)
    session = new_session()
    session.add(
        DropInsightSessionModel(
            id="insight-tree",
            query="trace a hotspot, then backtrack to an I/O sibling",
            target_json={},
            time_range_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            mode="AUTONOMOUS",
            budget_json={"max_diagnosis_rounds": 6},
            status="RUNNING",
            version=1,
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.add_all(
        [
            _hypothesis("hypothesis-root", round_index=1, parent_id=None),
            _hypothesis(
                "hypothesis-cpu",
                round_index=2,
                parent_id="hypothesis-root",
            ),
            _hypothesis(
                "hypothesis-io",
                round_index=2,
                parent_id="hypothesis-root",
            ),
            _tool("tool-root", "hypothesis-root", "collect_sys_metrics"),
            _tool("tool-cpu", "hypothesis-cpu", "start_perf_profile"),
            _tool("tool-io", "hypothesis-io", "start_ebpf_io_profile"),
            DropInsightEvidenceModel(
                id="evidence-io",
                diagnosis_id="insight-tree",
                hypothesis_id="hypothesis-io",
                role="SUPPORT",
                envelope_json={"observation": {"summary": "I/O latency is elevated"}},
                classification_json={"decision": "ACCEPT_SUPPORT"},
                created_at=timestamp,
            ),
            DropInsightReportModel(
                id="report-io",
                diagnosis_id="insight-tree",
                hypothesis_id="hypothesis-io",
                conclusion="I/O latency explains the slowdown.",
                confidence=700,
                evidence_refs_json=["evidence-io"],
                counter_evidence_refs_json=[],
                assumptions_json=[],
                limitations_json=[],
                next_actions_json=[],
                claims_json=[],
                verification_json={"status": "PARTIAL_WITHOUT_COUNTER"},
                effects_status="APPLIED",
                effects_fencing_token=1,
                created_at=timestamp,
            ),
        ]
    )
    for sequence, (hypothesis_id, iteration) in enumerate(
        [
            ("hypothesis-root", 1),
            ("hypothesis-cpu", 2),
            ("hypothesis-io", 3),
        ],
        start=1,
    ):
        session.add(
            DropInsightEventModel(
                id=f"event-{sequence}",
                diagnosis_id="insight-tree",
                sequence=sequence,
                event_type="lats.node_selected",
                actor="DIAGNOSIS_AGENT",
                payload_json={
                    "node_id": f"hypothesis:{hypothesis_id}",
                    "iteration": iteration,
                },
                effect_key=f"selected:{hypothesis_id}",
                occurred_at=timestamp,
            )
        )
    session.commit()
    session.close()

    tree = get_live_exploration_tree("insight-tree")

    assert tree is not None
    assert [item["round_index"] for item in tree["rounds"]] == [1, 2, 3]
    assert tree["stats"]["rounds"] == 3
    assert tree["stats"]["current_round"] == 3

    nodes = {item["id"]: item for item in tree["nodes"]}
    backtracked = nodes["hypothesis:hypothesis-io"]
    assert backtracked["parent_id"] == "hypothesis:hypothesis-root"
    assert backtracked["tree_depth"] == 2
    assert backtracked["round_index"] == 3
    assert backtracked["domain"] == "第 3 轮假设"
    assert nodes["tool:tool-io"]["round_index"] == 3
    assert nodes["evidence:evidence-io"]["round_index"] == 3
    assert nodes["report:report-io"]["round_index"] == 3

    third_round = tree["rounds"][2]
    assert third_round["hypothesis_count"] == 1
    assert third_round["tool_call_count"] == 1
    assert third_round["evidence_count"] == 1


def test_live_tree_projects_skill_trace_and_route_overlay():
    timestamp = datetime.now(timezone.utc)
    session = new_session()
    session.add(
        DropInsightSessionModel(
            id="insight-tree",
            query="Python GIL hotspot",
            target_json={},
            time_range_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            mode="AUTONOMOUS",
            budget_json={"max_diagnosis_rounds": 4},
            status="RUNNING",
            version=1,
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.add(
        DiagnosticSkillModel(
            id="skill-python",
            family_key="repository:python-runtime-diagnosis",
            category="PYTHON_RUNTIME",
            version=2,
            status="ACTIVE",
            source_diagnosis_ids_json=[],
            trigger_json={"repository_builtin": True},
            strategy_json={"probe_order": ["start_pyspy_profile", "collect_sys_metrics"]},
            gate_metrics_json={"eligible": True},
            created_by="test",
            created_at=timestamp,
            updated_at=timestamp,
            published_at=timestamp,
        )
    )
    session.add(_hypothesis("hypothesis-skill", round_index=1, parent_id=None))
    session.add(_tool("tool-skill", "hypothesis-skill", "start_pyspy_profile"))
    session.add(
        DiagnosticSkillActivationModel(
            id="activation-python",
            skill_id="skill-python",
            diagnosis_id="insight-tree",
            match_score=812,
            match_reason_json={
                "retrieval": "HYBRID_BM25_VECTOR",
                "matched_terms": ["python", "gil"],
                "route": ["start_pyspy_profile", "collect_sys_metrics"],
                "skill_version": 2,
                "skill_category": "PYTHON_RUNTIME",
                "category_correction": {
                    "from": "IO_LATENCY",
                    "to": "PYTHON_RUNTIME",
                    "guard": "STRONG_TEXT_SIGNAL",
                },
                "skill_instructions": {
                    "summary": "Python 运行时循证诊断",
                    "source_path": "skills/python-runtime-diagnosis/SKILL.md",
                    "source_sha256": "a" * 64,
                    "trust": "REPOSITORY_REVIEWED",
                },
                "reuse_trace": [
                    {
                        "trace_key": "1:INITIAL_PLAN",
                        "round_index": 1,
                        "phase": "INITIAL_PLAN",
                        "baseline_tool": "start_ebpf_io_profile",
                        "selected_tool": "start_pyspy_profile",
                        "category_before": "IO_LATENCY",
                        "category_after": "PYTHON_RUNTIME",
                        "category_corrected": True,
                        "applied_at": timestamp.isoformat(),
                    }
                ],
            },
            baseline_tool="start_ebpf_io_profile",
            selected_tool="start_pyspy_profile",
            outcome=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    tree = get_live_exploration_tree("insight-tree")

    assert tree is not None
    assert tree["stats"]["skill_activations"] == 1
    assert tree["stats"]["skill_reuse_rounds"] == 1
    trace = tree["skill_trace"][0]
    assert trace["skill_id"] == "skill-python"
    assert trace["summary"] == "Python 运行时循证诊断"
    assert trace["category_correction"]["to"] == "PYTHON_RUNTIME"
    assert trace["reuse_trace"][0]["round_index"] == 1

    overlay = tree["skill_route_overlay"]
    assert overlay["correlation"] == "TOOL_NAME_OBSERVATION_NOT_CAUSAL_ATTRIBUTION"
    assert overlay["routes"][0]["steps"][0] == {
        "route_index": 1,
        "tool": "start_pyspy_profile",
        "selected_by_skill": True,
        "selected_rounds": [1],
        "observed_tool_call_ids": ["tool-skill"],
        "observed_statuses": ["COMPLETED"],
    }
    tool_node = next(item for item in tree["nodes"] if item["id"] == "tool:tool-skill")
    assert tool_node["skill_route_refs"] == [
        {
            "activation_id": "activation-python",
            "skill_id": "skill-python",
            "skill_version": 2,
            "route_index": 1,
            "selected_by_skill": True,
        }
    ]
