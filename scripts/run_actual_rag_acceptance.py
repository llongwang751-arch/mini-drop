"""Same-corpus HTTP comparison of frozen, real AGI-saber source revisions."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.agi_saber.service import QUESTIONS, corpus
from scripts.run_business_acceptance import write_json
from server.app.drop_insight.business_acceptance import (
    Workload, RequestOutcome, MeasurementWindow, AcceptancePolicy, compare_business_windows, canonical_hash,
)


def get(url):
    with urlopen(url, timeout=3) as response:
        return json.load(response)


def measure(rag_root, output, name, workload, rows):
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    log = (output / f"{name}.log").open("wb")
    command = [sys.executable, str(ROOT/"integrations/agi_saber/service.py"),
               "--rag-root", str(rag_root), "--database", str(output/f"{name}.db"),
               "--port", str(port), "--rows", str(rows)]
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        deadline = time.monotonic()+90
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"RAG process stopped; inspect {name}.log")
            try:
                health = get(endpoint+"/health")
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("RAG startup exceeded 90 seconds")
                time.sleep(.3)
        details = []
        def query(i, due=None):
            question, expected = QUESTIONS[i % len(QUESTIONS)]
            trace = uuid4().hex
            start = time.perf_counter()
            lag = max(0, (start-due)*1000) if due is not None else 0
            info = {"request_id": f"q-{i}", "client_trace_id": trace}
            try:
                request = Request(endpoint+"/query", method="POST",
                    data=json.dumps({"question": question}).encode(),
                    headers={"Content-Type": "application/json", "traceparent": f"00-{trace}-{uuid4().hex[:16]}-01"})
                with urlopen(request, timeout=30) as response:
                    result = json.load(response)
                obs = result["observation"]
                valid = obs["trace_id"] == trace and obs["pid"] == health["pid"] and obs["instance_id"] == health["instance_id"]
                info.update(observation=obs, citations=result["citations"], retrieval_mode=result["retrieval_mode"])
                outcome = RequestOutcome(request_id=f"q-{i}", latency_ms=(time.perf_counter()-start)*1000+lag,
                    success=valid, quality_passed=valid and expected in result["answer"] and bool(result["citations"]),
                    trace_id=trace, stage_ms=obs["stage_ms"], degraded=False)
            except Exception as exc:
                info["error_type"] = type(exc).__name__
                outcome = RequestOutcome(request_id=f"q-{i}", latency_ms=(time.perf_counter()-start)*1000+lag,
                    success=False, quality_passed=False, trace_id=trace)
            return outcome, lag, info
        for i in range(workload.warmup_requests):
            query(i)
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workload.concurrency_limit) as pool:
            futures = []
            for i in range(workload.request_count):
                due = started+i/workload.arrival_rate
                time.sleep(max(0, due-time.perf_counter()))
                futures.append(pool.submit(query, i, due))
            pairs = [f.result() for f in futures]
        elapsed = time.perf_counter()-started
        window = MeasurementWindow(window_id=uuid4().hex, workload=workload,
            revision=health["source_sha256"], config_sha256=canonical_hash({"rows": rows, "mode": "local", "top_k": 3}),
            generator="EXTRACTIVE_LOCAL", elapsed_seconds=elapsed,
            max_dispatch_lag_ms=max(x[1] for x in pairs), requests=[x[0] for x in pairs])
        write_json(output/f"{name}-observations.json", {"health": health, "requests": [x[2] for x in pairs],
                                                     "observations": get(endpoint+"/observations")})
        return window
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def run(before, after, output, rows=2000, arrival_rate=2):
    output.mkdir(parents=True, exist_ok=False)
    resources = {"system": platform.system(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
                 "python": platform.python_version()}
    # Include enforced Linux limits when readable, rather than confusing host
    # CPU count with the capacity assigned to this acceptance process.
    for key,path in {"cpu_max":"/sys/fs/cgroup/cpu.max", "memory_max":"/sys/fs/cgroup/memory.max",
                     "cpu_quota_us":"/sys/fs/cgroup/cpu/cpu.cfs_quota_us",
                     "cpu_period_us":"/sys/fs/cgroup/cpu/cpu.cfs_period_us"}.items():
        try:resources[key]=Path(path).read_text().strip()
        except OSError:pass
    workload = Workload(dataset_sha256=canonical_hash(list(corpus(rows))), request_set_sha256=canonical_hash(QUESTIONS),
        arrival_rate=arrival_rate, request_count=30, concurrency_limit=4, seed=912, warmup_requests=4,
        environment="isolated-"+platform.system().lower(), service="agi-saber-rag", resources_sha256=canonical_hash(resources))
    policy = AcceptancePolicy()
    report = {"schema": "mini-drop.actual-rag-acceptance.v1", "status": "RUNNING",
              "started_at": datetime.now(timezone.utc).isoformat(), "resources": resources,
              "scope": "ACTUAL_RAG_ENGINE; SYNTHETIC_CORPUS; LOCAL_RETRIEVAL; EXTRACTIVE_ANSWER",
              "dataset": {"rows":rows,"embedding_dimensions":1536,"query_count":len(QUESTIONS)},
              "business_description": "知识库分块增加后，本地查询读取完整向量并反复计算词项，导致回答等待增加。",
              "change": "只读取文本列、按批流式处理、查询词项计算一次、用有界 TopK 保留原排序。仍是线性扫描，不宣称已使用 FTS5。",
              "policy": policy.model_dump(), "windows": {}}
    write_json(output/"report.json", report)
    for name, source in [("baseline", after), ("fault", before), ("after", after)]:
        print(f"measuring {name}", flush=True)
        window = measure(source, output, name, workload, rows)
        report["windows"][name] = window.model_dump()
        write_json(output/"report.json", report)
    report["comparison"] = compare_business_windows(
        *(MeasurementWindow.model_validate(report["windows"][n]) for n in ("baseline", "fault", "after")), policy, report["change"])
    report["status"] = "COMPLETED"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["ai_root_cause_verified"] = False
    report["sha256"] = canonical_hash(report)
    write_json(output/"report.json", report)
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rows", type=int, default=2000)
    parser.add_argument("--arrival-rate", type=float, default=2)
    args = parser.parse_args()
    result = run(args.before.resolve(), args.after.resolve(), args.output.resolve(), args.rows, args.arrival_rate)
    raise SystemExit(0 if result["comparison"]["outcome"] == "IMPROVEMENT_VERIFIED" else 1)
