#!/usr/bin/env python3
"""Run reproducible OTel Demo transport-aware fault experiments.

Supported cases:

* T1-MEM-001: email HTTP service memory leak;
* T1-DOWNSTREAM-001: payment gRPC service unavailable.

Every run records baseline, incident and recovery phases. The memory leak
fixture leaves heap state behind, so every exit path disables the flag and
restarts the email container before returning.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import grpc

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

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
        run_command,
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
        run_command,
        summarize_samples,
    )
from server.app.diagnosis.benchmark_adapters import set_otel_feature_flag


MEMORY_LIMIT_BYTES = 100 * 1024 * 1024
MEMORY_ABORT_BYTES = int(MEMORY_LIMIT_BYTES * 0.8)
MAX_FAULT_DURATION_SECONDS = 120
CASES = {
    "T1-MEM-001": {"container": "email", "port": 6060, "service": "email"},
    "T1-DOWNSTREAM-001": {
        "container": "payment", "port": 50051, "service": "payment",
    },
}
EMAIL_PAYLOAD = {
    "email": "benchmark@example.invalid",
    "order": {
        "order_id": "mini-drop-benchmark",
        "shipping_tracking_id": "TRACK-001",
        "shipping_cost": {"currency_code": "USD", "units": 1, "nanos": 0},
        "shipping_address": {
            "street_address": "1 Benchmark Road",
            "city": "Test City",
            "state": "TS",
            "country": "US",
            "zip_code": "00000",
        },
        "items": [],
    },
}


@dataclass(frozen=True)
class Workload:
    transport: str
    create_client: Callable[[str], Any]
    invoke: Callable[[Any], Any]
    close_client: Callable[[Any], None]
    request_errors: tuple[type[BaseException], ...]


class FixtureFailure(RuntimeError):
    """The bounded fault fixture failed independently of diagnosis quality."""


def memory_bytes(text: str) -> int:
    """Parse the used side of Docker's `12.3MiB / 100MiB` value."""

    value = text.split("/", 1)[0].strip()
    match = re.fullmatch(r"([0-9.]+)\s*([KMGT]?i?B)", value, re.IGNORECASE)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2).lower()
    factors = {
        "b": 1, "kb": 1000, "kib": 1024,
        "mb": 1000**2, "mib": 1024**2,
        "gb": 1000**3, "gib": 1024**3,
        "tb": 1000**4, "tib": 1024**4,
    }
    return int(number * factors[unit])


