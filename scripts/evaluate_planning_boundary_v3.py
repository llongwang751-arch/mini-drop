"""One frozen v3 JSON planning cohort, distinct from real Agent execution.

The public request-intent policy is included as deployed preprocessing. Its
deterministic server NORMAL route is not used to manufacture model responses.
Every planned chat request is issued once, and transport failures remain in 32.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app.agent_runtime.planning_output import planning_output_schema, validate_planning_output
from server.app.agent_runtime.planning_request import planning_request_intent
from server.app.agent_runtime.retrieval import retrieve_knowledge
from server.app.ai_provider import _assert_model_boundary
from server.app.drop_insight.adaptive_planner import EVIDENCE_PLANNING_REQUIREMENT, SYSTEM_PROMPT
from scripts.evaluate_heldout_diagnosis import load_remote_helper, remote_metadata, token_usage
from scripts.evaluate_planning_retrieval_v2 import AST_CANONICALIZATION, behavior_digest, canonical, sha

PREFIX = "benchmarks/retrieval/planning_boundary_v3_"
MANIFEST_SHA = "bd1c8ada128c384b447e817c804def2dfe29d3dfa650f586a7909ebbe19cfbfa"
COUNT = 32
POSITIVE_COUNT = NO_ANSWER_COUNT = 16
SCOPE = "AUTHOR_FROZEN_SYNTHETIC_OFFLINE_PLANNING"
PROJECTION = "JSON_PROJECTION_OF_CURRENT_PUBLIC_PRODUCTION_SCHEMA_NO_AGENT_EXECUTION"
METRIC_ARITHMETIC = "FSUM_FLOAT_MEANS_EXACT_INTEGER_COUNTERS"
ORACLE_KEYS = frozenset({"acceptable_dispositions", "acceptable_tools", "relevant_ids", "ground_truth",
                        "expected_answer", "evaluator_only_sentinel", "oracle"})
SOURCE_PATHS = (
    "server/app/agent_runtime/planning_output.py", "server/app/agent_runtime/planning_request.py",
    "server/app/drop_insight/adaptive_planner.py", "server/app/drop_insight/diagnosis_agent.py",
    "server/app/drop_insight/service.py", "server/app/agent_runtime/runtime.py", "server/app/agent_runtime/themes.py",
    "server/app/drop_insight/cpu_criteria.py", "server/app/drop_insight/performance_criteria.py",
    "server/app/agent_runtime/retrieval.py", "server/app/agent_runtime/relevance.py",
    "server/app/agent_runtime/semantic_retrieval.py", "server/app/ai_provider.py",
    "scripts/evaluate_heldout_diagnosis.py", "scripts/evaluate_planning_retrieval_v2.py",
)


def public_only(value):
    if isinstance(value, dict):
        if ORACLE_KEYS.intersection(str(key).casefold() for key in value):
            raise ValueError("private truth reached public projection")
        for item in value.values():
            public_only(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            public_only(item)
    elif isinstance(value, str):
        if "EVALUATOR_ONLY_" in value:
            raise ValueError("private sentinel reached public projection")
        if value.lstrip().startswith(("{", "[")):
            try:
                public_only(json.loads(value))
            except json.JSONDecodeError:
                pass


def validate_freeze(root=ROOT):
    raw = (root / (PREFIX + "manifest.json")).read_bytes()
    if sha(raw) != MANIFEST_SHA:
        raise ValueError("frozen manifest changed")
    manifest = json.loads(raw)
    for suffix, key in (("public", "question_sha256"), ("private", "oracle_sha256")):
        if sha((root / (PREFIX + suffix + ".json")).read_bytes()) != manifest[key]:
            raise ValueError("frozen question or truth changed")
    for name, digest in manifest["previous_question_pins"].items():
        if sha((root / name).read_bytes()) != digest:
            raise ValueError("previous frozen question bytes changed")
    questions = json.loads((root / (PREFIX + "public.json")).read_bytes())
    if len(questions["cases"]) != COUNT or len({case["case_id"] for case in questions["cases"]}) != COUNT:
        raise ValueError("32 unique public cases required")
    public_only(questions)
    return manifest, questions


def corpus_receipt(root=ROOT):
    return {path.relative_to(root).as_posix(): sha(path.read_bytes().replace(b"\r\n", b"\n"))
            for path in sorted((root / "knowledge").glob("*")) if path.suffix in {".md", ".json"}}


@contextmanager
def source_corpus(pins, root=ROOT):
    """Freeze final deployed public bytes for the duration of a single cohort."""
    with tempfile.TemporaryDirectory(prefix="mini-drop-v3-public-corpus-") as directory:
        owned = Path(directory).resolve()
        if corpus_receipt(root) != pins:
            raise ValueError("pinned candidate public corpus changed")
        for name, digest in pins.items():
            target = (owned / name).resolve()
            if not name.startswith("knowledge/") or not target.is_relative_to(owned):
                raise ValueError("unexpected public corpus path")
            raw = (root / name).read_bytes().replace(b"\r\n", b"\n")
            if sha(raw) != digest:
                raise ValueError("pinned public corpus bytes changed")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        yield owned / "knowledge"


def source_receipt(root=ROOT):
    result = {}
    for name in SOURCE_PATHS:
        raw = (root / name).read_bytes()
        result[name] = {"sha256": sha(raw), "bytes": len(raw), "base64": base64.b64encode(raw).decode(),
                        "ast_sha256": behavior_digest(raw), "ast_canonicalization": AST_CANONICALIZATION}
    return result


def bm25_record(query, pins=None):
    with source_corpus(pins if pins is not None else corpus_receipt()) as knowledge:
        matches = retrieve_knowledge(query, top_k=3, relevance_query=query, knowledge_root=knowledge)
    return {"requested_backend": "BM25", "actual_backend": "BM25", "degraded_reasons": [],
            "matched_ids": list(dict.fromkeys(row["knowledge_id"] for row in matches))[:3],
            "matches": matches, "knowledge_is_evidence": False}


def build_request(case, retrieval, model, tools, transport_options=None):
    context = {"problem": case["query"], "target_runtime": case["runtime"],
               "synthetic_observations": case["synthetic_observations"], "allowed_tools": tools,
               "request_intent": planning_request_intent(case["query"]),
               "knowledge_retrieval": {"matches": retrieval["matches"], "is_evidence": False},
               "scope": "SYNTHETIC_OFFLINE_PLANNING_ONLY"}
    payload = {"model": model, "messages": [
        {"role": "system", "content": SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT
         + "\n本次是合成题的离线 JSON 输出适配，不执行工具。只输出符合公共生产合同的 JSON 对象：\n"
         + json.dumps(planning_output_schema(tools), ensure_ascii=False)},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        "temperature": 0.1, "max_tokens": 1400, "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"}}
    if transport_options:
        if set(transport_options) != {"enable_thinking"} or type(transport_options["enable_thinking"]) is not bool:
            raise ValueError("unknown provider transport option")
        payload.update(transport_options)
    public_only(payload)
    _assert_model_boundary(payload)
    return payload


def parse_output(response, allowed):
    try:
        content = response["choices"][0]["message"]["content"]
        if not isinstance(content, str) or len(content.encode()) > 32768:
            raise ValueError("unbounded model content")
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("model output is not object")
        return validate_planning_output(value, allowed)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def retrieval_metrics(questions, private, records):
    truth = {case["case_id"]: case for case in private["cases"]}
    ids = {case["case_id"] for case in questions["cases"]}
    by_id = {row["case_id"]: row for row in records}
    if len(records) != COUNT or len(by_id) != COUNT or len(truth) != COUNT or set(by_id) != ids or set(truth) != ids:
        raise ValueError("missing, duplicate or unknown retrieval record/truth identity")
    rows = []
    for case in questions["cases"]:
        matched = by_id[case["case_id"]]["matched_ids"]
        if len(matched) > 3 or len(set(matched)) != len(matched):
            raise ValueError("retrieval top-k identity contract differs")
        relevant = set(truth[case["case_id"]]["relevant_ids"])
        ranks = [i + 1 for i, name in enumerate(matched) if name in relevant]
        rows.append({"case_id": case["case_id"], "matched_ids": matched, "relevant_ids": sorted(relevant),
                     "recall_at_3": len(relevant.intersection(matched)) / len(relevant) if relevant else None,
                     "reciprocal_rank_at_3": 1 / min(ranks) if ranks else 0,
                     "no_answer_false_positive": bool(matched) if not relevant else None})
    positive = [row for row in rows if row["relevant_ids"]]
    negative = [row for row in rows if not row["relevant_ids"]]
    if (len(positive), len(negative)) != (POSITIVE_COUNT, NO_ANSWER_COUNT):
        raise ValueError("frozen relevance denominators differ")
    return {"case_count": COUNT, "positive_cases": POSITIVE_COUNT, "no_answer_cases": NO_ANSWER_COUNT,
            "recall_at_3": math.fsum(row["recall_at_3"] for row in positive) / POSITIVE_COUNT,
            "mrr_at_3": math.fsum(row["reciprocal_rank_at_3"] for row in positive) / POSITIVE_COUNT,
            "no_answer_false_positive_count": sum(row["no_answer_false_positive"] for row in negative),
            "no_answer_false_positive_rate": sum(row["no_answer_false_positive"] for row in negative) / NO_ANSWER_COUNT}, rows


def score_records(questions, private, records, manifest):
    retrieval, scored = retrieval_metrics(questions, private, [
        {"case_id": row["case_id"], "matched_ids": row["retrieval"]["matched_ids"]} for row in records])
    by_id = {row["case_id"]: row for row in records}
    truth = {row["case_id"]: row for row in private["cases"]}
    for case, result in zip(questions["cases"], scored, strict=True):
        row = by_id[case["case_id"]]
        oracle = truth[case["case_id"]]
        parsed = parse_output(row.get("response") or {}, manifest["runtime_tools"][case["runtime"]]) if row["status"] == "OK" else None
        result.update(status=row["status"], output=parsed, structure_valid=parsed is not None,
                      disposition_correct=bool(parsed and parsed["disposition"] in oracle["acceptable_dispositions"]),
                      tool_choice_expected=bool(parsed and parsed["tool_name"] in oracle["acceptable_tools"]),
                      usage=token_usage(row.get("response") or {}))
    usage = [row["usage"] for row in scored if row["usage"] is not None]
    metrics = {**retrieval, "timeouts": sum(row["status"] == "TIMEOUT" for row in scored),
               "http_successes": sum(row["status"] == "OK" for row in scored),
               "api_errors": sum(row["status"] == "API_ERROR" for row in scored),
               "other_transport_errors": sum(row["status"] not in {"OK", "TIMEOUT", "API_ERROR"} for row in scored),
               "usage_case_count": len(usage), "all_cases_token_usage_known": len(usage) == COUNT,
               "tokens_from_available_usage": {key: sum(row[key] for row in usage) for key in
                                               ("prompt_tokens", "completion_tokens", "total_tokens")},
               "cost_usd": None, "cost_reason": "NO_PINNED_PROVIDER_PRICE_EVIDENCE"}
    for name in ("structure_valid", "disposition_correct", "tool_choice_expected"):
        metrics[name + "_count"] = sum(row[name] for row in scored)
        metrics[name + "_rate"] = metrics[name + "_count"] / COUNT
    metrics["disposition_expected_counts"] = manifest["disposition_counts"]
    metrics["disposition_correct_counts"] = {
        disposition: sum(row["disposition_correct"] and truth[row["case_id"]]["acceptable_dispositions"] == [disposition]
                         for row in scored) for disposition in manifest["disposition_counts"]}
    return metrics, scored


def write_json(path, value):
    with path.open("xb") as stream:
        stream.write((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def remote_public_source_receipt(helper, names, runtime_tools):
    code = "import hashlib,json\nfrom pathlib import Path\nfrom server.app.agent_runtime.planning_output import planning_output_schema\nimport server.app.ai_provider as p\n"
    code += "root=Path(p.__file__).resolve().parents[2]\nnames=" + repr(names) + "\ntools=" + repr(runtime_tools) + "\n"
    code += "print(json.dumps({'sources':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names},'corpus':{x.relative_to(root).as_posix():hashlib.sha256(x.read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest() for x in sorted((root/'knowledge').glob('*')) if x.suffix in {'.json','.md'}},'schemas':{k:hashlib.sha256(json.dumps(planning_output_schema(v),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest() for k,v in tools.items()}}))\n"
    outer = "import subprocess\ncode=" + repr(code) + "\n"
    outer += "p=subprocess.run(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-B','-'],input=code,text=True,capture_output=True,timeout=40)\n"
    outer += "assert p.returncode==0,'Deployed public source unavailable'\nprint(p.stdout,end='')\n"
    return json.loads(helper.remote(outer))


REMOTE_RUN_CODE = r'''
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib,json,sys,time
from server.app.ai_provider import chat_completions,get_ai_settings
items=json.load(sys.stdin)
s=get_ai_settings()
assert len(items)==32 and len({r['case_id'] for r in items})==32
assert s.api_key and s.rca_enabled and s.base_url.startswith('https://')
def run(row):
 started=time.monotonic()
 payload=row['payload']
 assert payload['model']==s.model and payload['max_tokens']==1400 and payload['temperature']==0.1
 result={'type':'result','case_id':row['case_id'],'status':'REQUEST_ERROR','response':{},'http_status':None}
 try:
  response=chat_completions(payload,timeout=48)
  result['http_status']=response.status_code
  body=response.content
  if len(body)>131072:
   result['status']='OVERSIZE_RESPONSE'
  else:
   text=body.decode('utf-8','replace').replace(s.api_key,'<redacted-api-key>')
   result['response_text']=text
   result['response_text_sha256']=hashlib.sha256(text.encode()).hexdigest()
   try:result['response']=json.loads(text)
   except ValueError:result['response']={}
   result['status']='OK' if response.status_code==200 else 'API_ERROR'
 except Exception as exc:
  result['status']='TIMEOUT' if 'timeout' in type(exc).__name__.lower() else 'REQUEST_ERROR'
  result['error_type']=type(exc).__name__[:80]
 result['seconds']=round(time.monotonic()-started,6)
 return result
with ThreadPoolExecutor(max_workers=2) as pool:
 for f in as_completed([pool.submit(run,row) for row in items]):
  print(json.dumps(f.result(),ensure_ascii=False),flush=True)
'''


def remote_stream(helper, program, public):
    public_only(public)
    outer = "import subprocess\nprogram=" + repr(program) + "\nrequests=" + repr(json.dumps(public, ensure_ascii=False)) + "\n"
    outer += "p=subprocess.Popen(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-B','-c',program],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)\n"
    outer += "p.stdin.write(requests);p.stdin.close()\nfor line in p.stdout:\n print(line,end='',flush=True)\n"
    outer += "assert p.wait(timeout=960)==0,'Read-only evaluation process failed'\n"
    process = subprocess.Popen(helper.SSH + ["python3 -"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        process.stdin.write(outer)
        process.stdin.close()
        for line in process.stdout:
            if len(line.encode()) > 512 * 1024:
                raise ValueError("bounded evaluation protocol exceeded")
            yield json.loads(line)
        if process.wait(timeout=60) != 0:
            raise ValueError("read-only evaluation failed; stderr withheld")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20)


def claim_budget(path, backend, source_contract):
    """An interrupted batch cannot silently be restarted in another directory."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"schema": "mini-drop.frozen-single-batch-budget.v3", "manifest_sha256": MANIFEST_SHA,
               "claimed_at_utc": datetime.now(timezone.utc).isoformat(), "backend": backend,
               "max_requests": COUNT, "retries": 0, "source_contract_sha256": sha(canonical(source_contract))}
    write_json(path, receipt)
    return receipt


