"""Synthetic negative controls; fixtures never masquerade as real model trials."""
from copy import deepcopy
import ast
import json
from pathlib import Path
import shutil

import pytest

from scripts import evaluate_planning_retrieval_v2 as evaluation


def proposal(disposition="NORMAL"):
    return {"schema_version": "mini-drop.planning-output.v2", "disposition": disposition,
            "reasoning_summary": "此结果只评价合成输入给出的范围，不证明实际平台健康。",
            "tool_name": None, "hypotheses": [], "missing_evidence": [],
            "limitations": ["仅合成题的有界观测，未执行平台采集。"], "causal_root_cause_verified": False}


def response(value):
    return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}


@pytest.mark.parametrize("disposition", ["NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"])
def test_noninvestigation_branches_allow_empty_conditions_and_no_tool(disposition):
    value = proposal(disposition)
    if disposition == "INSUFFICIENT_EVIDENCE":
        value["missing_evidence"] = ["尚未取得目标与测量窗口"]
    parsed = evaluation.parse_output(response(value), ["collect_sys_metrics"])
    assert parsed is not None and parsed["hypotheses"] == [] and parsed["tool_name"] is None


@pytest.mark.parametrize("change", [{"causal_root_cause_verified": True}, {"tool_name": "run_shell"},
                                   {"limitations": []}, {"disposition": "ALL_SYSTEM_HEALTHY"}])
def test_normal_branch_cannot_claim_cause_or_select_tool_or_drop_scope(change):
    value = {**proposal(), **change}
    assert evaluation.parse_output(response(value), ["collect_sys_metrics"]) is None


@pytest.mark.parametrize("truth", [{"relevant_ids": ["private"]}, {"nested": {"acceptable_tools": [None]}},
                                   "EVALUATOR_ONLY_PLANNING_V2_v01", '{"oracle":"secret"}'])
def test_private_truth_never_reaches_public_model_projection(truth):
    with pytest.raises(ValueError, match="private"):
        evaluation.public_only(truth)


