import copy

import pytest

from server.app.diagnosis.benchmark_catalog import (
    load_benchmark_catalog,
    validate_benchmark_catalog,
)


def test_unified_benchmark_has_ten_shared_cases_and_all_tiers():
    catalog = load_benchmark_catalog()

    assert len(catalog["core_cases"]) == 10
    assert catalog["policy"]["shared_by_all_strategies"] is True
    assert {item["tier"] for item in catalog["sources"]} == {"T0", "T1", "T2", "T3"}
    assert all(case["required_evidence"] for case in catalog["core_cases"])
    assert len(catalog["references"]) >= 10
    assert all(case["agent_deployment"] == "one_agent_per_host" for case in catalog["core_cases"])
    assert any(
        case["topology_mode"] == "multi_host_single_agent_each"
        for case in catalog["core_cases"]
    )
    assert all(case["oracle_visibility"] == "evaluation_only" for case in catalog["core_cases"])


def test_unified_benchmark_rejects_duplicate_case_ids():
    catalog = load_benchmark_catalog()
    broken = copy.deepcopy(catalog)
    broken["core_cases"][1]["case_id"] = broken["core_cases"][0]["case_id"]

    with pytest.raises(ValueError, match="duplicate benchmark case_id"):
        validate_benchmark_catalog(broken)


def test_unified_benchmark_rejects_duplicate_source_ids():
    catalog = load_benchmark_catalog()
    broken = copy.deepcopy(catalog)
    broken["sources"][1]["source_id"] = broken["sources"][0]["source_id"]

    with pytest.raises(ValueError, match="duplicate source_id"):
        validate_benchmark_catalog(broken)


def test_unified_benchmark_rejects_missing_reference_and_oracle_leak():
    catalog = load_benchmark_catalog()
    missing_ref = copy.deepcopy(catalog)
    missing_ref["sources"][1]["reference_ids"].append("missing-paper")
    with pytest.raises(ValueError, match="unknown citation"):
        validate_benchmark_catalog(missing_ref)

    oracle_leak = copy.deepcopy(catalog)
    oracle_leak["core_cases"][0]["oracle_visibility"] = "planner_context"
    with pytest.raises(ValueError, match="leaks oracle"):
        validate_benchmark_catalog(oracle_leak)
