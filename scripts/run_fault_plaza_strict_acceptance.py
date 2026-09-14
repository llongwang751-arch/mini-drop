"""Fresh 21-case acceptance; never promote transport success to root-cause success.

Use run_campaign with an authenticated Client and a bounded, read-only snapshot
provider. The provider returns actual lab /snapshot measurements; these are
test-oracle observations, NOT injected Agent evidence or production fix claims.
Every case retains the original diagnostic reports, including rejected claims.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from scripts import run_fault_plaza_closure_campaign as legacy
from scripts.verify_interview_demo import (
    _collect_task_lineage, _get_plaza, _run_diagnosis, _start_fault,
    _stop_fault, items_of,
)

# field, measurement mode, minimum injected signal, root-conclusion vocabulary.
# Rate comparisons use within-window differences: counters reset at fault start.
ORACLES = {
    "cpu-hotspot": ("operation_count", "counter", 1, ("_run", "sha256")),
    "source-hotspot": ("source_profile_samples", "counter", 1, ("source_hot_function",)),
    "memory-pressure": ("retained_memory_mb", "gauge", 8, ("内存", "RSS", "PSS")),
    "io-write-latency": ("io_workload_bytes", "counter", 4096, ("I/O", "写入", "磁盘")),
    "noisy-neighbor": ("peer_cpu_ticks", "gauge", 1, ("同机", "邻居", "争抢")),
    "load-saturation": ("load_queue_depth", "gauge", 1, ("饱和", "过载", "排队")),
    "queue-backlog": ("queue_lag", "gauge", 1, ("队列", "积压", "消费")),
    "go-cpu-hotspot": ("cpu_operations", "counter", 1, ("goCPUHotFunction", "runCPUFault")),
    "go-network-latency": ("network_requests", "counter", 1, ("网络", "下游", "依赖")),
    "go-memory-growth": ("retained_memory_bytes", "gauge", 8388608, ("内存", "RSS", "PSS")),
    "go-file-io": ("io_bytes_written", "counter", 4096, ("I/O", "写入", "磁盘")),
    "java-gc-pressure": ("allocated_bytes", "counter", 4096, ("GC", "分配")),
    "java-lock-contention": ("lock_wait_ms", "counter", 1, ("锁", "monitor", "lock")),
    "java-downstream-latency": ("downstream_requests", "counter", 1, ("下游", "依赖", "阻塞")),
    "java-offheap-growth": ("offheap_retained_bytes", "gauge", 8388608, ("堆外", "直接内存", "DirectByteBuffer")),
    "java-file-io": ("io_bytes_written", "counter", 4096, ("I/O", "写入", "磁盘")),
    "cpp-cpu-hotspot": ("cpu_operations", "counter", 1, ("cpp_cpu_hot_function", "cpu_worker")),
    "cpp-lock-contention": ("lock_wait_ms", "counter", 1, ("锁", "mutex", "futex")),
    "cpp-memory-growth": ("retained_memory_mb", "gauge", 8, ("内存", "RSS", "PSS")),
    "cpp-file-io": ("io_bytes_written", "counter", 4096, ("I/O", "写入", "磁盘")),
    "cpp-downstream-latency": ("downstream_requests", "counter", 1, ("下游", "依赖", "阻塞")),
}


def now():
    return datetime.now(timezone.utc).isoformat()


class RecordingClient:
    """Keep the diagnosis ID even when the old lineage verifier raises early."""
    def __init__(self, client):
        self.client = client
        self.diagnosis_id = None

    def request(self, method, path, *args, **kwargs):
        response = self.client.request(method, path, *args, **kwargs)
        if method == "POST" and path == "/api/v2/diagnoses":
            self.diagnosis_id = response.get("diagnosis_id") or response.get("id")
        return response


def measure(provider: Callable, lab: str, seconds: float):
    start = time.monotonic()
    first = {"observed_at": now(), "snapshot": provider(lab)}
    time.sleep(seconds)
    last = {"observed_at": now(), "snapshot": provider(lab)}
    return {"first": first, "last": last, "elapsed_seconds": time.monotonic() - start}


def evaluate_intervention(scenario_id, windows):
    field, mode, threshold, _ = ORACLES[scenario_id]
    values = {}
    try:
        for stage in ("baseline", "fault", "recovery"):
            window = windows[stage]
            first, last = (float(window[key]["snapshot"][field]) for key in ("first", "last"))
            if not all(math.isfinite(value) for value in (first, last, window["elapsed_seconds"])):
                raise ValueError(f"{stage}: non-finite measurement")
            if mode == "counter" and last < first:
                raise ValueError(f"{stage}: counter reset inside observation window")
            values[stage] = (last - first) / window["elapsed_seconds"] if mode == "counter" else last
        injected = values["fault"] >= threshold
        recovered = injected and values["recovery"] <= max(values["baseline"] * 1.2, values["fault"] * 0.2)
        return {"field": field, "mode": mode, "values": values,
                "injection_observed": injected, "recovery_observed": recovered,
                "scope": "WITHDRAWAL_OF_INJECTED_WORKLOAD; NOT_SAME_LOAD_FIX_OR_SLO_VERIFICATION"}
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return {"field": field, "values": values, "injection_observed": False,
                "recovery_observed": False, "error": str(exc)}


def evaluate_reports(scenario_id, reports):
    tokens = ORACLES[scenario_id][3]
    decisions = []
    for report in reports:
        gate = report.get("verification") or {}
        conclusion = str(report.get("conclusion") or "")
        verified = (gate.get("status") == "VERIFIED"
                    and gate.get("has_independent_counter_or_control") is True
                    and float(gate.get("coverage_ratio") or 0) >= 1
                    and bool(report.get("evidence_refs")))
        # Merely repeating an unverified hypothesis must never match the oracle.
        concrete = bool(conclusion) and not report.get("counter_evidence_refs") and not any(x in conclusion for x in (
            "尚未定位", "未定位到", "不能把假设", "仍待验证", "当前没有能够支持"))
        matches = concrete and any(token.casefold() in conclusion.casefold() for token in tokens)
        decisions.append({"report_id": report.get("report_id"), "gate_status": gate.get("status"),
                          "verified": verified, "concrete_finding": concrete,
                          "oracle_vocabulary_match": matches, "accepted": verified and matches})
    return {"reports": decisions, "root_gate_verified": any(x["verified"] for x in decisions),
            "root_cause_accepted": any(x["accepted"] for x in decisions),
            "match_boundary": "Conservative scenario vocabulary check after the evidence gate; not a production accuracy metric."}


def collect_records(client, diagnosis_id):
    base = f"/api/v2/diagnoses/{diagnosis_id}"
    records = {"diagnosis": client.request("GET", base)}
    for kind in ("reports", "evidence", "tool-calls", "hypotheses", "events"):
        records[kind] = items_of(client.request("GET", f"{base}/{kind}"))
    records["tasks"] = []
    for task_id in dict.fromkeys(row.get("task_id") for row in records["tool-calls"] if row.get("task_id")):
        try:
            records["tasks"].append(_collect_task_lineage(client, task_id))
        except Exception as exc:
            records["tasks"].append({"task_id": task_id, "lineage_error": str(exc)})
    return records


def run_case(client, scenario, provider, case_path: Path, *, agent_id, window_seconds=4):
    case_started = time.monotonic()
    sid = scenario["scenario_id"]
    lab = scenario.get("lab_key") or "python"
    recorder = RecordingClient(client)
    row = {"scenario_id": sid, "title": scenario.get("title"), "started_at": now(),
           "passed": False, "lineage_verified": False, "cleanup_verified": False,
           "root_cause_accepted": False, "fix_verified": False, "windows": {}, "session_drained": True}
    started = False
    try:
        if scenario.get("active"):
            raise RuntimeError("pre-existing active fault; refusing to interfere")
        if not scenario.get("available"):
            raise RuntimeError("scenario unavailable: " + str(scenario.get("unavailable_reason")))
        if any(x.get("active") for x in items_of(_get_plaza(client).get("scenarios"))):
            raise RuntimeError("another fault became active; refusing to overlap")
        row["windows"]["baseline"] = measure(provider, lab, window_seconds)
        # Set before the request: a lost response may still have started the fault.
        started = True
        request = _start_fault(client, sid, 300)
        time.sleep(3)
        row["windows"]["fault"] = measure(provider, lab, window_seconds)
        target = legacy._find_demo_process(client, agent_id=agent_id, lab_key=lab)
        row["target"] = {"agent_id": agent_id, "pid": target["pid"], "comm": target.get("comm")}
        row["legacy_lineage_result"] = _run_diagnosis(
            recorder, request["diagnosis_request"], policy="AUTO", demo_agent_id=agent_id,
            demo_pid=int(target["pid"]), timeout_seconds=240, poll_seconds=2,
            minimum_rounds=3, expected_collector=legacy._decisive_collector(scenario),
            expected_hot_function="", profile_validation="lineage_only")
        row["lineage_verified"] = True
    except Exception as exc:
        row["error"] = str(exc)
        row["error_type"] = type(exc).__name__
    finally:
        row["diagnosis_id"] = recorder.diagnosis_id
        if started:
            try:
                row["fault_before_stop"] = {"observed_at": now(), "snapshot": provider(lab)}
            except Exception as exc:
                row["pre_stop_observation_error"] = str(exc)
            try:
                _stop_fault(client, sid)
                time.sleep(3)
                row["windows"]["recovery"] = measure(provider, lab, window_seconds)
                fresh = _get_plaza(client)
                status = next(x for x in items_of(fresh.get("scenarios")) if x["scenario_id"] == sid)
                row["cleanup_verified"] = status.get("active") is False
            except Exception as exc:
                row["cleanup_error"] = str(exc)
        if recorder.diagnosis_id:
            try:
                # A verifier error (e.g. wrong target) does not stop the worker.
                # Let its bounded session finish before starting another case;
                # preserve the failed run rather than archiving/deleting it.
                while True:
                    state = client.request("GET", f"/api/v2/diagnoses/{recorder.diagnosis_id}") or {}
                    row["session_drained"] = state.get("status") in {"COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"}
                    if row["session_drained"] or time.monotonic() - case_started >= 330:
                        break
                    time.sleep(2)
                records = collect_records(client, recorder.diagnosis_id)
                row["records"] = records
                row["report_evaluation"] = evaluate_reports(sid, records["reports"])
                row["root_cause_accepted"] = row["report_evaluation"]["root_cause_accepted"]
            except Exception as exc:
                row["session_drained"] = False
                row["records_error"] = str(exc)
    row["intervention"] = evaluate_intervention(sid, row["windows"])
    row["passed"] = bool(row["lineage_verified"] and row["root_cause_accepted"]
                         and row["intervention"]["recovery_observed"] and row["cleanup_verified"] and row["session_drained"])
    row["finished_at"] = now()
    row["report_sha256"] = legacy._canonical_sha256(row)
    legacy._write_json_atomic(case_path, row)
    return row


def run_campaign(client, provider, output: Path, *, agent_id="control-interview-demo-agent", scenario_ids=None):
    plaza = _get_plaza(client)
    scenarios = items_of(plaza.get("scenarios"))
    if scenario_ids:
        scenarios = [row for row in scenarios if row["scenario_id"] in scenario_ids]
    if not scenarios or any(row["scenario_id"] not in ORACLES for row in scenarios):
        raise ValueError("missing strict scenario contract")
    if any(row.get("active") for row in items_of(plaza.get("scenarios"))):
        raise RuntimeError("a fault is already active; campaign not started")
    report = {"schema": "mini-drop.fault-plaza-strict-acceptance.v2", "started_at": now(),
              "run_status": "RUNNING", "selected_count": len(scenarios), "results": [],
              "truth_boundary": "PASS requires legacy lineage, verified concrete scenario-matching root, and measured withdrawal recovery. Stopping injected work is NOT a code fix or same-load service recovery. Independent lab snapshots are NOT inserted into Agent reports."}
    def checkpoint():
        report["completed_count"] = len(report["results"])
        report["passed_count"] = sum(x["passed"] for x in report["results"])
        report["failed_count"] = report["completed_count"] - report["passed_count"]
        report["passed"] = report["completed_count"] == report["selected_count"] == report["passed_count"]
        report["updated_at"] = now()
        report.pop("report_sha256", None)
        report["report_sha256"] = legacy._canonical_sha256(report)
        legacy._write_json_atomic(output, report)
    checkpoint()
    for scenario in scenarios:
        sid = scenario["scenario_id"]
        print(json.dumps({"event": "START", "scenario_id": sid, "time": now()}), flush=True)
        case_path = output.parent / (output.stem + "-cases") / (sid + ".json")
        row = run_case(client, scenario, provider, case_path, agent_id=agent_id)
        summary = {k: v for k, v in row.items() if k not in {"records", "windows", "legacy_lineage_result"}}
        summary["case_report"] = str(case_path)
        report["results"].append(summary)
        checkpoint()
        print(json.dumps({"event": "FINISH", "scenario_id": sid, "passed": row["passed"],
                          "root_cause_accepted": row["root_cause_accepted"], "cleanup": row["cleanup_verified"],
                          "completed": report["completed_count"]}, ensure_ascii=False), flush=True)
        if not row["cleanup_verified"] or not row["session_drained"]:
            report["run_status"] = "STOPPED_UNSAFE_TO_CONTINUE"
            checkpoint()
            return report
    report["run_status"] = "COMPLETED"
    checkpoint()
    return report
