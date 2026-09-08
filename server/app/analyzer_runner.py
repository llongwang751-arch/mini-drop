"""Server-side analyzer fallback for raw perf artifacts."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from server.app import storage
from server.app.artifact_integrity import prepare_artifact
from server.app.logging_utils import log_event
from server.app.metric_analyzers import summarize_application_metric_window

ANALYZER_TIMEOUT_SEC = 180


class AnalyzerQualityError(ValueError):
    """A profiler command ran, but its output cannot support a visualization.

    The JSON string representation is persisted as the AnalysisJob error
    message.  This preserves a stable reason code and a human-actionable hint
    even though the worker's public error code remains ANALYSIS_INPUT_INVALID.
    """

    def __init__(
        self,
        reason_code: str,
        message: str,
        action_hint: str,
        *,
        details: dict | None = None,
    ) -> None:
        self.payload = {
            "error_code": "ANALYSIS_INPUT_INVALID",
            "failure_kind": "SAMPLE_QUALITY",
            "reason_code": reason_code,
            "message": message,
            "action_hint": action_hint,
            "details": details or {},
        }
        super().__init__(message)

    def __str__(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))


def _quality_failure_from_process(
    proc,
    *,
    extra_details: dict | None = None,
) -> AnalyzerQualityError | None:
    """Decode the analyzer CLI's structured sample-quality failure, if any."""

    stdout = (
        proc.stdout.decode("utf-8", errors="replace")
        if isinstance(proc.stdout, bytes)
        else str(proc.stdout or "")
    )
    try:
        payload = json.loads(stdout.strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("failure_kind") != "SAMPLE_QUALITY":
        return None
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    return AnalyzerQualityError(
        str(payload.get("reason_code") or "UNUSABLE_PROFILE"),
        str(payload.get("message") or "采样结果不足以生成火焰图"),
        str(payload.get("action_hint") or "确认目标进程和采样权限后重新采集。"),
        details={**details, **(extra_details or {})},
    )


def _process_error_tail(proc, limit: int = 300) -> str:
    """Return bounded diagnostics even when a CLI reports errors on stdout."""

    chunks = []
    for value in (getattr(proc, "stderr", b""), getattr(proc, "stdout", b"")):
        text = (
            value.decode("utf-8", errors="replace")
            if isinstance(value, bytes)
            else str(value or "")
        )
        if text.strip():
            chunks.append(text.strip())
    return " | ".join(chunks)[-limit:]


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
            quality_error = _quality_failure_from_process(proc)
            log_event(
                "warning",
                "analyzer_runner_nonzero",
                task_id=task_id,
                returncode=proc.returncode,
                reason_code=(
                    quality_error.payload["reason_code"] if quality_error else None
                ),
                stderr=proc.stderr.decode("utf-8", errors="replace")[-500:],
            )
            if quality_error is not None:
                raise quality_error
            return []

        output_dir = output_root / task_id
        outputs = _collect_analyzer_outputs(output_dir)
        if remote_input is not None:
            _upload_outputs(task_id, outputs, remote_input["bucket"])
        return outputs
    finally:
        if temporary_dir is not None:
            temporary_dir.cleanup()


def analyze_continuous_perf_bundle(
    task_id: str,
    artifacts: list[dict],
    *,
    allow_remote: bool = False,
) -> list[dict]:
    """Analyze every perf window in a bounded, path-safe tar bundle."""
    source = next(
        (item for item in artifacts if item.get("artifact_type") == "continuous_bundle"),
        None,
    )
    if source is None:
        return []
    temporary_dir = tempfile.TemporaryDirectory(prefix="mini-drop-continuous-")
    root = Path(temporary_dir.name)
    try:
        archive_path = _resolve_under_root(source.get("local_path"))
        remote = None
        if archive_path is None or not archive_path.is_file():
            bucket = source.get("bucket")
            object_key = source.get("object_key") or source.get("cos_key")
            if not (allow_remote and bucket and object_key):
                return []
            remote = (bucket, object_key)
            payload = storage.read_object_bytes(bucket, object_key)
            max_bytes = int(os.getenv("MINI_DROP_ANALYZER_MAX_INPUT_BYTES", str(2 * 1024**3)))
            if len(payload) > max_bytes:
                raise ValueError("Continuous bundle exceeds MINI_DROP_ANALYZER_MAX_INPUT_BYTES")
            archive_path = root / "continuous-perf.tar"
            archive_path.write_bytes(payload)

        windows_dir = root / "windows"
        windows_dir.mkdir()
        member_pattern = re.compile(r"^windows/window-(\d+)\.data$")
        window_paths: list[tuple[int, Path]] = []
        expanded = 0
        max_expanded = int(os.getenv("MINI_DROP_ANALYZER_MAX_INPUT_BYTES", str(2 * 1024**3)))
        with tarfile.open(archive_path, "r:*") as bundle:
            for member in bundle.getmembers():
                match = member_pattern.fullmatch(member.name)
                if not match:
                    continue
                if not member.isfile() or member.issym() or member.islnk():
                    raise ValueError("Continuous bundle contains a non-regular perf window")
                expanded += member.size
                if expanded > max_expanded or len(window_paths) >= 2000:
                    raise ValueError("Continuous bundle expansion limit exceeded")
                index = int(match.group(1))
                destination = windows_dir / f"window-{index}.data"
                stream = bundle.extractfile(member)
                if stream is None:
                    raise ValueError("Continuous bundle window cannot be read")
                with stream, destination.open("wb") as output:
                    while chunk := stream.read(1024 * 1024):
                        output.write(chunk)
                window_paths.append((index, destination))
        if not window_paths:
            raise ValueError("Continuous bundle contains no perf windows")

        output_root = root / "outputs" if remote is not None else _artifact_root()
        generated: list[dict] = []
        type_mapping = {
            "flamegraph_json": "continuous_flamegraph_json",
            "flamegraph_svg": "continuous_flamegraph_svg",
            "top_json": "continuous_top_json",
            "callgraph_json": "continuous_callgraph_json",
        }
        for index, perf_path in sorted(window_paths):
            window_task_id = f"{task_id}-window-{index}"
            cmd = [
                sys.executable, "-m", "analyzer.mini_drop_analyzer.hotmethod_analyzer",
                "--task-id", window_task_id, "--perf-data", str(perf_path),
                "--output-dir", str(output_root),
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=ANALYZER_TIMEOUT_SEC)
            if proc.returncode != 0:
                quality_error = _quality_failure_from_process(
                    proc,
                    extra_details={"window_index": index},
                )
                if quality_error is not None:
                    raise quality_error
                raise RuntimeError(
                    f"continuous window {index} analyzer failed: "
                    + _process_error_tail(proc)
                )
            for artifact in _collect_analyzer_outputs(output_root / window_task_id):
                mapped = type_mapping.get(artifact["artifact_type"])
                if not mapped:
                    continue
                artifact["artifact_type"] = mapped
                artifact["filename"] = f"window-{index}-{artifact['filename']}"
                artifact["metadata"] = {
                    **(artifact.get("metadata") or {}),
                    "schema_version": "continuous_perf_analysis.v2",
                    "window_index": index,
                }
                generated.append(artifact)
        if remote is not None:
            _upload_outputs(task_id, generated, remote[0])
        return generated
    finally:
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


def analyze_java_flamegraph_artifacts(
    artifacts: list[dict],
    *,
    allow_remote: bool = False,
) -> dict[int, dict]:
    """Extract sample and frame metadata from async-profiler HTML safely.

    The self-contained HTML stores a prefix-compressed constant pool followed
    by ``f/u/n`` frame calls.  Decode only that bounded data section; no
    JavaScript is executed.
    """

    artifact = next(
        (
            item
            for item in artifacts
            if item.get("artifact_type") == "java_flamegraph_html"
            and item.get("id") is not None
        ),
        None,
    )
    if artifact is None:
        return {}
    payload = _read_java_artifact_bytes(artifact, allow_remote=allow_remote)
    if payload is None:
        return {}
    max_bytes = min(
        64 * 1024 * 1024,
        int(os.getenv("MINI_DROP_ANALYZER_MAX_INPUT_BYTES", str(2 * 1024**3))),
    )
    if not payload or len(payload) > max_bytes:
        raise ValueError("Java flame graph is empty or exceeds the analyzer limit")
    text = payload.decode("utf-8", errors="replace")
    sample_count, top_functions = _parse_async_profiler_html(text)
    if sample_count <= 0 or not top_functions:
        raise AnalyzerQualityError(
            "NO_PROFILE_SAMPLES",
            "Java 火焰图不含可核验的样本或函数帧",
            "确认 async-profiler 采集事件与目标负载匹配后重新采集。",
            details={"profile_kind": "java_async", "sample_count": sample_count},
        )
    counter_artifact = next(
        (
            item
            for item in artifacts
            if item.get("artifact_type") == "jvm_gc_metrics"
            and item.get("id") is not None
        ),
        None,
    )
    counter_metadata = None
    if counter_artifact is not None:
        counter_payload = _read_java_artifact_bytes(
            counter_artifact,
            allow_remote=allow_remote,
        )
        if counter_payload is not None:
            counter_metadata = _parse_jvm_counter_window(counter_payload)

    metadata = dict(artifact.get("metadata") or {})
    metadata.update(
        {
            "schema_version": "java_async_profile.v1",
            "sample_count": sample_count,
            "top_functions": top_functions,
            "profile_event": _async_profiler_event(text),
            "profile_quality": {
                "status": "USABLE",
                "reason_code": "OK",
                "sample_count": sample_count,
                "renderable_function_count": len(top_functions),
            },
        }
    )
    updates = {int(artifact["id"]): metadata}
    if counter_metadata is not None and counter_artifact is not None:
        metadata["jvm_gc_counters"] = counter_metadata
        updates[int(counter_artifact["id"])] = {
            **dict(counter_artifact.get("metadata") or {}),
            **counter_metadata,
        }
    return updates


def _read_java_artifact_bytes(
    artifact: dict,
    *,
    allow_remote: bool,
) -> bytes | None:
    local_path = _resolve_under_root(artifact.get("local_path"))
    if local_path is not None and local_path.is_file():
        return local_path.read_bytes()
    bucket = artifact.get("bucket")
    object_key = artifact.get("object_key") or artifact.get("cos_key")
    if not (allow_remote and bucket and object_key):
        return None
    return storage.read_object_bytes(bucket, object_key)


def _parse_jvm_counter_window(payload: bytes) -> dict:
    if not payload or len(payload) > 1024 * 1024:
        raise ValueError("JVM counter window is empty or exceeds the analyzer limit")
    document = json.loads(payload.decode("utf-8"))
    if document.get("schema_version") != "jvm_gc_metrics.v1":
        raise ValueError("unsupported JVM counter schema")
    before = document.get("before")
    after = document.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("JVM counter window requires before and after snapshots")
    identity_fields = ("pid", "host_pid", "runtime")
    if any(before.get(field) != after.get(field) for field in identity_fields):
        raise ValueError("JVM counter snapshots do not belong to the same process")
    fields = ("allocated_bytes", "gc_count", "gc_time_ms", "heap_used_bytes")
    normalized_before: dict[str, int] = {}
    normalized_after: dict[str, int] = {}
    deltas: dict[str, int] = {}
    for field in fields:
        try:
            left = int(before.get(field))
            right = int(after.get(field))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"JVM counter field is invalid: {field}") from exc
        if left < 0 or right < 0:
            raise ValueError(f"JVM counter field must be non-negative: {field}")
        normalized_before[field] = left
        normalized_after[field] = right
        deltas[field] = right - left
    if any(deltas[field] < 0 for field in ("allocated_bytes", "gc_count", "gc_time_ms")):
        raise ValueError("JVM cumulative counters moved backwards")
    before_ms = int(document.get("before_captured_at_unix_ms") or 0)
    after_ms = int(document.get("after_captured_at_unix_ms") or 0)
    if before_ms <= 0 or after_ms <= before_ms:
        raise ValueError("JVM counter timestamps are invalid")
    application_metrics, signals = summarize_application_metric_window(
        before,
        after,
    )
    return {
        "schema_version": "jvm_gc_metrics.v1",
        "sample_count": 2,
        "profile_event": str(document.get("event") or "unknown"),
        "process_identity": {
            "runtime": before.get("runtime"),
            "pid": before.get("pid"),
            "host_pid": before.get("host_pid"),
        },
        "before": normalized_before,
        "after": normalized_after,
        "delta": deltas,
        "application_metrics": application_metrics,
        "signals": signals,
        "window_duration_ms": after_ms - before_ms,
        "gc_activity_observed": deltas["gc_count"] > 0 or deltas["gc_time_ms"] > 0,
        "allocation_activity_observed": deltas["allocated_bytes"] > 0,
        "profile_quality": {
            "status": "USABLE",
            "reason_code": "OK",
            "sample_count": 2,
        },
    }


