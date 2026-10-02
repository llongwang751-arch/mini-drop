"""Frozen v3 protocol controls; no live model or held-out retrieval scoring."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from scripts import evaluate_planning_boundary_v3 as evaluation


@pytest.mark.parametrize("suffix", ["public", "private", "manifest"])
def test_frozen_v3_question_or_truth_cannot_change(tmp_path, suffix):
    manifest, _ = evaluation.validate_freeze()
    names = [evaluation.PREFIX + ending + ".json" for ending in ("public", "private", "manifest")]
    names.extend(manifest["previous_question_pins"])
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(evaluation.ROOT / name, target)
    target = tmp_path / (evaluation.PREFIX + suffix + ".json")
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        evaluation.validate_freeze(tmp_path)


@pytest.mark.parametrize("value", [{"oracle": "private"}, {"nested": {"relevant_ids": []}},
                                   '{"acceptable_dispositions":["NORMAL"]}', "EVALUATOR_ONLY_BOUNDARY_V3_test"])
def test_private_truth_and_nested_json_never_reach_model(value):
    with pytest.raises(ValueError, match="private"):
        evaluation.public_only(value)


def empty_retrieval():
    return {"requested_backend": "BM25", "actual_backend": "BM25", "degraded_reasons": [],
            "matched_ids": [], "matches": [], "knowledge_is_evidence": False}


def test_public_request_uses_actual_intent_without_server_normal_response():
    case = {"query": "仅解释已登记的进程身份，不检查当前状态，不判断健康，不采集。", "runtime": "PYTHON",
            "synthetic_observations": {"process_identity_bound": True, "measurements_present": False}}
    payload = evaluation.build_request(case, empty_retrieval(), "UNIT_FIXTURE_NOT_LIVE", ["collect_sys_metrics"])
    context = json.loads(payload["messages"][1]["content"])
    assert context["request_intent"] == evaluation.planning_request_intent(case["query"])
    assert "model_response" not in context and "planning_output" not in context
    assert "EVALUATOR_ONLY" not in json.dumps(payload)
    assert payload["max_tokens"] == 1400 and payload["temperature"] == 0.1
    assert "tools" not in payload


def test_all_transport_failures_stay_in_32_with_unknown_usage():
    manifest, questions = evaluation.validate_freeze()
    private = json.loads((evaluation.ROOT / (evaluation.PREFIX + "private.json")).read_bytes())
    records = [{"case_id": case["case_id"], "status": "TIMEOUT", "response": {}, "retrieval": empty_retrieval()}
               for case in questions["cases"]]
    metrics, _ = evaluation.score_records(questions, private, records, manifest)
    assert metrics["case_count"] == metrics["timeouts"] == 32
    assert metrics["positive_cases"] == metrics["no_answer_cases"] == 16
    assert metrics["structure_valid_rate"] == metrics["disposition_correct_rate"] == metrics["tool_choice_expected_rate"] == 0
    assert metrics["usage_case_count"] == 0 and metrics["all_cases_token_usage_known"] is False
    assert metrics["cost_usd"] is None


def test_duplicate_record_cannot_fill_missing_response():
    manifest, questions = evaluation.validate_freeze()
    private = json.loads((evaluation.ROOT / (evaluation.PREFIX + "private.json")).read_bytes())
    records = [{"case_id": case["case_id"], "status": "TIMEOUT", "response": {}, "retrieval": empty_retrieval()}
               for case in questions["cases"]]
    records[-1] = deepcopy(records[0])
    with pytest.raises(ValueError, match="duplicate"):
        evaluation.score_records(questions, private, records, manifest)


def test_single_batch_budget_refuses_restart_in_different_output(tmp_path):
    ledger = tmp_path / "single-batch.json"
    contract = {"source_head": "UNIT_FIXTURE_NO_PROVIDER", "corpus_lf_sha256": {}}
    first = evaluation.claim_budget(ledger, "CHAT_WITH_OFFLINE_BM25", contract)
    assert first["max_requests"] == 32 and first["retries"] == 0
    with pytest.raises(FileExistsError):
        evaluation.claim_budget(ledger, "CHAT_WITH_OFFLINE_BM25", contract)
    assert json.loads(ledger.read_bytes()) == first


def test_pinned_corpus_rejects_later_change(tmp_path):
    target = tmp_path / "knowledge/catalog.json"
    target.parent.mkdir()
    target.write_bytes(b"[]\n")
    pins = evaluation.corpus_receipt(tmp_path)
    target.write_bytes(b"[{}]\n")
    with pytest.raises(ValueError, match="corpus changed"):
        with evaluation.source_corpus(pins, tmp_path):
            pytest.fail("changed public knowledge cannot be used")


def test_source_scope_includes_intent_and_every_production_planning_entry():
    assert {"server/app/agent_runtime/planning_request.py", "server/app/drop_insight/adaptive_planner.py",
            "server/app/drop_insight/diagnosis_agent.py", "server/app/drop_insight/service.py",
            "server/app/agent_runtime/runtime.py", "server/app/agent_runtime/themes.py"}.issubset(evaluation.SOURCE_PATHS)
    receipt = evaluation.source_receipt()
    assert set(receipt) == set(evaluation.SOURCE_PATHS)
    assert all(row["ast_canonicalization"] == evaluation.AST_CANONICALIZATION for row in receipt.values())


@pytest.fixture
def synthetic_report(tmp_path, monkeypatch):
    """All 32 are synthetic TIMEOUTs; retrieval is a protocol stub, never scored live."""
    monkeypatch.setattr(evaluation, "bm25_record", lambda *args, **kwargs: empty_retrieval())
    manifest, questions = evaluation.validate_freeze()
    for folder in ("requests", "records"):
        (tmp_path / folder).mkdir()
    sources = evaluation.source_receipt()
    contract = {"schema": "mini-drop.evaluation-public-source-contract.v3", "source_head": "UNIT_TEST_NOT_REAL_DEPLOYMENT",
                "corpus_lf_sha256": evaluation.corpus_receipt(),
                "public_prompt_sha256": evaluation.sha((evaluation.SYSTEM_PROMPT + "\n" + evaluation.EVIDENCE_PLANNING_REQUIREMENT).encode()),
                "raw_source_sha256": {name: row["sha256"] for name, row in sources.items()},
                "schemas": {runtime: evaluation.sha(evaluation.canonical(evaluation.planning_output_schema(tools)))
                            for runtime, tools in manifest["runtime_tools"].items()},
                "planning_intent_policy_included": True, "server_deterministic_route_counted_as_model": False}
    provider = {"provider": "UNIT_FIXTURE_NEVER_LIVE", "model": "synthetic-no-request", "transport_options": {},
                "candidate_prompt_sha256": contract["public_prompt_sha256"]}
    evaluation.write_json(tmp_path / "source-contract.json", contract)
    evaluation.write_json(tmp_path / "provider.json", provider)
    evaluation.write_json(tmp_path / "implementation-source.json", sources)
    evaluation.write_json(tmp_path / "freeze.json", {"manifest_sha256": evaluation.MANIFEST_SHA, "manifest": manifest})
    evaluation.write_json(tmp_path / "budget.json", {"manifest_sha256": evaluation.MANIFEST_SHA,
                          "backend": "CHAT_WITH_OFFLINE_BM25", "max_requests": 32, "retries": 0,
                          "source_contract_sha256": evaluation.sha(evaluation.canonical(contract))})
    (tmp_path / "evaluator-source.py").write_bytes(Path(evaluation.__file__).read_bytes())
    records = []
    for case in questions["cases"]:
        tools = manifest["runtime_tools"][case["runtime"]]
        retrieval = empty_retrieval()
        payload = evaluation.build_request(case, retrieval, provider["model"], tools)
        evaluation.write_json(tmp_path / "requests" / (case["case_id"] + ".json"), {
            "case_id": case["case_id"], "payload": payload, "allowed_tools": tools,
            "payload_sha256": evaluation.sha(evaluation.canonical(payload))})
        row = {"case_id": case["case_id"], "status": "TIMEOUT", "http_status": None, "response": {}, "retrieval": retrieval}
        evaluation.write_json(tmp_path / "records" / (case["case_id"] + ".json"), row)
        records.append(row)
    private = json.loads((evaluation.ROOT / (evaluation.PREFIX + "private.json")).read_bytes())
    metrics, scored = evaluation.score_records(questions, private, records, manifest)
    pins = {path.relative_to(tmp_path).as_posix(): evaluation.sha(path.read_bytes()) for path in tmp_path.rglob("*") if path.is_file()}
    report = {**evaluation.report_labels(manifest), "run_at_utc": "UNIT_TEST_NOT_LIVE", "provider": provider,
              "source_head": contract["source_head"], "metrics": metrics, "cases": scored, "evidence_sha256": pins}
    evaluation.write_json(tmp_path / "report.json", report)
    assert evaluation.verify_report(tmp_path / "report.json")["timeouts"] == 32
    return tmp_path


@pytest.mark.parametrize("field,value", [("scope", "LIVE_CAUSAL_ACCURACY"), ("third_party_independent_author", True),
                                        ("actual_langgraph_agent_execution", True), ("server_deterministic_route_counted_as_model", True),
                                        ("retrieval_is_evidence", True), ("retries", 1), ("chat_calls_attempted", 31)])
def test_scope_server_route_and_failure_denominator_cannot_be_relabelled(synthetic_report, field, value):
    path = synthetic_report / "report.json"
    report = json.loads(path.read_bytes())
    report[field] = value
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="labels"):
        evaluation.verify_report(path)


@pytest.mark.parametrize("change", ["score", "ranking", "private_context", "http_success", "source_behavior", "intent_behavior", "corpus_contract"])
def test_repinned_evidence_cannot_forge_projection_or_new_policy(synthetic_report, change):
    report_path = synthetic_report / "report.json"
    report = json.loads(report_path.read_bytes())
    target = synthetic_report / "records/b01.json"
    row = json.loads(target.read_bytes())
    if change == "score":
        report["metrics"]["disposition_correct_count"] = 32
    elif change == "ranking":
        row["retrieval"]["matched_ids"] = ["jvm.cpu.thread_observation"]
    elif change == "http_success":
        row.update(status="OK", http_status=500)
    elif change == "private_context":
        target = synthetic_report / "requests/b01.json"
        row = json.loads(target.read_bytes())
        row["payload"]["messages"][1]["content"] = '{"oracle":"private"}'
        row["payload_sha256"] = evaluation.sha(evaluation.canonical(row["payload"]))
    elif change == "source_behavior":
        target = synthetic_report / "evaluator-source.py"
        target.write_bytes(target.read_bytes() + b"\nFORGED_BEHAVIOR=True\n")
    elif change == "intent_behavior":
        target = synthetic_report / "implementation-source.json"
        row = json.loads(target.read_bytes())
        row["server/app/agent_runtime/planning_request.py"]["ast_sha256"] = "0" * 64
    else:
        target = synthetic_report / "source-contract.json"
        row = json.loads(target.read_bytes())
        row["corpus_lf_sha256"]["knowledge/catalog.json"] = "0" * 64
    if change != "source_behavior":
        target.write_text(json.dumps(row), encoding="utf-8")
    report["evidence_sha256"][target.relative_to(synthetic_report).as_posix()] = evaluation.sha(target.read_bytes())
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="differs|different|requires HTTP|changed"):
        evaluation.verify_report(report_path)
