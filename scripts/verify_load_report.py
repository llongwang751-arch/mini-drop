"""Verify saved load evidence before displaying a successful acceptance.

Checks hashes, offered-slot accounting and recomputes summaries from raw rows.
Hashes detect corruption, not malicious fabrication of an entire campaign.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_load_endurance import Plan, capacity_summary, campaign_status, summarize
from scripts.process_resource_monitor import summarize_resources


def fail(message):
    raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def verify(path, require_pass=False):
    path = Path(path)
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != "mini-drop.load-endurance.v1" or report.get("status") not in {"PASSED", "INVALID", "FAILED"}:
        fail("unfinished or unsupported measurement")
    unsigned = {k:v for k,v in report.items() if k != "sha256"}
    if hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode()).hexdigest() != report.get("sha256"):
        fail("manifest digest mismatch")
    plan_data = dict(report["plan"])
    plan_data["rates"] = tuple(plan_data["rates"])
    plan = Plan(**plan_data)
    plan.validate()
    artifacts = {}
    for name, expected in report["artifact_sha256"].items():
        source = (path.parent / name).resolve()
        if Path(name).name != name or not source.is_relative_to(path.parent.resolve()) or not name.endswith(".jsonl"):
            fail("unsafe artifact path")
        if not source.is_file() or source.stat().st_size > 128 * 1024 * 1024:
            fail("missing or oversized artifact")
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            fail(f"artifact digest mismatch: {name}")
        artifacts[name] = [json.loads(line) for line in content.decode("utf-8").splitlines()]
    expected_phases = [(f"step-{i+1}", rate, plan.step_seconds) for i, rate in enumerate(plan.rates)]
    expected_phases += [("recovery", plan.soak_rate, plan.recovery_seconds), ("soak", plan.soak_rate, plan.soak_seconds)]
    if len(report["stages"]) != len(expected_phases):
        fail("incomplete phase sequence")
    for phase, (name, rate, duration) in zip(report["stages"], expected_phases):
        if (phase["name"], phase["rate"], phase["duration_seconds"]) != (name, rate, duration):
            fail("phase workload changed")
        if not finite(phase["elapsed_with_drain_seconds"]) or phase["elapsed_with_drain_seconds"] < duration:
            fail("phase elapsed time excludes scheduled duration")
        rows = artifacts.get(phase["raw_file"])
        count = rate * duration
        if rows is None or len(rows) != count or sorted(r["index"] for r in rows) != list(range(count)):
            fail("offered slots missing or duplicated")
        for row in rows:
            if any(type(row[key]) is not bool for key in ("sent", "success", "quality_passed")):
                fail("invalid boolean observation")
            offset = row["scheduled_offset_seconds"]
            if not finite(offset) or abs(offset - row["index"] / rate) > .001:
                fail("request schedule changed")
            if not finite(row["dispatch_lag_ms"]) or row["dispatch_lag_ms"] < 0:
                fail("invalid dispatch timing")
            if row["quality_passed"] and not row["success"]:
                fail("quality success without business success")
            if row["sent"]:
                if not finite(row["latency_ms"]) or row["latency_ms"] < row["dispatch_lag_ms"]:
                    fail("latency omits dispatch delay")
            elif row["latency_ms"] is not None or row["success"] or row["quality_passed"]:
                fail("unsent request has invented observation")
        if summarize(rows, plan, phase["elapsed_with_drain_seconds"]) != phase["summary"]:
            fail("summary does not match raw requests")
        buckets = []
        for offset in range(0, duration, plan.bucket_seconds):
            seconds = min(plan.bucket_seconds, duration - offset)
            selected = [r for r in rows if offset <= r["scheduled_offset_seconds"] < offset + seconds]
            summary = summarize(selected, plan, seconds)
            summary.pop("successful_rps_including_drain")
            buckets.append({"offset_seconds": offset, "duration_seconds": seconds, "summary": summary})
        if buckets != phase["buckets"]:
            fail("window summary does not match raw requests")
    if capacity_summary(report["stages"][:-2]) != report["capacity"]:
        fail("capacity summary mismatch")
    status = campaign_status(report["stages"])
    # Historical v1 reports without resource sampling remain verifiable as such.
    if ("resources" in report) != ("resources.jsonl" in artifacts):
        fail("resource summary and artifact must both be present")
    if "scripts/process_resource_monitor.py" in report.get("source_hashes", {}) and "resources" not in report:
        fail("resource-aware producer omitted resource evidence")
    if "resources" in report:
        samples = artifacts["resources.jsonl"]
        identities = {(r["pid"], r["create_time"]) for r in samples}
        if len(identities) != 1 or next(iter(identities))[0] != report["fixture_pid"]:
            fail("resource target identity mismatch")
        previous = -1
        for sample in samples:
            elapsed = sample["elapsed_seconds"]
            if not finite(elapsed) or elapsed <= previous:
                fail("resource timestamps not increasing")
            previous = elapsed
            if sample["status"] not in {"OK", "MISSING"}:
                fail("unknown resource observation status")
            if sample["status"] == "OK":
                for key in ("rss_bytes", "threads", "handles_or_fds", "cpu_seconds"):
                    if not finite(sample[key]) or sample[key] < 0:
                        fail("invalid resource counter")
                if sample["cpu_percent"] is not None and (not finite(sample["cpu_percent"]) or sample["cpu_percent"] < 0):
                    fail("invalid CPU observation")
        resources = summarize_resources(samples, plan.soak_seconds)
        if resources != report["resources"]:
            fail("resource summary mismatch")
        if resources["status"] != "PASSED":
            status = "INVALID" if resources["status"] == "INVALID" else "FAILED"
    if status != report["status"]:
        fail("overall verdict mismatch")
    if require_pass and status != "PASSED":
        fail(f"measurement did not pass: {status}")
    return {"integrity": "VERIFIED", "measurement_status": status,
            "offered_requests": sum(s["summary"]["offered"] for s in report["stages"]),
            "resources_present": "resources" in report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.report, args.require_pass)), flush=True)
