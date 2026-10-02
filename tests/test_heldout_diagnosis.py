"""Synthetic negative controls for the scorer, separate from real model receipts."""
import ast
import json
from pathlib import Path
import shutil

import pytest

from scripts import evaluate_heldout_diagnosis as heldout


from scripts.verify_historical_heldout import historical_test_scope


@pytest.fixture(scope="module", autouse=True)
def original_contract_runs_in_historical_source_sandbox():
    """Retain all original gates; current production is intentionally different."""
    with historical_test_scope(heldout):
        yield


def frozen_copy(tmp_path):
    manifest, _, _ = heldout.validate_freeze()
    names = [heldout.PREFIX + suffix + ".json" for suffix in ("public", "private", "manifest", "frozen_inputs")]
    names += ["benchmarks/retrieval/sre_queries.json", *manifest["corpus_files"], *manifest["production_source_files"]]
    for name in names:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(heldout.ROOT / name, destination)
    return tmp_path


@pytest.mark.parametrize("name", ["public", "private", "manifest"])
def test_preimplementation_freeze_rejects_changed_suite_or_truth(tmp_path, name):
    root = frozen_copy(tmp_path)
    path = root / (heldout.PREFIX + name + ".json")
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        heldout.validate_freeze(root)


@pytest.mark.parametrize("name", ["knowledge/linux_cpu.md", "server/app/agent_runtime/retrieval.py",
                                 "benchmarks/retrieval/sre_queries.json"])
def test_freeze_rejects_corpus_source_or_development_drift(tmp_path, name):
    root = frozen_copy(tmp_path)
    path = root / name
    path.write_bytes(path.read_bytes() + (b"\nSOURCE_BEHAVIOR_CHANGED = True\n" if name.endswith(".py") else b"\n"))
    with pytest.raises(ValueError, match="changed"):
        heldout.validate_freeze(root)


def test_heldout_is_distinct_from_development_and_uses_frozen_corpus():
    manifest, questions, digest = heldout.validate_freeze()
    assert digest == heldout.FROZEN_MANIFEST_SHA256
    assert (manifest["heldout_cases"], manifest["development_cases"]) == (24, 15)
    assert (manifest["corpus_documents"], manifest["corpus_chunks"]) == (17, 39)
    assert len({heldout.normalize_query(c["query"]) for c in questions["cases"]}) == 24


def test_clean_checkout_comments_and_crlf_do_not_replace_original_corpus_bytes(tmp_path):
    root = frozen_copy(tmp_path)
    manifest, _, _ = heldout.validate_freeze(root)
    for name in manifest["production_source_files"]:
        path = root / name
        path.write_text(ast.unparse(ast.parse(path.read_bytes())) + "\n", encoding="utf-8")
    for name in [*manifest["corpus_files"], "benchmarks/retrieval/sre_queries.json"]:
        path = root / name
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    heldout.validate_freeze(root)
    before = {name: (root / name).read_bytes() for name in manifest["corpus_files"]}
    matches = heldout.frozen_retrieve_knowledge("Python 高CPU热点", root)
    assert matches
    frozen = heldout.frozen_inputs(root)
    for match in matches:
        assert match["content_hash"] == heldout.sha(frozen[match["document"]])
    assert before == {name: (root / name).read_bytes() for name in manifest["corpus_files"]}


