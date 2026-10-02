"""Frozen v2 planning evaluation using the public production parser.

Real chat requests exercise a JSON projection of the production planning
contract. No LangGraph/LATS loop, Tool, Task, fault or Evidence gate is run.
"""
from __future__ import annotations

import argparse
import ast
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app.agent_runtime.planning_output import planning_output_schema, validate_planning_output
from server.app.agent_runtime.retrieval import retrieve_knowledge
from server.app.drop_insight.adaptive_planner import SYSTEM_PROMPT, EVIDENCE_PLANNING_REQUIREMENT
from server.app.ai_provider import _assert_model_boundary, get_ai_settings
from scripts.evaluate_heldout_diagnosis import (
    load_remote_helper, local_results, remote_metadata, remote_results, token_usage,
)

PREFIX = "benchmarks/retrieval/planning_retrieval_v2_"
MANIFEST_SHA = "ae57be9a369db8df7c10131810f4deaa68bf2ea91104911233a6ad67c8f58d4e"
SCOPE = "AUTHOR_FROZEN_SYNTHETIC_OFFLINE_PLANNING"
PROJECTION = "JSON_PROJECTION_OF_CURRENT_PUBLIC_PRODUCTION_SCHEMA_NO_AGENT_EXECUTION"
METRIC_ARITHMETIC = "FSUM_FLOAT_MEANS_EXACT_INTEGER_COUNTERS"
ORACLE_KEYS = frozenset({"acceptable_dispositions", "acceptable_tools", "relevant_ids",
                        "ground_truth", "expected_answer", "evaluator_only_sentinel", "oracle"})
SOURCE_PATHS = ("server/app/agent_runtime/planning_output.py",
                "server/app/drop_insight/adaptive_planner.py",
                "server/app/drop_insight/cpu_criteria.py",
                "server/app/drop_insight/performance_criteria.py",
                "server/app/agent_runtime/retrieval.py",
                "server/app/agent_runtime/semantic_retrieval.py",
                "server/app/agent_runtime/relevance.py",
                "server/app/ai_provider.py",
                "scripts/evaluate_heldout_diagnosis.py")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


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
        if "EVALUATOR_ONLY_PLANNING_V2_" in value:
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
    for name, digest in manifest["corpus_files"].items():
        if sha((root / name).read_bytes().replace(b"\r\n", b"\n")) != digest:
            raise ValueError("frozen public corpus changed")
    questions = json.loads((root / (PREFIX + "public.json")).read_bytes())
    if len(questions["cases"]) != 24 or len({c["case_id"] for c in questions["cases"]}) != 24:
        raise ValueError("24 unique public cases required")
    public_only(questions)
    return manifest, questions


def source_receipt(root=ROOT):
    result = {}
    for name in SOURCE_PATHS:
        path = root / name
        raw = path.read_bytes()
        result[name] = {"sha256": sha(raw), "bytes": len(raw), "base64": base64.b64encode(raw).decode(),
                        "ast_sha256": sha(ast.dump(ast.parse(raw), include_attributes=False).encode())}
    return result


@contextmanager
def frozen_corpus(root=ROOT):
    """Use the frozen LF_TEXT contract identically on Windows and Linux."""
    manifest, _ = validate_freeze(root)
    with tempfile.TemporaryDirectory(prefix="mini-drop-v2-public-corpus-") as directory:
        owned = Path(directory).resolve()
        for name, digest in manifest["corpus_files"].items():
            target = (owned / name).resolve()
            if not name.startswith("knowledge/") or not target.is_relative_to(owned):
                raise ValueError("unexpected public corpus path")
            raw = (root / name).read_bytes().replace(b"\r\n", b"\n")
            if sha(raw) != digest:
                raise ValueError("frozen public corpus changed")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        yield owned / "knowledge"


def bm25_record(query):
    with frozen_corpus() as knowledge:
        matches = retrieve_knowledge(query, top_k=3, relevance_query=query, knowledge_root=knowledge)
    return {"requested_backend": "BM25", "actual_backend": "BM25", "degraded_reasons": [],
            "matched_ids": list(dict.fromkeys(row["knowledge_id"] for row in matches))[:3],
            "matches": matches, "knowledge_is_evidence": False}