def _async_profiler_event(text: str) -> str:
    headings = re.findall(
        r"<(?:title|h1)\b[^>]*>\s*([^<]+?)\s*</(?:title|h1)>",
        text,
        re.IGNORECASE,
    )
    label = " ".join(headings).casefold()
    aliases = (
        ("alloc", ("alloc", "allocation")),
        ("lock", ("lock", "contention")),
        ("wall", ("wall", "wall clock")),
        ("cpu", ("cpu",)),
    )
    for event, tokens in aliases:
        if any(token in label for token in tokens):
            return event
    return "unknown"


def _parse_async_profiler_html(text: str) -> tuple[int, list[dict]]:
    """Decode async-profiler's constant pool and frame calls."""

    pool_match = re.search(
        r"const\s+cpool\s*=\s*\[(?P<body>.*?)\];\s*unpack\(cpool\);",
        text,
        re.DOTALL,
    )
    if pool_match is None:
        return 0, []
    encoded_pool: list[str] = []
    for match in re.finditer(r"'(?P<value>(?:\\.|[^'\\])*)'", pool_match.group("body")):
        try:
            decoded = ast.literal_eval("'" + match.group("value") + "'")
        except (SyntaxError, ValueError):
            return 0, []
        if not isinstance(decoded, str):
            return 0, []
        encoded_pool.append(decoded)
    if not encoded_pool:
        return 0, []
    pool = [encoded_pool[0]]
    for encoded in encoded_pool[1:]:
        if not encoded:
            return 0, []
        prefix_length = ord(encoded[0]) - 32
        if prefix_length < 0 or prefix_length > len(pool[-1]):
            return 0, []
        pool.append(pool[-1][:prefix_length] + encoded[1:])

    level0 = 0
    left0 = 0
    width0 = 0
    root_width = 0
    widths_by_name: dict[str, int] = {}
    for match in re.finditer(
        r"(?m)^\s*([fun])\(([^)]*)\)", text[pool_match.end() :]
    ):
        call = match.group(1)
        raw_args = [part.strip() for part in match.group(2).split(",")]
        try:
            values = [int(value) for value in raw_args if value]
        except ValueError:
            continue
        if not values:
            continue
        key = values[0]
        if call == "f":
            if len(values) < 3:
                continue
            level = values[1]
            left = values[2]
            width = values[3] if len(values) >= 4 else 0
        elif call == "u":
            level = level0 + 1
            left = 0
            width = values[1] if len(values) >= 2 else 0
        else:
            level = level0
            left = width0
            width = values[1] if len(values) >= 2 else 0
        level0 = level
        left0 += left
        width0 = width or width0
        pool_index = key >> 3
        if width0 <= 0 or pool_index < 0 or pool_index >= len(pool):
            continue
        if root_width == 0 and level0 == 0:
            root_width = width0
        name = pool[pool_index].strip()
        if not name or name.casefold() == "all":
            continue
        widths_by_name[name] = max(widths_by_name.get(name, 0), width0)
    if root_width <= 0:
        return 0, []
    top_functions = [
        {
            "name": name[:512],
            "samples": width,
            "percent": round(min(100.0, 100.0 * width / root_width), 2),
        }
        for name, width in sorted(
            widths_by_name.items(), key=lambda item: (-item[1], item[0])
        )[:20]
    ]
    return root_width, top_functions


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
            quality_error = _quality_failure_from_process(proc)
            log_event(
                "warning",
                "analyzer_runner_nonzero",
                task_id=task_id,
                module=module,
                returncode=proc.returncode,
                stderr=proc.stderr.decode("utf-8", errors="replace")[-500:],
            )
            if quality_error is not None:
                raise quality_error
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
    sample_count, top_functions, profile_quality = _validated_profile_output(
        output_dir,
        profile_kind=schema_version,
    )
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
                "profile_quality": profile_quality,
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
        "callgraph_json": ("callgraph.json", "application/json"),
        "suggestions_md": ("suggestions.md", "text/markdown"),
    }
    sample_count, top_functions, profile_quality = _validated_profile_output(
        output_dir,
        profile_kind="perf_cpu",
    )
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
                "profile_quality": profile_quality,
            },
        })
    return artifacts


