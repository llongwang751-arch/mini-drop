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
    def snapshot(value):
        return {"lock_wait_ms": value, "pid": 1,
                "acceptance_process_identity": {"source": "host_proc_and_docker_inspect",
                    "host_pid": 101, "namespace_pid": 1, "start_ticks": 1234,
                    "boot_id": "boot", "container_id": "container", "container_started_at": "2026-09-28T00:00:00Z"}}
    return {"first": {"snapshot": snapshot(first)},
            "last": {"snapshot": snapshot(last)}, "elapsed_seconds": 4}


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


import pytest
from scripts import run_fault_plaza_strict_acceptance as strict


@pytest.mark.parametrize("scope", ["BOUNDED_OBSERVATION", "UNKNOWN", "", None, {}, [], True])
def test_observation_or_invalid_scope_cannot_pass_even_with_exact_root_vocabulary(scope):
    report = {"conclusion": "source_hot_function", "evidence_refs": ["e"],
              "verification": {"status": "VERIFIED", "coverage_ratio": 1,
                               "has_independent_counter_or_control": True}}
    assert evaluate_reports("source-hotspot", [report])["root_cause_accepted"]
    report["verification"]["claim_scope"] = scope
    result = evaluate_reports("source-hotspot", [report])
    assert result["reports"][0]["oracle_vocabulary_match"]
    assert not result["root_gate_verified"] and not result["root_cause_accepted"]


@pytest.mark.parametrize("flag", [False, 0, 1, "true", None, [], {}])
def test_explicit_causal_flag_must_be_true_boolean(flag):
    report = {"conclusion": "source_hot_function", "evidence_refs": ["e"],
              "verification": {"status": "VERIFIED", "coverage_ratio": 1,
                               "has_independent_counter_or_control": True,
                               "claim_scope": "CAUSAL_ROOT_CAUSE", "causal_root_cause_verified": True}}
    assert evaluate_reports("source-hotspot", [report])["root_cause_accepted"]
    report["verification"]["causal_root_cause_verified"] = flag
    assert not evaluate_reports("source-hotspot", [report])["root_cause_accepted"]


@pytest.mark.parametrize("coverage", [True, False, float("inf"), float("nan"), -1, 1.01, "1", {}, None])
def test_invalid_coverage_never_promotes_or_crashes(coverage):
    report = {"conclusion": "source_hot_function", "evidence_refs": ["e"],
              "verification": {"status": "VERIFIED", "coverage_ratio": coverage,
                               "has_independent_counter_or_control": True}}
    assert not evaluate_reports("source-hotspot", [report])["root_gate_verified"]


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf"), "200", 10 ** 1000])
def test_invalid_observations_cannot_pass(bad):
    windows = {"baseline": _window(0, 0), "fault": _window(0, 200), "recovery": _window(0, bad)}
    assert not evaluate_intervention("cpp-lock-contention", windows)["recovery_observed"]


@pytest.mark.parametrize("bad", [True, 0, -1, float("inf"), "4"])
def test_nonpositive_or_invalid_window_cannot_pass(bad):
    windows = {"baseline": _window(0, 0), "fault": _window(0, 200), "recovery": _window(0, 0)}
    windows["recovery"]["elapsed_seconds"] = bad
    assert not evaluate_intervention("cpp-lock-contention", windows)["recovery_observed"]


@pytest.mark.parametrize("existing", ["report", "cases"])
def test_campaign_preserves_existing_evidence_before_network(tmp_path, existing):
    output = tmp_path / "campaign.json"
    if existing == "report":
        output.write_text("old evidence")
    else:
        (tmp_path / "campaign-cases").mkdir()
    with pytest.raises(FileExistsError):
        strict.run_campaign(None, None, output)
    if existing == "report":
        assert output.read_text() == "old evidence"


@pytest.mark.parametrize("selection", [[], ["unknown"], ["source-hotspot", "source-hotspot"]])
def test_bad_selection_rejected_before_network(tmp_path, selection):
    with pytest.raises(ValueError):
        strict.run_campaign(None, None, tmp_path / "campaign.json", scenario_ids=selection)