@pytest.mark.parametrize("change", ["raw", "registered_sha", "extra_path", "missing_path"])
def test_snapshot_package_cannot_resign_or_expand_original_inputs(tmp_path, change):
    root = frozen_copy(tmp_path)
    path = root / (heldout.PREFIX + "frozen_inputs.json")
    packet = json.loads(path.read_text(encoding="utf-8"))
    first = next(iter(packet["files"]))
    if change == "raw":
        packet["files"][first]["base64"] = "dGFtcGVyZWQ="
    elif change == "registered_sha":
        packet["files"][first]["sha256"] = "0" * 64
    elif change == "extra_path":
        packet["files"]["../private_oracle.json"] = packet["files"][first]
    else:
        del packet["files"][first]
    path.write_text(json.dumps(packet), encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot|unregistered"):
        heldout.validate_freeze(root)


@pytest.mark.parametrize("truth", [{"ground_truth": "secret"}, {"acceptable_tools": ["run_shell"]},
    {"nested": {"relevant_ids": ["postgres.waits"]}},
    '{"evaluation_oracle":{"acceptable_categories":["CPU"]}}',
    "EVALUATOR_ONLY_HELDOUT_20261002_6f7599b598"])
def test_model_projection_rejects_truth_or_sentinel_in_nested_payload(truth):
    case = {"case_id": "negative-control", "query": "检查", "runtime": "PYTHON", "synthetic_observations": truth}
    with pytest.raises(ValueError, match="evaluator-only"):
        heldout.build_request(case, [], "test-only-model")


def test_model_request_does_not_contain_oracle_or_prefilled_category():
    _, questions, _ = heldout.validate_freeze()
    for case in questions["cases"]:
        request, allowed = heldout.build_request(case, [], "test-only-model")
        public = json.loads(request["messages"][1]["content"])
        assert set(public) == {"problem", "target_runtime", "allowed_tools", "synthetic_observations", "knowledge_retrieval", "scope"}
        assert "category" not in public and "rule_baseline" not in public
        assert "collect_database_diagnostics" not in allowed
        assert request["max_tokens"] == 1200
        heldout.assert_no_oracle(request)


def answer(next_tool="collect_sys_metrics"):
    return {"category": "CPU", "disposition": "OBSERVATION_SUPPORTED", "next_tool": next_tool,
        "reasoning_summary": "仅能给出窗口观测，需要后续采样。",
        "expected_observations": ["目标进程CPU升高"], "falsification_criteria": ["完整窗口CPU低于阈值"],
        "causal_root_cause_verified": False}


def response(value):
    return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}


@pytest.mark.parametrize("change", [
    {"causal_root_cause_verified": True}, {"causal_root_cause_verified": 0},
    {"category": "MADE_UP_ROOT"}, {"falsification_criteria": []},
    {"expected_observations": [False]}, {"reasoning_summary": ""}, {"unexpected": "field"}])
def test_model_structure_cannot_promote_causal_claim_or_drop_contract(change):
    assert heldout.parse_output(response({**answer(), **change})) is None


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4},
    {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1}])
def test_missing_or_forged_usage_is_unknown_not_zero(usage):
    assert heldout.token_usage({"usage": usage}) is None


def test_unknown_price_is_null_and_errors_stay_in_all_case_denominator():
    case = {"case_id": "one", "runtime": "PYTHON"}
    truth = {"case_id": "one", "relevant_ids": ["linux.cpu.process_pressure"],
        "acceptable_categories": ["CPU"], "acceptable_dispositions": ["OBSERVATION_SUPPORTED"],
        "acceptable_tools": ["start_pyspy_profile"]}
    record = {"case_id": "one", "status": "TIMEOUT", "response": {}, "retrieval": {"matched_ids": []}}
    metrics, rows = heldout.score_records({"cases": [case]}, {"cases": [truth]}, [record])
    assert metrics["timeouts"] == metrics["case_count"] == 1
    assert metrics["structure_valid_rate"] == metrics["category_correct_rate"] == 0
    assert metrics["cost_usd"] is None and metrics["all_cases_token_usage_known"] is False
    assert rows[0]["usage"] is None


@pytest.mark.parametrize("tool", ["run_shell", "start_jvm_profile", "collect_database_diagnostics"])
def test_arbitrary_or_cross_runtime_tool_is_not_scored_as_safe(tool):
    case = {"case_id": "one", "runtime": "PYTHON"}
    truth = {"case_id": "one", "relevant_ids": [], "acceptable_categories": ["CPU"],
        "acceptable_dispositions": ["OBSERVATION_SUPPORTED"], "acceptable_tools": ["collect_sys_metrics"]}
    record = {"case_id": "one", "status": "OK", "response": response(answer(tool)), "retrieval": {"matched_ids": []}}
    metrics, _ = heldout.score_records({"cases": [case]}, {"cases": [truth]}, [record])
    assert metrics["tool_choice_allowlisted_count"] == metrics["tool_choice_expected_count"] == 0