def request_factory(case_id: str, demo_pb2: Any, demo_pb2_grpc: Any) -> Workload:
    if case_id == "T1-MEM-001":
        encoded = json.dumps(EMAIL_PAYLOAD).encode("utf-8")

        def invoke_http(target: str) -> int:
            request = Request(
                f"http://{target}/send_order_confirmation",
                data=encoded,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError(f"email returned HTTP {response.status}")
                response.read()
                return response.status

        return Workload(
            transport="http",
            create_client=lambda target: target,
            invoke=invoke_http,
            close_client=lambda _client: None,
            request_errors=(HTTPError, URLError, TimeoutError, OSError, RuntimeError),
        )

    if case_id != "T1-DOWNSTREAM-001":
        raise ValueError(f"unsupported OTel fault case: {case_id}")

    def create_grpc_client(target: str) -> tuple[Any, Any]:
        channel = grpc.insecure_channel(target)
        return channel, demo_pb2_grpc.PaymentServiceStub(channel)

    def invoke_grpc(client: tuple[Any, Any]) -> Any:
        _channel, stub = client
        return stub.Charge(
            demo_pb2.ChargeRequest(
                amount=demo_pb2.Money(currency_code="USD", units=42),
                credit_card=demo_pb2.CreditCardInfo(
                    credit_card_number="4111111111111111",
                    credit_card_cvv=123,
                    credit_card_expiration_year=2030,
                    credit_card_expiration_month=12,
                ),
            ),
            timeout=5,
        )

    return Workload(
        transport="grpc",
        create_client=create_grpc_client,
        invoke=invoke_grpc,
        close_client=lambda client: client[0].close(),
        request_errors=(grpc.RpcError,),
    )


def run_phase(
    *, case_id: str, name: str, enabled: bool, otel_root: Path,
    target: str, container: str, duration: float, workers: int,
    workload: Workload, expected_pid: int,
    memory_abort_bytes: int | None = None,
) -> dict[str, Any]:
    toggle = set_otel_feature_flag(case_id, otel_root=otel_root, enabled=enabled)
    time.sleep(2)
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + duration
    stop = threading.Event()
    counters = {"requests": 0, "errors": 0}
    latencies: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        client = workload.create_client(target)
        ok = failed = 0
        local_latency: list[float] = []
        try:
            while time.monotonic() < deadline and not stop.is_set():
                tick = time.perf_counter()
                try:
                    workload.invoke(client)
                    ok += 1
                    local_latency.append((time.perf_counter() - tick) * 1000)
                except workload.request_errors:
                    failed += 1
        finally:
            workload.close_client(client)
            with lock:
                counters["requests"] += ok
                counters["errors"] += failed
                latencies.extend(local_latency)

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
                sample = docker_stats(container)
                sample["memory_bytes"] = memory_bytes(sample.get("memory_usage", ""))
                samples.append(sample)
                if (
                    memory_abort_bytes is not None
                    and sample["memory_bytes"] >= memory_abort_bytes
                ):
                    fixture_failure = {
                        "reason": "memory_abort_threshold_reached",
                        "observed_bytes": sample["memory_bytes"],
                        "threshold_bytes": memory_abort_bytes,
                    }
                    stop.set()
                    break
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

    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else None
    resources = summarize_samples(samples)
    mem_values = [int(item.get("memory_bytes", 0)) for item in samples]
    resources.update({
        "memory_bytes_min": min(mem_values) if mem_values else None,
        "memory_bytes_max": max(mem_values) if mem_values else None,
        "memory_bytes_mean": round(statistics.fmean(mem_values), 2) if mem_values else None,
    })
    total = counters["requests"] + counters["errors"]
    return {
        "phase": name,
        "fault_enabled": enabled,
        "transport": workload.transport,
        "started_at": started.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration,
        "workers": workers,
        "requests": counters["requests"],
        "errors": counters["errors"],
        "error_rate": round(counters["errors"] / total, 4) if total else 0,
        "latency_ms_p95": round(p95, 3) if p95 is not None else None,
        "toggle": toggle,
        "resource_observation": resources,
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
    exercised = all((phase["requests"] + phase["errors"]) > 0 for phase in phases)
    if result["case_id"] == "T1-MEM-001":
        baseline_mem = baseline["resource_observation"]["memory_bytes_max"] or 0
        incident_mem = incident["resource_observation"]["memory_bytes_max"] or 0
        recovery_mem = recovery["resource_observation"]["memory_bytes_max"] or 0
        return {
            "incident_memory_growth_bytes": incident_mem - baseline_mem,
            "recovery_memory_below_incident": recovery_mem < incident_mem,
            "real_requests_observed": exercised,
            "passed": bool(
                incident_mem - baseline_mem >= 1024 * 1024
                and recovery_mem < incident_mem
                and exercised
            ),
        }
    return {
        "baseline_error_rate": baseline["error_rate"],
        "incident_error_rate": incident["error_rate"],
        "recovery_error_rate": recovery["error_rate"],
        "real_requests_observed": exercised,
        "passed": bool(
            incident["error_rate"] >= 0.5
            and baseline["error_rate"] < 0.1
            and recovery["error_rate"] < incident["error_rate"]
            and exercised
        ),
    }


def execute_experiment(
    *, case_id: str, otel_root: Path, duration: float, workers: int, output: Path,
    container: str | None = None, project_name: str | None = None,
    compose_files: list[Path] | None = None, environment_file: Path | None = None,
) -> dict[str, Any]:
    fixture = CASES[case_id]
    container = container or fixture["container"]
    if bool(project_name) != bool(compose_files):
        raise ValueError("project_name and compose_files must be provided together")
    result: dict[str, Any] = {
        "schema_version": "1.1.0",
        "case_id": case_id,
        "container": container,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "phases": [],
        "flag_transitions": [],
        "provenance": {
            "compose": None,
            "containers": {"initial": None, "recovery": None, "cleanup": None},
        },
    }
    provenance_enabled = project_name is not None and compose_files is not None
    incident_started = False
    recovery_completed = False
    pending_error: BaseException | None = None
    try:
        result["otel_commit"] = run_command(
            "git", "-C", str(otel_root), "rev-parse", "HEAD"
        )
        if provenance_enabled:
            result["provenance"]["compose"] = compose_config_provenance(
                project_name=project_name,
                compose_files=compose_files,
                environment_file=environment_file,
            )
        initial_pid = host_pid(container)
        if provenance_enabled:
            initial_container = docker_container_provenance(container)
            result["provenance"]["containers"]["initial"] = initial_container
            if initial_container["host_pid"] != initial_pid:
                raise FixtureFailure(
                    "docker provenance PID does not match current host PID: "
                    f"{initial_container['host_pid']} != {initial_pid}"
                )
        target = f"127.0.0.1:{docker_host_port(container, fixture['port'])}"
        result.update({"target": target, "host_pid": initial_pid})

        if case_id == "T1-DOWNSTREAM-001":
            demo_pb2, demo_pb2_grpc = compile_proto(
                otel_root, ROOT / "tmp" / "otel_pb"
            )
        else:
            demo_pb2 = demo_pb2_grpc = None
        workload = request_factory(case_id, demo_pb2, demo_pb2_grpc)

        baseline = run_phase(
            case_id=case_id, name="baseline", enabled=False,
            otel_root=otel_root, target=target, container=container,
            duration=duration, workers=workers, workload=workload,
            expected_pid=initial_pid,
        )
        result["phases"].append(baseline)
        result["flag_transitions"].append({"phase": "baseline", **baseline["toggle"]})
        if baseline["fixture_failure"]:
            raise FixtureFailure(baseline["fixture_failure"]["reason"])

        incident_started = True
        incident = run_phase(
            case_id=case_id, name="incident", enabled=True,
            otel_root=otel_root, target=target, container=container,
            duration=duration, workers=workers, workload=workload,
            expected_pid=initial_pid,
            memory_abort_bytes=(MEMORY_ABORT_BYTES if case_id == "T1-MEM-001" else None),
        )
        result["phases"].append(incident)
        result["flag_transitions"].append({"phase": "incident", **incident["toggle"]})
        if incident["fixture_failure"]:
            raise FixtureFailure(incident["fixture_failure"]["reason"])

        if case_id == "T1-MEM-001":
            recovery_reset = set_otel_feature_flag(
                case_id, otel_root=otel_root, enabled=False
            )
            result["flag_transitions"].append(
                {"phase": "recovery_intervention", **recovery_reset}
            )
            result["recovery_intervention"] = restart_and_wait_healthy(container)
            initial_pid = result["recovery_intervention"]["after_pid"]
            if provenance_enabled:
                recovery_container = docker_container_provenance(container)
                result["provenance"]["containers"]["recovery"] = recovery_container
                if recovery_container["host_pid"] != initial_pid:
                    raise FixtureFailure(
                        "recovery provenance PID does not match restarted host PID: "
                        f"{recovery_container['host_pid']} != {initial_pid}"
                    )
            recovery_completed = True
            target = f"127.0.0.1:{docker_host_port(container, fixture['port'])}"
        recovery = run_phase(
            case_id=case_id, name="recovery", enabled=False,
            otel_root=otel_root, target=target, container=container,
            duration=duration, workers=workers, workload=workload,
            expected_pid=initial_pid,
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
        cleanup: dict[str, Any] = {"flag_reset": None, "memory_restart": None, "errors": []}
        try:
            cleanup["flag_reset"] = set_otel_feature_flag(
                case_id, otel_root=otel_root, enabled=False
            )
            result["flag_transitions"].append(
                {"phase": "cleanup", **cleanup["flag_reset"]}
            )
        except Exception as exc:
            cleanup["errors"].append(f"flag_reset: {type(exc).__name__}: {exc}")
        if case_id == "T1-MEM-001" and incident_started and not recovery_completed:
            try:
                cleanup["memory_restart"] = restart_and_wait_healthy(container)
                if provenance_enabled:
                    cleanup_container = docker_container_provenance(container)
                    result["provenance"]["containers"]["cleanup"] = cleanup_container
                    cleanup_pid = cleanup["memory_restart"]["after_pid"]
                    if cleanup_container["host_pid"] != cleanup_pid:
                        raise FixtureFailure(
                            "cleanup provenance PID does not match restarted host PID: "
                            f"{cleanup_container['host_pid']} != {cleanup_pid}"
                        )
            except Exception as exc:
                cleanup["errors"].append(f"memory_restart: {type(exc).__name__}: {exc}")
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
    parser = argparse.ArgumentParser(description="Run an OTel transport-aware fault experiment")
    parser.add_argument("case_id", choices=sorted(CASES))
    parser.add_argument("--otel-root", type=Path, default=DEFAULT_OTEL_ROOT)
    parser.add_argument("--duration", type=float, default=6)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--container", default=None)
    parser.add_argument("--project-name", default=None)
    parser.add_argument(
        "--compose-file", type=Path, action="append", dest="compose_files",
        help="exact Compose file used by the fixture; repeat for merged files",
    )
    parser.add_argument("--environment-file", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if not 3 <= args.duration <= MAX_FAULT_DURATION_SECONDS:
        parser.error("--duration must be between 3 and 120 seconds")
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
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
        project_name=args.project_name,
        compose_files=args.compose_files,
        environment_file=args.environment_file,
    )
    print(output.resolve())
    print(json.dumps(result["verification"], ensure_ascii=False))
    raise SystemExit(0 if result["verification"]["passed"] else 2)


if __name__ == "__main__":
    main()