def fresh_output(output):
    output = output.resolve()
    workspace = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip()).resolve()
    if not output.is_relative_to(workspace) or output.exists():
        raise ValueError("fresh workspace output directory required")
    output.mkdir(parents=True)
    (output / "records").mkdir()
    return output


def candidate_contract(expected_head):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != expected_head:
        raise ValueError("exact candidate Git head differs")
    # Existing annotated worktrees are intentionally unsupported for real
    # calls. The caller restores exact Git blobs into an owned source tree.
    for name in (*SOURCE_PATHS, "scripts/evaluate_planning_boundary_v3.py", *corpus_receipt()):
        raw = subprocess.check_output(["git", "show", expected_head + ":" + name], cwd=ROOT)
        if raw != (ROOT / name).read_bytes():
            raise ValueError("candidate is not exact Git source: " + name)
    return {"schema": "mini-drop.evaluation-public-source-contract.v3", "source_head": head,
            "corpus_lf_sha256": corpus_receipt(),
            "public_prompt_sha256": sha((SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT).encode()),
            "schemas": {runtime: sha(canonical(planning_output_schema(tools))) for runtime, tools in validate_freeze()[0]["runtime_tools"].items()},
            "raw_source_sha256": {name: row["sha256"] for name, row in source_receipt().items()},
            "planning_intent_policy_included": True, "server_deterministic_route_counted_as_model": False}


