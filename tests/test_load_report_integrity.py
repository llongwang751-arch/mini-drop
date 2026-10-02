from dataclasses import asdict
import hashlib
import json

import pytest

from scripts.run_load_endurance import Plan, capacity_summary, summarize
from scripts.verify_load_report import verify
from scripts.process_resource_monitor import summarize_resources


def save(path, report):
    report.pop("sha256", None)
    report["sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(report), encoding="utf-8")


def fixture(tmp_path):
    plan = Plan(rates=(5,), step_seconds=6, recovery_seconds=6, soak_seconds=6, bucket_seconds=6)
    report = {"schema": "mini-drop.load-endurance.v1", "status": "PASSED", "plan": asdict(plan),
              "stages": [], "artifact_sha256": {}}
    for name in ["step-1", "recovery", "soak"]:
        rows = [{"index": i, "scheduled_offset_seconds": i / 5, "sent": True,
                 "success": True, "quality_passed": True, "dispatch_lag_ms": 0, "latency_ms": 20}
                for i in range(30)]
        raw = tmp_path / f"{name}.jsonl"
        raw.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        summary = summarize(rows, plan, 6)
        bucket = dict(summary)
        bucket.pop("successful_rps_including_drain")
        report["stages"].append({"name": name, "rate": 5, "duration_seconds": 6,
                                 "elapsed_with_drain_seconds": 6, "summary": summary, "raw_file": raw.name,
                                 "buckets": [{"offset_seconds": 0, "duration_seconds": 6, "summary": bucket}]})
        report["artifact_sha256"][raw.name] = hashlib.sha256(raw.read_bytes()).hexdigest()
    report["capacity"] = capacity_summary(report["stages"][:-2])
    path = tmp_path / "report.json"
    save(path, report)
    return path, report


def test_complete_raw_evidence_is_verified_without_inventing_resources(tmp_path):
    path, _ = fixture(tmp_path)
    assert verify(path, True) == {"integrity": "VERIFIED", "measurement_status": "PASSED",
                                  "offered_requests": 90, "resources_present": False}


def test_raw_corruption_is_rejected(tmp_path):
    path, _ = fixture(tmp_path)
    (tmp_path / "soak.jsonl").write_text("{}")
    with pytest.raises(ValueError, match="digest mismatch"):
        verify(path)


@pytest.mark.parametrize("mutation,reason", [
    (lambda r:r["stages"][-1]["summary"].update(p95_ms=1), "summary does not match"),
    (lambda r:r["stages"][-1].update(buckets=[]), "window summary"),
    (lambda r:r["stages"].pop(), "phase sequence"),
    (lambda r:r["capacity"].update(highest_contiguous_passing_rps=999), "capacity summary"),
    (lambda r:r.update(status="INVALID"), "overall verdict"),
    (lambda r:r["stages"][-1].update(duration_seconds=5), "workload changed"),
])
def test_rehashed_manifest_cannot_override_raw_measurement(tmp_path, mutation, reason):
    path, report = fixture(tmp_path)
    mutation(report)
    save(path, report)
    with pytest.raises(ValueError, match=reason):
        verify(path)


@pytest.mark.parametrize("mutation,reason", [
    (lambda r:r.update(index=1), "missing or duplicated"),
    (lambda r:r.update(scheduled_offset_seconds=99), "schedule changed"),
    (lambda r:r.update(sent=False), "invented observation"),
    (lambda r:r.update(success=False), "quality success"),
    (lambda r:r.update(latency_ms=float("nan")), "latency omits"),
    (lambda r:r.update(dispatch_lag_ms=21), "latency omits"),
])
def test_rehashed_raw_records_still_obey_accounting_invariants(tmp_path, mutation, reason):
    path, report = fixture(tmp_path)
    raw = tmp_path / "soak.jsonl"
    rows = [json.loads(line) for line in raw.read_text().splitlines()]
    mutation(rows[0])
    raw.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    report["artifact_sha256"][raw.name] = hashlib.sha256(raw.read_bytes()).hexdigest()
    save(path, report)
    with pytest.raises(ValueError, match=reason):
        verify(path)


def test_artifact_path_cannot_escape_report_directory(tmp_path):
    path, report = fixture(tmp_path)
    report["artifact_sha256"]["../outside.jsonl"] = "a" * 64
    save(path, report)
    with pytest.raises(ValueError, match="unsafe artifact"):
        verify(path)


def test_manifest_hash_and_running_state_are_not_accepted(tmp_path):
    path, report = fixture(tmp_path)
    report["status"] = "INVALID"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="manifest digest"):
        verify(path)
    report["status"] = "RUNNING"
    save(path, report)
    with pytest.raises(ValueError, match="unfinished"):
        verify(path)


def add_resources(path, report):
    samples = [{"pid": 42, "create_time": 100, "status": "OK", "phase": "soak",
                "elapsed_seconds": i, "rss_bytes": 1024, "threads": 2, "handles_or_fds": 3,
                "handle_kind": "posix_file_descriptors", "cpu_percent": None if i == 0 else 1,
                "cpu_seconds": i * .01} for i in range(6)]
    raw = path.parent / "resources.jsonl"
    raw.write_text("\n".join(json.dumps(row) for row in samples), encoding="utf-8")
    report["artifact_sha256"][raw.name] = hashlib.sha256(raw.read_bytes()).hexdigest()
    report["resources"] = summarize_resources(samples, 6)
    report["fixture_pid"] = 42
    report["source_hashes"] = {"scripts/process_resource_monitor.py": "a" * 64}
    save(path, report)
    return raw, samples


def test_resource_summary_is_recomputed_and_cannot_be_omitted(tmp_path):
    path, report = fixture(tmp_path)
    add_resources(path, report)
    assert verify(path)["resources_present"] is True
    report.pop("resources")
    save(path, report)
    with pytest.raises(ValueError, match="both be present"):
        verify(path)
    report["artifact_sha256"].pop("resources.jsonl")
    save(path, report)
    with pytest.raises(ValueError, match="omitted resource"):
        verify(path)


@pytest.mark.parametrize("mutation,reason", [
    (lambda r:r.update(pid=99), "identity mismatch"),
    (lambda r:r.update(elapsed_seconds=-1), "timestamps"),
    (lambda r:r.update(rss_bytes=float("nan")), "resource counter"),
    (lambda r:r.update(cpu_percent=-1), "CPU observation"),
])
def test_resource_data_must_belong_to_target_with_real_counters(tmp_path, mutation, reason):
    path, report = fixture(tmp_path)
    raw, samples = add_resources(path, report)
    mutation(samples[0])
    raw.write_text("\n".join(json.dumps(row) for row in samples), encoding="utf-8")
    report["artifact_sha256"][raw.name] = hashlib.sha256(raw.read_bytes()).hexdigest()
    save(path, report)
    with pytest.raises(ValueError, match=reason):
        verify(path)
