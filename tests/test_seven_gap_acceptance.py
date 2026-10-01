"""Independent counterexamples for seven gaps; fixtures are not live acceptance.

These checks deliberately preserve prior failures, and verify the distinction
between an absent measurement, a measured counterexample and a causal claim.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.engineering_profile_observation import measured_runtime_profile
from scripts.evaluate_engineering_diagnosis import evaluate_engineering_case
from server.app.drop_insight.cpu_criteria import (
    compile_cpu_observation_contract,
    cpu_observation_plan,
    cpu_plan_validation_error,
)
from server.app.drop_insight.performance_criteria import evaluate_performance_criterion
from server.app.metric_analyzers import (
    _application_counter_average,
    _parse_sys_metrics_document,
    _resource_competition_window,
)

ROOT = Path(__file__).resolve().parents[1]
OLD_CASES = ROOT / "reports/quality/interview-completion-20261001/performance-18-cases"
FROZEN_FAILURES = {
    "source-hotspot": ("cpu_hot_path", "205e8f48cd99e45566ca50db3c51033c7d698a8b427c15438038d00c025265ae"),
    "cpp-cpu-hotspot": ("cpu_hot_path", "d3dfc840dcb276a0a989dbeedd3e273a6e2c8df3dacf5e5b48f519ab283212a5"),
    "cpp-lock-contention": ("lock_contention", "3cf1451c1c3bb69d022c9e3299ac5a650584eed0acb7fceee4c3370cf2efa8d3"),
    "io-write-latency": ("io_latency", "30f7524f041840a4af432bb0ca3560831a4c322f4a25f1da0a715da3128c067d"),
    "java-file-io": ("io_latency", "f14a425e95152879abcba8e50c7de149f5d83162a782d1b2a2762482bb463731"),
    "cpp-file-io": ("io_latency", "f792715cb9cda5cb76c879e34676d7f855d7ddeb5070b419d1cd532b23cfccc1"),
    "noisy-neighbor": ("noisy_neighbor", "03bbdf4352fb529929b3a951d64d1b81bd89ed9aa5a83e8999c85043a6dea694"),
}


@pytest.mark.parametrize("scenario", FROZEN_FAILURES)
def test_old_failures_remain_immutable_and_are_not_upgraded_by_new_rules(scenario):
    domain, digest = FROZEN_FAILURES[scenario]
    raw = (OLD_CASES / (scenario + ".json")).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == digest
    case = json.loads(raw)
    before = deepcopy(case)
    result = evaluate_engineering_case(case, {"domain": domain})
    assert result["diagnosis_accepted"] is False
    assert result["causal_root_cause_verified"] is False
    assert case == before


@pytest.mark.parametrize("runtime", ["PYTHON", "GO", "CPP"])
def test_fifty_percent_cpu_contract_preserves_percentage_units(runtime):
    plan = cpu_observation_plan(runtime, 50)
    compiled = compile_cpu_observation_contract(plan["statement"], plan["expected_observations"], plan["falsification_criteria"])
    assert compiled["executable"] is True and compiled["cpu_threshold"] == 50
    low_cpu = {"cpu_hotspot": {"metrics": {"process_cpu_core_usage": 0.7}}}
    assert evaluate_performance_criterion("cpu_hotspot.process_cpu_core_usage >= 50", low_cpu)["matches"] is False
    assert evaluate_performance_criterion("cpu_hotspot.process_cpu_core_usage >= 0.5", low_cpu)["matches"] is True
    # A legitimate 0.5-percent comparison is different from the stated 50%.
    plan["expected_observations"] = ["cpu_hotspot.process_cpu_core_usage >= 0.5"]
    plan["falsification_criteria"] = ["cpu_hotspot.process_cpu_core_usage < 0.5"]
    assert cpu_plan_validation_error([plan]) is not None


def test_cpu_source_samples_cannot_be_relabelled_as_lock_holder_or_shared_quota_evidence():
    case = json.loads((OLD_CASES / "cpu-hotspot.json").read_text(encoding="utf-8"))
    records = case["records"]
    profile = next(e for e in records["evidence"] if e.get("role") == "SUPPORT"
                   and e.get("envelope", {}).get("source", {}).get("tool_name") == "pyspy")
    admitted = {e["evidence_id"] for e in records["evidence"]}
    rule = {"collector": "pyspy", "observation_contract": "python-profile-and-os-cpu.v1"}
    for other_domain in ("lock_contention", "noisy_neighbor", "io_latency"):
        assert measured_runtime_profile(profile, rule, records, admitted, other_domain) is False


def _cpp_profile_observation():
    plan = cpu_observation_plan("CPP", 50)
    location = {"file": "/test-source/main.cpp", "line": 131, "percent": 80.0}
    profile = {"evidence_id": "profile", "hypothesis_id": "candidate",
               "envelope": {"scope": {"pid": 321},
                            "source": {"tool_name": "perf_cpu", "artifact_id": "profile-artifact", "task_id": "profile-task"},
                            "observation": {"metadata": {
                                "schema_version": "perf_analysis.v1", "sample_count": 100,
                                "top_functions": [{"name": "spinCPU(int)", "samples": 80, **location}],
                                "hypothesis_predicate": {"metrics": {
                                    "observation_contract": "cpp-perf-and-os-cpu.v1",
                                    "dominant_function": "spinCPU(int)", "dominant_percent": 80.0,
                                    "source_locations": [location]}}}}}}
    system = {"evidence_id": "os", "hypothesis_id": "candidate",
              "envelope": {"source": {"tool_name": "sys_metrics", "artifact_id": "os-artifact", "task_id": "os-task"},
                           "observation": {"metadata": {
                               "process_identity": {"verified": True, "pid": 321, "start_ticks": 77},
                               "hypothesis_predicate": {"metrics": {
                                   "source": "linux_proc_stat", "pid": 321, "start_ticks": 77,
                                   "clock_ticks_per_second": 100, "sample_count": 6,
                                   "start_cpu_ticks": 100, "end_cpu_ticks": 450,
                                   "start_unix_ms": 1000, "end_unix_ms": 6000,
                                   "process_cpu_core_usage": 70.0}}}}}}
    records = {"evidence": [profile, system], "hypotheses": [{"hypothesis_id": "candidate", **plan}]}
    rule = {"collector": "perf_cpu", "observation_contract": "cpp-perf-and-os-cpu.v1"}
    return profile, system, records, rule


def test_registered_cpp_path_requires_recomputed_independent_cpu_and_exact_plan():
    profile, _, records, rule = _cpp_profile_observation()
    assert measured_runtime_profile(profile, rule, records, {"profile", "os"}, "cpu_hot_path") is True


@pytest.mark.parametrize("fault", ["same_task", "same_artifact", "other_hypothesis", "wrong_pid",
    "ticks_disagree", "low_cpu", "short_os_window", "missing_os", "forged_sample_share",
    "boolean_sample_count", "missing_source", "runtime_wrapper", "causal_statement"])
def test_cpp_runtime_observation_rejects_correlated_or_forged_proof(fault):
    profile, system, records, rule = _cpp_profile_observation()
    meta = profile["envelope"]["observation"]["metadata"]
    os_meta = system["envelope"]["observation"]["metadata"]
    if fault == "same_task": system["envelope"]["source"]["task_id"] = "profile-task"
    elif fault == "same_artifact": system["envelope"]["source"]["artifact_id"] = "profile-artifact"
    elif fault == "other_hypothesis": system["hypothesis_id"] = "other-candidate"
    elif fault == "wrong_pid": os_meta["process_identity"]["pid"] = 999
    elif fault == "ticks_disagree": os_meta["hypothesis_predicate"]["metrics"]["end_cpu_ticks"] = 101
    elif fault == "low_cpu": os_meta["hypothesis_predicate"]["metrics"].update(end_cpu_ticks=104, process_cpu_core_usage=.8)
    elif fault == "short_os_window": os_meta["hypothesis_predicate"]["metrics"]["end_unix_ms"] = 1001
    elif fault == "missing_os": records["evidence"] = [profile]
    elif fault == "forged_sample_share": meta["top_functions"][0]["samples"] = 1
    elif fault == "boolean_sample_count": meta["sample_count"] = True
    elif fault == "missing_source": meta["hypothesis_predicate"]["metrics"]["source_locations"] = []
    elif fault == "runtime_wrapper":
        meta["top_functions"][0]["name"] = "execute_native_thread_routine"
        meta["hypothesis_predicate"]["metrics"]["dominant_function"] = "execute_native_thread_routine"
    else: records["hypotheses"][0]["statement"] += "; this function caused all request delay"
    assert measured_runtime_profile(profile, rule, records, {"profile", "os"}, "cpu_hot_path") is False


@pytest.mark.parametrize("fault", ["missing_before", "missing_after", "reset_count", "reset_duration",
                                    "fractional_count", "boolean_count", "nan_duration", "negative_duration", "no_operations"])
def test_io_counts_and_bytes_do_not_supply_missing_or_invalid_latency(fault):
    before = {"io_operations": 100, "io_operation_duration_ms_total": 250.0}
    after = {"io_operations": 110, "io_operation_duration_ms_total": 280.0}
    if fault == "missing_before": before.pop("io_operation_duration_ms_total")
    elif fault == "missing_after": after.pop("io_operation_duration_ms_total")
    elif fault == "reset_count": after["io_operations"] = 10
    elif fault == "reset_duration": after["io_operation_duration_ms_total"] = 0
    elif fault == "fractional_count": after["io_operations"] = 110.5
    elif fault == "boolean_count": before["io_operations"] = True
    elif fault == "nan_duration": after["io_operation_duration_ms_total"] = float("nan")
    elif fault == "negative_duration": before["io_operation_duration_ms_total"] = -1
    else: after["io_operations"] = before["io_operations"]
    assert _application_counter_average({"before": before, "after": after}, "io_operations", "io_operation_duration_ms_total") is None
    assert evaluate_performance_criterion("io_latency.average_latency_ms < 10",
                                         {"io_activity": {"metrics": {"io_bytes_written_delta": 10**8, "io_operations_delta": 100}}}) is None


def _quota_document():
    """Synthetic parser input with independently specified stable identities."""
    rows = []
    for index in range(6):
        target_ticks = 200 + 20 * index
        resource = {
            "schema_version": "mini-drop.cgroup-cpu-observation.v1",
            "source": "linux_proc_and_cgroup_v2", "target_pid": 321,
            "target_start_ticks": 77, "target_cpu_ticks": target_ticks,
            "boot_id": "test-boot", "cgroup_path": "/test-owned-shared-quota",
            "cpu_quota_us": 65000, "cpu_period_us": 100000,
            "nr_periods": 100 + 10 * index, "nr_throttled": 30 + index,
            "throttled_usec": 100000 + 50000 * index,
            "peers": [{"pid": 654, "start_ticks": 88, "cpu_ticks": 500 + 30 * index,
                       "cgroup_path": "/test-owned-shared-quota"}],
        }
        rows.append({"offset_sec": index, "captured_at_unix_ms": 1000 + 1000 * index,
                     "rss_kb": 100000, "process_start_ticks": 77,
                     "process_cpu_ticks": target_ticks,
                     "resource_competition_json": json.dumps(resource)})
    return {"schema_version": "sys_metrics.v2", "pid": 321,
            "clock_ticks_per_second": 100, "samples": rows}


def _edit_resource(document, index, mutate):
    sample = document["samples"][index]
    resource = json.loads(sample["resource_competition_json"])
    mutate(resource)
    sample["resource_competition_json"] = json.dumps(resource)


def test_shared_quota_window_recomputes_counts_and_does_not_claim_causality():
    metadata = _parse_sys_metrics_document(_quota_document())
    measured = metadata["resource_competition_window"]
    assert measured["peer_cpu_ticks_delta"] == 150
    assert measured["target_cpu_ticks_delta"] == 100
    assert measured["throttled_usec_delta"] == 250000
    assert measured["throttled_periods_delta"] == 5
    assert measured["measurement_scope"] == "TARGET_SHARED_CGROUP_CPU"
    assert metadata["signals"]["noisy_neighbor"]["detected"] is True
    assert measured.get("causal_root_cause_verified") is not True


@pytest.mark.parametrize("fault", ["missing_sample", "other_cgroup", "peer_pid_reused", "target_pid_reused",
    "boot_changed", "quota_changed", "unlimited", "missing_period", "reset_target", "reset_peer",
    "reset_throttle", "invalid_throttle", "boolean_ticks", "duplicated_peer", "self_peer",
    "os_ticks_disagree", "clock_reversed", "partial_window", "old_app_peer_activity"])
def test_peer_activity_and_corrupted_counters_cannot_establish_resource_competition(fault):
    document = _quota_document()
    if fault == "missing_sample": document["samples"][2].pop("resource_competition_json")
    elif fault == "other_cgroup": _edit_resource(document, 2, lambda r: r["peers"][0].update(cgroup_path="/another-container"))
    elif fault == "peer_pid_reused": _edit_resource(document, 2, lambda r: r["peers"][0].update(start_ticks=999))
    elif fault == "target_pid_reused": _edit_resource(document, 2, lambda r: r.update(target_start_ticks=999))
    elif fault == "boot_changed": _edit_resource(document, 2, lambda r: r.update(boot_id="new-boot"))
    elif fault == "quota_changed": _edit_resource(document, 2, lambda r: r.update(cpu_quota_us=100000))
    elif fault == "unlimited":
        for i in range(6): _edit_resource(document, i, lambda r: r.update(cpu_quota_us=None))
    elif fault == "missing_period": _edit_resource(document, 2, lambda r: r.pop("cpu_period_us"))
    elif fault == "reset_target": _edit_resource(document, 2, lambda r: r.update(target_cpu_ticks=1))
    elif fault == "reset_peer": _edit_resource(document, 2, lambda r: r["peers"][0].update(cpu_ticks=1))
    elif fault == "reset_throttle": _edit_resource(document, 2, lambda r: r.update(throttled_usec=1))
    elif fault == "invalid_throttle": _edit_resource(document, 2, lambda r: r.update(nr_throttled=999999))
    elif fault == "boolean_ticks": _edit_resource(document, 2, lambda r: r["peers"][0].update(cpu_ticks=True))
    elif fault == "duplicated_peer": _edit_resource(document, 2, lambda r: r["peers"].append(deepcopy(r["peers"][0])))
    elif fault == "self_peer": _edit_resource(document, 2, lambda r: r["peers"][0].update(pid=321))
    elif fault == "os_ticks_disagree": _edit_resource(document, 2, lambda r: r.update(target_cpu_ticks=999))
    elif fault == "clock_reversed": document["samples"][2]["captured_at_unix_ms"] = 1000
    elif fault == "partial_window": document["samples"] = document["samples"][:1]
    else:
        for sample in document["samples"]:
            sample.pop("resource_competition_json")
            sample["application_metrics_json"] = json.dumps({"peer_active": True, "peer_cpu_ticks": 999999})
    assert _resource_competition_window(document["samples"], 321, 77) is None


def test_busy_peer_in_unthrottled_window_is_measured_negative_not_positive_competition():
    document = _quota_document()
    for index in range(6):
        _edit_resource(document, index, lambda r: r.update(nr_throttled=30, throttled_usec=100000))
    metadata = _parse_sys_metrics_document(document)
    signal = metadata["signals"]["noisy_neighbor"]
    assert signal["detected"] is False
    assert signal["metrics"]["peer_cpu_ticks_delta"] == 150
    counter = evaluate_performance_criterion("noisy_neighbor.throttled_usec_delta == 0", metadata["signals"])
    assert counter["matches"] is True and counter["value"] == 0
