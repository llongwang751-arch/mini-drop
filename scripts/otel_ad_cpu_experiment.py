"""Run reproducible OTel Demo ad-service CPU and GC fault experiments."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
from typing import Any

import grpc

try:
    from scripts.otel_experiment_common import (
        DEFAULT_OTEL_ROOT,
        ROOT,
        atomic_write_json,
        compile_proto,
        compose_config_provenance,
        docker_container_provenance,
        docker_host_port,
        docker_stats,
        host_pid,
        restart_and_wait_healthy,
        resolve_otel_revision,
        summarize_samples,
    )
except ModuleNotFoundError:
    from otel_experiment_common import (
        DEFAULT_OTEL_ROOT,
        ROOT,
        atomic_write_json,
        compile_proto,
        compose_config_provenance,
        docker_container_provenance,
        docker_host_port,
        docker_stats,
        host_pid,
        restart_and_wait_healthy,
        resolve_otel_revision,
        summarize_samples,
    )
from server.app.diagnosis.benchmark_adapters import set_otel_feature_flag


DEFAULT_CASE_ID = "T1-CPU-001"
AD_CASE_IDS = ("T1-CPU-001", "T1-GC-001")
MAX_FAULT_DURATION_SECONDS = 120


class FixtureFailure(RuntimeError):
    """The bounded fault fixture failed independently of diagnosis quality."""


def run_phase(
    *,
    case_id: str,
    name: str,
    enabled: bool,
    otel_root: Path,
    flag_config_path: Path | None = None,
    target: str,
    container: str,
    duration: float,
    workers: int,
    demo_pb2: Any,
    demo_pb2_grpc: Any,
    expected_pid: int,
) -> dict[str, Any]:
    toggle = set_otel_feature_flag(
        case_id,
        otel_root=otel_root,
        enabled=enabled,
        flag_config_path=flag_config_path,
    )
    time.sleep(2)
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + duration
    stop = threading.Event()
    counters = {"requests": 0, "errors": 0}
    latencies_ms: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        channel = grpc.insecure_channel(target)
        stub = demo_pb2_grpc.AdServiceStub(channel)
        request = demo_pb2.AdRequest(context_keys=["binoculars", "telescopes"])
        local_requests = 0
        local_errors = 0
        local_latencies: list[float] = []
        try:
            while time.monotonic() < deadline and not stop.is_set():
                tick = time.perf_counter()
                try:
                    stub.GetAds(request, timeout=5)
                    local_requests += 1
                    local_latencies.append((time.perf_counter() - tick) * 1000)
                except grpc.RpcError:
                    local_errors += 1
        finally:
            channel.close()
            with lock:
                counters["requests"] += local_requests
                counters["errors"] += local_errors
                latencies_ms.extend(local_latencies)

    samples: list[dict[str, Any]] = []
    fixture_failure: dict[str, Any] | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker) for _ in range(workers)]
        while time.monotonic() < deadline and not stop.is_set():
            try:
                current_pid = host_pid(container)
                if current_pid != expected_pid:
                    fixture_failure = {
                        "reason": "unexpected_container_restart",
                        "expected_pid": expected_pid,
                        "observed_pid": current_pid,
                    }
                    stop.set()
                    break
                samples.append(docker_stats(container))
            except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as exc:
                fixture_failure = {
                    "reason": "resource_sampling_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                stop.set()
                break
            time.sleep(1)
        for future in futures:
            future.result()

    latency_sorted = sorted(latencies_ms)
    p95_index = max(0, min(len(latency_sorted) - 1, int(len(latency_sorted) * 0.95)))
    return {
        "phase": name,
        "fault_enabled": enabled,
        "started_at": started.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration,
        "workers": workers,
        "requests": counters["requests"],
        "errors": counters["errors"],
        "request_rate_per_second": round(counters["requests"] / duration, 3),
        "latency_ms_mean": (
            round(statistics.fmean(latencies_ms), 3) if latencies_ms else None
        ),
        "latency_ms_p95": (
            round(latency_sorted[p95_index], 3) if latency_sorted else None
        ),
        "toggle": toggle,
        "resource_observation": summarize_samples(samples),
        "fixture_failure": fixture_failure,
    }


def verification_for(result: dict[str, Any]) -> dict[str, Any]:
    phases = result["phases"]
    if len(phases) != 3 or result.get("fixture_failure"):
        return {
            "real_requests_observed": False,
            "passed": False,
            "reason": "fixture_failure_or_incomplete_phases",
        }
    baseline, incident, recovery = phases
    baseline_cpu = baseline["resource_observation"]["cpu_percent_mean"] or 0
    incident_cpu = incident["resource_observation"]["cpu_percent_mean"] or 0
    recovery_cpu = recovery["resource_observation"]["cpu_percent_mean"] or 0
    cpu_ratio = round(incident_cpu / max(baseline_cpu, 0.001), 3)
    latency_ratio = round(
        (incident["latency_ms_p95"] or 0)
        / max((baseline["latency_ms_p95"] or 0), 0.001),
        3,
    )
    exercised = all((item["requests"] + item["errors"]) > 0 for item in phases)
    verification = {
        "incident_cpu_increase_ratio": cpu_ratio,
        "incident_latency_p95_ratio": latency_ratio,
        "recovery_cpu_below_incident": recovery_cpu < incident_cpu,
        "real_requests_observed": exercised,
        "passed": False,
    }
    if result["case_id"] == "T1-CPU-001":
        signal_passed = cpu_ratio >= 1.5
        recovered = verification["recovery_cpu_below_incident"]
    else:
        request_rate_drop = incident["request_rate_per_second"] < (
            baseline["request_rate_per_second"] * 0.5
        )
        signal_passed = bool(
            incident["errors"] > baseline["errors"]
            or request_rate_drop
            or cpu_ratio >= 1.2
            or latency_ratio >= 1.2
        )
        recovered = bool(
            recovery["requests"] > incident["requests"]
            and recovery["errors"] <= incident["errors"]
        )
        verification["incident_request_rate_drop"] = request_rate_drop
        verification["recovery_requests_resumed"] = recovered
    verification["passed"] = bool(signal_passed and recovered and exercised)
    return verification


def execute_experiment(
    *,
    case_id: str,
    otel_root: Path,
    duration: float,
    workers: int,
    output: Path,
    container: str = "ad",
    flag_config_path: Path | None = None,
    project_name: str | None = None,
    compose_files: list[Path] | None = None,
    environment_file: Path | None = None,
) -> dict[str, Any]:
    if case_id not in AD_CASE_IDS:
        raise ValueError(f"unsupported OTel ad fault case: {case_id}")
    if not 3 <= duration <= MAX_FAULT_DURATION_SECONDS:
        raise ValueError("duration must be between 3 and 120 seconds")
    if not 1 <= workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    if bool(project_name) != bool(compose_files):
        raise ValueError("project_name and compose_files must be provided together")

    provenance_enabled = project_name is not None and compose_files is not None
    result: dict[str, Any] = {
        "schema_version": "1.1.0",
        "case_id": case_id,
        "fixture": (
            "OpenTelemetry Demo ad-service CPU fault"
            if case_id == "T1-CPU-001"
            else "OpenTelemetry Demo ad-service manual GC fault"
        ),
        "container": container,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "phases": [],
        "flag_transitions": [],
        "provenance": {
            "compose": None,
            "containers": {"initial": None, "recovery": None, "cleanup": None},
        },
    }
    incident_started = False
    recovery_completed = False
    pending_error: BaseException | None = None
    try:
        result["otel_commit"] = resolve_otel_revision(otel_root)
        if provenance_enabled:
            result["provenance"]["compose"] = compose_config_provenance(
                project_name=project_name,
                compose_files=compose_files,
                environment_file=environment_file,
            )
        if case_id == "T1-GC-001":
            reset = set_otel_feature_flag(
                case_id,
                otel_root=otel_root,
                enabled=False,
                flag_config_path=flag_config_path,
            )
            result["flag_transitions"].append({"phase": "precondition", **reset})
            result["precondition"] = restart_and_wait_healthy(container)

        current_pid = host_pid(container)
        if provenance_enabled:
            initial = docker_container_provenance(container)
            result["provenance"]["containers"]["initial"] = initial
            if initial["host_pid"] != current_pid:
                raise FixtureFailure(
                    "docker provenance PID does not match current host PID: "
                    f"{initial['host_pid']} != {current_pid}"
                )
        target = f"127.0.0.1:{docker_host_port(container, 9555)}"
        result.update({"target": target, "host_pid": current_pid})
        demo_pb2, demo_pb2_grpc = compile_proto(otel_root, ROOT / "tmp" / "otel_pb")

        for name, enabled in (("baseline", False), ("incident", True)):
            if name == "incident":
                incident_started = True
            phase = run_phase(
                case_id=case_id,
                name=name,
                enabled=enabled,
                otel_root=otel_root,
                flag_config_path=flag_config_path,
                target=target,
                container=container,
                duration=duration,
                workers=workers,
                demo_pb2=demo_pb2,
                demo_pb2_grpc=demo_pb2_grpc,
                expected_pid=current_pid,
            )
            result["phases"].append(phase)
            result["flag_transitions"].append({"phase": name, **phase["toggle"]})
            if phase["fixture_failure"]:
                raise FixtureFailure(phase["fixture_failure"]["reason"])

        if case_id == "T1-GC-001":
            reset = set_otel_feature_flag(
                case_id,
                otel_root=otel_root,
                enabled=False,
                flag_config_path=flag_config_path,
            )
            result["flag_transitions"].append({"phase": "recovery_intervention", **reset})
            result["recovery_intervention"] = restart_and_wait_healthy(container)
            current_pid = result["recovery_intervention"]["after_pid"]
            if provenance_enabled:
                recovery_container = docker_container_provenance(container)
                result["provenance"]["containers"]["recovery"] = recovery_container
                if recovery_container["host_pid"] != current_pid:
                    raise FixtureFailure(
                        "recovery provenance PID does not match restarted host PID: "
                        f"{recovery_container['host_pid']} != {current_pid}"
                    )
            recovery_completed = True
            target = f"127.0.0.1:{docker_host_port(container, 9555)}"

        recovery = run_phase(
            case_id=case_id,
            name="recovery",
            enabled=False,
            otel_root=otel_root,
            flag_config_path=flag_config_path,
            target=target,
            container=container,
            duration=duration,
            workers=workers,
            demo_pb2=demo_pb2,
            demo_pb2_grpc=demo_pb2_grpc,
            expected_pid=current_pid,
        )
        result["phases"].append(recovery)
        result["flag_transitions"].append({"phase": "recovery", **recovery["toggle"]})
        if recovery["fixture_failure"]:
            raise FixtureFailure(recovery["fixture_failure"]["reason"])
    except BaseException as exc:
        pending_error = exc
        result["fixture_failure"] = {
            "reason": "experiment_exception",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        cleanup: dict[str, Any] = {"flag_reset": None, "gc_restart": None, "errors": []}
        try:
            cleanup["flag_reset"] = set_otel_feature_flag(
                case_id,
                otel_root=otel_root,
                enabled=False,
                flag_config_path=flag_config_path,
            )
            result["flag_transitions"].append(
                {"phase": "cleanup", **cleanup["flag_reset"]}
            )
        except Exception as exc:
            cleanup["errors"].append(f"flag_reset: {type(exc).__name__}: {exc}")
        if case_id == "T1-GC-001" and incident_started and not recovery_completed:
            try:
                cleanup["gc_restart"] = restart_and_wait_healthy(container)
                if provenance_enabled:
                    cleanup_container = docker_container_provenance(container)
                    result["provenance"]["containers"]["cleanup"] = cleanup_container
                    cleanup_pid = cleanup["gc_restart"]["after_pid"]
                    if cleanup_container["host_pid"] != cleanup_pid:
                        raise FixtureFailure(
                            "cleanup provenance PID does not match restarted host PID: "
                            f"{cleanup_container['host_pid']} != {cleanup_pid}"
                        )
            except Exception as exc:
                cleanup["errors"].append(f"gc_restart: {type(exc).__name__}: {exc}")
        result["cleanup"] = cleanup
        if cleanup["errors"]:
            result["fixture_failure"] = {
                "reason": "cleanup_failed",
                "errors": cleanup["errors"],
                "prior_error": result.get("fixture_failure"),
            }
        result["ended_at"] = datetime.now(timezone.utc).isoformat()
        result["verification"] = verification_for(result)
        atomic_write_json(output, result)
    if pending_error is not None and isinstance(pending_error, (KeyboardInterrupt, SystemExit)):
        raise pending_error
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run OTel ad-service baseline/fault/recovery experiment"
    )
    parser.add_argument("--otel-root", type=Path, default=DEFAULT_OTEL_ROOT)
    parser.add_argument("--flag-config-path", type=Path, default=None)
    parser.add_argument("--case-id", choices=AD_CASE_IDS, default=DEFAULT_CASE_ID)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--container", default="ad")
    parser.add_argument("--duration", type=float, default=12)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--project-name", default=None)
    parser.add_argument(
        "--compose-file",
        type=Path,
        action="append",
        dest="compose_files",
        help="exact Compose file used by the fixture; repeat for merged files",
    )
    parser.add_argument("--environment-file", type=Path, default=None)
    args = parser.parse_args()
    if not 3 <= args.duration <= MAX_FAULT_DURATION_SECONDS:
        parser.error("--duration must be between 3 and 120 seconds")
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
    if args.case_id == "T1-CPU-001" and args.workers != 1:
        parser.error("formal T1-CPU-001 fixture requires exactly one worker")
    if bool(args.project_name) != bool(args.compose_files):
        parser.error("--project-name and --compose-file must be provided together")
    if args.environment_file is not None and not args.compose_files:
        parser.error("--environment-file requires --compose-file")

    otel_root = args.otel_root.resolve()
    output = args.output or ROOT / "reports" / "benchmark" / f"{args.case_id}-live.json"
    result = execute_experiment(
        case_id=args.case_id,
        otel_root=otel_root,
        duration=args.duration,
        workers=args.workers,
        output=output,
        container=args.container,
        flag_config_path=args.flag_config_path,
        project_name=args.project_name,
        compose_files=args.compose_files,
        environment_file=args.environment_file,
    )
    print(output.resolve())
    print(json.dumps(result["verification"], ensure_ascii=False))


if __name__ == "__main__":
    main()
