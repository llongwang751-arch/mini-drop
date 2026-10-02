"""Verify existing live records locally; never invoke a provider or mutate a session."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
SMOKE = STAGE / "live-smoke"
sys.path.insert(0, str(ROOT))

from server.app.agent_runtime.planning_output import validate_planning_output
from server.app.agent_runtime.planning_request import (
    PLANNING_CLAIM_SCOPE, audited_planner_metadata, planning_request_intent,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    receipt_path = STAGE / "live-contract-readback.json"
    browser_path = SMOKE / "browser-readback-summary.json"
    assert not receipt_path.exists() and not browser_path.exists(), "preserve prior receipts"
    original_path = SMOKE / "summary.json"
    audit_path = STAGE / "live-runtime-audit.json"
    source_files = [
        ROOT / "server/app/agent_runtime/planning_output.py",
        ROOT / "server/app/agent_runtime/planning_request.py",
        ROOT / "server/app/drop_insight/service.py",
        STAGE / "live_planning_smoke.py",
        STAGE / "live_runtime_audit.py",
        Path(__file__).resolve(),
    ]
    inputs = sorted([*SMOKE.rglob("*.json"), audit_path, *source_files])
    hashes = {p: sha(p) for p in inputs}
    original = read(original_path)
    audit = read(audit_path)
    witness = read(SMOKE / "deployment-witness.json")
    task_counts = read(SMOKE / "persisted-task-counts.json")
    assert original["status"] == "FAILED"
    assert original["source_head"] == "cfea6744fc2395d5392440b2404ee275ada0a69f"
    assert audit["source_head"] == original["source_head"]
    assert audit["release"] == witness["release"] == original["release"]
    assert original["planner_invocations"] == 3 and original["mutation_retries"] == 0
    assert [r["name"] for r in original["cases"]] == ["normal", "missing", "refused"]
    assert [r["passed"] for r in original["cases"]] == [True, False, False]
    checkpoints = {r["diagnosis_id"]: r for r in audit["checkpoint_audit"]}
    tasks = {r["diagnosis_id"]: r for r in task_counts}
    findings = []
    lifetime = None
    for original_case in original["cases"]:
        name, diagnosis_id = original_case["name"], original_case["diagnosis_id"]
        records = read(SMOKE / name / "records.json")
        response = read(SMOKE / name / "planner-response.json")
        request = read(SMOKE / name / "request.json")
        result = read(SMOKE / name / "result.json")
        checkpoint = checkpoints[diagnosis_id]
        assert result["diagnosis_id"] == records["detail"]["diagnosis_id"] == diagnosis_id
        assert result["passed"] is original_case["passed"]
        assert request["query"] == records["detail"]["query"]
        assert request["health_check"] is False and request["auto_scope"] is False
        assert request["mode"] == "ASSISTED" and request["skill_policy"] == "DISABLED"
        binding = records["detail"]["target"]["process_binding"]
        identity_keys = ("agent_id", "pid", "boot_id", "process_start_ticks",
                         "pid_namespace_inode", "namespace_pid", "executable_identity")
        identity = {k: binding[k] for k in identity_keys}
        assert binding["pid"] == witness["target_pid"]
        if lifetime is None:
            lifetime = identity
        assert identity == lifetime, "snapshot refresh must not change target lifetime"
        assert checkpoint["physical_thread_id"] == "diagnosis-agent-v8-request-intent:" + diagnosis_id
        assert checkpoint["legacy_checkpoint_present"] is False
        assert tasks[diagnosis_id]["persisted_tasks"] == tasks[diagnosis_id]["persisted_tool_calls"] == 0
        assert tasks[diagnosis_id]["task_ids"] == []
        assert all(records[k] == [] for k in ("hypotheses", "tool-calls", "evidence", "reports"))
        assert all(e["diagnosis_id"] == diagnosis_id for e in records["events"])
        assert not any("health_check" in e["event_type"] for e in records["events"])
        outputs = [e for e in records["events"] if e["event_type"] == "planner.output_recorded"]
        assert len(records["retrievals"]) == 1
        retrieval = records["retrievals"][0]
        trace = retrieval["retrieval_trace"]
        assert trace["outcome"] == "NO_RELEVANT_KNOWLEDGE" and trace["health_scope"] == "RETRIEVAL_ONLY"
        assert trace["requested_backend"] == "HYBRID" and trace["actual_backend"] == "BM25_ENTITY_CHROMA_RRF"
        assert trace["degraded_reasons"] == [] and trace["recall_channels"]["DENSE"]["available"] is True
        assert trace["embedding_model"] == "Qwen/Qwen3-Embedding-4B"
        common = {
            "name": name, "diagnosis_id": diagnosis_id,
            "expected_disposition": original_case["expected_disposition"],
            "original_smoke_passed": original_case["passed"],
            "original_failure_type": original_case.get("failure_type"),
            "original_failure_reason": original_case.get("failure_reason"),
            "http_response_planner_kind": response["planner_kind"],
            "http_response_status": response["status"],
            "record_status_before_original_cleanup": records["detail"]["status"],
            "persisted_tasks": 0, "persisted_tool_calls": 0, "persisted_hypotheses": 0,
            "persisted_evidence": 0, "persisted_reports": 0,
            "health_check_performed": False, "causal_root_cause_verified": False,
            "checkpoint_present": checkpoint["checkpoint_present"],
            "raw_findings_verified": True,
            "upstream_retrieval": {
                "event_id": retrieval["event_id"],
                **{k: trace[k] for k in ("outcome", "health_scope", "requested_backend",
                                        "actual_backend", "degraded_reasons", "embedding_model", "dimensions")},
                "dense_recall_available": True,
                "query_embedding_executed": True,
                "all_ai_or_embedding_http_call_count": None,
                "scope": "Upstream HYBRID retrieval includes query embedding; chat-zero is not all-AI-zero.",
            },
        }
        if name in {"normal", "refused"}:
            assert len(outputs) == 1
            event = outputs[0]
            payload = event["payload"]
            dto = validate_planning_output(payload["planning_output"], [])
            assert dto == payload["planning_output"] == response["planning_output"]
            assert dto["disposition"] == response["planning_disposition"] == payload["planning_disposition"] == original_case["expected_disposition"]
            assert payload["claim_scope"] == PLANNING_CLAIM_SCOPE
            assert all(payload[k] is False for k in ("is_evidence", "health_check_performed", "causal_root_cause_verified", "new_tool_requested"))
            assert response["hypothesis"] is None and response["tool_call"] is None
            assert response["status"] == records["detail"]["status"] == "INSUFFICIENT_EVIDENCE"
            assert payload["finalization"]["finalized"] is True
            assert payload["finalization"]["best_report_id"] is None
            common.update(contract_verified=True, actual_disposition=dto["disposition"],
                          readback_status="VERIFIED_PLANNING_RESULT", output_event_id=event["event_id"],
                          planning_output=dto, claim_scope=PLANNING_CLAIM_SCOPE,
                          response_and_persisted_output_identical=True)
            if name == "normal":
                metadata = audited_planner_metadata({**dto, **{k: v for k, v in payload.items() if k != "planning_output"}})
                assert metadata["model_invocations"] == response["model_invocations"] == 0
                assert response["planner_kind"] == metadata["planner_kind"] == "SERVER_REQUEST_INTENT"
                assert response["planning_request_intent"] == metadata["planning_request_intent"] == planning_request_intent(request["query"])
                assert checkpoint["checkpoint_present"] is False and checkpoint["accepted_proposal"] is None
                assert checkpoint["messages"] == [] and audit["normal_model_invocations"] == 0
                common.update(planner_kind="SERVER_REQUEST_INTENT", model_invocations=0,
                              diagnostic_chat_provider_creation=False,
                              model_invocations_scope="Diagnostic chat/planner branch only; upstream retrieval query embedding executed.",
                              server_metadata_audit="PUBLIC_POLICY_VALIDATED", agent_checkpoint_absent=True)
            else:
                assert response["planner_kind"] == "MODEL_DISPOSITION"
                assert "planner_kind" not in payload
                assert checkpoint["checkpoint_present"] is True and checkpoint["accepted_proposal"] == dto
                ais = [m for m in checkpoint["messages"] if m["type"] == "ai"]
                tools = [m for m in checkpoint["messages"] if m["type"] == "tool"]
                assert len(ais) == len(tools) == 1
                assert ais[0]["tool_names"] == ["finish_diagnosis_plan"]
                assert ais[0]["planning_dispositions"] == ["REFUSED"]
                assert tools[0]["name"] == "finish_diagnosis_plan" and tools[0]["accepted"] is True
                assert ais[0]["tool_call_ids"] == [tools[0]["tool_call_id"]]
                assert original_case["failure_type"] == "KeyError" and original_case["failure_reason"] == "'planner_kind'"
                common.update(planner_kind="MODEL_DISPOSITION", agent_finish_tool_accepted=True,
                              agent_finish_tool_call_id=tools[0]["tool_call_id"],
                              accepted_agent_proposal_matches_http_and_event=True,
                              agent_ai_message_count=1, provider_http_call_count=None,
                              original_failure_explanation="Smoke helper incorrectly required event.planner_kind. Public v2 MODEL event omits it; HTTP response carries MODEL_DISPOSITION. Original KeyError grade is preserved.")
        else:
            assert outputs == [] and "planning_output" not in response
            assert checkpoint["checkpoint_present"] is True and checkpoint["accepted_proposal"] is None
            assert checkpoint["messages"] == [] and response["status"] == "NEEDS_CLARIFICATION"
            failures = [r for r in audit["provider_failure_log_types"] if r["diagnosis_id"] == diagnosis_id]
            assert len(failures) == 1 and failures[0]["error"] == "OpenAITimeoutError"
            assert failures[0]["event"] == "diagnosis_agent_plan_failed"
            common.update(contract_verified=False, actual_disposition=None,
                          readback_status="PROVIDER_TIMEOUT_NO_PLANNING_OUTPUT", provider_failure=failures[0],
                          original_failure_explanation="Diagnostic Agent supplier timed out; fallback clarification response is not an accepted INSUFFICIENT_EVIDENCE planning result. This case remains unsuccessful.")
        findings.append(common)

    assert sum(r["contract_verified"] for r in findings) == 2
    assert all(sha(p) == digest for p, digest in hashes.items()), "raw input bytes changed during readback"
    scope = ("Local independent readback of the same original three production sessions; two persisted planning results verified, "
             "one correlated supplier timeout with no planning output. Original FAILED smoke grade remains immutable. "
             "No new session, planner request, provider call, retry, deployment or health/causal conclusion. "
             "NORMAL uses zero diagnostic chat model calls; upstream HYBRID query embedding did execute. Not a 3/3 smoke pass.")
    browser = copy.deepcopy(original)
    browser.update(status="PASSED_AVAILABLE_CASES", scope=scope,
                   passed_value_scope="Independent readback eligibility of existing persisted cards; original grades are separate and unchanged.",
                   original_smoke_status="FAILED", original_smoke_path=str(original_path.relative_to(ROOT)).replace("\\", "/"),
                   original_smoke_sha256=hashes[original_path], original_smoke_passed_cases=1,
                   verified_available_cases=2, total_original_cases=3, supplier_timeout_cases=1,
                   new_planner_invocations=0, new_diagnostic_chat_model_invocations=0,
                   new_provider_calls=0, new_sessions=0, readback_only=True,
                   all_three_planning_results_passed=False,
                   normal_model_invocations_scope="Diagnostic chat/planner only; original upstream HYBRID query embedding executed.",
                   all_ai_or_embedding_http_call_count=None)
    for row, finding in zip(browser["cases"], findings, strict=True):
        row.update(passed=finding["contract_verified"], original_smoke_passed=finding["original_smoke_passed"],
                   actual_disposition=finding["actual_disposition"], readback_status=finding["readback_status"],
                   original_failure_type=finding["original_failure_type"], original_failure_reason=finding["original_failure_reason"],
                   passed_value_scope="Existing original-session contract readback, not a new model attempt.",
                   upstream_query_embedding_executed=True, all_ai_or_embedding_http_call_count=None)
        row["provider_http_count_scope"] = "Original diagnostic chat/planner branch only; excludes upstream retrieval query embedding. HTTP count for LangGraph remains unknown."
    receipt = {
        "schema": "mini-drop.live-planning-output-contract-readback.v1", "status": "VERIFIED",
        "status_scope": "Readback findings verified; this is not a replacement smoke grade or a 3/3 success.",
        "completed_at": datetime.now(timezone.utc).isoformat(), "scope": scope,
        "source_head": original["source_head"], "release": original["release"],
        "original_smoke_status": "FAILED", "original_smoke_sha256": hashes[original_path],
        "original_smoke_passed_cases": 1, "total_original_cases": 3,
        "verified_planning_result_cases": 2, "supplier_timeout_cases": 1,
        "all_three_planning_results_passed": False, "original_grade_preserved": True,
        "original_raw_input_bytes_preserved": True, "heldout_evaluation": False,
        "new_sessions": 0, "new_planner_invocations": 0, "new_provider_calls": 0,
        "new_diagnostic_chat_model_invocations": 0, "mutation_retries": 0,
        "health_check_performed": False, "causal_root_cause_verified": False,
        "normal_diagnostic_chat_model_invocations": 0, "upstream_query_embedding_executed": True,
        "normal_model_invocations_scope": "Diagnostic chat/provider creation branch only; upstream HYBRID retrieval embedding is separate and executed.",
        "all_ai_or_embedding_http_call_count": None,
        "target_lifetime_identity": lifetime, "target_lifetime_same_in_all_original_records": True,
        "deployment_witness": witness, "cases": findings,
        "input_files": [{"path": str(p.relative_to(ROOT)).replace("\\", "/"),
                         "sha256": hashes[p], "bytes": p.stat().st_size} for p in inputs],
        "browser_input_path": str(browser_path.relative_to(ROOT)).replace("\\", "/"),
    }
    browser_bytes = (json.dumps(browser, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    receipt["browser_input_sha256"] = hashlib.sha256(browser_bytes).hexdigest()
    assert all(sha(p) == digest for p, digest in hashes.items())
    with browser_path.open("xb") as stream:
        stream.write(browser_bytes)
    with receipt_path.open("xb") as stream:
        stream.write((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    assert all(sha(p) == digest for p, digest in hashes.items())
    print(json.dumps({"status": receipt["status"], "receipt": str(receipt_path), "receipt_sha256": sha(receipt_path),
                      "browser_input": str(browser_path), "browser_sha256": sha(browser_path),
                      "original_smoke_status": original["status"], "original_smoke_sha256": hashes[original_path],
                      "verified_planning_results": 2, "supplier_timeouts": 1, "new_provider_calls": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
