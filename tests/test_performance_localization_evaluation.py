from copy import deepcopy

import pytest

from scripts.evaluate_performance_localization import evaluate_localization_reports


def report(domain="network_latency"):
    return {"report_id": "r1", "evidence_refs": ["e1"], "counter_evidence_refs": [],
            "verification": {"status": "VERIFIED", "claim_scope": "BOUNDED_OBSERVATION",
                "causal_root_cause_verified": False,
                "observation_verification": {"status": "VERIFIED", "checked_ratio": 1},
                "bottleneck_localization": {"status": "LOCALIZED", "domain": domain,
                    "location": "HTTP 调用耗时路径", "evidence_refs": ["e1"],
                    "causal_root_cause_verified": False, "same_load_fix_verified": False}}}


def test_localization_is_graded_separately_from_causal_and_fix_acceptance():
    original = report()
    before = deepcopy(original)
    result = evaluate_localization_reports("go-network-latency", [original])
    assert result["localization_accepted"] is True
    assert original == before
    assert original["verification"]["causal_root_cause_verified"] is False


@pytest.mark.parametrize("fault", ["wrong_domain", "missing_reference", "unverified", "causal", "fix", "unchecked", "counter"])
def test_localization_rejects_wrong_domain_or_unproven_contract(fault):
    item = report()
    verification = item["verification"]
    local = verification["bottleneck_localization"]
    if fault == "wrong_domain":
        local["domain"] = "io_latency"
    elif fault == "missing_reference":
        local["evidence_refs"] = ["unknown"]
    elif fault == "unverified":
        verification["status"] = "PARTIAL_WITHOUT_COUNTER"
    elif fault == "causal":
        verification["causal_root_cause_verified"] = True
    elif fault == "fix":
        local["same_load_fix_verified"] = True
    elif fault == "unchecked":
        verification["observation_verification"]["checked_ratio"] = .5
    else:
        item["counter_evidence_refs"] = ["e2"]
    assert evaluate_localization_reports("go-network-latency", [item])["localization_accepted"] is False


def test_cpu_localization_requires_independent_cpu_observation_and_go_hot_path():
    item = report("cpu_hot_path")
    verification = item["verification"]
    verification.update(coverage_ratio=1, has_independent_counter_or_control=True)
    verification["bottleneck_localization"]["location"] = "main.goCPUHotFunction"
    assert evaluate_localization_reports("go-cpu-hotspot", [item])["localization_accepted"] is True
    verification["has_independent_counter_or_control"] = False
    assert evaluate_localization_reports("go-cpu-hotspot", [item])["localization_accepted"] is False