def test_duplicate_responses_cannot_replace_missing_cases():
    questions = {"cases": [{"case_id": "a"}, {"case_id": "b"}]}
    private = {"cases": [{"case_id": "a"}, {"case_id": "b"}]}
    with pytest.raises(ValueError, match="missing or duplicated"):
        heldout.score_records(questions, private, [{"case_id": "a"}, {"case_id": "a"}])


def test_real_report_verification_rejects_score_only_pins(tmp_path):
    _, _, digest = heldout.validate_freeze()
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"manifest_sha256": digest, "evidence_sha256": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        heldout.verify_report(report)


@pytest.fixture
def synthetic_report(tmp_path):
    """Fabricated responses test integrity only; never enter real-run evidence."""
    manifest, questions, digest = heldout.validate_freeze()
    private = json.loads((heldout.ROOT / (heldout.PREFIX + "private.json")).read_text(encoding="utf-8"))
    truth = {r["case_id"]: r for r in private["cases"]}
    (tmp_path / "requests").mkdir()
    (tmp_path / "records").mkdir()
    metadata = {"model": "unit-only-fabricated-model", "transport_options": {}}
    heldout.write_json(tmp_path / "provider.json", metadata)
    heldout.write_json(tmp_path / "freeze.json", {"manifest_sha256": digest, "manifest": manifest})
    records = []
    for case in questions["cases"]:
        cid = case["case_id"]
        matches = heldout.frozen_retrieve_knowledge(case["query"])
        payload, allowed = heldout.build_request(case, matches, metadata["model"])
        heldout.write_json(tmp_path / "requests" / (cid + ".json"), {"case_id": cid, "payload": payload,
            "allowed_tools": allowed, "payload_sha256": heldout.sha(heldout.canonical(payload))})
        value = answer(truth[cid]["acceptable_tools"][0])
        value.update(category=truth[cid]["acceptable_categories"][0], disposition=truth[cid]["acceptable_dispositions"][0])
        model = response(value)
        text = json.dumps(model, ensure_ascii=False)
        record = {"case_id": cid, "status": "OK", "http_status": 200, "response": model,
            "response_text": text, "response_text_sha256": heldout.sha(text.encode()),
            "retrieval": {"matches": matches, "matched_ids": list(dict.fromkeys(r["knowledge_id"] for r in matches))[:3],
                "actual_backend": "BM25", "requested_backend": "BM25", "degraded_reasons": []}}
        heldout.write_json(tmp_path / "records" / (cid + ".json"), record)
        records.append(record)
    metrics, rows = heldout.score_records(questions, private, records)
    pins = {p.relative_to(tmp_path).as_posix(): heldout.sha(p.read_bytes()) for p in tmp_path.rglob("*.json")}
    report = {"schema": "mini-drop.heldout-evaluation.v1", "suite_id": manifest["suite_id"],
        "scope": manifest["scope"], "evaluation_adapter": manifest["evaluation_adapter"],
        "third_party_independent_author": False, "truth_frozen_before_implementation": True,
        "question_or_oracle_tuned_after_first_run": False, "actual_retrieval_backend": "BM25",
        "retrieval_is_evidence": False, "max_parallel_chat_calls": 2, "cost_usd": None,
        "production_prompt_sha256": heldout.sha((heldout.SYSTEM_PROMPT + "\n" + heldout.EVIDENCE_PLANNING_REQUIREMENT).encode()),
        "adapter_sha256": heldout.sha(heldout.ADAPTER.encode()), "run_at_utc": "unit-only-synthetic-receipt",
        "manifest_sha256": digest, "provider": metadata, "evidence_sha256": pins,
        "evaluator_source_sha256": heldout.sha(Path(heldout.__file__).read_bytes()), "metrics": metrics,
        "cases": rows, "chat_calls_attempted": 24, "retries": 0, "actual_tasks_dispatched": 0, "actual_faults_injected": 0}
    heldout.write_json(tmp_path / "report.json", report)
    return tmp_path / "report.json"


