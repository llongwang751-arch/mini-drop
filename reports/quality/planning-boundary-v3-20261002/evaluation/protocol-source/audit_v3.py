"""Independent raw-record grading; never invokes chat/embedding/rerank APIs."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

FREEZE_SHA = "bd1c8ada128c384b447e817c804def2dfe29d3dfa650f586a7909ebbe19cfbfa"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def usage(response):
    values = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(values, dict):
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    if any(type(values.get(key)) is not int or values[key] < 0 for key in keys):
        return None
    if values["total_tokens"] != values["prompt_tokens"] + values["completion_tokens"]:
        return None
    return {key: values[key] for key in keys}


def pinned_report(directory):
    report = read(directory / "report.json")
    for name, digest in report["evidence_sha256"].items():
        path = (directory / name).resolve()
        assert path.is_relative_to(directory.resolve()) and sha(path.read_bytes()) == digest, "changed raw evidence"
    return report


def retrieval_score(questions, truths, rows):
    assert len(rows) == 32 and len({row["case_id"] for row in rows}) == 32
    by_id = {row["case_id"]: row for row in rows}
    assert set(by_id) == {case["case_id"] for case in questions}
    results = []
    for case in questions:
        cid = case["case_id"]
        matched = by_id[cid]["matched_ids"]
        relevant = set(truths[cid]["relevant_ids"])
        assert len(matched) <= 3 and len(matched) == len(set(matched))
        ranks = [rank for rank, kid in enumerate(matched, 1) if kid in relevant]
        results.append({"case_id": cid, "matched_ids": matched, "relevant_ids": sorted(relevant),
                        "recall_at_3": len(relevant.intersection(matched)) / len(relevant) if relevant else None,
                        "reciprocal_rank_at_3": 1 / min(ranks) if ranks else 0,
                        "no_answer_false_positive": bool(matched) if not relevant else None})
    positive = [row for row in results if row["relevant_ids"]]
    negative = [row for row in results if not row["relevant_ids"]]
    assert len(positive) == len(negative) == 16
    metrics = {"case_count": 32, "positive_cases": 16, "no_answer_cases": 16,
               "recall_at_3": math.fsum(row["recall_at_3"] for row in positive) / 16,
               "mrr_at_3": math.fsum(row["reciprocal_rank_at_3"] for row in positive) / 16,
               "no_answer_false_positive_count": sum(row["no_answer_false_positive"] for row in negative),
               "no_answer_false_positive_rate": sum(row["no_answer_false_positive"] for row in negative) / 16}
    return metrics, results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--first-run", type=Path, required=True)
    parser.add_argument("--hybrid", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_root.resolve()
    sys.path.insert(0, str(source))
    # Production behavior is the system under test; this file does not import
    # either v3 scoring implementation or its report verifier.
    from server.app.agent_runtime.planning_output import planning_output_schema, validate_planning_output
    from server.app.agent_runtime.planning_request import planning_request_intent
    from server.app.agent_runtime.retrieval import _catalog_entries, retrieve_knowledge
    from server.app.agent_runtime.semantic_retrieval import corpus
    from server.app.agent_runtime.relevance import assess_relevance, relevance_audit
    from server.app.drop_insight.adaptive_planner import SYSTEM_PROMPT, EVIDENCE_PLANNING_REQUIREMENT
    freeze_path = source / "benchmarks/retrieval/planning_boundary_v3_manifest.json"
    assert sha(freeze_path.read_bytes()) == FREEZE_SHA
    manifest = read(freeze_path)
    for name, digest in manifest["previous_question_pins"].items():
        prior_raw = (source / name).read_bytes()
        legacy_lf_projection = (
            name == "benchmarks/retrieval/sre_queries.json"
            and digest == "7cc753b96cf5cfa64a66617f095c9f5a55ece0eb2f5a0a78c1614ce6c6f925a2"
            and sha(prior_raw) == "0b857132135ac69b5ce719cab7539c86dca6d0611717bb5dd004a28a2a2fbecb"
            and b"\r" not in prior_raw and sha(prior_raw.replace(b"\n", b"\r\n")) == digest)
        assert sha(prior_raw) == digest or legacy_lf_projection, "previous frozen input changed"
    public_path = source / "benchmarks/retrieval/planning_boundary_v3_public.json"
    private_path = source / "benchmarks/retrieval/planning_boundary_v3_private.json"
    assert sha(public_path.read_bytes()) == manifest["question_sha256"]
    assert sha(private_path.read_bytes()) == manifest["oracle_sha256"]
    questions = read(public_path)["cases"]
    truths = {row["case_id"]: row for row in read(private_path)["cases"]}
    assert len(questions) == len(truths) == 32
    report = pinned_report(args.first_run)
    assert report["manifest_sha256"] == FREEZE_SHA and report["chat_calls_attempted"] == 32 and report["retries"] == 0
    assert report["actual_langgraph_agent_execution"] is False and report["server_deterministic_route_counted_as_model"] is False
    contract = read(args.first_run / "source-contract.json")
    capture = read(source / "git-source-capture.json")
    assert report["source_head"] == contract["source_head"] == capture["source_head"]
    for name, receipt in capture["files"].items():
        raw = (source / name).read_bytes()
        assert len(raw) == receipt["bytes"] and sha(raw) == receipt["sha256"]
    corpus_pins = {path.relative_to(source).as_posix(): sha(path.read_bytes().replace(b"\r\n", b"\n"))
                   for path in sorted((source / "knowledge").glob("*")) if path.suffix in {".md", ".json"}}
    assert contract["corpus_lf_sha256"] == corpus_pins
    implementation = read(args.first_run / "implementation-source.json")
    assert contract["raw_source_sha256"] == {name: row["sha256"] for name, row in implementation.items()}
    for name, row in implementation.items():
        raw = base64.b64decode(row["base64"], validate=True)
        assert len(raw) == row["bytes"] and sha(raw) == row["sha256"] and raw == (source / name).read_bytes()
    assert sha((SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT).encode()) == contract["public_prompt_sha256"]
    provider = read(args.first_run / "provider.json")
    assert provider == report["provider"] and provider["published_source_head"] == report["source_head"]
    records = []
    for case in questions:
        cid = case["case_id"]
        row = read(args.first_run / "records" / (cid + ".json"))
        request = read(args.first_run / "requests" / (cid + ".json"))
        assert row["case_id"] == request["case_id"] == cid
        payload = request["payload"]
        assert sha(canonical(payload)) == request["payload_sha256"]
        serialized = canonical(payload)
        assert b"EVALUATOR_ONLY_" not in serialized and b'"acceptable_dispositions"' not in serialized and b'"relevant_ids"' not in serialized
        tools = manifest["runtime_tools"][case["runtime"]]
        assert payload["max_tokens"] == 1400 and payload["temperature"] == 0.1 and payload["model"] == provider["model"]
        assert "tools" not in payload
        expected_system = SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT + "\n本次是合成题的离线 JSON 输出适配，不执行工具。只输出符合公共生产合同的 JSON 对象：\n" + json.dumps(planning_output_schema(tools), ensure_ascii=False)
        assert payload["messages"][0] == {"role": "system", "content": expected_system}
        context = json.loads(payload["messages"][1]["content"])
        assert context == {"problem": case["query"], "target_runtime": case["runtime"],
                           "synthetic_observations": case["synthetic_observations"], "allowed_tools": tools,
                           "request_intent": planning_request_intent(case["query"]),
                           "knowledge_retrieval": {"matches": row["retrieval"]["matches"], "is_evidence": False},
                           "scope": "SYNTHETIC_OFFLINE_PLANNING_ONLY"}
        matches = retrieve_knowledge(case["query"], top_k=3, relevance_query=case["query"], knowledge_root=source / "knowledge")
        assert matches == row["retrieval"]["matches"]
        assert list(dict.fromkeys(match["knowledge_id"] for match in matches))[:3] == row["retrieval"]["matched_ids"]
        if "response_text" in row:
            assert sha(row["response_text"].encode()) == row["response_text_sha256"]
            try:
                response = json.loads(row["response_text"])
            except ValueError:
                response = {}
            assert response == row["response"]
        if row["status"] == "OK":
            assert row["http_status"] == 200
        records.append(row)
    bm25_metrics, scored = retrieval_score(questions, truths, [{"case_id": row["case_id"], "matched_ids": row["retrieval"]["matched_ids"]} for row in records])
    for case, row, result in zip(questions, records, scored, strict=True):
        parsed = None
        if row["status"] == "OK":
            try:
                value = json.loads(row["response"]["choices"][0]["message"]["content"])
                parsed = validate_planning_output(value, manifest["runtime_tools"][case["runtime"]])
            except (ValueError, KeyError, IndexError, TypeError):
                pass
        oracle = truths[case["case_id"]]
        result.update(status=row["status"], output=parsed, structure_valid=parsed is not None,
                      disposition_correct=bool(parsed and parsed["disposition"] in oracle["acceptable_dispositions"]),
                      tool_choice_expected=bool(parsed and parsed["tool_name"] in oracle["acceptable_tools"]), usage=usage(row.get("response", {})))
    usage_rows = [row["usage"] for row in scored if row["usage"] is not None]
    metrics = {**bm25_metrics, "timeouts": sum(row["status"] == "TIMEOUT" for row in scored),
               "http_successes": sum(row["status"] == "OK" for row in scored),
               "api_errors": sum(row["status"] == "API_ERROR" for row in scored),
               "other_transport_errors": sum(row["status"] not in {"OK", "TIMEOUT", "API_ERROR"} for row in scored),
               "usage_case_count": len(usage_rows), "all_cases_token_usage_known": len(usage_rows) == 32,
               "tokens_from_available_usage": {key: sum(row[key] for row in usage_rows) for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
               "cost_usd": None, "cost_reason": "NO_PINNED_PROVIDER_PRICE_EVIDENCE"}
    for key in ("structure_valid", "disposition_correct", "tool_choice_expected"):
        metrics[key + "_count"] = sum(row[key] for row in scored)
        metrics[key + "_rate"] = metrics[key + "_count"] / 32
    metrics["disposition_expected_counts"] = manifest["disposition_counts"]
    metrics["disposition_correct_counts"] = {
        disposition: sum(row["disposition_correct"] and truths[row["case_id"]]["acceptable_dispositions"] == [disposition]
                         for row in scored) for disposition in manifest["disposition_counts"]}
    assert (metrics, scored) == (report["metrics"], report["cases"]), "independent raw model grading differs"
    hybrid_result = None
    if args.hybrid:
        hybrid = pinned_report(args.hybrid)
        assert hybrid["source_head"] == report["source_head"] and hybrid["manifest_sha256"] == FREEZE_SHA
        assert hybrid["chat_calls_attempted"] == 0 and hybrid["index_creation_attempted"] is False
        assert read(args.hybrid / "source-contract.json") == contract
        chunks = corpus(source / "knowledge")
        by_chunk = {chunk["chunk_id"]: chunk for chunk in chunks}
        entries = {entry["knowledge_id"]: entry for entry in _catalog_entries(source / "knowledge")}
        public_entries = [entries[kid] for kid in {chunk["knowledge_id"] for chunk in chunks}]
        rows = []
        for case in questions:
            cid = case["case_id"]
            row = read(args.hybrid / "records" / (cid + ".json"))
            trace = row["trace"]
            assert row["query"] == trace["query"] == trace["relevance_query"] == case["query"]
            assert trace["requested_backend"] == "HYBRID" and trace["health_scope"] == "RETRIEVAL_ONLY" and trace["no_match_is_normal"] is False
            assert trace["evidence_contract"]["is_evidence"] is False and trace["top_k"] == 3
            assert trace["matched_count"] == len(trace["matches"])
            assert trace["outcome"] == ("MATCHED" if trace["matches"] else "NO_RELEVANT_KNOWLEDGE")
            for channel in trace["recall_channels"].values():
                assert channel["candidate_count"] == len(channel["chunk_ids"])
                assert all(chunk_id in by_chunk for chunk_id in channel["chunk_ids"])
            scores = []
            for match in trace["matches"]:
                chunk = by_chunk[match["chunk_id"]]
                assert all(match[key] == chunk[key] for key in ("knowledge_id", "title", "document", "content_hash", "required_evidence", "caveats"))
                assert match["excerpt"] == chunk["excerpt"][:700]
                decision = assess_relevance(case["query"], entries[match["knowledge_id"]], catalog_entries=public_entries)
                assert decision["accepted"] is True and match["relevance"] == decision
                assert math.isfinite(match["score"])
                if match["score_kind"] == "RERANK_RELEVANCE":
                    assert match["score"] >= 0.1
                scores.append(match["score"])
            assert scores == sorted(scores, reverse=True)
            audit = relevance_audit(case["query"], trace["accepted"] + trace["rejected"], degraded=bool(trace["degraded_reasons"]))
            assert all(trace[key] == value for key, value in audit.items())
            assert row["matched_ids"] == list(dict.fromkeys(match["knowledge_id"] for match in trace["matches"]))[:3]
            rows.append(row)
        hybrid_metrics, hybrid_scored = retrieval_score(questions, truths, rows)
        assert (hybrid_metrics, hybrid_scored) == (hybrid["metrics"], hybrid["cases"])
        backend_counts = dict(Counter(row["trace"]["actual_backend"] for row in rows))
        assert backend_counts == hybrid["actual_backend_counts"]
        hybrid_result = {"metrics": hybrid_metrics, "actual_backend_counts": backend_counts,
                         "degraded_case_count": sum(bool(row["trace"]["degraded_reasons"]) for row in rows),
                         "chunk_count": len(chunks), "index_versions": sorted({row["trace"]["index_version"] for row in rows}),
                         "provider_embedding_rerank_scores_recomputed": False,
                         "scope": "RAW_RETURNED_RANKING_GRADING_SOURCE_CHUNK_ADMISSION_AND_TRACE_REPLAY_NO_NETWORK"}
    result = {"schema": "mini-drop.independent-v3-raw-audit.v1", "status": "VERIFIED",
              "audited_at_utc": datetime.now(timezone.utc).isoformat(), "manifest_sha256": FREEZE_SHA,
              "source_head": report["source_head"], "exact_git_blob_count": capture["file_count"],
              "independent_of_v3_scoring_functions": True, "provider_calls_attempted": 0,
              "third_party_independent_author": False,
              "independence_scope": "SEPARATE_RAW_RECOMPUTATION_CODE_NOT_EXTERNAL_AUTHOR_OR_DATASET",
              "faults_injected": 0, "tasks_dispatched": 0, "question_or_truth_changed": False,
              "real_langgraph_agent_execution_claimed": False, "server_deterministic_route_counted_as_model": False,
              "model_and_bm25_metrics": metrics, "hybrid": hybrid_result,
              "first_report_sha256": sha((args.first_run / "report.json").read_bytes()),
              "hybrid_report_sha256": sha((args.hybrid / "report.json").read_bytes()) if args.hybrid else None}
    with args.output.open("xb") as stream:
        stream.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
