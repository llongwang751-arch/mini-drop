from copy import deepcopy

import pytest

from scripts.audit_fault_plaza_failures import audit_case, evaluate_recorded_lineage


def records():
    return {"diagnosis": {"status": "INSUFFICIENT_EVIDENCE", "target": {
        "agent_id": "a", "pid": 1, "process_binding": {"boot_id": "boot", "process_start_ticks": 2}}},
        "tool-calls": [{"task_id": "t"}], "tasks": [{"task_id": "t", "status": "DONE", "analysis_status": "SUCCESS",
            "event_count": 2, "attempts": [{"id": "attempt"}], "artifacts": [
                {"id": 1, "sha256": "a" * 64, "integrity_status": "VERIFIED"}]}],
        "evidence": [{"evidence_id": "e", "role": "NEUTRAL", "classification": {"decision": "ACCEPT_LIMITED"},
            "envelope": {"source": {"task_id": "t", "task_attempt_id": "attempt", "artifact_id": "1",
                "artifact_sha256": "a" * 64, "analysis_job_id": "j"}, "scope": {"agent_id": "a", "pid": 1},
                "quality": {"analyzer_validated": True}}}]}


def test_insufficient_outcome_does_not_destroy_valid_recorded_lineage():
    result = evaluate_recorded_lineage(records(), {"agent_id": "a", "pid": 1})
    assert result["recorded_chain_consistent"]
    case = {"scenario_id": "io-write-latency", "target": {"agent_id": "a", "pid": 1}, "records": records(),
            "lineage_verified": False, "root_cause_accepted": False,
            "error": "diagnosis d ended as INSUFFICIENT_EVIDENCE"}
    before = deepcopy(case)
    assert audit_case(case)["failure_category"] == "OUTCOME_REJECTED_BY_LINEAGE_WRAPPER"
    assert case == before


@pytest.mark.parametrize("mutation", ["hash", "attempt", "artifact", "analysis", "scope", "binding", "target"])
def test_chain_break_or_wrong_target_is_rejected(mutation):
    data = records()
    source = data["evidence"][0]["envelope"]["source"]
    if mutation == "hash": source["artifact_sha256"] = "b" * 64
    elif mutation == "attempt": source["task_attempt_id"] = "old"
    elif mutation == "artifact": data["tasks"][0]["artifacts"][0]["integrity_status"] = "UNKNOWN"
    elif mutation == "analysis": data["evidence"][0]["envelope"]["quality"]["analyzer_validated"] = False
    elif mutation == "scope": data["evidence"][0]["envelope"]["scope"]["agent_id"] = "other"
    elif mutation == "binding": data["diagnosis"]["target"]["process_binding"] = {}
    else: data["diagnosis"]["target"]["agent_id"] = "other"
    assert not evaluate_recorded_lineage(data, {"agent_id": "a", "pid": 1})["recorded_chain_consistent"]