def _validated_profile_output(
    output_dir: Path,
    *,
    profile_kind: str,
) -> tuple[int, list[dict], dict]:
    """Fail closed before empty visualization artifacts enter persistence."""

    sample_count = _read_perf_sample_count(output_dir)
    top_functions = _read_top_functions(output_dir)
    if sample_count <= 0:
        raise AnalyzerQualityError(
            "NO_PROFILE_SAMPLES",
            "Analyzer 输出不含任何可渲染样本，不能登记为空火焰图",
            "确认目标进程在采样窗口内有活动，并检查采样权限、采集时长和调用栈模式后重新采集。",
            details={"profile_kind": profile_kind, "sample_count": sample_count},
        )
    if not top_functions:
        raise AnalyzerQualityError(
            "NO_RENDERABLE_STACKS",
            "Analyzer 记录到了样本，但没有可渲染的函数调用栈",
            "检查符号、帧指针或 unwind 配置；确保采集器输出包含调用栈后重新采集。",
            details={"profile_kind": profile_kind, "sample_count": sample_count},
        )
    return sample_count, top_functions, {
        "status": "USABLE",
        "reason_code": "OK",
        "sample_count": sample_count,
        "renderable_function_count": len(top_functions),
    }


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
        self_samples_by_name: dict[str, int] = {}
        callgraph_total = 0
        callgraph_path = output_dir / "callgraph.json"
        if callgraph_path.is_file():
            callgraph = json.loads(callgraph_path.read_text(encoding="utf-8"))
            if isinstance(callgraph, dict):
                callgraph_total = max(0, int(callgraph.get("total_samples") or 0))
                for node in callgraph.get("nodes") or []:
                    if not isinstance(node, dict) or not isinstance(node.get("name"), str):
                        continue
                    self_samples_by_name[node["name"]] = max(
                        0,
                        int(node.get("self_samples") or 0),
                    )

        result = []
        for row in payload[:20]:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                continue
            normalized = {
                "name": row["name"][:512],
                "samples": max(0, int(row.get("samples") or 0)),
                "percent": max(0.0, min(100.0, float(row.get("percent") or 0))),
            }
            if isinstance(row.get("file"), str) and row["file"]:
                normalized["file"] = row["file"][:1024]
            try:
                if int(row.get("line") or 0) > 0:
                    normalized["line"] = int(row["line"])
            except (TypeError, ValueError):
                pass
            if callgraph_total > 0 and row["name"] in self_samples_by_name:
                self_samples = self_samples_by_name[row["name"]]
                normalized["self_samples"] = self_samples
                normalized["self_percent"] = round(
                    self_samples / callgraph_total * 100,
                    2,
                )
            result.append(normalized)
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