def repin(path, report):
    report["evidence_sha256"][path.name if path.parent.name not in {"records", "requests"}
                              else path.parent.name + "/" + path.name] = heldout.sha(path.read_bytes())


def test_integrity_fixture_has_valid_projection_without_claiming_live_execution(synthetic_report):
    metrics = heldout.verify_report(synthetic_report)
    assert metrics["case_count"] == 24
    assert metrics["all_cases_token_usage_known"] is False


@pytest.mark.parametrize("change", ["score", "case_identity", "ranking", "backend", "payload_truth", "response_parse", "http_status", "source_pin", "missing_pin"])
def test_even_repinned_tampering_cannot_forge_a_valid_report(synthetic_report, change):
    path = synthetic_report
    report = json.loads(path.read_text(encoding="utf-8"))
    record_path = path.parent / "records/h01.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if change == "score":
        report["metrics"]["category_correct_rate"] = 0
    elif change == "source_pin":
        report["evaluator_source_sha256"] = "0" * 64
    elif change == "missing_pin":
        del report["evidence_sha256"]["requests/h01.json"]
    elif change == "payload_truth":
        request_path = path.parent / "requests/h01.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request["payload"]["messages"][1]["content"] = '{"ground_truth":"CPU"}'
        request["payload_sha256"] = heldout.sha(heldout.canonical(request["payload"]))
        request_path.write_text(json.dumps(request), encoding="utf-8")
        repin(request_path, report)
    else:
        if change == "case_identity":
            record["case_id"] = "h02"
        elif change == "ranking":
            record["retrieval"]["matched_ids"] = ["synthetic-guaranteed-hit"]
        elif change == "backend":
            record["retrieval"]["actual_backend"] = "MOCK_EMBEDDING"
        elif change == "response_parse":
            record["response"] = response(answer("run_shell"))
        elif change == "http_status":
            record["http_status"] = 403
        record_path.write_text(json.dumps(record), encoding="utf-8")
        repin(record_path, report)
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        heldout.verify_report(path)


@pytest.mark.parametrize("change", ["comment_only", "scorer", "adapter"])
def test_portable_verifier_requires_original_bytes_and_identical_grader_prompt(synthetic_report, change):
    report = json.loads(synthetic_report.read_text(encoding="utf-8"))
    source = Path(heldout.__file__).read_text(encoding="utf-8")
    if change == "scorer":
        source = source.replace("return metrics, scored", "return {}, scored")
    elif change == "adapter":
        source = source.replace("以下是离线评估输出适配器", "此处是修改过的评估提示")
    else:
        source += "\n# Captured source has a comment difference only.\n"
    raw = source.encode()
    (synthetic_report.parent / "evaluator-source.py").write_bytes(raw)
    report["evaluator_source_sha256"] = heldout.sha(raw)
    synthetic_report.write_text(json.dumps(report), encoding="utf-8")
    if change == "comment_only":
        assert heldout.verify_report(synthetic_report)["case_count"] == 24
    else:
        with pytest.raises(ValueError, match="first-run scorer"):
            heldout.verify_report(synthetic_report)


@pytest.mark.parametrize("key,value", [
    ("scope", "CLOUD_CAUSAL_ROOT_ACCURACY"), ("third_party_independent_author", True),
    ("evaluation_adapter", "CURRENT_PLATFORM_END_TO_END_AGENT"), ("retrieval_is_evidence", True),
    ("actual_retrieval_backend", "HYBRID"), ("cost_usd", 0),
    ("truth_frozen_before_implementation", False), ("question_or_oracle_tuned_after_first_run", True),
    ("causal_accuracy", 1.0)])
def test_cannot_relabel_offline_run_as_live_causal_or_third_party_benchmark(synthetic_report, key, value):
    report = json.loads(synthetic_report.read_text(encoding="utf-8"))
    report[key] = value
    synthetic_report.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="scope|schema"):
        heldout.verify_report(synthetic_report)
