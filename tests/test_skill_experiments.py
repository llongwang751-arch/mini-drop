from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.app.database import init_db, reset_engine
from server.app.drop_insight.operator_memory import (
    delete_operator_preference,
    load_safe_agent_preferences,
    put_operator_preference,
)
from server.app.drop_insight.schemas import (
    AssignDiagnosticExperimentRequest,
    CreateDiagnosticExperimentRequest,
    CreateDiagnosisRequestV2,
    DiagnosticTarget,
    DiagnosticTimeRange,
    RecordDiagnosticExperimentOutcomeRequest,
)
from server.app.drop_insight.service import create_diagnosis
from server.app.drop_insight.skill_experiments import (
    approve_experiment_rollout,
    assign_experiment_diagnosis,
    create_experiment,
    evaluate_experiment,
    record_experiment_outcome,
    summarize_experiment,
)
from server.app.diagnosis_worker import _evaluate_changed_skill_experiments


@pytest.fixture(autouse=True)
def _database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _diagnosis_request() -> CreateDiagnosisRequestV2:
    end = datetime.now(timezone.utc)
    return CreateDiagnosisRequestV2(
        query="受控 Python 服务 CPU 持续升高",
        target=DiagnosticTarget(
            service="python-hotspot",
            environment="controlled-lab",
            agent_id="agent-a",
            pid=123,
        ),
        time_range=DiagnosticTimeRange(start=end - timedelta(minutes=5), end=end),
        mode="ASSISTED",
    )


def test_randomized_experiment_persists_significance_and_human_gate() -> None:
    experiment = create_experiment(
        CreateDiagnosticExperimentRequest(
            name="Skill route controlled A/B",
            minimum_labeled_per_arm=5,
            minimum_effect_percentage_points=10,
        ),
        created_by="operator-a",
    )
    arm_counts = {"AUTO": 0, "DISABLED": 0}
    assignments = []
    for index in range(200):
        if min(arm_counts.values()) >= 10:
            break
        value = assign_experiment_diagnosis(
            experiment.id,
            AssignDiagnosticExperimentRequest(
                unit_key=f"controlled-case-{index}",
                stratum="python-cpu",
                diagnosis=_diagnosis_request(),
            ),
            principal="operator-a",
        )
        assert value is not None
        assignment = value["assignment"]
        arm = assignment["arm"]
        arm_counts[arm] += 1
        assignments.append(assignment)
    assert min(arm_counts.values()) >= 10

    accepted = {"AUTO": 0, "DISABLED": 0}
    for assignment in assignments:
        arm = assignment["arm"]
        if accepted[arm] >= 10:
            continue
        accepted[arm] += 1
        # Strong deterministic fixture: 10/10 vs 1/10. This verifies the
        # platform statistics, not a claim about production diagnosis quality.
        correct = arm == "AUTO" or accepted[arm] == 1
        result = record_experiment_outcome(
            experiment.id,
            assignment["diagnosis_id"],
            RecordDiagnosticExperimentOutcomeRequest(
                root_cause_correct=correct,
                outcome_source="CONTROLLED_ORACLE",
            ),
            recorded_by="oracle-runner",
        )
        assert result is not None

    evaluation = evaluate_experiment(experiment.id, evaluated_by="operator-a")
    assert evaluation is not None
    assert evaluation["significance"]["p_value"] < 0.05
    assert evaluation["significance"]["delta_percentage_points"] == 90.0
    assert evaluation["recommendation"]["eligible"] is True
    assert evaluation["experiment"]["status"] == "ROLLOUT_RECOMMENDED"
    assert evaluation["truth_boundary"]["automatic_rollout"] is False

    approved = approve_experiment_rollout(
        experiment.id,
        approved_by="approver-a",
        reason="配对评审通过，先小流量放量",
    )
    assert approved is not None
    assert approved.status == "APPROVED"
    assert approved.approved_by == "approver-a"

    summary = summarize_experiment(experiment.id)
    assert summary is not None
    assert len(summary["metric_history"]) == 1
    assert summary["experiment"]["assignment_salt_sha256"]
    assert "assignment_salt" not in summary["experiment"]


def test_operator_memory_is_explicit_scoped_and_non_authoritative() -> None:
    stored = put_operator_preference(
        "operator-a",
        project_scope="*",
        memory_key="explanation_depth",
        value="detailed",
    )
    assert stored.source == "EXPLICIT_USER"
    diagnosis = create_diagnosis(
        _diagnosis_request(),
        created_by="operator-a",
    )

    assert load_safe_agent_preferences(diagnosis.id) == {
        "explanation_depth": "detailed"
    }
    other = create_diagnosis(_diagnosis_request(), created_by="operator-b")
    assert load_safe_agent_preferences(other.id) == {}

    with pytest.raises(ValueError, match="unsupported"):
        put_operator_preference(
            "operator-a",
            project_scope="*",
            memory_key="allowed_tools",
            value=["shell"],
        )
    assert delete_operator_preference(
        "operator-a",
        project_scope="*",
        memory_key="explanation_depth",
    ) is True
    assert load_safe_agent_preferences(diagnosis.id) == {}


def test_background_monitor_snapshots_only_after_new_labels() -> None:
    experiment = create_experiment(
        CreateDiagnosticExperimentRequest(name="Monitored Skill experiment"),
        created_by="operator-a",
    )
    assigned = assign_experiment_diagnosis(
        experiment.id,
        AssignDiagnosticExperimentRequest(
            unit_key="monitor-case-1",
            stratum="python-cpu",
            diagnosis=_diagnosis_request(),
        ),
        principal="operator-a",
    )
    assert assigned is not None
    record_experiment_outcome(
        experiment.id,
        assigned["assignment"]["diagnosis_id"],
        RecordDiagnosticExperimentOutcomeRequest(
            root_cause_correct=True,
            outcome_source="CONTROLLED_ORACLE",
        ),
        recorded_by="oracle-runner",
    )

    assert _evaluate_changed_skill_experiments() == 1
    assert _evaluate_changed_skill_experiments() == 0
    summary = summarize_experiment(experiment.id)
    assert summary is not None
    assert len(summary["metric_history"]) == 1
