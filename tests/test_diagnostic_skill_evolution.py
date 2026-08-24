from datetime import datetime, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.skill_evolution import (
    apply_active_skill,
    create_candidate_from_diagnosis,
    evaluate_skill,
    get_skill,
    publish_skill,
    record_activation_outcome,
    rollback_skill,
)
from server.app.models import (
    DiagnosticSkillActivationModel,
    DiagnosticSkillModel,
    DropInsightFeedbackModel,
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


def _seed_verified_trajectory(diagnosis_id: str, *, environment: str = "staging") -> None:
    session = new_session()
    timestamp = datetime.now(timezone.utc)
    session.add(
        DropInsightSessionModel(
            id=diagnosis_id,
            query="order service CPU is high",
            target_json={
                "service": "order-service",
                "environment": environment,
                "agent_id": "agent-a",
                "pid": 123,
            },
            time_range_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            mode="ASSISTED",
            budget_json={},
            status="COMPLETED",
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.add(
        DropInsightToolCallModel(
            id=f"tool-{diagnosis_id}",
            diagnosis_id=diagnosis_id,
            tool_name="start_perf_profile",
            arguments_json={"agent_id": "agent-a", "pid": 123},
            policy_decision="ALLOW",
            policy_checks_json=[],
            policy_reason="approved safe read-only probe",
            status="COMPLETED",
            result_json={"top_symbol": "calculate_price", "cpu_percent": 74},
            requested_by="planner",
            created_at=timestamp,
        )
    )
    session.add(
        DropInsightReportModel(
            id=f"report-{diagnosis_id}",
            diagnosis_id=diagnosis_id,
            conclusion="calculate_price is the verified CPU hotspot",
            confidence=920,
            evidence_refs_json=[f"evidence-{diagnosis_id}"],
            counter_evidence_refs_json=[],
            assumptions_json=[],
            limitations_json=[],
            next_actions_json=[],
            claims_json=[],
            verification_json={"status": "VERIFIED"},
            effects_status="APPLIED",
            created_at=timestamp,
        )
    )
    session.add(
        DropInsightFeedbackModel(
            id=f"feedback-{diagnosis_id}",
            diagnosis_id=diagnosis_id,
            report_id=f"report-{diagnosis_id}",
            feedback_label="correct",
            predicted_conclusion="calculate_price is the verified CPU hotspot",
            requested_replan=False,
            created_by="reviewer",
            created_at=timestamp,
        )
    )
    session.commit()
    session.close()


def _publish_source(diagnosis_id: str = "diagnosis-source") -> dict:
    _seed_verified_trajectory(diagnosis_id)
    candidate = create_candidate_from_diagnosis(diagnosis_id, created_by="reviewer")
    evaluated = evaluate_skill(candidate["skill_id"])
    assert evaluated["gate_metrics"]["eligible"] is True
    return publish_skill(candidate["skill_id"])


def test_verified_trajectory_becomes_versioned_active_skill_once():
    _seed_verified_trajectory("diagnosis-source")
    first = create_candidate_from_diagnosis("diagnosis-source", created_by="reviewer")
    duplicate = create_candidate_from_diagnosis("diagnosis-source", created_by="reviewer")

    assert duplicate["skill_id"] == first["skill_id"]
    evaluated = evaluate_skill(first["skill_id"])
    assert evaluated["gate_metrics"]["eligible"] is True
    assert evaluated["gate_metrics"]["passed"] == 3
    assert evaluated["gate_metrics"]["total"] == 3
    assert evaluated["gate_metrics"]["evaluation_mode"] == "DETERMINISTIC_CONTRACT_GATE"
    assert evaluated["gate_metrics"]["requires_campaign_validation"] is True
    assert evaluated["gate_metrics"]["negative_transfer_guard"] is True
    assert evaluated["gate_metrics"]["environment_drift_guard"] is True
    assert {item["case_kind"] for item in evaluated["evaluations"]} == {
        "POSITIVE_REPLAY",
        "MISLEADING_NEGATIVE",
        "ENVIRONMENT_DRIFT",
    }
    assert publish_skill(first["skill_id"])["status"] == "ACTIVE"


def test_active_skill_reuses_route_only_for_matching_context():
    published = _publish_source()
    plan = {"tool_name": "collect_sys_metrics"}
    activation = apply_active_skill(
        "diagnosis-reuse",
        "CPU_HOTSPOT",
        plan,
        {"service": "order-service", "environment": "staging"},
    )

    assert activation is not None
    assert activation["skill_id"] == published["skill_id"]
    assert activation["baseline_tool"] == "collect_sys_metrics"
    assert activation["selected_tool"] == "start_perf_profile"
    assert plan["tool_name"] == "start_perf_profile"

    drift_plan = {"tool_name": "collect_sys_metrics"}
    assert apply_active_skill(
        "diagnosis-drift",
        "CPU_HOTSPOT",
        drift_plan,
        {"service": "order-service", "environment": "production"},
    ) is None
    assert drift_plan["tool_name"] == "collect_sys_metrics"

    misleading_plan = {"tool_name": "collect_network_diagnostics"}
    assert apply_active_skill(
        "diagnosis-misleading",
        "NETWORK_DEGRADATION",
        misleading_plan,
        {"service": "order-service", "environment": "staging"},
    ) is None


def test_active_skill_advances_through_verified_probe_order():
    published = _publish_source()
    session = new_session()
    skill = session.get(DiagnosticSkillModel, published["skill_id"])
    skill.strategy_json = {
        **(skill.strategy_json or {}),
        "probe_order": ["start_perf_profile", "collect_sys_metrics"],
    }
    session.commit()
    session.close()

    target = {"service": "order-service", "environment": "staging"}
    first_plan = {"tool_name": "collect_network_diagnostics"}
    first = apply_active_skill("diagnosis-route", "CPU_HOTSPOT", first_plan, target)
    assert first["selected_tool"] == "start_perf_profile"

    session = new_session()
    session.add(
        DropInsightToolCallModel(
            id="tool-diagnosis-route-perf",
            diagnosis_id="diagnosis-route",
            tool_name="start_perf_profile",
            arguments_json={},
            policy_decision="ALLOW",
            policy_checks_json=[],
            policy_reason="approved",
            status="COMPLETED",
            result_json={},
            requested_by="planner",
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    session.close()

    second_plan = {"tool_name": "collect_network_diagnostics"}
    second = apply_active_skill("diagnosis-route", "CPU_HOTSPOT", second_plan, target)
    assert second["selected_tool"] == "collect_sys_metrics"
    assert second_plan["tool_name"] == "collect_sys_metrics"


def test_repeated_wrong_feedback_quarantines_skill():
    published = _publish_source()
    target = {"service": "order-service", "environment": "staging"}
    for diagnosis_id in ("diagnosis-wrong-1", "diagnosis-wrong-2"):
        plan = {"tool_name": "collect_sys_metrics"}
        assert apply_active_skill(diagnosis_id, "CPU_HOTSPOT", plan, target)
        record_activation_outcome(diagnosis_id, "wrong")

    skill = get_skill(published["skill_id"])
    assert skill["status"] == "QUARANTINED"
    assert skill["gate_metrics"]["negative_transfer_count"] == 2


def test_new_version_can_roll_back_to_previous_published_strategy():
    first = _publish_source("diagnosis-v1")
    _seed_verified_trajectory("diagnosis-v2")
    second = create_candidate_from_diagnosis("diagnosis-v2", created_by="reviewer")
    assert second["version"] == 2
    evaluate_skill(second["skill_id"])
    publish_skill(second["skill_id"])

    assert get_skill(first["skill_id"])["status"] == "RETIRED"
    restored = rollback_skill(second["skill_id"])
    assert restored["skill_id"] == first["skill_id"]
    assert restored["status"] == "ACTIVE"
    assert get_skill(second["skill_id"])["status"] == "QUARANTINED"