def evaluate(output, helper_path, expected_head, budget_ledger):
    manifest, questions = validate_freeze()
    contract = candidate_contract(expected_head)
    helper = load_remote_helper(helper_path)
    metadata = remote_metadata(helper)
    server_names = [name for name in SOURCE_PATHS if name.startswith("server/")]
    deployed = remote_public_source_receipt(helper, server_names, manifest["runtime_tools"])
    expected = {"sources": {name: contract["raw_source_sha256"][name] for name in server_names},
                "corpus": contract["corpus_lf_sha256"], "schemas": contract["schemas"]}
    if deployed != expected or metadata.get("published_source_head") != expected_head or metadata.get("production_prompt_sha256") != contract["public_prompt_sha256"]:
        raise ValueError("deployed source/corpus/schema/prompt differs; do not spend frozen chat budget")
    metadata.update(candidate_prompt_sha256=contract["public_prompt_sha256"],
                    ssh_helper_sha256=sha(helper_path.read_bytes()), deployed_public_source=deployed,
                    runtime_prompt_equivalence_claimed=True, candidate_full_runtime_source_equivalence_claimed=False,
                    model_contract_source="CANDIDATE_PUBLIC_PRODUCTION_SCHEMA")
    output = fresh_output(output)
    (output / "requests").mkdir()
    sources = source_receipt()
    write_json(output / "implementation-source.json", sources)
    write_json(output / "source-contract.json", contract)
    write_json(output / "provider.json", metadata)
    write_json(output / "freeze.json", {"manifest_sha256": MANIFEST_SHA, "manifest": manifest})
    (output / "evaluator-source.py").write_bytes(Path(__file__).read_bytes())
    requests, retrievals = [], {}
    for case in questions["cases"]:
        cid = case["case_id"]
        retrievals[cid] = bm25_record(case["query"], contract["corpus_lf_sha256"])
        tools = manifest["runtime_tools"][case["runtime"]]
        payload = build_request(case, retrievals[cid], metadata["model"], tools, metadata.get("transport_options"))
        requests.append({"case_id": cid, "payload": payload})
        write_json(output / "requests" / (cid + ".json"), {"case_id": cid, "payload": payload,
                   "allowed_tools": tools, "payload_sha256": sha(canonical(payload))})
    write_json(output / "budget.json", claim_budget(budget_ledger, "CHAT_WITH_OFFLINE_BM25", contract))
    records = []
    for row in remote_stream(helper, REMOTE_RUN_CODE, requests):
        cid = row["case_id"]
        if row.get("type") != "result" or cid not in retrievals or cid in {item["case_id"] for item in records}:
            raise ValueError("unexpected or duplicate provider record")
        row["retrieval"] = retrievals[cid]
        records.append(row)
        write_json(output / "records" / (cid + ".json"), row)
        print(json.dumps({"completed": len(records), "case_id": cid, "status": row["status"]}), flush=True)
    validate_freeze()
    if source_receipt() != sources or candidate_contract(expected_head) != contract:
        raise ValueError("candidate implementation changed during first run")
    private = json.loads((ROOT / (PREFIX + "private.json")).read_bytes())
    metrics, scored = score_records(questions, private, records, manifest)
    pins = {path.relative_to(output).as_posix(): sha(path.read_bytes()) for path in sorted(output.rglob("*")) if path.is_file()}
    report = {**report_labels(manifest), "run_at_utc": datetime.now(timezone.utc).isoformat(),
              "provider": metadata, "source_head": expected_head, "metrics": metrics,
              "cases": scored, "evidence_sha256": pins}
    write_json(output / "report.json", report)
    print(json.dumps(metrics, ensure_ascii=False), flush=True)
    return report


