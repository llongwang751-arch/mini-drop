from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.run_cpu_campaign_window import create_payload

from server.app.diagnosis.benchmark_cases import load_benchmark_case
from server.app.diagnosis.benchmark_runner import (
    STRATEGIES,
    build_run_plan,
    campaign_progress,
    evaluate_submissions,
    render_html_report,
    scoring_detail_from_api,
    upsert_submission,
    validate_campaign_completeness,
)


def _perfect_detail(case_id: str) -> dict:
    case = load_benchmark_case(case_id)
    oracle = case["oracle"]
    return {
        "status": "COMPLETED",
        "evidence": [{
            "evidence_id": "ev-1",
            "source_type": "derived_artifact",
            "observed_value": {
                "benchmark_evidence_tags": case["evidence_plan"]["required_evidence"]
            },
        }],
        "evidence_snapshots": [
            {"evidence_role": role}
            for role in case["evidence_plan"]["snapshot_roles"]
        ],
        "latest_conclusion": {
            "root_location": {
                "type": oracle["expected_location_type"],
                "target_ref": oracle.get("expected_instance_id"),
            },
            "domain_cause": {"type": oracle["expected_domain_type"]},
            "cluster_assessment": {
                "classification": oracle.get("expected_classification"),
                "evidence_refs": ["ev-1"],
            },
        },
    }


def test_run_plan_is_10_cases_times_three_strategies_times_three_runs() -> None:
    plan = build_run_plan()

    assert plan["case_count"] == 10
    assert plan["repetitions"] == 3
    assert plan["execution_count"] == 90
    assert plan["oracle_in_planner_input"] is False
    assert all(
        "oracle" not in execution["planner_input"]
        for execution in plan["executions"]
    )


def test_cpu_campaign_payload_binds_controlled_baseline_and_incident_window() -> None:
    incident_start = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)
    payload = create_payload(
        "CONSTRAINED_HYBRID",
        agent_id="agent-1",
        host_id="host-1",
        pid=1234,
        baseline_task_id="task-baseline-1",
        incident_started_at=incident_start,
    )

    assert payload["baseline_task_ids"] == ["task-baseline-1"]
    assert payload["context"]["time_range"]["start"] == incident_start.isoformat()
    assert payload["context"]["time_range"]["source"] == "request_context"


def test_run_plan_rejects_less_than_required_repetitions() -> None:
    with pytest.raises(ValueError, match="at least 3"):
        build_run_plan(repetitions=2)


def test_shared_oracle_compares_all_three_strategies() -> None:
    submissions = [
        {
            "case_id": "T1-CODE-001",
            "strategy": strategy,
            "repetition": 1,
            "diagnosis_detail": _perfect_detail("T1-CODE-001"),
        }
        for strategy in STRATEGIES
    ]

    report = evaluate_submissions(submissions)

    assert report["result_count"] == 3
    assert report["oracle_isolated"] is True
    assert set(report["strategy_metrics"]) == set(STRATEGIES)
    assert all(
        metrics["average_score_pct"] == 100
        for metrics in report["strategy_metrics"].values()
    )
    assert all(
        metrics["analysis_output_coverage_rate"] == 1.0
        for metrics in report["strategy_metrics"].values()
    )
    html = render_html_report(report)
    assert "90 次诊断策略评测" in html
    assert "分析输出覆盖" in html
    assert "逐次执行明细" in html


def test_complete_campaign_gate_rejects_partial_results() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        validate_campaign_completeness([
            {
                "case_id": "T1-CODE-001",
                "strategy": "CONSTRAINED_HYBRID",
                "repetition": 1,
                "diagnosis_detail": _perfect_detail("T1-CODE-001"),
            }
        ])


def test_complete_campaign_gate_accepts_all_90_results() -> None:
    submissions = []
    for execution in build_run_plan()["executions"]:
        submissions.append({
            "case_id": execution["case_id"],
            "strategy": execution["strategy"],
            "repetition": execution["repetition"],
            "diagnosis_detail": _perfect_detail(execution["case_id"]),
        })
    assert validate_campaign_completeness(submissions)["complete"] is True


def test_scoring_detail_removes_oracle_and_unrelated_api_fields() -> None:
    compact = scoring_detail_from_api({
        "code": 0,
        "data": {
            "diagnosis_id": "diag-1",
            "status": "COMPLETED",
            "normalized_intent": {"analysis_strategy": "CONSTRAINED_HYBRID"},
            "latest_conclusion": {"summary": "done"},
            "evidence_snapshots": [],
            "evaluation_oracle": {"expected_domain_type": "cpu"},
            "events": [{"large": "audit payload"}],
        },
    })

    assert compact["diagnosis_id"] == "diag-1"
    assert "evaluation_oracle" not in compact
    assert "events" not in compact


def test_submission_file_is_atomic_unique_and_resumable(tmp_path) -> None:
    path = tmp_path / "submissions.json"
    item = {
        "case_id": "T1-CODE-001",
        "strategy": "CONSTRAINED_HYBRID",
        "repetition": 1,
        "diagnosis_detail": _perfect_detail("T1-CODE-001"),
    }

    result = upsert_submission(path, item)
    assert result["recorded"] == 1
    assert campaign_progress(__import__("json").loads(path.read_text()))["remaining"] == 89
    with pytest.raises(ValueError, match="already recorded"):
        upsert_submission(path, item)
    assert upsert_submission(path, item, overwrite=True)["action"] == "replaced"


def test_submission_rejects_non_terminal_diagnosis(tmp_path) -> None:
    detail = _perfect_detail("T1-CODE-001")
    detail["status"] = "WAITING_APPROVAL"
    with pytest.raises(ValueError, match="must be terminal"):
        upsert_submission(tmp_path / "submissions.json", {
            "case_id": "T1-CODE-001",
            "strategy": "CONSTRAINED_HYBRID",
            "repetition": 1,
            "diagnosis_detail": detail,
        })


def test_score_exposes_missing_required_evidence_and_overconfident_completion() -> None:
    detail = _perfect_detail("T1-CPU-001")
    detail["evidence"][0]["observed_value"] = {
        "summary": {"process_cpu_core_usage": 1.7}
    }

    report = evaluate_submissions([{
        "case_id": "T1-CPU-001",
        "strategy": "CONSTRAINED_HYBRID",
        "repetition": 1,
        "diagnosis_detail": detail,
    }])
    result = report["results"][0]

    assert result["captured_required_evidence"] == ["cpu_metric_change"]
    assert result["missing_required_evidence"] == ["profile_hot_function"]
    assert result["required_evidence_coverage_pct"] == 50.0
    assert result["completion_calibrated"] is False
    assert report["strategy_metrics"]["CONSTRAINED_HYBRID"][
        "completion_calibration_rate"
    ] == 0.0


def test_missing_artifact_tags_do_not_satisfy_evidence_requirement() -> None:
    detail = _perfect_detail("T1-CPU-001")
    detail["evidence"][0]["data_quality"] = "MISSING"

    report = evaluate_submissions([{
        "case_id": "T1-CPU-001",
        "strategy": "CONSTRAINED_HYBRID",
        "repetition": 1,
        "diagnosis_detail": detail,
    }])

    result = report["results"][0]
    assert result["captured_required_evidence"] == []
    assert result["required_evidence_coverage_pct"] == 0.0