@pytest.mark.parametrize("suffix", ["public", "private", "manifest"])
def test_question_or_oracle_or_manifest_cannot_be_rewritten(tmp_path, suffix):
    manifest, _ = evaluation.validate_freeze()
    for name in [evaluation.PREFIX + s + ".json" for s in ("public", "private", "manifest")] + list(manifest["corpus_files"]):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(evaluation.ROOT / name, destination)
    changed = tmp_path / (evaluation.PREFIX + suffix + ".json")
    changed.write_bytes(changed.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        evaluation.validate_freeze(tmp_path)


def test_frozen_interface_and_public_projection_use_actual_production_schema():
    manifest, questions = evaluation.validate_freeze()
    case = questions["cases"][0]
    retrieval = evaluation.bm25_record(case["query"])
    payload = evaluation.build_request(case, retrieval, "fixture-not-a-real-model", manifest["runtime_tools"][case["runtime"]])
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "mini-drop.planning-output.v2" in serialized
    assert "EVALUATOR_ONLY" not in serialized and "acceptable_dispositions" not in serialized
    assert payload["max_tokens"] == 1200 and payload["temperature"] == 0.1
    assert "tools" not in payload


def test_transport_failure_remains_in_all_24_denominator_and_usage_unknown():
    manifest, questions = evaluation.validate_freeze()
    private = json.loads((evaluation.ROOT / (evaluation.PREFIX + "private.json")).read_bytes())
    records = [{"case_id": case["case_id"], "status": "TIMEOUT", "response": {},
                "retrieval": {"matched_ids": []}} for case in questions["cases"]]
    metrics, _ = evaluation.score_records(questions, private, records, manifest)
    assert metrics["case_count"] == metrics["timeouts"] == 24
    assert metrics["structure_valid_rate"] == 0 and metrics["usage_case_count"] == 0
    assert metrics["all_cases_token_usage_known"] is False and metrics["cost_usd"] is None


def test_duplicate_response_cannot_replace_a_missing_case():
    manifest, questions = evaluation.validate_freeze()
    private = json.loads((evaluation.ROOT / (evaluation.PREFIX + "private.json")).read_bytes())
    records = [{"case_id": case["case_id"], "status": "TIMEOUT", "response": {},
                "retrieval": {"matched_ids": []}} for case in questions["cases"]]
    records[-1] = deepcopy(records[0])
    with pytest.raises(ValueError, match="duplicate"):
        evaluation.score_records(questions, private, records, manifest)


@pytest.fixture
def synthetic_report(tmp_path):
    """Protocol integrity only: 24 TIMEOUTs, no fake successful model output."""
    manifest, questions = evaluation.validate_freeze()
    for folder in ("requests", "records"):
        (tmp_path / folder).mkdir()
    evaluation.write_json(tmp_path / "freeze.json", {"manifest_sha256": evaluation.MANIFEST_SHA, "manifest": manifest})
    provider = {"provider": "UNIT_FIXTURE_NEVER_LIVE", "model": "synthetic-no-request", "transport_options": {},
                "candidate_prompt_sha256": evaluation.sha((evaluation.SYSTEM_PROMPT + "\n" + evaluation.EVIDENCE_PLANNING_REQUIREMENT).encode())}
    evaluation.write_json(tmp_path / "provider.json", provider)
    evaluation.write_json(tmp_path / "implementation-source.json", evaluation.source_receipt())
    (tmp_path / "evaluator-source.py").write_bytes(Path(evaluation.__file__).read_bytes())
    records = []
    for case in questions["cases"]:
        retrieval = evaluation.bm25_record(case["query"])
        tools = manifest["runtime_tools"][case["runtime"]]
        payload = evaluation.build_request(case, retrieval, provider["model"], tools)
        evaluation.write_json(tmp_path / "requests" / (case["case_id"] + ".json"),
                              {"case_id": case["case_id"], "payload": payload, "allowed_tools": tools, "payload_sha256": evaluation.sha(evaluation.canonical(payload))})
        record = {"case_id": case["case_id"], "status": "TIMEOUT", "http_status": None,
                  "response": {}, "retrieval": retrieval}
        records.append(record)
        evaluation.write_json(tmp_path / "records" / (case["case_id"] + ".json"), record)
    private = json.loads((evaluation.ROOT / (evaluation.PREFIX + "private.json")).read_bytes())
    metrics, scored = evaluation.score_records(questions, private, records, manifest)
    pins = {p.relative_to(tmp_path).as_posix(): evaluation.sha(p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()}
    report = {"schema": "mini-drop.planning-retrieval-evaluation.v2", "suite_id": manifest["suite_id"],
              "scope": evaluation.SCOPE, "evaluation_adapter": evaluation.PROJECTION,
              "third_party_independent_author": False, "truth_frozen_before_evaluator_and_provider_calls": True,
              "question_or_truth_tuned_after_run": False, "run_at_utc": "UNIT_TEST_NOT_LIVE",
              "manifest_sha256": evaluation.MANIFEST_SHA, "actual_retrieval_backend": "BM25",
              "retrieval_is_evidence": False, "chat_calls_attempted": 24, "max_parallel_chat_calls": 2,
              "metric_arithmetic": evaluation.METRIC_ARITHMETIC,
              "retries": 0, "actual_tasks_dispatched": 0, "actual_faults_injected": 0, "cost_usd": None,
              "provider": provider, "metrics": metrics, "cases": scored, "evidence_sha256": pins}
    evaluation.write_json(tmp_path / "report.json", report)
    assert evaluation.verify_report(tmp_path / "report.json")["timeouts"] == 24
    return tmp_path


@pytest.mark.parametrize("field,value", [("scope", "LIVE_CAUSAL_ACCURACY"), ("third_party_independent_author", True),
                                        ("retrieval_is_evidence", True), ("actual_retrieval_backend", "HYBRID"),
                                        ("cost_usd", 0), ("retries", 1)])
def test_scope_backend_author_or_cost_cannot_be_relabelled(synthetic_report, field, value):
    path = synthetic_report / "report.json"
    report = json.loads(path.read_bytes())
    report[field] = value
    path.write_text(json.dumps(report), encoding="utf8")
    with pytest.raises(ValueError, match="labels"):
        evaluation.verify_report(path)


@pytest.mark.parametrize("change", ["score", "ranking", "private_context", "http_success", "source_behavior"])
def test_repinned_evidence_still_cannot_forge_runtime_projection(synthetic_report, change):
    directory = synthetic_report
    report_path = directory / "report.json"
    report = json.loads(report_path.read_bytes())
    target = directory / "records/v01.json"
    row = json.loads(target.read_bytes())
    if change == "score":
        report["metrics"]["structure_valid_count"] = 24
    elif change == "ranking":
        row["retrieval"]["matched_ids"] = ["mysql.lock_wait"]
    elif change == "http_success":
        row["status"] = "OK"
        row["http_status"] = 500
    elif change == "private_context":
        target = directory / "requests/v01.json"
        row = json.loads(target.read_bytes())
        row["payload"]["messages"][1]["content"] = '{"oracle":"private"}'
        row["payload_sha256"] = evaluation.sha(evaluation.canonical(row["payload"]))
    else:
        target = directory / "evaluator-source.py"
        target.write_bytes(target.read_bytes() + b"\nFORGED_BEHAVIOR=True\n")
    if change != "source_behavior":
        target.write_text(json.dumps(row), encoding="utf8")
    report["evidence_sha256"][target.relative_to(directory).as_posix()] = evaluation.sha(target.read_bytes())
    report_path.write_text(json.dumps(report), encoding="utf8")
    with pytest.raises(ValueError, match="differs|different|requires HTTP"):
        evaluation.verify_report(report_path)


def test_retrieval_rank_mean_uses_explicit_portable_float_arithmetic():
    _, questions = evaluation.validate_freeze()
    private = json.loads((evaluation.ROOT / (evaluation.PREFIX + "private.json")).read_bytes())
    by_id = {c["case_id"]: c for c in private["cases"]}
    records = []
    for case in questions["cases"]:
        relevant = by_id[case["case_id"]]["relevant_ids"]
        matched = ["fixture-unrelated-a", "fixture-unrelated-b", relevant[0]] if relevant else []
        records.append({"case_id": case["case_id"], "matched_ids": matched})
    metrics, _ = evaluation.retrieval_metrics(questions, private, records)
    naive = 0.0
    for _ in range(16):
        naive += 1 / 3
    assert naive / 16 == 0.33333333333333326
    assert metrics["mrr_at_3"] == 1 / 3
    assert type(metrics["no_answer_false_positive_count"]) is int


def test_source_behavior_digest_does_not_depend_on_native_ast_dump_or_empty_fields(monkeypatch):
    source = b"def probe():\n    return 42\n"
    expected = evaluation.behavior_digest(source)
    monkeypatch.setattr(evaluation.ast, "dump", lambda *args, **kwargs: "DIFFERENT_INTERPRETER_FORMAT")
    assert evaluation.behavior_digest(source) == expected
    original_parse = ast.parse
    def parse_with_new_empty_field(*args, **kwargs):
        tree = original_parse(*args, **kwargs)
        function = tree.body[0]
        function._fields = (*function._fields, "added_interpreter_empty_list")
        function.added_interpreter_empty_list = []
        return tree
    monkeypatch.setattr(evaluation.ast, "parse", parse_with_new_empty_field)
    assert evaluation.behavior_digest(source) == expected


@pytest.mark.parametrize("different", [b"def probe():\n    return 43\n",
                                      b"def probe(value):\n    return 42\n",
                                      b"@decorate\ndef probe():\n    return 42\n"])
def test_source_behavior_digest_rejects_value_argument_and_nonempty_structural_changes(different):
    assert evaluation.behavior_digest(b"def probe():\n    return 42\n") != evaluation.behavior_digest(different)


def test_source_behavior_digest_supports_real_pipeline_ellipsis_and_bytes_constants():
    receipt = evaluation.source_receipt()
    assert set(receipt) == set(evaluation.SOURCE_PATHS)
    assert all(row["ast_canonicalization"] == evaluation.AST_CANONICALIZATION for row in receipt.values())
    assert evaluation.behavior_digest(b"signature: tuple[str, ...]\n") != evaluation.behavior_digest(b"signature: tuple[str, None]\n")
    assert evaluation.behavior_digest(b"payload = b'bytes'\n") != evaluation.behavior_digest(b"payload = 'bytes'\n")