def report_labels(manifest):
    return {"schema": "mini-drop.planning-boundary-evaluation.v3", "suite_id": manifest["suite_id"],
            "scope": SCOPE, "evaluation_adapter": PROJECTION, "third_party_independent_author": False,
            "truth_frozen_before_evaluator_and_provider_calls": True, "question_or_truth_tuned_after_run": False,
            "manifest_sha256": MANIFEST_SHA, "actual_retrieval_backend": "BM25", "retrieval_is_evidence": False,
            "metric_arithmetic": METRIC_ARITHMETIC, "chat_calls_attempted": COUNT,
            "max_parallel_chat_calls": 2, "retries": 0, "actual_tasks_dispatched": 0, "actual_faults_injected": 0,
            "actual_langgraph_agent_execution": False, "server_deterministic_route_counted_as_model": False, "cost_usd": None}


def verified_pins(directory, report, expected):
    if set(report["evidence_sha256"]) != expected:
        raise ValueError("missing or unregistered evidence pins")
    for name, digest in report["evidence_sha256"].items():
        target = (directory / name).resolve()
        if not target.is_relative_to(directory) or sha(target.read_bytes()) != digest:
            raise ValueError("raw evidence changed")


def verify_report(path):
    manifest, questions = validate_freeze()
    report = json.loads(path.read_bytes())
    labels = report_labels(manifest)
    if set(report) != set(labels) | {"run_at_utc", "provider", "source_head", "metrics", "cases", "evidence_sha256"}:
        raise ValueError("report scope schema differs")
    if any(report.get(key) != value or type(report.get(key)) is not type(value) for key, value in labels.items()):
        raise ValueError("report scope/budget/independence labels differ")
    directory = path.parent.resolve()
    expected = {folder + "/" + case["case_id"] + ".json" for folder in ("requests", "records") for case in questions["cases"]}
    expected.update({"freeze.json", "provider.json", "source-contract.json", "implementation-source.json", "evaluator-source.py", "budget.json"})
    verified_pins(directory, report, expected)
    if behavior_digest((directory / "evaluator-source.py").read_bytes()) != behavior_digest(Path(__file__).read_bytes()):
        raise ValueError("original evaluator behavior differs")
    captured = json.loads((directory / "implementation-source.json").read_bytes())
    current = source_receipt()
    if set(captured) != set(current):
        raise ValueError("candidate source scope changed")
    for name, row in captured.items():
        raw = base64.b64decode(row["base64"], validate=True)
        if len(raw) != row["bytes"] or sha(raw) != row["sha256"] or row["ast_sha256"] != behavior_digest(raw) or row["ast_sha256"] != current[name]["ast_sha256"] or row["ast_canonicalization"] != AST_CANONICALIZATION:
            raise ValueError("candidate behavior changed from first run")
    contract = json.loads((directory / "source-contract.json").read_bytes())
    # Historical replay may run from an archived source directory outside Git;
    # inspect original captured bytes/behavior and corpus instead of new HEAD.
    if contract["source_head"] != report["source_head"] or contract["corpus_lf_sha256"] != corpus_receipt() or contract["public_prompt_sha256"] != sha((SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT).encode()):
        raise ValueError("source contract differs")
    if contract["raw_source_sha256"] != {name: row["sha256"] for name, row in captured.items()} or contract["server_deterministic_route_counted_as_model"] is not False or contract["planning_intent_policy_included"] is not True:
        raise ValueError("source contract policy differs")
    if contract["schemas"] != {runtime: sha(canonical(planning_output_schema(tools))) for runtime, tools in manifest["runtime_tools"].items()}:
        raise ValueError("public schema contract differs")
    provider = json.loads((directory / "provider.json").read_bytes())
    if provider != report["provider"] or provider["candidate_prompt_sha256"] != contract["public_prompt_sha256"]:
        raise ValueError("provider/prompt differs")
    if json.loads((directory / "freeze.json").read_bytes()) != {"manifest_sha256": MANIFEST_SHA, "manifest": manifest}:
        raise ValueError("freeze receipt differs")
    budget = json.loads((directory / "budget.json").read_bytes())
    if budget["manifest_sha256"] != MANIFEST_SHA or budget["backend"] != "CHAT_WITH_OFFLINE_BM25" or budget["max_requests"] != COUNT or budget["retries"] != 0 or budget["source_contract_sha256"] != sha(canonical(contract)):
        raise ValueError("single-batch budget differs")
    records = []
    for case in questions["cases"]:
        cid = case["case_id"]
        request = json.loads((directory / "requests" / (cid + ".json")).read_bytes())
        row = json.loads((directory / "records" / (cid + ".json")).read_bytes())
        retrieval = bm25_record(case["query"], contract["corpus_lf_sha256"])
        tools = manifest["runtime_tools"][case["runtime"]]
        payload = build_request(case, retrieval, provider["model"], tools, provider.get("transport_options"))
        if request != {"case_id": cid, "payload": payload, "allowed_tools": tools, "payload_sha256": sha(canonical(payload))} or row["case_id"] != cid or row["retrieval"] != retrieval:
            raise ValueError("public request, identity or ranking differs")
        if row["status"] == "OK" and row.get("http_status") != 200:
            raise ValueError("success requires HTTP 200 receipt")
        if "response_text" in row:
            if sha(row["response_text"].encode()) != row["response_text_sha256"]:
                raise ValueError("response text digest differs")
            try:
                raw = json.loads(row["response_text"])
            except ValueError:
                raw = {}
            if raw != row.get("response"):
                raise ValueError("raw response projection differs")
        records.append(row)
    private = json.loads((ROOT / (PREFIX + "private.json")).read_bytes())
    metrics, scored = score_records(questions, private, records, manifest)
    if (metrics, scored) != (report["metrics"], report["cases"]):
        raise ValueError("score differs from raw recomputation")
    return metrics


