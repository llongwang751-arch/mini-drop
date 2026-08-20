from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.app.diagnosis.benchmark_cases import (
    BenchmarkCase,
    evaluation_request,
    load_benchmark_cases,
    score_diagnosis_detail,
)
from server.app.diagnosis.benchmark_catalog import load_benchmark_catalog


def test_exactly_ten_strict_executable_cases_match_catalog() -> None:
    catalog = load_benchmark_catalog()
    cases = load_benchmark_cases()

    assert len(cases) == 10
    assert {case["case_id"] for case in cases} == {
        case["case_id"] for case in catalog["core_cases"]
    }


def test_oracle_is_not_part_of_planner_input() -> None:
    case = load_benchmark_cases()[0]
    planner_input = {
        "query": case["query"],
        "trigger": case["trigger"],
        "topology": case["topology"],
        "evidence_plan": case["evidence_plan"],
    }

    assert "oracle" not in planner_input
    assert evaluation_request(case)["case_id"] == case["case_id"]
    assert "expected_root_cause" not in evaluation_request(case)


def test_multi_host_cases_require_one_agent_per_host() -> None:
    multi_host = [
        case for case in load_benchmark_cases()
        if case["topology"]["mode"] == "multi_host_single_agent_each"
    ]

    assert multi_host
    assert all(case["topology"]["minimum_hosts"] >= 2 for case in multi_host)
    assert all(
        case["topology"]["agent_deployment"] == "one_agent_per_host"
        for case in multi_host
    )


def test_oracle_classification_uses_cluster_location_taxonomy() -> None:
    expected_by_location = {
        "self": "self_code_or_process_pressure",
        "same_host": "same_host_noisy_neighbor",
        "downstream": "downstream_dependency",
        "shared_resource": "host_resource_contention",
        "unknown": "insufficient_evidence",
    }
    for case in load_benchmark_cases():
        oracle = case["oracle"]
        assert oracle["expected_classification"] == expected_by_location[
            oracle["expected_location_type"]
        ]


def test_oracle_scoring_checks_root_cause_snapshots_and_evidence() -> None:
    case = load_benchmark_cases()[0]
    oracle = case["oracle"]
    evidence = [{
        "evidence_id": "evidence-1",
        "source_type": "derived_artifact",
        "observed_value": {
            "benchmark_evidence_tags": case["evidence_plan"]["required_evidence"]
        },
    }]
    detail = {
        "normalized_intent": {"analysis_strategy": "CONSTRAINED_HYBRID"},
        "evidence": evidence,
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
                "evidence_refs": ["evidence-1"],
            },
        },
    }

    score = score_diagnosis_detail(case, detail)

    assert score["root_cause_exact_match"] is True
    assert score["snapshot_role_coverage_pct"] == 100
    assert score["evidence_integrity"] is True
    assert score["unsupported_claim"] is False
    assert score["score_pct"] == 100


def test_malformed_multi_host_case_is_rejected() -> None:
    payload = load_benchmark_cases()[0]
    payload["topology"] = {
        "mode": "multi_host_single_agent_each",
        "agent_deployment": "one_agent_per_host",
        "minimum_hosts": 1,
    }

    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(payload)