def build_request(case, retrieval, model, tools, transport_options=None):
    schema = planning_output_schema(tools)
    context = {"problem": case["query"], "target_runtime": case["runtime"],
               "synthetic_observations": case["synthetic_observations"], "allowed_tools": tools,
               "knowledge_retrieval": {"matches": retrieval["matches"], "is_evidence": False},
               "scope": "SYNTHETIC_OFFLINE_PLANNING_ONLY"}
    payload = {"model": model, "messages": [
        {"role": "system", "content": SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT
         + "\n本次是合成题的离线 JSON 输出适配，不执行工具。只输出符合公共生产合同的 JSON 对象：\n"
         + json.dumps(schema, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        "temperature": 0.1, "max_tokens": 1200, "thinking": {"type": "disabled"},
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


def score_records(questions, private, records, manifest):
    cases = questions["cases"]
    truth = {row["case_id"]: row for row in private["cases"]}
    by_id = {row["case_id"]: row for row in records}
    ids = {row["case_id"] for row in cases}
    if len(truth) != 24 or len(records) != 24 or len(by_id) != 24 or set(truth) != ids or set(by_id) != ids:
        raise ValueError("missing, unknown or duplicate record/truth identity")
    scored = []
    for case in cases:
        row = by_id[case["case_id"]]
        oracle = truth[case["case_id"]]
        relevant = set(oracle["relevant_ids"])
        matched = row["retrieval"]["matched_ids"]
        ranks = [i + 1 for i, name in enumerate(matched) if name in relevant]
        parsed = parse_output(row.get("response") or {}, manifest["runtime_tools"][case["runtime"]]) if row["status"] == "OK" else None
        scored.append({"case_id": case["case_id"], "status": row["status"], "output": parsed,
                       "structure_valid": parsed is not None,
                       "disposition_correct": bool(parsed and parsed["disposition"] in oracle["acceptable_dispositions"]),
                       "tool_choice_expected": bool(parsed and parsed["tool_name"] in oracle["acceptable_tools"]),
                       "matched_ids": matched, "relevant_ids": oracle["relevant_ids"],
                       "recall_at_3": len(relevant.intersection(matched)) / len(relevant) if relevant else None,
                       "reciprocal_rank_at_3": 1 / min(ranks) if ranks else 0,
                       "no_answer_false_positive": bool(matched) if not relevant else None,
                       "usage": token_usage(row.get("response") or {})})
    positives = [row for row in scored if row["relevant_ids"]]
    negatives = [row for row in scored if not row["relevant_ids"]]
    if (len(positives), len(negatives)) != (16, 8):
        raise ValueError("frozen relevance denominators differ")
    usage = [row["usage"] for row in scored if row["usage"] is not None]
    metrics = {"case_count": 24, "retrieval_positive_cases": 16, "retrieval_no_answer_cases": 8,
               "recall_at_3": math.fsum(row["recall_at_3"] for row in positives) / 16,
               "mrr_at_3": math.fsum(row["reciprocal_rank_at_3"] for row in positives) / 16,
               "no_answer_false_positive_count": sum(row["no_answer_false_positive"] for row in negatives),
               "no_answer_false_positive_rate": sum(row["no_answer_false_positive"] for row in negatives) / 8,
               "timeouts": sum(row["status"] == "TIMEOUT" for row in scored),
               "http_successes": sum(row["status"] == "OK" for row in scored),
               "api_errors": sum(row["status"] == "API_ERROR" for row in scored),
               "usage_case_count": len(usage), "all_cases_token_usage_known": len(usage) == 24,
               "tokens_from_available_usage": {key: sum(row[key] for row in usage) for key in
                                               ("prompt_tokens", "completion_tokens", "total_tokens")},
               "cost_usd": None, "cost_reason": "NO_PINNED_PROVIDER_PRICE_EVIDENCE"}
    for name in ("structure_valid", "disposition_correct", "tool_choice_expected"):
        metrics[name + "_count"] = sum(row[name] for row in scored)
        metrics[name + "_rate"] = metrics[name + "_count"] / 24
    return metrics, scored


def write_json(path, value):
    with path.open("xb") as stream:
        stream.write((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def remote_schema_receipt(helper, runtime_tools):
    code = "import hashlib,json\nfrom server.app.agent_runtime.planning_output import planning_output_schema\n"
    code += "tools=" + repr(runtime_tools) + "\n"
    code += "print(json.dumps({k:hashlib.sha256(json.dumps(planning_output_schema(v),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest() for k,v in tools.items()}))\n"
    outer = "import subprocess\ncode=" + repr(code) + "\n"
    outer += "p=subprocess.run(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-B','-'],input=code,text=True,capture_output=True,timeout=30)\n"
    outer += "assert p.returncode==0,'Deployed public planning schema unavailable'\nprint(p.stdout,end='')\n"
    return json.loads(helper.remote(outer))


def evaluate(output, provider, helper_path):
    manifest, questions = validate_freeze()
    output = output.resolve()
    if not output.is_relative_to(ROOT.resolve()) or output.exists():
        raise ValueError("fresh workspace output directory required")
    output.mkdir(parents=True)
    for folder in ("requests", "records"):
        (output / folder).mkdir()
    sources = source_receipt()
    write_json(output / "implementation-source.json", sources)
    (output / "evaluator-source.py").write_bytes(Path(__file__).read_bytes())
    write_json(output / "freeze.json", {"manifest_sha256": MANIFEST_SHA, "manifest": manifest})
    if provider == "ssh-current":
        helper = load_remote_helper(helper_path)
        metadata = remote_metadata(helper)
        metadata["ssh_helper_sha256"] = sha(helper_path.read_bytes())
        if metadata.get("production_prompt_sha256") != sha((SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT).encode()):
            raise ValueError("deployed production prompt differs from candidate; do not spend frozen chat budget")
        schemas = {k: sha(canonical(planning_output_schema(v))) for k, v in manifest["runtime_tools"].items()}
        if remote_schema_receipt(helper, manifest["runtime_tools"]) != schemas:
            raise ValueError("deployed public planning schema differs; do not spend frozen chat budget")
        metadata["deployed_public_schema_sha256_by_runtime"] = schemas
    else:
        settings = get_ai_settings()
        if not settings.api_key or not settings.rca_enabled or not settings.base_url.startswith("https://"):
            raise ValueError("configured HTTPS provider required; no synthetic fallback")
        metadata = {"provider": settings.provider, "model": settings.model, "key_present": True, "transport_options": {}}
    metadata["model_contract_source"] = "CANDIDATE_PUBLIC_PRODUCTION_SCHEMA"
    metadata["runtime_prompt_equivalence_claimed"] = provider == "ssh-current"
    metadata["candidate_full_runtime_source_equivalence_claimed"] = False
    metadata["candidate_prompt_sha256"] = sha((SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT).encode())
    write_json(output / "provider.json", metadata)
    requests = []
    retrievals = {}
    for case in questions["cases"]:
        cid = case["case_id"]
        retrievals[cid] = bm25_record(case["query"])
        tools = manifest["runtime_tools"][case["runtime"]]
        payload = build_request(case, retrievals[cid], metadata["model"], tools, metadata.get("transport_options"))
        item = {"case_id": cid, "payload": payload}
        requests.append(item)
        write_json(output / "requests" / (cid + ".json"), {**item, "allowed_tools": tools, "payload_sha256": sha(canonical(payload))})
    records = []
    generator = remote_results(helper, requests) if provider == "ssh-current" else local_results(requests)
    for row in generator:
        cid = row["case_id"]
        if cid not in retrievals or cid in {r["case_id"] for r in records}:
            raise ValueError("unexpected or duplicate provider record")
        row["retrieval"] = retrievals[cid]
        records.append(row)
        write_json(output / "records" / (cid + ".json"), row)
        print(json.dumps({"completed": len(records), "case_id": cid, "status": row["status"]}), flush=True)
    validate_freeze()
    if source_receipt() != sources:
        raise ValueError("candidate implementation changed during first run")
    # Scoring is the first parse of private truth; no request/retry uses it.
    private = json.loads((ROOT / (PREFIX + "private.json")).read_bytes())
    metrics, scored = score_records(questions, private, records, manifest)
    pins = {path.relative_to(output).as_posix(): sha(path.read_bytes()) for path in sorted(output.rglob("*")) if path.is_file()}
    report = {"schema": "mini-drop.planning-retrieval-evaluation.v2", "suite_id": manifest["suite_id"],
              "scope": SCOPE, "evaluation_adapter": PROJECTION, "third_party_independent_author": False,
              "truth_frozen_before_evaluator_and_provider_calls": True, "question_or_truth_tuned_after_run": False,
              "run_at_utc": datetime.now(timezone.utc).isoformat(), "manifest_sha256": MANIFEST_SHA,
              "actual_retrieval_backend": "BM25", "retrieval_is_evidence": False,
              "metric_arithmetic": METRIC_ARITHMETIC,
              "provider": metadata, "chat_calls_attempted": 24, "max_parallel_chat_calls": 2,
              "retries": 0, "actual_tasks_dispatched": 0, "actual_faults_injected": 0,
              "cost_usd": None, "metrics": metrics, "cases": scored, "evidence_sha256": pins}
    write_json(output / "report.json", report)
    print(json.dumps(metrics, ensure_ascii=False), flush=True)
    return report


def verify_report(path):
    manifest, questions = validate_freeze()
    report = json.loads(path.read_bytes())
    labels = {"schema": "mini-drop.planning-retrieval-evaluation.v2", "suite_id": manifest["suite_id"],
              "scope": SCOPE, "evaluation_adapter": PROJECTION, "third_party_independent_author": False,
              "truth_frozen_before_evaluator_and_provider_calls": True, "question_or_truth_tuned_after_run": False,
              "manifest_sha256": MANIFEST_SHA, "actual_retrieval_backend": "BM25", "retrieval_is_evidence": False,
              "metric_arithmetic": METRIC_ARITHMETIC,
              "chat_calls_attempted": 24, "max_parallel_chat_calls": 2, "retries": 0,
              "actual_tasks_dispatched": 0, "actual_faults_injected": 0, "cost_usd": None}
    if set(report) != set(labels) | {"run_at_utc", "provider", "metrics", "cases", "evidence_sha256"}:
        raise ValueError("report scope schema differs")
    if any(report.get(k) != v or type(report.get(k)) is not type(v) for k, v in labels.items()):
        raise ValueError("report scope/budget/independence labels differ")
    directory = path.parent.resolve()
    expected = {folder + "/" + c["case_id"] + ".json" for folder in ("requests", "records") for c in questions["cases"]}
    expected.update(("freeze.json", "provider.json", "implementation-source.json", "evaluator-source.py"))
    pins = report["evidence_sha256"]
    if set(pins) != expected:
        raise ValueError("missing or unregistered evidence pins")
    for name, digest in pins.items():
        target = (directory / name).resolve()
        if not target.is_relative_to(directory) or sha(target.read_bytes()) != digest:
            raise ValueError("raw evidence changed")
    if ast.dump(ast.parse((directory / "evaluator-source.py").read_bytes()), include_attributes=False) != ast.dump(ast.parse(Path(__file__).read_bytes()), include_attributes=False):
        raise ValueError("original evaluator behavior differs")
    captured = json.loads((directory / "implementation-source.json").read_bytes())
    current = source_receipt()
    if set(current) != set(captured):
        raise ValueError("candidate source scope changed")
    for name, row in captured.items():
        raw = base64.b64decode(row["base64"], validate=True)
        if len(raw) != row["bytes"] or sha(raw) != row["sha256"] or row["ast_sha256"] != sha(ast.dump(ast.parse(raw), include_attributes=False).encode()) or row["ast_sha256"] != current[name]["ast_sha256"]:
            raise ValueError("candidate behavior changed from first run")
    provider = json.loads((directory / "provider.json").read_bytes())
    if report["provider"] != provider or provider.get("candidate_prompt_sha256") != sha((SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT).encode()):
        raise ValueError("candidate provider/prompt differs")
    if json.loads((directory / "freeze.json").read_bytes()) != {"manifest_sha256": MANIFEST_SHA, "manifest": manifest}:
        raise ValueError("freeze receipt differs")
    records = []
    for case in questions["cases"]:
        cid = case["case_id"]
        request = json.loads((directory / "requests" / (cid + ".json")).read_bytes())
        row = json.loads((directory / "records" / (cid + ".json")).read_bytes())
        retrieval = bm25_record(case["query"])
        tools = manifest["runtime_tools"][case["runtime"]]
        payload = build_request(case, retrieval, provider["model"], tools, provider.get("transport_options"))
        if request != {"case_id": cid, "payload": payload, "allowed_tools": tools, "payload_sha256": sha(canonical(payload))} or row["case_id"] != cid or row["retrieval"] != retrieval:
            raise ValueError("public request, identity or actual ranking differs")
        if row["status"] == "OK" and row.get("http_status") != 200:
            raise ValueError("success requires HTTP 200 receipt")
        if "response_text" in row:
            if sha(row["response_text"].encode()) != row["response_text_sha256"]:
                raise ValueError("response text digest differs")
            try:
                parsed = json.loads(row["response_text"])
            except ValueError:
                parsed = {}
            if parsed != row.get("response"):
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
import server.app.agent_runtime.retrieval as retrieval_module
items=json.load(sys.stdin)
assert len(items)==24 and len({c['case_id'] for c in items})==24
# Only this disposable evaluation process overrides its requested mode. No
# container config, index, application data or environment file is changed.
os.environ['MINI_DROP_RETRIEVAL_MODE']='hybrid'
root=Path(retrieval_module.__file__).resolve().parents[3]
names=('server/app/agent_runtime/retrieval.py','server/app/agent_runtime/relevance.py',
       'server/app/agent_runtime/semantic_retrieval.py')
sources={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
def run(case):
 return {'case_id':case['case_id'],'query':case['query'],
         'trace':build_retrieval_trace(case['query'],top_k=3,relevance_query=case['query'])}
print(json.dumps({'type':'source','runtime_source_sha256':sources,
 'corpus_lf_sha256':{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
  for p in sorted((root/'knowledge').glob('*')) if p.suffix in {'.json','.md'}},
 'index_creation_attempted':False,'chat_calls_attempted':0}),flush=True)
with ThreadPoolExecutor(max_workers=2) as pool:
 for future in as_completed([pool.submit(run,item) for item in items]):
  print(json.dumps({'type':'result',**future.result()},ensure_ascii=False),flush=True)
'''


def remote_hybrid_results(helper, cases):
    public = [{"case_id": c["case_id"], "query": c["query"]} for c in cases]
    public_only(public)
    outer = "import subprocess\nprogram=" + repr(HYBRID_READONLY_CODE) + "\n"
    outer += "requests=" + repr(json.dumps(public, ensure_ascii=False)) + "\n"
    outer += "p=subprocess.Popen(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-B','-c',program],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)\n"
    outer += "p.stdin.write(requests);p.stdin.close()\nfor line in p.stdout:\n print(line,end='',flush=True)\n"
    outer += "assert p.wait(timeout=660)==0,'Read-only retrieval process failed'\n"
    process = subprocess.Popen(helper.SSH + ["python3 -"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf8")
    try:
        process.stdin.write(outer)
        process.stdin.close()
        for line in process.stdout:
            if len(line.encode()) > 512 * 1024:
                raise ValueError("retrieval protocol size exceeded")
            yield json.loads(line)
        if process.wait(timeout=60) != 0:
            raise ValueError("read-only deployed retrieval failed; stderr withheld")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20)


def retrieval_metrics(questions, private, records):
    truth = {c["case_id"]: c for c in private["cases"]}
    ids = {c["case_id"] for c in questions["cases"]}
    by_id = {r["case_id"]: r for r in records}
    if len(records) != 24 or len(by_id) != 24 or set(by_id) != ids or set(truth) != ids:
        raise ValueError("retrieval records missing, duplicated or unknown")
    rows = []
    for case in questions["cases"]:
        record = by_id[case["case_id"]]
        matched = record["matched_ids"]
        relevant = set(truth[case["case_id"]]["relevant_ids"])
        ranks = [i + 1 for i, kid in enumerate(matched) if kid in relevant]
        rows.append({"case_id": case["case_id"], "matched_ids": matched,
                     "relevant_ids": sorted(relevant),
                     "recall_at_3": len(relevant.intersection(matched)) / len(relevant) if relevant else None,
                     "reciprocal_rank_at_3": 1 / min(ranks) if ranks else 0,
                     "no_answer_false_positive": bool(matched) if not relevant else None})
    positive = [r for r in rows if r["relevant_ids"]]
    negative = [r for r in rows if not r["relevant_ids"]]
    if (len(positive), len(negative)) != (16, 8):
        raise ValueError("retrieval frozen denominators differ")
    return {"case_count": 24, "positive_cases": 16, "no_answer_cases": 8,
            "recall_at_3": math.fsum(r["recall_at_3"] for r in positive) / 16,
            "mrr_at_3": math.fsum(r["reciprocal_rank_at_3"] for r in positive) / 16,
            "no_answer_false_positive_count": sum(r["no_answer_false_positive"] for r in negative),
            "no_answer_false_positive_rate": sum(r["no_answer_false_positive"] for r in negative) / 8}, rows


def evaluate_retrieval(output, backend, helper_path):
    manifest, questions = validate_freeze()
    output = output.resolve()
    if not output.is_relative_to(ROOT.resolve()) or output.exists():
        raise ValueError("fresh retrieval output required")
    output.mkdir(parents=True)
    (output / "records").mkdir()
    write_json(output / "implementation-source.json", source_receipt())
    records = []
    provenance = None
    if backend == "HYBRID":
        generator = remote_hybrid_results(load_remote_helper(helper_path), questions["cases"])
        for item in generator:
            if item.get("type") == "source":
                if provenance is not None or item.get("corpus_lf_sha256") != manifest["corpus_files"]:
                    raise ValueError("deployed public corpus or source receipt differs")
                sources = source_receipt()
                if any(digest != sources.get(name, {}).get("sha256") for name, digest in item["runtime_source_sha256"].items()):
                    raise ValueError("deployed retrieval source differs from clean candidate")
                provenance = item
                continue
            if item.get("type") != "result" or item["case_id"] not in {c["case_id"] for c in questions["cases"]}:
                raise ValueError("unexpected hybrid retrieval protocol")
            trace = item["trace"]
            if trace.get("requested_backend") != "HYBRID" or trace.get("evidence_contract", {}).get("is_evidence") is not False:
                raise ValueError("hybrid retrieval backend/evidence receipt differs")
            row = {"case_id": item["case_id"], "query": item["query"], "trace": trace,
                   "matched_ids": list(dict.fromkeys(m["knowledge_id"] for m in trace["matches"]))[:3]}
            records.append(row)
            write_json(output / "records" / (row["case_id"] + ".json"), row)
            print(json.dumps({"retrieval_completed": len(records), "case_id": row["case_id"], "actual_backend": trace["actual_backend"]}), flush=True)
        if provenance is None:
            raise ValueError("missing actual deployed source receipt")
    else:
        for case in questions["cases"]:
            row = {"case_id": case["case_id"], "query": case["query"], **bm25_record(case["query"])}
            records.append(row)
            write_json(output / "records" / (row["case_id"] + ".json"), row)
        provenance = {"scope": "CURRENT_PRODUCTION_BM25_WITH_FROZEN_LF_PUBLIC_CORPUS", "chat_calls_attempted": 0}
    write_json(output / "provenance.json", provenance)
    private = json.loads((ROOT / (PREFIX + "private.json")).read_bytes())
    metrics, rows = retrieval_metrics(questions, private, records)
    pins = {p.relative_to(output).as_posix(): sha(p.read_bytes()) for p in output.rglob("*") if p.is_file()}
    report = {"schema": "mini-drop.readonly-retrieval-evaluation.v2", "scope": "FROZEN_PUBLIC_KNOWLEDGE_RETRIEVAL_ONLY",
              "manifest_sha256": MANIFEST_SHA, "requested_backend": backend,
              "metric_arithmetic": METRIC_ARITHMETIC,
              "actual_backend_counts": {name: sum(r.get("trace", r).get("actual_backend") == name for r in records)
                                        for name in sorted({r.get("trace", r).get("actual_backend") for r in records})},
              "chat_calls_attempted": 0, "actual_tasks_dispatched": 0, "actual_faults_injected": 0,
              "index_creation_attempted": False, "knowledge_is_evidence": False, "cost_usd": None,
              "retrieval_provider_token_usage": None, "metrics": metrics, "cases": rows, "evidence_sha256": pins}
    write_json(output / "report.json", report)
    print(json.dumps(metrics, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--provider", choices=("local", "ssh-current"), default="local")
    parser.add_argument("--verify-report", type=Path)
    parser.add_argument("--check-freeze", action="store_true")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--backend", choices=("BM25", "HYBRID"), default="BM25")
    parser.add_argument("--remote-provider-helper", type=Path, default=ROOT / "output/acceptance/deployment-20260930/run_strict.py")
    args = parser.parse_args()
    if args.check_freeze:
        manifest, questions = validate_freeze()
        print(json.dumps({"status": "VERIFIED", "manifest_sha256": MANIFEST_SHA, "case_count": len(questions["cases"]), "max_chat_calls": manifest["model_budget"]["max_chat_calls"]}))
    elif args.verify_report:
        print(json.dumps(verify_report(args.verify_report), ensure_ascii=False, indent=2))
    elif args.output and args.retrieval_only:
        evaluate_retrieval(args.output, args.backend, args.remote_provider_helper)
    elif args.output:
        evaluate(args.output, args.provider, args.remote_provider_helper)
    else:
        parser.error("choose --check-freeze, --verify-report or --output")


if __name__ == "__main__":
    main()