HYBRID_READONLY_CODE = r'''
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib,json,os,sys
from pathlib import Path
from server.app.agent_runtime.retrieval import build_retrieval_trace
import server.app.agent_runtime.retrieval as m
packet=json.load(sys.stdin)
items=packet['cases']
assert len(items)==32 and len({c['case_id'] for c in items})==32
root=Path(m.__file__).resolve().parents[3]
corpus={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in sorted((root/'knowledge').glob('*')) if p.suffix in {'.json','.md'}}
sources={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in packet['expected_sources']}
assert corpus==packet['expected_corpus'] and sources==packet['expected_sources']
os.environ['MINI_DROP_RETRIEVAL_MODE']='hybrid'
def run(case):
 return {'case_id':case['case_id'],'query':case['query'],'trace':build_retrieval_trace(case['query'],top_k=3,relevance_query=case['query'])}
print(json.dumps({'type':'source','runtime_source_sha256':sources,'corpus_lf_sha256':corpus,'index_creation_attempted':False,'chat_calls_attempted':0}),flush=True)
with ThreadPoolExecutor(max_workers=2) as pool:
 for f in as_completed([pool.submit(run,item) for item in items]):
  print(json.dumps({'type':'result',**f.result()},ensure_ascii=False),flush=True)
'''


