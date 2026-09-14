from scripts.run_fault_plaza_strict_acceptance import evaluate_intervention, evaluate_reports, ORACLES
from scripts.run_fault_plaza_closure_campaign import DECISIVE_COLLECTOR_BY_SCENARIO


def test_all_21_have_strict_contract():
    assert len(ORACLES) == 21
    assert set(ORACLES) == set(DECISIVE_COLLECTOR_BY_SCENARIO)


def test_partial_hypothesis_and_verified_wrong_cause_never_pass():
    report = {"report_id": "r", "evidence_refs": ["e"], "conclusion": "C++ 内存 RSS 增长",
              "verification": {"status": "PARTIAL_WITHOUT_COUNTER", "coverage_ratio": 1,
                               "has_independent_counter_or_control": True}}
    assert not evaluate_reports("cpp-memory-growth", [report])["root_cause_accepted"]
    report["verification"]["status"] = "VERIFIED"
    assert evaluate_reports("cpp-memory-growth", [report])["root_cause_accepted"]
    assert not evaluate_reports("cpp-lock-contention", [report])["root_cause_accepted"]
    report["counter_evidence_refs"] = ["unresolved-counter"]
    assert not evaluate_reports("cpp-memory-growth", [report])["root_cause_accepted"]
    report["counter_evidence_refs"] = []
    report["conclusion"] = "不能把假设 RSS 增长写成最终根因"
    assert not evaluate_reports("cpp-memory-growth", [report])["root_cause_accepted"]


def _window(first, last):
    return {"first": {"snapshot": {"lock_wait_ms": first}},
            "last": {"snapshot": {"lock_wait_ms": last}}, "elapsed_seconds": 4}


def test_recovery_uses_deltas_not_historical_counter_totals():
    windows = {"baseline": _window(1000, 1000), "fault": _window(0, 200),
               "recovery": _window(5000, 5000)}
    assert evaluate_intervention("cpp-lock-contention", windows)["recovery_observed"]
    windows["recovery"] = _window(5000, 5200)
    assert not evaluate_intervention("cpp-lock-contention", windows)["recovery_observed"]


def test_missing_measurements_or_counter_reset_fail_closed():
    assert not evaluate_intervention("cpp-lock-contention", {})["recovery_observed"]
    windows = {"baseline": _window(0, 0), "fault": _window(100, 0), "recovery": _window(0, 0)}
    assert not evaluate_intervention("cpp-lock-contention", windows)["injection_observed"]
    windows["fault"] = _window(0, float("inf"))
    assert not evaluate_intervention("cpp-lock-contention", windows)["injection_observed"]
