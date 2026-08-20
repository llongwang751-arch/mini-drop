"""Server-side analyzer fallback for raw perf artifacts."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from server.app.logging_utils import log_event
from server.app import storage
from server.app.artifact_integrity import prepare_artifact

ANALYZER_TIMEOUT_SEC = 180


def analyze_raw_perf_artifacts(
    task_id: str,
    artifacts: list[dict],
    *,
    allow_remote: bool = False,
) -> list[dict]:
    raw_path = _find_local_raw_perf_path(artifacts)
    remote_input = None
    temporary_dir = None
    if raw_path is None and allow_remote:
        remote_input = _find_remote_raw_perf(artifacts)
        if remote_input is None:
            return []
        temporary_dir = tempfile.TemporaryDirectory(prefix="mini-drop-analysis-")
        raw_path = Path(temporary_dir.name) / "perf.data"
        payload = storage.read_object_bytes(
            remote_input["bucket"], remote_input["object_key"]
        )
        max_bytes = int(os.getenv("MINI_DROP_ANALYZER_MAX_INPUT_BYTES", str(2 * 1024**3)))
        if len(payload) > max_bytes:
            temporary_dir.cleanup()
            raise ValueError("Analyzer input exceeds MINI_DROP_ANALYZER_MAX_INPUT_BYTES")
        raw_path.write_bytes(payload)
    if raw_path is None:
        return []

    output_root = (
        Path(temporary_dir.name) / "outputs"
        if temporary_dir is not None
        else _artifact_root()
    )
    cmd = [
        sys.executable,
        "-m",
        "analyzer.mini_drop_analyzer.hotmethod_analyzer",
        "--task-id",
        task_id,
        "--perf-data",
        str(raw_path),
        "--output-dir",
        str(output_root),
    ]
    try:
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=ANALYZER_TIMEOUT_SEC)
        except (subprocess.SubprocessError, OSError) as exc:
            log_event("warning", "analyzer_runner_failed", error=str(exc)[:200])
            return []
        if proc.returncode != 0:
            log_event(
                "warning",
                "analyzer_runner_nonzero",
                task_id=task_id,
                returncode=proc.returncode,
                stderr=proc.stderr.decode("utf-8", errors="replace")[-500:],
            )
            return []

        output_dir = output_root / task_id
        outputs = _collect_analyzer_outputs(output_dir)
        if remote_input is not None:
            _upload_outputs(task_id, outputs, remote_input["bucket"])
        return outputs
    finally:
        if temporary_dir is not None:
            temporary_dir.cleanup()


def _find_local_raw_perf_path(artifacts: list[dict]) -> Path | None:
    for artifact in artifacts:
        if artifact.get("artifact_type") not in {"raw", "continuous_raw"}:
            continue
        local_path = artifact.get("local_path")
        filename = artifact.get("filename") or ""
        if filename and filename not in {"perf.data", "continuous-perf.data"}:
            continue
        path = _resolve_under_root(local_path)
        if path and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _find_remote_raw_perf(artifacts: list[dict]) -> dict | None:
    for artifact in artifacts:
        if artifact.get("artifact_type") not in {"raw", "continuous_raw"}:
            continue
        filename = artifact.get("filename") or ""
        if filename and filename not in {"perf.data", "continuous-perf.data"}:
            continue
        bucket = artifact.get("bucket")
        object_key = artifact.get("object_key") or artifact.get("cos_key")
        if bucket and object_key:
            return {"bucket": bucket, "object_key": object_key}
    return None


def analyze_pprof_artifacts(
    task_id: str,
    artifacts: list[dict],
    *,
    allow_remote: bool = False,
) -> list[dict]:
    """Run the Go pprof analyzer over a raw profile.pb.gz / go-cpu.pprof."""
    return _run_artifact_analyzer(
        task_id,
        artifacts,
        allow_remote=allow_remote,
        find_input=_find_pprof_input,
        module="analyzer.mini_drop_analyzer.pprof_analyzer",
        input_flag="--profile",
        schema_version="go_pprof_analysis.v1",
    )


def analyze_speedscope_artifacts(
    task_id: str,
    artifacts: list[dict],
    *,
    allow_remote: bool = False,
) -> list[dict]:
    """Run the py-spy speedscope analyzer over a speedscope JSON file."""
    return _run_artifact_analyzer(
        task_id,
        artifacts,
        allow_remote=allow_remote,
        find_input=_find_speedscope_input,
        module="analyzer.mini_drop_analyzer.pyspy_analyzer",
        input_flag="--speedscope",
        schema_version="pyspy_analysis.v1",
    )


def _run_artifact_analyzer(
    task_id: str,
    artifacts: list[dict],
    *,
    allow_remote: bool,
    find_input,
    module: str,
    input_flag: str,
    schema_version: str,
) -> list[dict]:
    """Generic runner for CLI analyzers that emit top.json + flamegraph.json."""
    input_info = find_input(artifacts)
    if input_info is None:
        return []
    local_path = input_info.get("local_path")
    remote = input_info.get("remote")
    temporary_dir = None
    if local_path is None:
        if not (allow_remote and remote):
            return []
        temporary_dir = tempfile.TemporaryDirectory(prefix="mini-drop-analysis-")
        bucket, object_key = remote
        payload = storage.read_object_bytes(bucket, object_key)
        max_bytes = int(os.getenv("MINI_DROP_ANALYZER_MAX_INPUT_BYTES", str(2 * 1024**3)))
        if len(payload) > max_bytes:
            temporary_dir.cleanup()
            raise ValueError("Analyzer input exceeds MINI_DROP_ANALYZER_MAX_INPUT_BYTES")
        input_path = Path(temporary_dir.name) / (input_info.get("filename") or "input")
        input_path.write_bytes(payload)
        local_path = str(input_path)

    output_root = (
        Path(temporary_dir.name) / "outputs"
        if temporary_dir is not None
        else _artifact_root()
    )
    cmd = [
        sys.executable,
        "-m",
        module,
        "--task-id",
        task_id,
        input_flag,
        str(local_path),
        "--output-dir",
        str(output_root),
    ]
    try:
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=ANALYZER_TIMEOUT_SEC)
        except (subprocess.SubprocessError, OSError) as exc:
            log_event("warning", "analyzer_runner_failed", error=str(exc)[:200])
            return []
        if proc.returncode != 0:
            log_event(
                "warning",
                "analyzer_runner_nonzero",
                task_id=task_id,
                module=module,
                returncode=proc.returncode,
                stderr=proc.stderr.decode("utf-8", errors="replace")[-500:],
            )
            return []

        output_dir = output_root / task_id
        outputs = _collect_generic_analyzer_outputs(output_dir, schema_version)
        if temporary_dir is not None:
            _upload_outputs(task_id, outputs, remote[0])
        return outputs
    finally:
        if temporary_dir is not None:
            temporary_dir.cleanup()


def _find_pprof_input(artifacts: list[dict]) -> dict | None:
    for artifact in artifacts:
        filename = artifact.get("filename") or ""
        artifact_type = artifact.get("artifact_type") or ""
        lowered = filename.lower()
        is_pprof = (
            artifact_type == "pprof_raw"
            or lowered.endswith(".pb.gz")
            or lowered.endswith(".pprof")
            or lowered.endswith(".prof")
        )
        if not is_pprof:
            continue
        return _resolve_input(artifact)
    return None


def _find_speedscope_input(artifacts: list[dict]) -> dict | None:
    for artifact in artifacts:
        filename = artifact.get("filename") or ""
        if "speedscope" not in filename.lower():
            continue
        return _resolve_input(artifact)
    return None


def _resolve_input(artifact: dict) -> dict | None:
    filename = artifact.get("filename") or "input"
    info: dict = {"filename": filename}
    local_path = _resolve_under_root(artifact.get("local_path"))
    if local_path is not None and local_path.is_file() and local_path.stat().st_size > 0:
        info["local_path"] = str(local_path)
        return info
    bucket = artifact.get("bucket")
    object_key = artifact.get("object_key") or artifact.get("cos_key")
    if bucket and object_key:
        info["remote"] = (bucket, object_key)
        return info
    return None


def _collect_generic_analyzer_outputs(output_dir: Path, schema_version: str) -> list[dict]:
    outputs = {
        "flamegraph_json": ("flamegraph.json", "application/json"),
        "top_json": ("top.json", "application/json"),
    }
    sample_count = _read_perf_sample_count(output_dir)
    top_functions = _read_top_functions(output_dir)
    artifacts: list[dict] = []
    for artifact_type, (filename, content_type) in outputs.items():
        path = output_dir / filename
        if not path.is_file():
            continue
        artifacts.append({
            "artifact_type": artifact_type,
            "filename": filename,
            "local_path": str(path),
            "content_type": content_type,
            "size_bytes": path.stat().st_size,
            "metadata": {
                "schema_version": schema_version,
                "sample_count": sample_count,
                "top_functions": top_functions,
            },
        })
    return artifacts


def _upload_outputs(task_id: str, artifacts: list[dict], bucket: str) -> None:
    for artifact in artifacts:
        local_path = artifact.get("local_path")
        filename = artifact.get("filename")
        if not local_path or not filename:
            continue
        object_key = f"tasks/{task_id}/analysis/{filename}"
        storage.upload_file(
            local_path,
            bucket,
            object_key,
            artifact.get("content_type", "application/octet-stream"),
        )
        artifact["bucket"] = bucket
        artifact["object_key"] = object_key
        # The temporary analyzer directory is removed before the repository
        # transaction starts. Bind the uploaded object to the exact local bytes
        # now, then persist only the immutable object-store reference.
        verified = prepare_artifact(task_id, artifact)
        artifact.clear()
        artifact.update(verified)
        artifact["local_path"] = None


def _collect_analyzer_outputs(output_dir: Path) -> list[dict]:
    outputs = {
        "flamegraph_json": ("flamegraph.json", "application/json"),
        "flamegraph_svg": ("flamegraph.svg", "image/svg+xml"),
        "top_json": ("top.json", "application/json"),
        "suggestions_md": ("suggestions.md", "text/markdown"),
    }
    sample_count = _read_perf_sample_count(output_dir)
    top_functions = _read_top_functions(output_dir)
    artifacts: list[dict] = []
    for artifact_type, (filename, content_type) in outputs.items():
        path = output_dir / filename
        if not path.is_file():
            continue
        artifacts.append({
            "artifact_type": artifact_type,
            "filename": filename,
            "local_path": str(path),
            "content_type": content_type,
            "size_bytes": path.stat().st_size,
            "metadata": {
                "schema_version": "perf_analysis.v1",
                "sample_count": sample_count,
                "top_functions": top_functions,
            },
        })
    return artifacts


def _read_top_functions(output_dir: Path) -> list[dict]:
    """Read the Analyzer-produced TopN predicate input conservatively."""

    path = output_dir / "top.json"
    if not path.is_file():
        return []
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        result = []
        for row in payload[:20]:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                continue
            result.append({
                "name": row["name"][:512],
                "samples": max(0, int(row.get("samples") or 0)),
                "percent": max(0.0, min(100.0, float(row.get("percent") or 0))),
            })
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []


def _read_perf_sample_count(output_dir: Path) -> int:
    """Read the root value produced by the flame-tree parser."""

    path = output_dir / "flamegraph.json"
    if not path.is_file():
        return 0
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("value", 0) if isinstance(payload, dict) else 0
        return max(0, int(value))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _artifact_root() -> Path:
    return Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).expanduser().resolve()


def _resolve_under_root(local_path: str | None) -> Path | None:
    if not local_path:
        return None
    root = _artifact_root()
    candidate = Path(local_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved
