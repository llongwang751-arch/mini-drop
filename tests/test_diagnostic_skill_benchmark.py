import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.skill_benchmark import (
    BenchmarkObservation,
    EXPECTED_FAMILY_COUNTS,
    REQUIRED_CASE_FIELDS,
    compare_benchmark_runs,
    validate_benchmark_dataset,
)
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
    DropInsightFeedbackModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
)


FIXTURE = Path(__file__).parent / "fixtures" / "diagnostic_skill_evolution" / "cases.json"


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _load_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _load_cases() -> list[dict]:
    return _load_payload()["cases"]


def _seed_verified_trajectory(
    diagnosis_id: str,
    *,
    evidence_refs: list[str] | None = None,
) -> None:
    timestamp = datetime.now(timezone.utc)
    session = new_session()
    session.add(
        DropInsightSessionModel(
            id=diagnosis_id,
            query="order service CPU is high",
            target_json={
                "service": "order-service",
                "environment": "staging",
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
            arguments_json={},
            policy_decision="ALLOW",
            policy_checks_json=[],
            policy_reason="approved",
            status="COMPLETED",
            result_json={"top_symbol": "calculate_price"},
            requested_by="planner",
            created_at=timestamp,
        )
    )
    session.add(
        DropInsightReportModel(
            id=f"report-{diagnosis_id}",
            diagnosis_id=diagnosis_id,
            conclusion="verified user-space CPU hotspot",
            confidence=930,
            evidence_refs_json=evidence_refs or [f"evidence-{diagnosis_id}"],
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
            predicted_conclusion="verified user-space CPU hotspot",
            requested_replan=False,
            created_by="independent-reviewer",
            created_at=timestamp,
        )
    )
    session.commit()
    session.close()


def _publish_cpu_skill(diagnosis_id: str = "source-cpu-trajectory") -> dict:
    _seed_verified_trajectory(diagnosis_id)
    candidate = create_candidate_from_diagnosis(diagnosis_id, created_by="reviewer")
    evaluated = evaluate_skill(candidate["skill_id"])
    assert evaluated["gate_metrics"]["eligible"] is True
    return publish_skill(candidate["skill_id"])


def _baseline_observations(cases: list[dict]) -> list[BenchmarkObservation]:
    observations = []
    for case in cases:
        family = case["incident_family"]
        if family == "CONTAMINATED_EVIDENCE":
            observations.append(
                BenchmarkObservation(
                    case_id=case["case_id"],
                    action="create_candidate",
                    mode="APPLY",
                    tool_calls=0,
                    candidate_admitted=True,
                    evidence_refs=("missing-evidence-artifact",),
                    evidence_integrity="FAILED",
                    evidence_refs_valid=False,
                )
            )
        else:
            observations.append(
                BenchmarkObservation(
                    case_id=case["case_id"],
                    action=case["baseline_action"],
                    mode="FALLBACK",
                    tool_calls=case["baseline_tool_calls"],
                )
            )
    return observations


def _route_observation(case: dict) -> BenchmarkObservation:
    plan = {"tool_name": case["baseline_action"]}
    activation = apply_active_skill(
        f"benchmark-{case['case_id']}", case["category"], plan, case["target"]
    )
    return BenchmarkObservation(
        case_id=case["case_id"],
        action=plan["tool_name"],
        mode="APPLY" if activation else "FALLBACK",
        tool_calls=1 if activation else case["baseline_tool_calls"],
    )


def _skill_enabled_observations(cases: list[dict]) -> list[BenchmarkObservation]:
    published = _publish_cpu_skill()
    by_family = {
        family: [case for case in cases if case["incident_family"] == family]
        for family in EXPECTED_FAMILY_COUNTS
    }
    observations = [
        _route_observation(case)
        for family in ("SIMILAR_INCIDENT", "MISLEADING_INCIDENT", "ENVIRONMENT_DRIFT")
        for case in by_family[family]
    ]

    for case in by_family["WRONG_FEEDBACK"]:
        routed = _route_observation(case)
        record_activation_outcome(f"benchmark-{case['case_id']}", "wrong")
        observations.append(
            BenchmarkObservation(
                case_id=routed.case_id,
                action=routed.action,
                mode=routed.mode,
                tool_calls=routed.tool_calls,
                skill_status=get_skill(published["skill_id"])["status"],
            )
        )

    post_quarantine_plan = {"tool_name": "collect_sys_metrics"}
    assert apply_active_skill(
        "benchmark-post-quarantine",
        "CPU_HOTSPOT",
        post_quarantine_plan,
        {"service": "order-service", "environment": "staging"},
    ) is None

    second = _publish_cpu_skill("source-cpu-trajectory-v2")
    rollback_case = by_family["VERSION_ROLLBACK"][0]
    failed_plan = {"tool_name": rollback_case["baseline_action"]}
    failed_activation = apply_active_skill(
        "benchmark-rollback-v2-failure",
        rollback_case["category"],
        failed_plan,
        rollback_case["target"],
    )
    assert failed_activation is not None
    assert failed_activation["skill_id"] == second["skill_id"]
    record_activation_outcome(
        "benchmark-rollback-v2-failure", rollback_case["failure_feedback"]
    )
    assert get_skill(second["skill_id"])["status"] == "ACTIVE"
    restored = rollback_skill(second["skill_id"])
    rollback_plan = {"tool_name": rollback_case["baseline_action"]}
    restored_activation = apply_active_skill(
        f"benchmark-{rollback_case['case_id']}",
        rollback_case["category"],
        rollback_plan,
        rollback_case["target"],
    )
    assert restored_activation is not None
    assert restored_activation["skill_id"] == published["skill_id"]
    observations.append(
        BenchmarkObservation(
            case_id=rollback_case["case_id"],
            action=rollback_plan["tool_name"],
            mode="APPLY",
            tool_calls=1,
            restored_version=restored["version"],
        )
    )
    assert restored["skill_id"] == published["skill_id"]
    assert get_skill(second["skill_id"])["status"] == "QUARANTINED"

    polluted_case = by_family["CONTAMINATED_EVIDENCE"][0]
    _seed_verified_trajectory(
        "polluted-evidence-source", evidence_refs=["missing-evidence-artifact"]
    )
    polluted_candidate = create_candidate_from_diagnosis(
        "polluted-evidence-source", created_by="reviewer"
    )
    observations.append(
        BenchmarkObservation(
            case_id=polluted_case["case_id"],
            action="create_candidate",
            mode="APPLY",
            tool_calls=0,
            candidate_admitted=bool(polluted_candidate),
            evidence_refs=("missing-evidence-artifact",),
            evidence_integrity="FAILED",
            evidence_refs_valid=False,
        )
    )
    return observations


def test_fixture_has_exact_independent_composition_and_schema():
    payload = _load_payload()
    validate_benchmark_dataset(payload)
    cases = payload["cases"]

    assert len(cases) == 15
    assert Counter(case["incident_family"] for case in cases) == Counter(
        EXPECTED_FAMILY_COUNTS
    )
    assert all(REQUIRED_CASE_FIELDS <= case.keys() for case in cases)
    assert all(case["oracle_refs"] for case in cases)
    assert not {
        case["case_id"] for case in cases
    }.intersection(payload["source_diagnosis_ids"])


def test_offline_benchmark_reports_baseline_and_skill_enabled_contracts():
    cases = _load_cases()
    result = compare_benchmark_runs(
        cases,
        _baseline_observations(cases),
        _skill_enabled_observations(cases),
    )

    assert result["benchmark_kind"] == "DETERMINISTIC_SKILL_EVOLUTION_REPLAY"
    assert set(result) >= {"baseline", "skill_enabled", "delta"}
    assert result["baseline"]["passed"] == 6
    assert result["skill_enabled"]["passed"] == 9
    assert result["delta"]["pass_rate"] > 0
    assert result["delta"]["average_tool_call_count"] < 0

    metrics = result["skill_enabled"]["metrics"]
    assert metrics["root_cause_accuracy"]["status"] == "NOT_MEASURED"
    assert metrics["average_diagnosis_duration_ms"]["status"] == "NOT_MEASURED"
    assert metrics["misleading_counterexample_refusal_rate"] == {
        "status": "MEASURED",
        "numerator": 0,
        "denominator": 4,
        "value": 0.0,
    }
    assert metrics["environment_drift_correct_degradation_rate"] == {
        "status": "MEASURED",
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert metrics["similar_incident_benefit_rate"] == {
        "status": "MEASURED",
        "numerator": 5,
        "denominator": 5,
        "value": 1.0,
    }
    assert metrics["quarantine_success_rate"] == {
        "status": "MEASURED",
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }
    assert metrics["rollback_success_rate"]["value"] == 1.0
    assert metrics["evidence_reference_completeness_rate"] == {
        "status": "MEASURED",
        "numerator": 0,
        "denominator": 1,
        "value": 0.0,
    }
    assert metrics["negative_transfer_rate"] == {
        "status": "MEASURED",
        "numerator": 6,
        "denominator": 15,
        "value": 0.4,
    }


def test_scorer_rejects_duplicate_unknown_and_invalid_observations():
    case = _load_cases()[0]
    valid = BenchmarkObservation(case["case_id"], "start_perf_profile", "APPLY", 1)

    with pytest.raises(ValueError, match="duplicate observation"):
        compare_benchmark_runs([case], [valid, valid], [valid])
    with pytest.raises(ValueError, match="unexpected observation"):
        compare_benchmark_runs(
            [case],
            [BenchmarkObservation("unknown", "start_perf_profile", "APPLY", 1)],
            [valid],
        )
    with pytest.raises(ValueError, match="non-negative"):
        compare_benchmark_runs(
            [case],
            [BenchmarkObservation(case["case_id"], "start_perf_profile", "APPLY", -1)],
            [valid],
        )


def test_missing_observation_fails_without_zero_cost_credit():
    cases = _load_cases()[:2]
    observation = BenchmarkObservation(
        cases[0]["case_id"], "start_perf_profile", "APPLY", 1
    )
    result = compare_benchmark_runs(cases, [observation], [observation])["skill_enabled"]

    assert result["passed"] == 1
    assert result["metrics"]["average_tool_call_count"] == {
        "status": "PROJECTED",
        "sample_count": 1,
        "value": 1.0,
    }
    assert {row["reason"] for row in result["cases"]} == {
        "PASS",
        "MISSING_OBSERVATION",
    }


def test_relative_negative_transfer_counts_baseline_regression_and_cost_increase():
    similar, misleading = _load_cases()[:6:5]
    baseline = [
        BenchmarkObservation(
            similar["case_id"], similar["expected_action"], "APPLY", 1
        ),
        BenchmarkObservation(
            misleading["case_id"], misleading["expected_action"], "FALLBACK", 1
        ),
    ]
    skill_enabled = [
        BenchmarkObservation(
            similar["case_id"], similar["expected_action"], "APPLY", 2
        ),
        BenchmarkObservation(
            misleading["case_id"], misleading["forbidden_action"], "APPLY", 1
        ),
    ]

    result = compare_benchmark_runs([similar, misleading], baseline, skill_enabled)

    assert result["skill_enabled"]["metrics"]["negative_transfer_rate"] == {
        "status": "MEASURED",
        "numerator": 2,
        "denominator": 2,
        "value": 1.0,
    }
    assert all(row["negative_transfer"] for row in result["skill_enabled"]["cases"])


@pytest.mark.xfail(
    strict=True,
    reason="active-skill matching does not distinguish pseudo-similar root-cause subsystems",
)
def test_pseudo_similar_incidents_refuse_the_learned_cpu_route():
    _publish_cpu_skill()
    cases = [
        case
        for case in _load_cases()
        if case["incident_family"] == "MISLEADING_INCIDENT"
    ]

    for case in cases:
        plan = {"tool_name": case["baseline_action"]}
        activation = apply_active_skill(
            f"misleading-probe-{case['case_id']}",
            case["category"],
            plan,
            case["target"],
        )
        assert activation is None
        assert plan["tool_name"] == case["expected_action"]


@pytest.mark.xfail(
    strict=True,
    reason="active-skill matching does not preflight collector capability or permission",
)
def test_capability_drift_refuses_unavailable_learned_collector():
    _publish_cpu_skill()
    case = next(case for case in _load_cases() if case["case_id"] == "DRIFT-CAP-001")
    plan = {"tool_name": case["baseline_action"]}

    activation = apply_active_skill(
        "capability-drift-probe", case["category"], plan, case["target"]
    )

    assert activation is None
    assert plan["tool_name"] == case["expected_action"]


@pytest.mark.xfail(
    strict=True,
    reason="candidate admission checks only a non-empty evidence reference list",
)
def test_contaminated_evidence_is_rejected_before_candidate_creation():
    _seed_verified_trajectory(
        "polluted-evidence-probe", evidence_refs=["missing-evidence-artifact"]
    )

    with pytest.raises(ValueError, match="evidence|provenance|integrity"):
        create_candidate_from_diagnosis(
            "polluted-evidence-probe", created_by="reviewer"
        )
