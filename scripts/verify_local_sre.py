"""Exercise a bounded Python CPU diagnosis on the loopback-only local stack.

This checks real integration, not root-cause accuracy or a code-fix benchmark.
The injected workload is stopped in finally and also has an auto-stop timer.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse

if __package__:
    from .verify_interview_demo import Client, items_of
else:
    from verify_interview_demo import Client, items_of


def collection_chain_checks(calls, artifacts, evidence):
    """A successful single probe must not hide another failed collection."""
    completed = bool(calls) and all(c.get('status') == 'COMPLETED' for c in calls)
    task_ids = {c.get('task_id') for c in calls}
    verified_tasks = {a.get('task_id') for a in artifacts
                      if a.get('integrity_status') == 'VERIFIED' and (a.get('size_bytes') or 0) > 0}
    return {'all_tools_completed': completed,
            'every_task_has_verified_artifact': bool(task_ids) and None not in task_ids and task_ids <= verified_tasks,
            'accepted_evidence_present': any(e.get('classification', {}).get('decision') in
                                            {'ACCEPT_SUPPORT', 'ACCEPT_COUNTER', 'ACCEPT_NEUTRAL', 'ACCEPT_LIMITED'} for e in evidence)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--deployment", choices=["local", "cloud"], default="local")
    parser.add_argument("--strategy", choices=["REACT", "LATS"], default="REACT")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a new report path; existing evidence is retained.")
    if args.deployment == "local" and urlparse(args.base_url).hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Local verification is restricted to loopback.")
    if args.deployment == "cloud" and (args.base_url != "https://120.24.187.205" or not os.getenv("MINI_DROP_API_KEY")):
        raise SystemExit("Cloud verification requires the configured HTTPS Control and API key environment.")
    client = Client(args.base_url, os.getenv("MINI_DROP_API_KEY", "") if args.deployment == "cloud" else "")
    result = {"kind": args.deployment.upper() + "_REAL_INTEGRATION", "strategy": args.strategy, "started_at": datetime.now(timezone.utc).isoformat(),
              "passed": False, "not_a_root_cause_accuracy_benchmark": True}
    scenario = "source-hotspot"
    started = False
    try:
        result["health"] = client.request("GET", "/api/healthz")
        result["agents"] = items_of(client.request("GET", "/api/agents?limit=100"))
        plaza = client.request("GET", "/api/v2/showcases/fault-plaza")
        selected = next(x for x in items_of(plaza.get("scenarios")) if x["scenario_id"] == scenario)
        if selected.get("active"):
            raise RuntimeError("SCENARIO_ALREADY_ACTIVE")
        started = True
        fault = client.request("POST", f"/api/v2/showcases/fault-plaza/{scenario}/start", {"duration_seconds": 300})
        request = fault["diagnosis_request"]
        payload = {"query": request["query"] + " 请先调用 search_knowledge 查阅相关性能知识，再根据真实采样判断。",
                   "auto_scope": True, "mode": "AUTONOMOUS", "target": {"agent_id": "control-interview-demo-agent" if args.deployment == "cloud" else "local-sre-native"},
                   "budget": {"max_duration_seconds": 300, "max_tool_calls": 6, "max_diagnosis_rounds": 3,
                              "min_diagnosis_rounds": 2, "max_hosts": 1, "investigation_strategy": args.strategy}}
        created = client.request("POST", "/api/v2/diagnoses", payload)
        diagnosis_id = created.get("diagnosis_id") or created["id"]
        result["diagnosis_id"] = diagnosis_id
        print(json.dumps({"diagnosis_id": diagnosis_id}), flush=True)
        previous = None
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            detail = client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}", timeout=20)
            status = detail.get("status")
            if status != previous:
                print(json.dumps({"status": status}), flush=True)
                previous = status
            if status in {"COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"}:
                break
            time.sleep(4)
        result["diagnosis"] = detail
        for field in ["tool-calls", "evidence", "reports", "retrievals", "events", "hypotheses"]:
            result[field] = items_of(client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/{field}"))
        result["artifacts"] = []
        for call in result["tool-calls"]:
            if call.get("task_id"):
                result["artifacts"].extend({**a, 'task_id': call['task_id']} for a in
                    items_of(client.request("GET", f"/api/tasks/{call['task_id']}/artifacts")))
        result["model_planning_observed"] = any(str(h.get("source", "")).startswith("MODEL") for h in result["hypotheses"])
        result["hybrid_retrieval_observed"] = any(
            (e.get("payload", {}).get("retrieval_trace") or {}).get("actual_backend") in {"BM25_CHROMA_RRF_RERANK", "BM25_ENTITY_CHROMA_RRF_RERANK"}
            for e in result["events"])
        result['collection_chain_checks'] = collection_chain_checks(result['tool-calls'], result['artifacts'], result['evidence'])
        result["integration_passed"] = (detail.get("status") in {"COMPLETED", "INSUFFICIENT_EVIDENCE"}
            and bool(result["reports"]) and all(result['collection_chain_checks'].values()))
        result["passed"] = result["integration_passed"] and result["model_planning_observed"] and result["hybrid_retrieval_observed"]
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc)[:1000])
    finally:
        if started:
            try:
                stopped = client.request("POST", f"/api/v2/showcases/fault-plaza/{scenario}/stop")
                result["fault_stopped"] = not (stopped.get("scenario") or {}).get("active", True)
            except Exception as exc:
                result["cleanup_error"] = type(exc).__name__
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result["passed"] = result["passed"] and result.get("fault_stopped", False)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "output": str(args.output), "error": result.get("error")}))
    return 0 if result["passed"] and result.get("fault_stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
