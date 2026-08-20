"""Shared infrastructure helpers for bounded OTel Demo experiments."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any

from grpc_tools import protoc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OTEL_ROOT = ROOT / "external" / "opentelemetry-demo"


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically, including on Windows with active file scanners.

    A unique temporary file avoids multiple rapid campaign snapshots contending
    for the same ``.tmp`` path.  Windows antivirus/indexing can briefly retain a
    handle after close, so replacement is retried for a short bounded interval.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with open(descriptor, "w", encoding="utf-8", newline="\n", closefd=True) as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def run_command(*args: str) -> str:
    process = subprocess.run(
        list(args),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return process.stdout.strip()


def docker_host_port(container: str, port: int) -> int:
    output = run_command("docker", "port", container, f"{port}/tcp")
    first = output.splitlines()[0].strip()
    match = re.search(r":(\d+)$", first)
    if not match:
        raise RuntimeError(f"cannot parse docker port output: {output!r}")
    return int(match.group(1))


def docker_stats(container: str) -> dict[str, Any]:
    raw = run_command(
        "docker", "stats", container, "--no-stream", "--format", "{{json .}}"
    )
    payload = json.loads(raw)
    cpu_text = str(payload.get("CPUPerc", "0")).strip().rstrip("%")
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "cpu_percent": float(cpu_text or 0),
        "memory_usage": str(payload.get("MemUsage", "")),
        "raw": payload,
    }


def host_pid(container: str) -> int:
    return int(run_command("docker", "inspect", container, "--format", "{{.State.Pid}}"))


def docker_container_provenance(container: str) -> dict[str, Any]:
    """Capture immutable Docker identity and effective runtime limits."""

    containers = json.loads(run_command("docker", "inspect", container))
    if not isinstance(containers, list) or len(containers) != 1:
        shape = f"{len(containers)} records" if isinstance(containers, list) else type(containers).__name__
        raise RuntimeError(f"docker inspect returned {shape} for {container}")
    inspected = containers[0]
    if not isinstance(inspected, dict):
        raise RuntimeError(f"docker inspect returned a non-object record for {container}")
    image_id = str(inspected.get("Image", ""))
    if not image_id:
        raise RuntimeError(f"docker inspect returned no image ID for {container}")
    image_records = json.loads(run_command("docker", "image", "inspect", image_id))
    if (
        not isinstance(image_records, list)
        or len(image_records) != 1
        or not isinstance(image_records[0], dict)
    ):
        raise RuntimeError(f"docker image inspect returned invalid data for {image_id}")

    labels = inspected.get("Config", {}).get("Labels") or {}
    host_config = inspected.get("HostConfig") or {}
    log_config = host_config.get("LogConfig") or {}
    return {
        "container_id": inspected.get("Id"),
        "container_name": str(inspected.get("Name", "")).lstrip("/"),
        "host_pid": inspected.get("State", {}).get("Pid"),
        "image": {
            "configured_reference": inspected.get("Config", {}).get("Image"),
            "image_id": image_id,
            "repo_digests": image_records[0].get("RepoDigests") or [],
        },
        "compose": {
            "project": labels.get("com.docker.compose.project"),
            "service": labels.get("com.docker.compose.service"),
            "container_config_hash": labels.get("com.docker.compose.config-hash"),
        },
        "resource_limits": {
            "nano_cpus": host_config.get("NanoCpus"),
            "cpu_quota": host_config.get("CpuQuota"),
            "cpu_period": host_config.get("CpuPeriod"),
            "cpuset_cpus": host_config.get("CpusetCpus"),
            "memory_bytes": host_config.get("Memory"),
            "memory_swap_bytes": host_config.get("MemorySwap"),
            "pids_limit": host_config.get("PidsLimit"),
        },
        "logging": {
            "driver": log_config.get("Type"),
            "options": log_config.get("Config") or {},
        },
    }


def _compose_args(
    *, project_name: str, compose_files: list[Path], environment_file: Path | None
) -> list[str]:
    if not project_name.strip():
        raise ValueError("project_name must not be empty")
    if not compose_files:
        raise ValueError("compose_files must not be empty")
    args = ["docker", "compose"]
    for compose_file in compose_files:
        args.extend(("-f", str(compose_file.resolve())))
    if environment_file is not None:
        args.extend(("--env-file", str(environment_file.resolve())))
    args.extend(("--project-name", project_name))
    return args


def resolve_compose_service_container(
    *,
    project_name: str,
    compose_files: list[Path],
    service: str,
    environment_file: Path | None = None,
) -> str:
    """Resolve exactly one running container from an explicitly scoped Compose project."""

    if not service.strip():
        raise ValueError("service must not be empty")
    output = run_command(
        *_compose_args(
            project_name=project_name,
            compose_files=compose_files,
            environment_file=environment_file,
        ),
        "ps",
        "-q",
        "--status",
        "running",
        service,
    )
    container_ids = [line.strip() for line in output.splitlines() if line.strip()]
    if len(container_ids) != 1:
        raise RuntimeError(
            f"expected one running container for Compose service {service!r} "
            f"in project {project_name!r}, got {len(container_ids)}"
        )
    return container_ids[0]


def compose_config_provenance(
    *, project_name: str, compose_files: list[Path], environment_file: Path | None = None,
) -> dict[str, Any]:
    """Hash the exact normalized Compose configuration used by a fixture."""

    args = _compose_args(
        project_name=project_name,
        compose_files=compose_files,
        environment_file=environment_file,
    )
    args.append("config")
    normalized = run_command(*args)
    return {
        "project": project_name,
        "files": [str(path.resolve()) for path in compose_files],
        "environment_file": (
            str(environment_file.resolve()) if environment_file is not None else None
        ),
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def wait_container_healthy(container: str, *, timeout: float = 90) -> str:
    deadline = time.monotonic() + timeout
    status = "unknown"
    while time.monotonic() < deadline:
        status = run_command(
            "docker",
            "inspect",
            container,
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
        )
        if status in {"healthy", "running"}:
            return status
        time.sleep(2)
    raise TimeoutError(f"{container} did not become healthy: {status}")


def restart_and_wait_healthy(container: str, *, timeout: float = 90) -> dict[str, Any]:
    before_pid = host_pid(container)
    run_command("docker", "restart", container)
    status = wait_container_healthy(container, timeout=timeout)
    after_pid = host_pid(container)
    if after_pid == before_pid:
        raise RuntimeError(f"{container} restart did not change host PID {before_pid}")
    return {
        "action": "container_restart",
        "container": container,
        "before_pid": before_pid,
        "after_pid": after_pid,
        "status": status,
    }


def compile_proto(otel_root: Path, generated_dir: Path) -> tuple[Any, Any]:
    proto_file = otel_root / "pb" / "demo.proto"
    if not proto_file.exists():
        raise FileNotFoundError(proto_file)
    generated_dir.mkdir(parents=True, exist_ok=True)
    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{proto_file.parent}",
            f"--python_out={generated_dir}",
            f"--grpc_python_out={generated_dir}",
            str(proto_file),
        ]
    )
    if result != 0:
        raise RuntimeError(f"protoc failed with status {result}")
    sys.path.insert(0, str(generated_dir))
    return importlib.import_module("demo_pb2"), importlib.import_module("demo_pb2_grpc")


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    cpu = [float(item["cpu_percent"]) for item in samples]
    return {
        "sample_count": len(samples),
        "cpu_percent_min": min(cpu) if cpu else None,
        "cpu_percent_max": max(cpu) if cpu else None,
        "cpu_percent_mean": round(statistics.fmean(cpu), 3) if cpu else None,
        "samples": samples,
    }
