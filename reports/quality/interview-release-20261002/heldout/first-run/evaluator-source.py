"""Frozen offline planner/retrieval evaluation; never dispatch a Task or a fault.

The fixed output adapter measures planning decisions from synthetic observations.
It does not exercise LangGraph/LATS, Evidence Gate, or live incident diagnosis.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app.ai_provider import _assert_model_boundary, chat_completions, get_ai_settings
from server.app.agent_runtime.retrieval import retrieve_knowledge
from server.app.agent_runtime.semantic_retrieval import corpus
from server.app.drop_insight.adaptive_planner import SYSTEM_PROMPT, EVIDENCE_PLANNING_REQUIREMENT

PREFIX = "benchmarks/retrieval/heldout_20261002_"
FROZEN_MANIFEST_SHA256 = "d6fde80a176a268ef9c13cd6ca5ddce2a8d33755769b0dc2a00ab6f6ce15e0c2"
RUNTIME_TOOLS = {
    "PYTHON": ("collect_sys_metrics", "start_pyspy_profile", "collect_memory_profile", "start_ebpf_io_profile"),
    "CPP": ("collect_sys_metrics", "start_perf_profile", "collect_memory_profile", "start_ebpf_io_profile"),
    "JAVA": ("collect_sys_metrics", "start_jvm_profile", "collect_memory_profile", "start_ebpf_io_profile"),
    "GO": ("collect_sys_metrics", "collect_go_profile", "collect_memory_profile", "start_ebpf_io_profile"),
    "UNKNOWN": ("collect_sys_metrics",),
}
CATEGORIES = ("CPU", "MEMORY", "IO", "NETWORK", "DOWNSTREAM", "LOCK", "GC", "QUEUE",
              "RESOURCE_CONTENTION", "NORMAL", "UNKNOWN", "MIXED", "DATABASE",
              "AGENT_GOVERNANCE", "RETRIEVAL", "NOT_A_DIAGNOSIS")
DISPOSITIONS = ("OBSERVATION_SUPPORTED", "OBSERVATION_REFUTED", "NORMAL_IN_CHECKED_SCOPE",
                "INSUFFICIENT_DATA", "MIXED_OBSERVATIONS", "NOT_A_DIAGNOSIS")
ORACLE_KEYS = frozenset({"oracle_only_sentinel", "evaluator_only", "relevant_ids",
    "acceptable_categories", "acceptable_dispositions", "acceptable_tools", "ground_truth",
    "evaluation_oracle", "expected_answer", "expected_root_cause"})
ADAPTER = """\n以下是离线评估输出适配器，输入观测为合成题，不能成为实际事故Evidence。
在允许范围内提出规划，不执行工具，不声明因果根因已验证。
只输出一个JSON对象，字段固定为category、disposition、next_tool、reasoning_summary、
expected_observations、falsification_criteria、causal_root_cause_verified。
category从给定分类取一个；复合资源异常用MIXED，正常仅限已检查范围，缺测用UNKNOWN或具体待查域。
disposition说明合成窗口中的有界判断：OBSERVATION_SUPPORTED为测量支持异常，
OBSERVATION_REFUTED为完整测量反驳被描述的异常，NORMAL_IN_CHECKED_SCOPE为例行范围正常，
INSUFFICIENT_DATA为缺测/未证实，MIXED_OBSERVATIONS为多个异常，NOT_A_DIAGNOSIS为非诊断请求。
next_tool只能选允许列表；NO_TOOL表示无需/无法继续，ASK_CLARIFICATION表示需澄清。
reasoning_summary为简短中文说明；两组条件是中文非空字符串数组；因果标志必须false。
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def normalize_query(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def assert_no_oracle(value):
    if isinstance(value, dict):
        if ORACLE_KEYS.intersection(str(key).casefold() for key in value):
            raise ValueError("evaluator-only truth reached public model context")
        for item in value.values():
            assert_no_oracle(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_oracle(item)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                assert_no_oracle(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        if "EVALUATOR_ONLY_HELDOUT_" in value:
            raise ValueError("evaluator-only sentinel reached model context")


def validate_freeze(root=ROOT):
    manifest_path = root / (PREFIX + "manifest.json")
    if sha(manifest_path.read_bytes()) != FROZEN_MANIFEST_SHA256:
        raise ValueError("pre-implementation manifest bytes changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for suffix, key in (("public", "question_sha256"), ("private", "oracle_sha256")):
        if sha((root / (PREFIX + suffix + ".json")).read_bytes()) != manifest[key]:
            raise ValueError("frozen " + suffix + " bytes changed")
    development_path = root / "benchmarks/retrieval/sre_queries.json"
    if sha(development_path.read_bytes()) != manifest["development_set_sha256"]:
        raise ValueError("development set changed")
    for path, digest in {**manifest["corpus_files"], **manifest["production_source_files"]}.items():
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root.resolve()) or sha(candidate.read_bytes()) != digest:
            raise ValueError("frozen corpus/source bytes changed: " + path)
    questions = json.loads((root / (PREFIX + "public.json")).read_text(encoding="utf-8"))
    development = json.loads(development_path.read_text(encoding="utf-8"))
    cases = questions["cases"]
    normalized = {normalize_query(c["query"]) for c in cases}
    if len(cases) != 24 or len(normalized) != len(cases):
        raise ValueError("heldout must contain 24 distinct questions")
    if normalized & {normalize_query(c["query"]) for c in development["cases"]}:
        raise ValueError("heldout overlaps development questions")
    if len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("duplicate case IDs")
    for case in cases:
        if set(case) != {"case_id", "query", "runtime", "synthetic_observations"}:
            raise ValueError("public case includes an unexpected field")
        if case["runtime"] not in RUNTIME_TOOLS:
            raise ValueError("unsupported target runtime")
        assert_no_oracle(case)
    chunks = corpus(root / "knowledge")
    if sha(json.dumps(chunks, sort_keys=True, ensure_ascii=False).encode()) != manifest["corpus_chunk_sha256"]:
        raise ValueError("frozen chunk projection changed")
    if (manifest.get("max_chat_calls") != 24 or manifest.get("max_parallel_chat_calls") != 2
        or manifest.get("retrieval_backend") != "BM25" or manifest.get("no_retries") is not True):
        raise ValueError("frozen execution budget/backend changed")
    return manifest, questions, sha(manifest_path.read_bytes())


def build_request(case, matches, model, transport_options=None):
    allowed = [*RUNTIME_TOOLS[case["runtime"]], "NO_TOOL", "ASK_CLARIFICATION"]
    context = {"problem": case["query"], "target_runtime": case["runtime"],
        "allowed_tools": allowed, "synthetic_observations": case["synthetic_observations"],
        "knowledge_retrieval": {"matches": matches, "is_evidence": False},
        "scope": "SYNTHETIC_OFFLINE_PLANNING_ONLY"}
    assert_no_oracle(context)
    payload = {"model": model, "messages": [
        {"role": "system", "content": SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT + ADAPTER
            + "\n分类: " + json.dumps(CATEGORIES) + "\n判断: " + json.dumps(DISPOSITIONS)},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        "thinking": {"type": "disabled"}, "temperature": 0.1, "max_tokens": 1200,
        "response_format": {"type": "json_object"}}
    if transport_options:
        if set(transport_options) != {"enable_thinking"} or not isinstance(transport_options["enable_thinking"], bool):
            raise ValueError("unsupported provider transport options")
        payload.update(transport_options)
    assert_no_oracle(payload)
    _assert_model_boundary(payload)
    return payload, allowed


def parse_output(response):
    try:
        value = response["choices"][0]["message"]["content"]
        if not isinstance(value, str) or len(value.encode()) > 32768:
            raise ValueError("unbounded content")
        result = json.loads(value)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    required = {"category", "disposition", "next_tool", "reasoning_summary", "expected_observations",
                "falsification_criteria", "causal_root_cause_verified"}
    if not isinstance(result, dict) or set(result) != required:
        return None
    if result["category"] not in CATEGORIES or result["disposition"] not in DISPOSITIONS:
        return None
    if not isinstance(result["next_tool"], str) or result["causal_root_cause_verified"] is not False:
        return None
    if not isinstance(result["reasoning_summary"], str) or not result["reasoning_summary"].strip():
        return None
    for name in ("expected_observations", "falsification_criteria"):
        items = result[name]
        if not isinstance(items, list) or not 1 <= len(items) <= 6 or any(
            not isinstance(x, str) or not x.strip() or len(x) > 1500 for x in items):
            return None
    return result


def token_usage(response):
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        result[key] = value
    if result["total_tokens"] != result["prompt_tokens"] + result["completion_tokens"]:
        return None
    return result


def score_records(questions, private, records):
    cases = questions["cases"]
    truth = {c["case_id"]: c for c in private["cases"]}
    if len(truth) != len(cases) or set(truth) != {c["case_id"] for c in cases}:
        raise ValueError("oracle/public ID coverage differs")
    if len(records) != len(cases) or len({r["case_id"] for r in records}) != len(cases):
        raise ValueError("responses missing or duplicated; cannot publish complete score")
    by_id = {r["case_id"]: r for r in records}
    if set(by_id) != set(truth):
        raise ValueError("response/public ID coverage differs")
    scored = []
    for case in cases:
        row, oracle = by_id[case["case_id"]], truth[case["case_id"]]
        ids = row["retrieval"]["matched_ids"]
        relevant = set(oracle["relevant_ids"])
        ranks = [i + 1 for i, kid in enumerate(ids) if kid in relevant]
        output = parse_output(row.get("response") or {}) if row["status"] == "OK" else None
        allowed = [*RUNTIME_TOOLS[case["runtime"]], "NO_TOOL", "ASK_CLARIFICATION"]
        scored.append({"case_id": case["case_id"], "status": row["status"],
            "matched_ids": ids, "relevant_ids": oracle["relevant_ids"],
            "recall_at_3": len(relevant.intersection(ids)) / len(relevant) if relevant else None,
            "reciprocal_rank_at_3": 1 / min(ranks) if ranks else 0,
            "no_answer_false_positive": bool(ids) if not relevant else None,
            "structure_valid": output is not None,
            "tool_choice_allowlisted": bool(output and output["next_tool"] in allowed),
            "tool_choice_expected": bool(output and output["next_tool"] in oracle["acceptable_tools"]),
            "category_correct": bool(output and output["category"] in oracle["acceptable_categories"]),
            "disposition_correct": bool(output and output["disposition"] in oracle["acceptable_dispositions"]),
            "output": output, "usage": token_usage(row.get("response") or {}),
            "expected": {key: oracle[key] for key in ("acceptable_categories", "acceptable_dispositions", "acceptable_tools")}})
    positive = [r for r in scored if r["relevant_ids"]]
    negative = [r for r in scored if not r["relevant_ids"]]
    usage = [r["usage"] for r in scored if r["usage"] is not None]
    count = len(scored)
    metrics = {"case_count": count, "retrieval_positive_cases": len(positive),
        "retrieval_no_answer_cases": len(negative),
        "recall_at_3": sum(r["recall_at_3"] for r in positive) / len(positive) if positive else None,
        "mrr_at_3": sum(r["reciprocal_rank_at_3"] for r in positive) / len(positive) if positive else None,
        "no_answer_false_positive_rate": sum(r["no_answer_false_positive"] for r in negative) / len(negative) if negative else None,
        "cost_usd": None, "cost_reason": "NO_PINNED_PROVIDER_PRICE_EVIDENCE", "usage_case_count": len(usage),
        "tokens_from_available_usage": {k: sum(u[k] for u in usage) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
        "all_cases_token_usage_known": len(usage) == count,
        "timeouts": sum(r["status"] == "TIMEOUT" for r in scored),
        "api_errors": sum(r["status"] == "API_ERROR" for r in scored),
        "other_request_errors": sum(r["status"] not in {"OK", "TIMEOUT", "API_ERROR"} for r in scored)}
    for key in ("structure_valid", "tool_choice_allowlisted", "tool_choice_expected", "category_correct", "disposition_correct"):
        metrics[key + "_count"] = sum(r[key] for r in scored)
        metrics[key + "_rate"] = sum(r[key] for r in scored) / count
    return metrics, scored


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def load_remote_helper(path):
    spec = importlib.util.spec_from_file_location("heldout_readonly_existing_provider", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if "StrictHostKeyChecking=yes" not in module.SSH:
        raise ValueError("SSH helper must preserve host-key checking")
    return module


METADATA_CODE = '''
import hashlib,json,os
from pathlib import Path
from server.app.ai_provider import get_ai_settings
import server.app.ai_provider as provider_module
from server.app.drop_insight.adaptive_planner import SYSTEM_PROMPT,EVIDENCE_PLANNING_REQUIREMENT
s=get_ai_settings()
if not s.api_key or not s.rca_enabled or not s.base_url.startswith('https://'):
 raise RuntimeError('Configured HTTPS planning provider required')
release=Path('/workspace-source/manifest.json')
provenance=json.loads(release.read_text()) if release.is_file() else {}
names=('server/app/ai_provider.py','server/app/drop_insight/adaptive_planner.py',
 'server/app/drop_insight/cpu_criteria.py','server/app/drop_insight/performance_criteria.py',
 'server/app/agent_runtime/retrieval.py','server/app/agent_runtime/semantic_retrieval.py','server/app/drop_insight/tools.py')
root=Path(provider_module.__file__).resolve().parents[2]
source_hashes={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names}
thinking=os.getenv('MINI_DROP_SILICONFLOW_ENABLE_THINKING','').lower()
options={'enable_thinking':thinking=='true'} if s.provider.lower()=='siliconflow' and thinking in {'true','false'} else {}
print(json.dumps({'provider':s.provider,'model':s.model,'key_present':True,
 'https_verified_by_default_requests_client':True,
 'provider_source_sha256':hashlib.sha256(Path(provider_module.__file__).read_bytes()).hexdigest(),
 'production_prompt_sha256':hashlib.sha256((SYSTEM_PROMPT+'\\n'+EVIDENCE_PLANNING_REQUIREMENT).encode()).hexdigest(),
 'published_source_head':provenance.get('git_head'),'runtime_source_sha256':source_hashes,
 'transport_options':options}))
'''


def remote_metadata(helper):
    outer = "import subprocess\ncode=" + repr(METADATA_CODE) + "\n"
    outer += "result=subprocess.run(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-B','-'],input=code,text=True,capture_output=True,timeout=30)\n"
    outer += "assert result.returncode==0, 'Provider metadata unavailable'\nprint(result.stdout,end='')\n"
    return json.loads(helper.remote(outer))


REMOTE_RUN_CODE = '''
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib,json,sys,time
from server.app.ai_provider import chat_completions,get_ai_settings
items=json.load(sys.stdin)
s=get_ai_settings()
assert len(items)==24 and len({r['case_id'] for r in items})==24
assert s.api_key and s.rca_enabled and s.base_url.startswith('https://')
def run(row):
 started=time.monotonic()
 payload=row['payload']
 assert payload['model']==s.model and payload['max_tokens']==1200 and payload['temperature']==0.1
 result={'type':'result','case_id':row['case_id'],'status':'REQUEST_ERROR','response':{},'http_status':None}
 try:
  response=chat_completions(payload,timeout=40)
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


def remote_results(helper, items):
    # stdin contains public requests only. Remote code/requests remain in memory;
    # no container env, index, application file or database writes occur.
    outer = "import subprocess,sys\nprogram=" + repr(REMOTE_RUN_CODE) + "\n"
    outer += "requests=" + repr(json.dumps(items, ensure_ascii=False)) + "\n"
    outer += "p=subprocess.Popen(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-B','-c',program],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)\n"
    outer += "p.stdin.write(requests);p.stdin.close()\nfor line in p.stdout:\n print(line,end='',flush=True)\n"
    outer += "code=p.wait(timeout=660)\nassert code==0, 'Isolated provider process failed'\n"
    process = subprocess.Popen(helper.SSH + ["python3 -"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        process.stdin.write(outer)
        process.stdin.close()
        for line in process.stdout:
            if len(line.encode()) > 512 * 1024:
                raise RuntimeError("bounded remote response protocol exceeded")
            result = json.loads(line)
            if result.get("type") != "result":
                raise RuntimeError("unexpected remote response protocol")
            yield result
        if process.wait(timeout=60) != 0:
            raise RuntimeError("Isolated provider process failed; stderr withheld")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20)


def local_results(items):
    settings = get_ai_settings()
    if not settings.api_key or not settings.rca_enabled or not settings.base_url.startswith("https://"):
        raise ValueError("Configured HTTPS planning provider required")
    def run(row):
        started = time.monotonic()
        result = {"case_id": row["case_id"], "status": "REQUEST_ERROR", "response": {}, "http_status": None}
        try:
            response = chat_completions(row["payload"], timeout=40)
            result["http_status"] = response.status_code
            if len(response.content) > 131072:
                result["status"] = "OVERSIZE_RESPONSE"
            else:
                text = response.content.decode("utf-8", "replace").replace(settings.api_key, "<redacted-api-key>")
                result.update(response_text=text, response_text_sha256=sha(text.encode()))
                try:
                    result["response"] = json.loads(text)
                except ValueError:
                    pass
                result["status"] = "OK" if response.status_code == 200 else "API_ERROR"
        except Exception as exc:
            result.update(status="TIMEOUT" if "timeout" in type(exc).__name__.lower() else "REQUEST_ERROR",
                          error_type=type(exc).__name__[:80])
        result["seconds"] = round(time.monotonic() - started, 6)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(run, item) for item in items]):
            yield future.result()


def evaluate(output, provider, helper_path=None):
    manifest, questions, manifest_sha = validate_freeze()
    output = output.resolve()
    if not output.is_relative_to(ROOT.resolve()) or output.exists():
        raise ValueError("fresh output directory inside the workspace required")
    output.mkdir(parents=True)
    (output / "requests").mkdir()
    (output / "records").mkdir()
    write_json(output / "freeze.json", {"manifest_sha256": manifest_sha, "manifest": manifest})
    if provider == "ssh-current":
        helper = load_remote_helper(helper_path)
        metadata = remote_metadata(helper)
        metadata["ssh_helper_sha256"] = sha(helper_path.read_bytes())
        for name, digest in metadata["runtime_source_sha256"].items():
            expected = sha(subprocess.check_output(["git", "show", manifest["application_head"] + ":" + name], cwd=ROOT))
            if digest != expected:
                raise ValueError("runtime source differs from frozen application: " + name)
        metadata["runtime_source_equivalent_application_head"] = manifest["application_head"]
    else:
        settings = get_ai_settings()
        if not settings.api_key:
            raise ValueError("provider not configured; no mock fallback")
        metadata = {"provider": settings.provider, "model": settings.model, "key_present": True,
            "production_prompt_sha256": sha((SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT).encode())}
        import os
        flag = os.getenv("MINI_DROP_SILICONFLOW_ENABLE_THINKING", "").lower()
        metadata["transport_options"] = ({"enable_thinking": flag == "true"}
            if settings.provider.lower() == "siliconflow" and flag in {"true", "false"} else {})
    expected_prompt_sha = sha((SYSTEM_PROMPT + "\n" + EVIDENCE_PLANNING_REQUIREMENT).encode())
    if metadata["production_prompt_sha256"] != expected_prompt_sha:
        raise ValueError("published planner prompt differs from frozen source")
    write_json(output / "provider.json", metadata)
    requests, retrieval_by_id = [], {}
    for case in questions["cases"]:
        started = time.monotonic()
        matches = retrieve_knowledge(case["query"], top_k=3, knowledge_root=ROOT / "knowledge")
        payload, allowed = build_request(case, matches, metadata["model"], metadata["transport_options"])
        retrieval = {"requested_backend": "BM25", "actual_backend": "BM25", "degraded_reasons": [],
            "matched_ids": list(dict.fromkeys(m["knowledge_id"] for m in matches))[:3],
            "matches": matches, "seconds": round(time.monotonic() - started, 6)}
        retrieval_by_id[case["case_id"]] = retrieval
        item = {"case_id": case["case_id"], "payload": payload}
        requests.append(item)
        write_json(output / "requests" / (case["case_id"] + ".json"),
            {**item, "allowed_tools": allowed, "payload_sha256": sha(canonical(payload))})
    if len(requests) != manifest["max_chat_calls"]:
        raise ValueError("request budget differs from frozen case count")
    records = []
    generator = remote_results(helper, requests) if provider == "ssh-current" else local_results(requests)
    for result in generator:
        case_id = result["case_id"]
        if case_id not in retrieval_by_id or any(r["case_id"] == case_id for r in records):
            raise ValueError("unknown or duplicate provider response ID")
        result["retrieval"] = retrieval_by_id[case_id]
        write_json(output / "records" / (case_id + ".json"), result)
        records.append(result)
        print(json.dumps({"completed": len(records), "case_id": case_id, "status": result["status"]}), flush=True)
    # Oracle is first parsed after all outbound requests have finished and the
    # original results are on disk. It never influences a query, prompt or retry.
    validate_freeze()
    private = json.loads((ROOT / (PREFIX + "private.json")).read_text(encoding="utf-8"))
    metrics, scored = score_records(questions, private, records)
    evidence = {p.relative_to(output).as_posix(): sha(p.read_bytes())
                for folder in ("requests", "records") for p in sorted((output / folder).glob("*.json"))}
    evidence["provider.json"] = sha((output / "provider.json").read_bytes())
    evidence["freeze.json"] = sha((output / "freeze.json").read_bytes())
    report = {"schema": "mini-drop.heldout-evaluation.v1", "suite_id": manifest["suite_id"],
        "scope": manifest["scope"], "evaluation_adapter": manifest["evaluation_adapter"],
        "third_party_independent_author": False, "truth_frozen_before_implementation": True,
        "question_or_oracle_tuned_after_first_run": False, "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": manifest_sha, "evaluator_source_sha256": sha(Path(__file__).read_bytes()),
        "production_prompt_sha256": expected_prompt_sha, "adapter_sha256": sha(ADAPTER.encode()),
        "provider": metadata, "actual_retrieval_backend": "BM25", "retrieval_is_evidence": False,
        "chat_calls_attempted": len(requests), "max_parallel_chat_calls": 2, "retries": 0,
        "actual_tasks_dispatched": 0, "actual_faults_injected": 0, "cost_usd": None,
        "metrics": metrics, "cases": scored, "evidence_sha256": evidence}
    write_json(output / "report.json", report)
    print(json.dumps(metrics, ensure_ascii=False), flush=True)
    return report


def verify_report(path):
    manifest, questions, manifest_sha = validate_freeze()
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("manifest_sha256") != manifest_sha:
        raise ValueError("report references different frozen suite")
    directory = path.parent
    pins = report["evidence_sha256"]
    expected_names = {folder + "/" + c["case_id"] + ".json"
                      for c in questions["cases"] for folder in ("requests", "records")}
    expected_names.update(("provider.json", "freeze.json"))
    if set(pins) != expected_names:
        raise ValueError("expected 24 requests, 24 records and two provenance receipts")
    for name, digest in pins.items():
        p = (directory / name).resolve()
        if not p.is_relative_to(directory.resolve()) or sha(p.read_bytes()) != digest:
            raise ValueError("report evidence missing/tampered")
    provider = json.loads((directory / "provider.json").read_text(encoding="utf-8"))
    freeze = json.loads((directory / "freeze.json").read_text(encoding="utf-8"))
    if report["provider"] != provider or freeze != {"manifest_sha256": manifest_sha, "manifest": manifest}:
        raise ValueError("report provenance differs from pinned receipts")
    if report["evaluator_source_sha256"] != sha(Path(__file__).read_bytes()):
        raise ValueError("evaluator source differs from first run")
    records = []
    for case in questions["cases"]:
        cid = case["case_id"]
        request = json.loads((directory / "requests" / (cid + ".json")).read_text(encoding="utf-8"))
        record = json.loads((directory / "records" / (cid + ".json")).read_text(encoding="utf-8"))
        if request["case_id"] != cid or record["case_id"] != cid:
            raise ValueError("evidence case identity differs")
        matches = retrieve_knowledge(case["query"], top_k=3, knowledge_root=ROOT / "knowledge")
        payload, allowed = build_request(case, matches, report["provider"]["model"], report["provider"].get("transport_options"))
        if request["payload"] != payload or request["payload_sha256"] != sha(canonical(payload)):
            raise ValueError("outbound payload differs from frozen public projection")
        if request["allowed_tools"] != allowed or record["retrieval"]["matches"] != matches:
            raise ValueError("retrieval or allow-list differs from real frozen backend")
        retrieval = record["retrieval"]
        ids = list(dict.fromkeys(m["knowledge_id"] for m in matches))[:3]
        if (retrieval["matched_ids"] != ids or retrieval["actual_backend"] != "BM25"
            or retrieval["requested_backend"] != "BM25" or retrieval["degraded_reasons"] != []):
            raise ValueError("retrieval ranking/backend differs from frozen projection")
        if record["status"] == "OK" and record["http_status"] != 200:
            raise ValueError("successful model record requires actual HTTP 200 receipt")
        if "response_text" in record:
            if sha(record["response_text"].encode()) != record["response_text_sha256"]:
                raise ValueError("response text digest differs")
            try:
                parsed = json.loads(record["response_text"])
            except ValueError:
                parsed = {}
            if parsed != record["response"]:
                raise ValueError("parsed response differs from original response text")
        records.append(record)
    private = json.loads((ROOT / (PREFIX + "private.json")).read_text(encoding="utf-8"))
    metrics, scored = score_records(questions, private, records)
    if report["metrics"] != metrics or report["cases"] != scored:
        raise ValueError("published score differs from independent raw recomputation")
    if (report["chat_calls_attempted"] != 24 or report["retries"] != 0
        or report["actual_tasks_dispatched"] != 0 or report["actual_faults_injected"] != 0):
        raise ValueError("report scope/budget differs")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--provider", choices=("local", "ssh-current"), default="local")
    parser.add_argument("--remote-provider-helper", type=Path,
        default=ROOT / "output/acceptance/deployment-20260930/run_strict.py")
    parser.add_argument("--verify-report", type=Path)
    args = parser.parse_args()
    if args.verify_report:
        print(json.dumps(verify_report(args.verify_report), ensure_ascii=False))
    elif args.output:
        evaluate(args.output, args.provider, args.remote_provider_helper)
    else:
        parser.error("--output or --verify-report required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