def test_missing_deployed_selection_does_not_silently_shrink_campaign(tmp_path, monkeypatch):
    monkeypatch.setattr(strict, "_get_plaza", lambda _: {"scenarios": [{"scenario_id": "source-hotspot"}]})
    with pytest.raises(ValueError, match="missing"):
        strict.run_campaign(None, None, tmp_path / "campaign.json", scenario_ids=["source-hotspot", "go-cpu-hotspot"])


def test_lost_start_response_still_stops_and_persists_case(tmp_path, monkeypatch):
    stopped = []
    monkeypatch.setattr(strict.time, "sleep", lambda _: None)
    monkeypatch.setattr(strict, "measure", lambda *a: _window(0, 0))
    monkeypatch.setattr(strict, "_get_plaza", lambda _: {"scenarios": [{"scenario_id": "cpp-lock-contention", "active": False}]})
    def lost_response(*args):
        raise TimeoutError("response lost after start")
    monkeypatch.setattr(strict, "_start_fault", lost_response)
    monkeypatch.setattr(strict, "_stop_fault", lambda _, sid: stopped.append(sid))
    path = tmp_path / "case.json"
    row = strict.run_case(None, {"scenario_id": "cpp-lock-contention", "available": True}, lambda _: {}, path, agent_id="test")
    assert stopped == ["cpp-lock-contention"]
    assert row["cleanup_verified"] and not row["passed"] and path.exists()


def test_campaign_records_version_and_stops_after_unsafe_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(strict, "_get_plaza", lambda _: {"scenarios": [{"scenario_id": "source-hotspot"}, {"scenario_id": "go-cpu-hotspot"}]})
    calls = []
    def unsafe(_, scenario, *args, **kwargs):
        calls.append(scenario["scenario_id"])
        return {"passed": False, "root_cause_accepted": False, "cleanup_verified": False, "session_drained": True}
    monkeypatch.setattr(strict, "run_case", unsafe)
    result = strict.run_campaign(None, None, tmp_path / "campaign.json", deployment_provenance={"release": "test"})
    assert calls == ["source-hotspot"]
    assert result["run_status"] == "STOPPED_UNSAFE_TO_CONTINUE"
    assert len(result["provenance"]["source_sha256"]) == 3
    assert result["provenance"]["deployment"] == {"release": "test"}


@pytest.mark.parametrize("field,value", [("host_pid", 102), ("start_ticks", 1235), ("boot_id", "reboot"), ("container_id", "replaced"), ("container_started_at", "2026-09-28T00:01:00Z"), ("namespace_pid", 2)])
@pytest.mark.parametrize("stage,endpoint", [("fault", "first"), ("recovery", "last")])
def test_restarts_cannot_masquerade_as_recovery(field, value, stage, endpoint):
    windows = {"baseline": _window(0, 0), "fault": _window(0, 200), "recovery": _window(0, 0)}
    windows[stage][endpoint]["snapshot"]["acceptance_process_identity"][field] = value
    result = evaluate_intervention("cpp-lock-contention", windows)
    assert not result["process_identity"]["verified"]
    assert not result["recovery_observed"]


@pytest.mark.parametrize("field,value", [("host_pid", True), ("start_ticks", 0), ("boot_id", ""), ("container_started_at", None), ("source", "application")])
def test_missing_or_untrusted_lifetime_identity_fails_closed(field, value):
    windows = {"baseline": _window(0, 0), "fault": _window(0, 200), "recovery": _window(0, 0)}
    for window in windows.values():
        for endpoint in ("first", "last"):
            window[endpoint]["snapshot"]["acceptance_process_identity"][field] = value
    assert not evaluate_intervention("cpp-lock-contention", windows)["recovery_observed"]


def test_historical_namespace_pid_alone_does_not_verify_new_recovery():
    windows = {"baseline": _window(0, 0), "fault": _window(0, 200), "recovery": _window(0, 0)}
    del windows["recovery"]["last"]["snapshot"]["acceptance_process_identity"]
    assert not evaluate_intervention("cpp-lock-contention", windows)["recovery_observed"]
