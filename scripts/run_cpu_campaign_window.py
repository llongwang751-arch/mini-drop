#!/usr/bin/env python3
"""Run all diagnosis strategies inside one bounded real OTel CPU fault window."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.campaign_window_common import (  # noqa: E402
    api_json as _common_api_json,
    publish_submissions,
    resolve_agent_host as _common_resolve_agent_host,
    wait_for_terminal as _common_wait_for_terminal,
    wait_task_terminal as _common_wait_task_terminal,
)
from scripts.otel_ad_cpu_experiment import (  # noqa: E402
    DEFAULT_OTEL_ROOT,
    MAX_FAULT_DURATION_SECONDS,
)
from scripts.otel_experiment_common import (  # noqa: E402
    atomic_write_json,
    compose_config_provenance,
    docker_container_provenance,
    docker_host_port,
    docker_stats,
    host_pid,
    wait_container_healthy as _wait_container_healthy,
)
from server.app.diagnosis.benchmark_adapters import set_otel_feature_flag  # noqa: E402
from server.app.diagnosis.benchmark_runner import (  # noqa: E402
    STRATEGIES,
    scoring_detail_from_api,
    upsert_submission,
)


FAULT_SIGNAL_CPU_PERCENT = 5.0
BASELINE_LOAD_DURATION_SECONDS = 30
FAULT_OBSERVATION_PHASES = {"fault_window_ready"}
FAULT_OBSERVATION_PREFIXES = ("before_", "after_")


class FixtureFailure(RuntimeError):
    """The shared fault window failed independently of diagnosis quality."""


def wait_container_healthy(container: str, timeout_seconds: float = 90) -> None:
    _wait_container_healthy(container, timeout=timeout_seconds)


def api_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    retries: int = 6,
) -> dict:
    return _common_api_json(base_url, method, path, payload, retries=retries)


def wait_for_terminal(
    base_url: str,
    diagnosis_id: str,
    *,
    approve_r2: bool,
    timeout_seconds: float,
    fixture_check=None,
) -> dict[str, Any]:
    return _common_wait_for_terminal(
        base_url,
        diagnosis_id,
        approve_r2=approve_r2,
        timeout_seconds=timeout_seconds,
        fixture_check=fixture_check,
        api=api_json,
    )


def wait_task_terminal(
    base_url: str,
    task_id: str,
    *,
    timeout_seconds: float = 90,
) -> dict[str, Any]:
    return _common_wait_task_terminal(
        base_url,
        task_id,
        timeout_seconds=timeout_seconds,
        api=api_json,
    )


def resolve_agent_host(base_url: str, agent_id: str) -> str:
    return _common_resolve_agent_host(base_url, agent_id, api=api_json)


def create_payload(
    strategy: str,
    *,
    agent_id: str,
    host_id: str,
    pid: int,
    baseline_task_id: str,
    incident_started_at: datetime,
) -> dict[str, Any]:
    return {
        "query": (
            f"OpenTelemetry ad service PID {pid} has abnormally high CPU and latency. "
            "Diagnose the root cause and cite real evidence."
        ),
        "context": {
            "service_id": "otel-ad",
            "environment": "staging",
            "instances": [
                {
                    "service_id": "otel-ad",
                    "instance_id": "otel-ad-1",
                    "host_id": host_id,
                    "agent_id": agent_id,
                    "pid": pid,
                    "container_id": "ad",
                    "environment": "staging",
                    "runtime": "java",
                }
            ],
            "dependencies": [],
            "time_range": {
                "start": incident_started_at.isoformat(),
                "end": (incident_started_at + timedelta(minutes=10)).isoformat(),
                "source": "request_context",
            },
        },
        "budget_profile": "staging",
        "diagnosis_mode": "LIVE",
        "analysis_strategy": strategy,
        "evidence_time_policy": {
            "max_clock_skew_seconds": 5,
            "require_overlap": True,
            "allow_reproduction_evidence": False,
        },
        "baseline_task_ids": [baseline_task_id],
    }


def _publish_submissions(
    path: Path,
    submissions: list[dict[str, Any]],
    *,
    overwrite: bool,
) -> list[dict[str, Any]]:
    return publish_submissions(
        path,
        submissions,
        overwrite=overwrite,
        publisher=upsert_submission,
    )


def execute_campaign_window(
    *,
    repetition: int,
    otel_root: Path,
    base_url: str,
    agent_id: str,
    load_duration: int,
    load_workers: int,
    diagnosis_timeout: int,
    approve_r2: bool,
    overwrite: bool,
    submissions: Path,
    output_dir: Path,
    container: str = "ad",
    project_name: str | None = None,
    compose_files: list[Path] | None = None,
    environment_file: Path | None = None,
    window_id: str | None = None,
) -> dict[str, Any]:
    if repetition not in {1, 2, 3}:
        raise ValueError("repetition must be 1, 2, or 3")
    if load_workers != 1:
        raise ValueError("formal T1-CPU-001 campaign requires exactly one load worker")
    if not 1 <= load_duration <= MAX_FAULT_DURATION_SECONDS:
        raise ValueError("load_duration must be between 1 and 120 seconds")
    if diagnosis_timeout <= 0:
        raise ValueError("diagnosis_timeout must be positive")
    required_fault_load = 4 + len(STRATEGIES) * diagnosis_timeout
    if load_duration < required_fault_load:
        raise ValueError(
            "load_duration must cover fault warmup plus all diagnosis timeouts: "
            f"requires at least {required_fault_load} seconds"
        )
    if not approve_r2:
        raise ValueError("formal live campaign requires explicit R2 approval")
    if bool(project_name) != bool(compose_files):
        raise ValueError("project_name and compose_files must be provided together")
    if environment_file is not None and not compose_files:
        raise ValueError("environment_file requires compose_files")

    otel_root = otel_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    window_id = window_id or f"T1-CPU-001-r{repetition}-{uuid4().hex[:8]}"
    manifest_path = output_dir / f"{window_id}-manifest.json"
    baseline_load_output = output_dir / f"{window_id}-baseline-load.json"
    load_output = output_dir / f"{window_id}-load.json"
    provenance_enabled = project_name is not None and compose_files is not None
    manifest: dict[str, Any] = {
        "schema_version": "1.1.0",
        "window_id": window_id,
        "case_id": "T1-CPU-001",
        "repetition": repetition,
        "agent_id": agent_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "load": {"duration_seconds": load_duration, "workers": load_workers},
        "strategies": [],
        "resource_observations": [],
        "provenance": {"compose": None, "containers": {"initial": None, "final": None}},
        "publication": {"published": False, "records": []},
    }
    atomic_write_json(manifest_path, manifest)

    load_process: subprocess.Popen[str] | None = None
    baseline_load_process: subprocess.Popen[str] | None = None
    pending_submissions: list[dict[str, Any]] = []
    pending_error: BaseException | None = None
    fault_deadline: float | None = None
    target_pid: int | None = None

    def start_load(duration: int, output: Path) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "scripts" / "otel_ad_load.py"),
                "--otel-root",
                str(otel_root),
                "--container",
                container,
                "--duration",
                str(duration),
                "--workers",
                str(load_workers),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def stop_load(
        process: subprocess.Popen[str], *, allow_termination: bool
    ) -> dict[str, Any]:
        try:
            stdout, _ = process.communicate(timeout=10)
            result = {
                "returncode": process.returncode,
                "stdout": (stdout or "")[-2000:],
            }
        except subprocess.TimeoutExpired:
            if not allow_termination:
                raise FixtureFailure("baseline load did not finish after baseline task")
            process.terminate()
            stdout, _ = process.communicate(timeout=10)
            result = {
                "returncode": process.returncode,
                "terminated_after_window": True,
                "stdout": (stdout or "")[-2000:],
            }
        if not result.get("terminated_after_window") and result["returncode"] != 0:
            raise FixtureFailure(
                f"load process exited with unexpected return code {result['returncode']}"
            )
        return result

    def persist() -> None:
        atomic_write_json(manifest_path, manifest)

    def record_resource(phase: str) -> None:
        if target_pid is None:
            raise FixtureFailure("target PID is not initialized")
        observed_pid = host_pid(container)
        if observed_pid != target_pid:
            raise FixtureFailure(
                f"unexpected container restart: expected PID {target_pid}, observed {observed_pid}"
            )
        manifest["resource_observations"].append(
            {"phase": phase, "host_pid": observed_pid, **docker_stats(container)}
        )
        persist()

    def check_active_fixture() -> None:
        if fault_deadline is not None and time.monotonic() >= fault_deadline:
            raise FixtureFailure(
                f"bounded fault window exceeded {load_duration} seconds"
            )
        if target_pid is None or host_pid(container) != target_pid:
            raise FixtureFailure("ad container PID changed during diagnosis window")
        if load_process is None:
            raise FixtureFailure("load process was not started")
        returncode = load_process.poll()
        if returncode is not None:
            raise FixtureFailure(f"load process exited during diagnosis window: {returncode}")

    try:
        wait_container_healthy(container)
        target_pid = host_pid(container)
        host_id = resolve_agent_host(base_url, agent_id)
        manifest.update(
            {
                "host_id": host_id,
                "target_pid": target_pid,
                "target": f"127.0.0.1:{docker_host_port(container, 9555)}",
            }
        )
        if provenance_enabled:
            manifest["provenance"]["compose"] = compose_config_provenance(
                project_name=project_name,
                compose_files=compose_files,
                environment_file=environment_file,
            )
            initial = docker_container_provenance(container)
            manifest["provenance"]["containers"]["initial"] = initial
            if initial["host_pid"] != target_pid:
                raise FixtureFailure(
                    "docker provenance PID does not match current host PID: "
                    f"{initial['host_pid']} != {target_pid}"
                )
        persist()

        manifest["fault_reset_before_baseline"] = set_otel_feature_flag(
            "T1-CPU-001", otel_root=otel_root, enabled=False
        )
        persist()
        baseline_load_process = start_load(
            BASELINE_LOAD_DURATION_SECONDS, baseline_load_output
        )
        manifest["baseline_load"] = {
            "duration_seconds": BASELINE_LOAD_DURATION_SECONDS,
            "output": str(baseline_load_output.resolve()),
            "status": "RUNNING",
        }
        persist()
        time.sleep(4)
        if baseline_load_process.poll() is not None:
            raise FixtureFailure(
                "baseline load exited before baseline collection: "
                f"{baseline_load_process.returncode}"
            )
        record_resource("baseline_window_ready")

        baseline_created = api_json(
            base_url,
            "POST",
            "/api/tasks",
            {
                "name": f"benchmark baseline:{window_id}",
                "agent_id": agent_id,
                "target_pid": target_pid,
                "collector_type": "sys_metrics",
                "sample_rate": 10,
                "duration_sec": 10,
                "options": {
                    "benchmark_case_id": "T1-CPU-001",
                    "campaign_window_id": window_id,
                    "evidence_role": "baseline",
                },
            },
        )
        baseline_task_id = baseline_created["data"]["task_id"]
        manifest["baseline"] = {"task_id": baseline_task_id, "status": "PENDING"}
        persist()
        baseline_task = wait_task_terminal(base_url, baseline_task_id, timeout_seconds=90)
        if baseline_task.get("status") != "DONE":
            raise FixtureFailure(
                f"controlled baseline task failed: {baseline_task_id} "
                f"status={baseline_task.get('status')}"
            )
        manifest["baseline"] = {
            "task_id": baseline_task_id,
            "status": baseline_task.get("status"),
            "finished_at": baseline_task.get("finished_at"),
        }
        manifest["baseline_load"].update(
            {"status": "STOPPED", **stop_load(baseline_load_process, allow_termination=True)}
        )
        baseline_load_process = None
        persist()

        load_process = start_load(load_duration, load_output)
        manifest["incident_load"] = {
            "duration_seconds": load_duration,
            "output": str(load_output.resolve()),
            "status": "RUNNING",
        }
        incident_started_at = datetime.now(timezone.utc)
        fault_deadline = time.monotonic() + load_duration
        manifest["incident_started_at"] = incident_started_at.isoformat()
        manifest["fault_deadline_seconds"] = load_duration
        persist()
        time.sleep(4)
        check_active_fixture()
        manifest["fault_enable"] = set_otel_feature_flag(
            "T1-CPU-001", otel_root=otel_root, enabled=True
        )
        persist()
        time.sleep(4)
        check_active_fixture()
        record_resource("fault_window_ready")

        for strategy in STRATEGIES:
            check_active_fixture()
            record_resource(f"before_{strategy.lower()}")
            created = api_json(
                base_url,
                "POST",
                "/api/v1/diagnoses",
                create_payload(
                    strategy,
                    agent_id=agent_id,
                    host_id=host_id,
                    pid=target_pid,
                    baseline_task_id=baseline_task_id,
                    incident_started_at=incident_started_at,
                ),
            )
            diagnosis_id = created["data"]["diagnosis_id"]
            strategy_record = {
                "strategy": strategy,
                "diagnosis_id": diagnosis_id,
                "status": "RUNNING",
            }
            manifest["strategies"].append(strategy_record)
            persist()
            remaining = fault_deadline - time.monotonic()
            if remaining <= 0:
                raise FixtureFailure("bounded fault window expired before diagnosis")
            final = wait_for_terminal(
                base_url,
                diagnosis_id,
                approve_r2=approve_r2,
                timeout_seconds=min(float(diagnosis_timeout), remaining),
                fixture_check=check_active_fixture,
            )
            output_path = output_dir / f"{window_id}-{strategy}.json"
            atomic_write_json(output_path, final)
            detail = scoring_detail_from_api(final)
            detail["campaign_window"] = {
                "window_id": window_id,
                "target_pid": target_pid,
                "load_workers": load_workers,
                "raw_result": str(output_path.resolve()),
                "manifest": str(manifest_path.resolve()),
            }
            pending_submissions.append(
                {
                    "case_id": "T1-CPU-001",
                    "strategy": strategy,
                    "repetition": repetition,
                    "diagnosis_detail": detail,
                }
            )
            check_active_fixture()
            record_resource(f"after_{strategy.lower()}")
            strategy_record.update(
                {
                    "status": final["data"]["status"],
                    "output": str(output_path.resolve()),
                    "submission_staged": True,
                }
            )
            persist()
    except BaseException as exc:
        pending_error = exc
        manifest["fixture_failure"] = {
            "reason": "campaign_window_exception",
            "error": f"{type(exc).__name__}: {exc}",
        }
        persist()
    finally:
        cleanup: dict[str, Any] = {
            "flag_reset": None,
            "baseline_load_process": None,
            "load_process": None,
            "errors": [],
        }
        try:
            cleanup["flag_reset"] = set_otel_feature_flag(
                "T1-CPU-001", otel_root=otel_root, enabled=False
            )
        except Exception as exc:
            cleanup["errors"].append(f"flag_reset: {type(exc).__name__}: {exc}")
        for process_role, process, manifest_key in (
            ("baseline_load_process", baseline_load_process, "baseline_load"),
            ("load_process", load_process, "incident_load"),
        ):
            if process is None:
                continue
            try:
                process_result = stop_load(process, allow_termination=True)
                cleanup[process_role] = process_result
                if manifest_key in manifest:
                    manifest[manifest_key].update(
                        {"status": "STOPPED", **process_result}
                    )
            except Exception as exc:
                cleanup["errors"].append(
                    f"{process_role}_stop: {type(exc).__name__}: {exc}"
                )
                if manifest_key in manifest:
                    manifest[manifest_key]["status"] = "FAILED"
        try:
            if target_pid is not None:
                final_pid = host_pid(container)
                cleanup["final_host_pid"] = final_pid
                if final_pid != target_pid:
                    raise FixtureFailure(
                        f"cleanup PID mismatch: expected {target_pid}, observed {final_pid}"
                    )
                if provenance_enabled:
                    final_container = docker_container_provenance(container)
                    manifest["provenance"]["containers"]["final"] = final_container
                    if final_container["host_pid"] != final_pid:
                        raise FixtureFailure(
                            "final provenance PID does not match current host PID: "
                            f"{final_container['host_pid']} != {final_pid}"
                        )
        except Exception as exc:
            cleanup["errors"].append(f"container_check: {type(exc).__name__}: {exc}")

        fault_samples = [
            item
            for item in manifest.get("resource_observations", [])
            if item.get("phase") in FAULT_OBSERVATION_PHASES
            or str(item.get("phase", "")).startswith(FAULT_OBSERVATION_PREFIXES)
        ]
        cpu_values = [
            float(item.get("cpu_percent", 0) or 0) for item in fault_samples
        ]
        max_cpu = max(cpu_values, default=0.0)
        manifest["fault_observation"] = {
            "max_cpu_percent": max_cpu,
            "sample_count": len(cpu_values),
            "phases": [item.get("phase") for item in fault_samples],
            "observed": max_cpu >= FAULT_SIGNAL_CPU_PERCENT,
            "threshold_cpu_percent": FAULT_SIGNAL_CPU_PERCENT,
        }
        if not manifest["fault_observation"]["observed"]:
            cleanup["errors"].append("fault_signal: CPU threshold was not observed")
        manifest["cleanup"] = cleanup
        if cleanup["errors"]:
            manifest["fixture_failure"] = {
                "reason": "cleanup_or_verification_failed",
                "errors": cleanup["errors"],
                "prior_error": manifest.get("fixture_failure"),
            }
        manifest["ended_at"] = datetime.now(timezone.utc).isoformat()
        persist()

    window_valid = (
        pending_error is None
        and not manifest.get("fixture_failure")
        and len(pending_submissions) == len(STRATEGIES)
    )
    if window_valid:
        try:
            records = _publish_submissions(
                submissions,
                pending_submissions,
                overwrite=overwrite,
            )
            manifest["publication"] = {"published": True, "records": records}
            for strategy_record, publication in zip(manifest["strategies"], records):
                strategy_record["submission"] = publication
            persist()
        except Exception as exc:
            manifest["fixture_failure"] = {
                "reason": "submission_publication_failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
            persist()

    if pending_error is not None and isinstance(pending_error, (KeyboardInterrupt, SystemExit)):
        raise pending_error
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetition", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--otel-root", type=Path, default=DEFAULT_OTEL_ROOT)
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--agent-id", default="agent_local_demo")
    parser.add_argument("--container", default="ad")
    parser.add_argument("--load-duration", type=int, default=120)
    parser.add_argument("--load-workers", type=int, default=1)
    parser.add_argument("--diagnosis-timeout", type=int, default=35)
    parser.add_argument("--approve-r2", action="store_true")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace the same case/strategy/repetition after an invalidated run",
    )
    parser.add_argument("--project-name", default=None)
    parser.add_argument(
        "--compose-file",
        type=Path,
        action="append",
        dest="compose_files",
        help="exact Compose file used by the fixture; repeat for merged files",
    )
    parser.add_argument("--environment-file", type=Path, default=None)
    parser.add_argument(
        "--submissions",
        type=Path,
        default=ROOT / "reports" / "benchmark" / "submissions.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "benchmark" / "campaign-windows",
    )
    args = parser.parse_args()
    try:
        manifest = execute_campaign_window(
            repetition=args.repetition,
            otel_root=args.otel_root,
            base_url=args.base_url,
            agent_id=args.agent_id,
            load_duration=args.load_duration,
            load_workers=args.load_workers,
            diagnosis_timeout=args.diagnosis_timeout,
            approve_r2=args.approve_r2,
            overwrite=args.overwrite,
            submissions=args.submissions,
            output_dir=args.output_dir,
            container=args.container,
            project_name=args.project_name,
            compose_files=args.compose_files,
            environment_file=args.environment_file,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print((args.output_dir / f"{manifest['window_id']}-manifest.json").resolve())
    if manifest.get("fixture_failure"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