def evaluate_retrieval(output, helper_path, expected_head, budget_ledger):
    manifest, questions = validate_freeze()
    contract = candidate_contract(expected_head)
    helper = load_remote_helper(helper_path)
    names = [name for name in SOURCE_PATHS if name.startswith("server/")]
    deployed = remote_public_source_receipt(helper, names, manifest["runtime_tools"])
    if deployed != {"sources": {name: contract["raw_source_sha256"][name] for name in names}, "corpus": contract["corpus_lf_sha256"], "schemas": contract["schemas"]}:
        raise ValueError("deployed source differs before read-only retrieval")
    output = fresh_output(output)
    write_json(output / "source-contract.json", contract)
    write_json(output / "implementation-source.json", source_receipt())
    (output / "evaluator-source.py").write_bytes(Path(__file__).read_bytes())
    write_json(output / "freeze.json", {"manifest_sha256": MANIFEST_SHA, "manifest": manifest})
    write_json(output / "budget.json", claim_budget(budget_ledger, "HYBRID_READ_ONLY", contract))
    packet = {"cases": [{"case_id": case["case_id"], "query": case["query"]} for case in questions["cases"]],
              "expected_sources": {name: contract["raw_source_sha256"][name] for name in names}, "expected_corpus": contract["corpus_lf_sha256"]}
    records, provenance = [], None
    for item in remote_stream(helper, HYBRID_READONLY_CODE, packet):
        if item.get("type") == "source":
            if provenance is not None or item["corpus_lf_sha256"] != contract["corpus_lf_sha256"] or item["runtime_source_sha256"] != packet["expected_sources"]:
                raise ValueError("deployed retrieval provenance differs")
            provenance = item
            continue
        cid = item["case_id"]
        if item.get("type") != "result" or cid not in {case["case_id"] for case in questions["cases"]} or cid in {row["case_id"] for row in records}:
            raise ValueError("unexpected or duplicate hybrid record")
        trace = item["trace"]
        if trace.get("requested_backend") != "HYBRID" or trace.get("evidence_contract", {}).get("is_evidence") is not False:
            raise ValueError("hybrid backend/evidence contract differs")
        row = {"case_id": cid, "query": item["query"], "trace": trace,
               "matched_ids": list(dict.fromkeys(match["knowledge_id"] for match in trace["matches"]))[:3]}
        records.append(row)
        write_json(output / "records" / (cid + ".json"), row)
        print(json.dumps({"retrieval_completed": len(records), "case_id": cid, "actual_backend": trace["actual_backend"]}), flush=True)
    if provenance is None or candidate_contract(expected_head) != contract:
        raise ValueError("missing/changed retrieval source receipt")
    write_json(output / "provenance.json", provenance)
    private = json.loads((ROOT / (PREFIX + "private.json")).read_bytes())
    metrics, scored = retrieval_metrics(questions, private, records)
    report = {"schema": "mini-drop.readonly-retrieval-evaluation.v3", "scope": "FROZEN_PUBLIC_KNOWLEDGE_RETRIEVAL_ONLY",
              "manifest_sha256": MANIFEST_SHA, "source_head": expected_head, "requested_backend": "HYBRID",
              "metric_arithmetic": METRIC_ARITHMETIC,
              "actual_backend_counts": {name: sum(row["trace"]["actual_backend"] == name for row in records)
                                        for name in sorted({row["trace"]["actual_backend"] for row in records})},
              "chat_calls_attempted": 0, "actual_tasks_dispatched": 0, "actual_faults_injected": 0,
              "index_creation_attempted": False, "knowledge_is_evidence": False, "cost_usd": None,
              "retrieval_provider_token_usage": None, "metrics": metrics, "cases": scored,
              "evidence_sha256": {path.relative_to(output).as_posix(): sha(path.read_bytes()) for path in sorted(output.rglob("*")) if path.is_file()}}
    write_json(output / "report.json", report)
    print(json.dumps(metrics, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-report", type=Path)
    parser.add_argument("--check-freeze", action="store_true")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--expected-source-head")
    parser.add_argument("--budget-ledger", type=Path)
    parser.add_argument("--remote-provider-helper", type=Path, default=ROOT / "output/acceptance/deployment-20260930/run_strict.py")
    args = parser.parse_args()
    if args.check_freeze:
        manifest, questions = validate_freeze()
        print(json.dumps({"status": "VERIFIED", "manifest_sha256": MANIFEST_SHA, "case_count": len(questions["cases"]), "max_chat_calls": manifest["model_budget"]["max_chat_calls"]}))
    elif args.verify_report:
        print(json.dumps(verify_report(args.verify_report), ensure_ascii=False, indent=2))
    elif args.output and args.expected_source_head and args.budget_ledger:
        function = evaluate_retrieval if args.retrieval_only else evaluate
        function(args.output, args.remote_provider_helper, args.expected_source_head, args.budget_ledger)
    else:
        parser.error("choose --check-freeze, --verify-report, or --output with --expected-source-head and --budget-ledger")


if __name__ == "__main__":
    main()
