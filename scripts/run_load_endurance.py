"""Bounded open-arrival HTTP load against a disposable local fixture process.

All offered requests, including client saturation, are preserved. No arbitrary
target URL, production traffic, retries, or implicit dependency installation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.rag_service.app import DATASET_SHA, QUESTIONS, KnowledgeService, Settings, serve
from scripts.process_resource_monitor import ResourceMonitor, summarize_resources


@dataclass(frozen=True)
class Plan:
    rates: tuple[int, ...] = (5, 20, 40, 80)
    step_seconds: int = 15
    soak_rate: int = 5
    soak_seconds: int = 600
    recovery_seconds: int = 15
    bucket_seconds: int = 30
    concurrency: int = 64
    timeout_seconds: float = 5
    p95_limit_ms: float = 200
    minimum_success_rate: float = .99
    minimum_quality_rate: float = .99
    max_dispatch_lag_ms: float = 250
    minimum_samples: int = 30
    dependency_latency_ms: int = 10

    def validate(self):
        integers = (self.step_seconds, self.soak_rate, self.soak_seconds,
                    self.recovery_seconds, self.bucket_seconds, self.concurrency,
                    self.minimum_samples, *self.rates)
        if any(type(x) is not int or x < 1 for x in integers):
            raise ValueError("durations, rates, concurrency and sample count must be positive integers")
        if not self.rates or tuple(sorted(set(self.rates))) != self.rates:
            raise ValueError("rates must be nonempty and strictly increasing")
        if self.concurrency > 128 or max((*self.rates, self.soak_rate)) > 500:
            raise ValueError("local safety limit: concurrency <=128 and rate <=500")
        if self.soak_seconds > 3600 or self.step_seconds > 60 or self.recovery_seconds > 60:
            raise ValueError("local duration limit exceeded")
        if type(self.dependency_latency_ms) is not int or not 0 <= self.dependency_latency_ms <= 100:
            raise ValueError("fixture dependency delay must be 0..100 ms")
        for value in (self.timeout_seconds, self.p95_limit_ms, self.max_dispatch_lag_ms):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("latency and timeout limits must be finite and positive")
        if self.timeout_seconds > 10:
            raise ValueError("client timeout must be <=10 seconds")
        if not all(math.isfinite(v) and 0 < v <= 1 for v in
                   (self.minimum_success_rate, self.minimum_quality_rate)):
            raise ValueError("quality/success floors must be in (0,1]")
        count = sum(self.rates) * self.step_seconds + self.soak_rate * (
            self.soak_seconds + self.recovery_seconds)
        if count > 100000:
            raise ValueError("at most 100000 offered requests per campaign")


def write_json(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def percentile(values, quantile):
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)]


def summarize(rows, plan, duration):
    """Latency includes planned-arrival delay and failed HTTP attempts.

    Unsent requests have no invented latency. They invalidate capacity inference
    and remain in both success and quality denominators.
    """
    sent = [r for r in rows if r["sent"]]
    latencies = [r["latency_ms"] for r in sent]
    count = len(rows)
    success = sum(r["success"] for r in rows) / count if count else 0
    quality = sum(r["quality_passed"] for r in rows) / count if count else 0
    reasons = []
    if count < plan.minimum_samples:
        reasons.append("INSUFFICIENT_SAMPLES")
    if len(sent) != count:
        reasons.append("CLIENT_INFLIGHT_LIMIT")
    lag = max((r["dispatch_lag_ms"] for r in rows), default=0)
    if lag > plan.max_dispatch_lag_ms:
        reasons.append("LOAD_GENERATOR_LAG")
    invalid = bool(reasons)
    p95 = percentile(latencies, .95)
    if success < plan.minimum_success_rate:
        reasons.append("SUCCESS_RATE")
    if quality < plan.minimum_quality_rate:
        reasons.append("QUALITY_RATE")
    if p95 is None or p95 > plan.p95_limit_ms:
        reasons.append("P95_LIMIT")
    return {"status": "INVALID" if invalid else "SLO_FAILED" if reasons else "PASSED",
            "reasons": reasons, "offered": count, "sent": len(sent), "unsent": count - len(sent),
            "success_rate": success, "quality_rate": quality,
            "p50_ms": percentile(latencies, .5), "p95_ms": p95,
            "p99_ms": percentile(latencies, .99) if len(sent) >= 100 else None,
            "latency_sample_count": len(sent), "max_dispatch_lag_ms": lag,
            "successful_rps_including_drain": sum(r["success"] for r in rows) / duration if duration else 0}


def capacity_summary(stages):
    prefix = []
    boundary = None
    for stage in stages:
        if stage["summary"]["status"] != "PASSED":
            boundary = stage
            break
        prefix.append(stage["rate"])
    return {"highest_contiguous_passing_rps": max(prefix) if prefix else None,
            "first_nonpassing_rps": boundary["rate"] if boundary else None,
            "boundary": ("NOT_REACHED" if boundary is None else
                         "GENERATOR_OR_SAMPLE_LIMIT" if boundary["summary"]["status"] == "INVALID"
                         else "SLO_FAILED_AT_TESTED_RATE"),
            "scope": "LOCAL_FIXTURE_TESTED_RATES_ONLY; NOT_PRODUCTION_CAPACITY"}


def campaign_status(stages):
    capacity, recovery, soak = stages[:-2], stages[-2], stages[-1]
    if any(s["summary"]["status"] == "INVALID" for s in stages):
        return "INVALID"
    if any(b["summary"]["status"] == "INVALID" for b in soak["buckets"]):
        return "INVALID"
    if (capacity_summary(capacity)["highest_contiguous_passing_rps"] is None
            or recovery["summary"]["status"] != "PASSED"
            or soak["summary"]["status"] != "PASSED"
            or any(b["summary"]["status"] != "PASSED" for b in soak["buckets"])):
        return "FAILED"
    return "PASSED"


def request(endpoint, index, due, origin, timeout):
    started = time.perf_counter()
    row = {"index": index, "scheduled_offset_seconds": due - origin,
           "sent": True, "dispatch_lag_ms": max(0, (started - due) * 1000),
           "success": False, "quality_passed": False, "http_status": None, "error": None}
    question, expected = QUESTIONS[index % len(QUESTIONS)]
    try:
        req = Request(endpoint, data=json.dumps({"question": question}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=timeout) as response:
            row["http_status"] = response.status
            body = json.load(response)
        row["success"] = body.get("success") is True
        row["quality_passed"] = row["success"] and expected in body.get("citations", [])
        row["trace_id"] = body.get("trace_id")
        row["stage_ms"] = body.get("stage_ms", {})
        row["error"] = body.get("error")
    except HTTPError as exc:
        row["http_status"] = exc.code
        row["error"] = "HTTP_ERROR"
    except Exception as exc:
        row["error"] = type(exc).__name__
    row["latency_ms"] = (time.perf_counter() - due) * 1000
    return row


def measure(endpoint, name, rate, seconds, plan, output):
    origin = time.perf_counter()
    rows, pending = [], set()
    next_progress = origin + 30
    with (output / f"{name}.jsonl").open("x", encoding="utf-8") as raw:
        def record(row):
            # Scheduling is index/rate; subtracting two large clock values can
            # move exact bucket-boundary arrivals into the preceding cohort.
            row["scheduled_offset_seconds"] = row["index"] / rate
            rows.append(row)
            raw.write(json.dumps(row, allow_nan=False) + "\n")
            raw.flush()

        with ThreadPoolExecutor(max_workers=plan.concurrency) as pool:
            for index in range(rate * seconds):
                due = origin + index / rate
                time.sleep(max(0, due - time.perf_counter()))
                done = {f for f in pending if f.done()}
                for future in done:
                    record(future.result())
                pending -= done
                if len(pending) >= plan.concurrency:
                    record({"index": index, "scheduled_offset_seconds": index / rate,
                            "sent": False, "dispatch_lag_ms": max(0, (time.perf_counter() - due) * 1000),
                            "latency_ms": None, "success": False, "quality_passed": False,
                            "http_status": None, "error": "CLIENT_INFLIGHT_LIMIT"})
                else:
                    pending.add(pool.submit(request, endpoint, index, due, origin, plan.timeout_seconds))
                if time.perf_counter() >= next_progress:
                    print(json.dumps({"phase": name, "offered": index + 1,
                                      "recorded": len(rows), "inflight": len(pending)}), flush=True)
                    next_progress = time.perf_counter() + 30
            for future in pending:
                record(future.result())
        time.sleep(max(0, origin + seconds - time.perf_counter()))
    elapsed = time.perf_counter() - origin
    buckets = []
    for offset in range(0, seconds, plan.bucket_seconds):
        duration = min(plan.bucket_seconds, seconds - offset)
        selected = [r for r in rows if offset <= r["scheduled_offset_seconds"] < offset + duration]
        # Buckets are arrival cohorts; do not label their successful count / time
        # as achieved throughput, because completions may belong to later buckets.
        summary = summarize(selected, plan, duration)
        summary.pop("successful_rps_including_drain")
        buckets.append({"offset_seconds": offset, "duration_seconds": duration, "summary": summary})
    return {"name": name, "rate": rate, "duration_seconds": seconds, "elapsed_with_drain_seconds": elapsed,
            "raw_file": f"{name}.jsonl", "summary": summarize(rows, plan, elapsed), "buckets": buckets}


@contextmanager
def fixture(output, plan):
    with (output / "fixture.stderr.log").open("x", encoding="utf-8") as errors:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve-fixture",
                                  "--dependency-latency-ms", str(plan.dependency_latency_ms)],
                                 cwd=ROOT, stdout=subprocess.PIPE, stderr=errors, text=True)
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
        reader.start()
        try:
            details = json.loads(ready.get(timeout=20))
            if child.poll() is not None or not 1 <= details["port"] <= 65535:
                raise RuntimeError("fixture failed to start")
            yield f"http://127.0.0.1:{details['port']}/query", child
        finally:
            child.terminate() if child.poll() is None else None
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            reader.join(timeout=2)
            child.stdout.close()


def source_hashes():
    paths = ("scripts/run_load_endurance.py", "scripts/process_resource_monitor.py", "demo/rag_service/app.py")
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def run(output, plan):
    plan.validate()
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "mini-drop.load-endurance.v1", "status": "RUNNING",
              "started_at": datetime.now(timezone.utc).isoformat(), "plan": asdict(plan),
              "source_hashes": source_hashes(), "dataset_sha256": DATASET_SHA,
              "question_set_sha256": hashlib.sha256(json.dumps(QUESTIONS).encode()).hexdigest(),
              "environment": {"platform": platform.platform(), "python": platform.python_version(),
                              "cpu_count": os.cpu_count()},
              "scope": "ISOLATED_LOCAL_HTTP_FIXTURE; CONTROLLED_DEPENDENCY_DELAY; NO_LIVE_LLM",
              "limitations": ["No production capacity claim", "Resource growth screen does not prove absence of leaks",
                              "Shared host with generator; invalid generator stages cannot prove server capacity"],
              "stages": []}
    report["git_head"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    path = output / "report.json"
    write_json(path, report)
    try:
        with fixture(output, plan) as (endpoint, process):
            report["fixture_pid"] = process.pid
            warmup = []
            for i in range(4):
                now = time.perf_counter()
                warmup.append(request(endpoint, i, now, now, plan.timeout_seconds))
            report["warmup"] = warmup
            if not all(r["success"] and r["quality_passed"] for r in warmup):
                raise RuntimeError("fixture warmup failed")
            phases = [(f"step-{i + 1}", rate, plan.step_seconds) for i, rate in enumerate(plan.rates)]
            phases += [("recovery", plan.soak_rate, plan.recovery_seconds),
                       ("soak", plan.soak_rate, plan.soak_seconds)]
            with ResourceMonitor(process.pid, output / "resources.jsonl") as monitor:
                for name, rate, duration in phases:
                    monitor.phase = name
                    stage = measure(endpoint, name, rate, duration, plan, output)
                    report["stages"].append(stage)
                    write_json(path, report)
                    print(json.dumps({"phase": name, "summary": stage["summary"]}), flush=True)
                    if process.poll() is not None:
                        raise RuntimeError("fixture exited during measurement")
            report["resources"] = summarize_resources(monitor.rows, plan.soak_seconds)
            capacity = report["stages"][:-2]
            recovery, soak = report["stages"][-2:]
            report["capacity"] = capacity_summary(capacity)
            report["recovery_status"] = recovery["summary"]["status"]
            report["soak_status"] = ("PASSED" if soak["summary"]["status"] == "PASSED" and
                                      all(b["summary"]["status"] == "PASSED" for b in soak["buckets"])
                                      else "FAILED")
            report["status"] = campaign_status(report["stages"])
            if report["resources"]["status"] != "PASSED":
                report["status"] = "INVALID" if report["resources"]["status"] == "INVALID" else "FAILED"
        if source_hashes() != report["source_hashes"]:
            raise RuntimeError("measurement source changed during run")
    except BaseException as exc:
        report["status"] = "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["artifact_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in output.glob("*.jsonl")}
        report["sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
        write_json(path, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quick", action="store_true", help="Short pipeline regression, not endurance evidence")
    parser.add_argument("--soak-seconds", type=int, help="Continuous duration; 600 by default, at most 3600")
    parser.add_argument("--rates", type=int, nargs="+", help="Strictly increasing offered requests per second")
    parser.add_argument("--step-seconds", type=int, help="Duration of each staircase step; default 15")
    parser.add_argument("--serve-fixture", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dependency-latency-ms", type=int, default=10, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_fixture:
        service = KnowledgeService(Settings(dependency_latency_ms=args.dependency_latency_ms))
        http = serve(service, port=0)
        print(json.dumps({"port": http.server_port}), flush=True)
        http.serve_forever()
    else:
        if args.output is None:
            parser.error("--output is required")
        if args.quick and any(v is not None for v in (args.soak_seconds, args.rates, args.step_seconds)):
            parser.error("--quick cannot be mixed with duration/rate overrides")
        plan = Plan(rates=(5, 10), step_seconds=6, soak_seconds=12,
                    recovery_seconds=6, bucket_seconds=6, p95_limit_ms=500) if args.quick else Plan()
        overrides = {name: getattr(args, name) for name in ("soak_seconds", "step_seconds")
                     if getattr(args, name) is not None}
        if args.rates is not None:
            overrides["rates"] = tuple(args.rates)
        plan = replace(plan, **overrides)
        result = run(args.output, plan)
        raise SystemExit(0 if result["status"] == "PASSED" else 1)
