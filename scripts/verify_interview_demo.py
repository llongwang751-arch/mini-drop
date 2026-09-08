"""Verify a live, repeatable Fault Plaza interview demonstration.

The script runs two isolated replays of a supported allow-listed fault.  The
first diagnosis disables Skill retrieval and the second enables it.  Both runs
must create fresh server-issued diagnosis/task/attempt/artifact/evidence/report
lineage.  The AUTO arm must produce a real runtime profile and the scenario's
expected source-level hotspot; a DISABLED miss remains a measured A/B outcome
instead of aborting the comparison before it can be reported.

The API key is read only from an environment variable.  It is never accepted
as a command-line value, printed, or persisted in the JSON report.  Every
started fault is stopped in ``finally`` so Ctrl-C and failed assertions do not
leave the demo target busy (the target also has its own auto-stop deadline).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_DIAGNOSIS = {
    "COMPLETED",
    "INSUFFICIENT_EVIDENCE",
    "FAILED",
    "CANCELLED",
}
TERMINAL_TASK = {"DONE", "FAILED", "CANCELLED"}
EXPECTED_SCENARIO = "source-hotspot"
EXPECTED_HOT_FUNCTION = "source_hot_function"
EXPECTED_SKILL_FAMILY = "repository:python-runtime-diagnosis"

SCENARIO_ACCEPTANCE_PROFILES: dict[str, dict[str, Any]] = {
    "source-hotspot": {
        "process_names": ("python-hotspot",),
        "collector_type": "pyspy",
        "tool_name": "start_pyspy_profile",
        "expected_hot_function": EXPECTED_HOT_FUNCTION,
        "expected_skill_family": EXPECTED_SKILL_FAMILY,
    },
    "go-cpu-hotspot": {
        "process_names": ("go-hotspot",),
        "collector_type": "go_pprof",
        "tool_name": "collect_go_profile",
        "expected_hot_function": "goCPUHotFunction",
        "expected_skill_family": "repository:go-runtime-diagnosis",
    },
    "cpp-cpu-hotspot": {
        "process_names": ("cpp-hotspot",),
        "collector_type": "perf_cpu",
        "tool_name": "start_perf_profile",
        "expected_hot_function": "cpp_cpu_hot_function",
        "expected_skill_family": "repository:cpp-runtime-diagnosis",
    },
    "java-gc-pressure": {
        "process_names": ("java", "java-hotspot"),
        "collector_type": "java_async",
        # The GC Skill deliberately starts with a low-risk system baseline;
        # the JVM flame graph is a later, independently required probe.
        "tool_name": "collect_sys_metrics",
        "expected_hot_function": "Hotspot",
        "expected_skill_family": "repository:gc-pressure-diagnosis",
        "profile_validation": "java_gc",
        # Both arms may legitimately start with the same low-risk host
        # baseline.  The Java experiment proves treatment by route divergence,
        # JVM-supporting Evidence and the terminal outcome, not by forcing a
        # riskier profiler to run first merely for presentation.
        "requires_distinct_first_tool": False,
    },
}


class AcceptanceError(RuntimeError):
    """A failed live acceptance invariant."""


class Client:
    """Small authenticated JSON client with no credential-bearing repr/output."""

    def __init__(self, base_url: str, api_key: str, insecure: bool = False):
        self.base = base_url.rstrip("/")
        self._key = api_key
        self._context = (
            ssl._create_unverified_context()  # noqa: SLF001 - explicit CLI opt-in
            if insecure
            else ssl.create_default_context()
        )
        self._last_headers: dict[str, str] = {}

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int = 90,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={
                "X-API-Key": self._key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=self._context,
            ) as response:
                self._last_headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            raise AcceptanceError(
                f"{method} {path}: HTTP {error.code}: {detail}"
            ) from error
        except urllib.error.URLError as error:
            raise AcceptanceError(f"{method} {path}: {error.reason}") from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise AcceptanceError(f"{method} {path}: response is not JSON") from error
        if isinstance(value, dict) and "code" in value:
            if value.get("code") != 0:
                raise AcceptanceError(
                    f"{method} {path}: {value.get('message') or 'API request failed'}"
                )
            return value.get("data")
        return value

    def last_header(self, name: str) -> str:
        return self._last_headers.get(name.lower(), "")

    def request_raw(self, method: str, path: str, *, timeout: int = 90) -> bytes:
        """Read an authenticated non-JSON artifact without logging credentials."""

        request = urllib.request.Request(
            self.base + path,
            method=method,
            headers={"X-API-Key": self._key},
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=self._context,
            ) as response:
                self._last_headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            raise AcceptanceError(
                f"{method} {path}: HTTP {error.code}: {detail}"
            ) from error
        except urllib.error.URLError as error:
            raise AcceptanceError(f"{method} {path}: {error.reason}") from error


def items_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        items = value.get("items") or value.get("data") or []
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def _report_execution_round_indexes(
    reports: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[int]:
    """Return real LATS iterations that reached a persisted Report.

    A hypothesis keeps the tree depth at which it was created in
    ``round_index``. LATS may later backtrack to an older, unvisited sibling,
    so that birth depth is not necessarily the round in which the hypothesis
    was selected and reported. The durable ``lats.node_selected.iteration``
    is authoritative for live acceptance. Legacy/non-LATS records without a
    matching selection event fall back to the hypothesis birth round.
    """

    birth_round_by_hypothesis: dict[str, int] = {}
    for hypothesis in hypotheses:
        hypothesis_id = str(
            hypothesis.get("hypothesis_id") or hypothesis.get("id") or ""
        )
        if not hypothesis_id:
            continue
        try:
            round_index = int(hypothesis.get("round_index") or 1)
        except (TypeError, ValueError):
            round_index = 1
        birth_round_by_hypothesis[hypothesis_id] = max(1, round_index)

    selection_iteration_by_hypothesis: dict[str, int] = {}
    for event in events:
        if event.get("event_type") != "lats.node_selected":
            continue
        payload = event.get("payload") or event.get("payload_json") or {}
        if not isinstance(payload, dict):
            continue
        node_id = str(payload.get("node_id") or "")
        hypothesis_id = str(payload.get("hypothesis_id") or "")
        if node_id.startswith("hypothesis:"):
            hypothesis_id = node_id.removeprefix("hypothesis:")
        elif not hypothesis_id and node_id in birth_round_by_hypothesis:
            hypothesis_id = node_id
        if not hypothesis_id:
            continue
        try:
            iteration = int(payload.get("iteration"))
        except (TypeError, ValueError):
            continue
        if iteration > 0:
            selection_iteration_by_hypothesis.setdefault(hypothesis_id, iteration)

    round_indexes: set[int] = set()
    for report in reports:
        hypothesis_id = str(report.get("hypothesis_id") or "")
        if not hypothesis_id:
            continue
        round_index = selection_iteration_by_hypothesis.get(
            hypothesis_id,
            birth_round_by_hypothesis.get(hypothesis_id),
        )
        if round_index is not None:
            round_indexes.add(round_index)
    return sorted(round_indexes)


def _scenario(plaza: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    return next(
        (
            item
            for item in items_of(plaza.get("scenarios"))
            if item.get("scenario_id") == scenario_id
        ),
        {},
    )


def _get_plaza(client: Client) -> dict[str, Any]:
    plaza = client.request("GET", "/api/v2/showcases/fault-plaza") or {}
    _require(isinstance(plaza, dict), "Fault Plaza response is not an object")
    _require(
        client.last_header("X-Mini-Drop-AI-Transport").lower() == "grpc",
        "Fault Plaza did not traverse the Go-to-diagnosis-worker gRPC boundary",
    )
    return plaza


def _stop_fault(client: Client, scenario_id: str, *, verify: bool = True) -> None:
    # ``stop`` explicitly requires an empty body, so payload stays None.
    client.request(
        "POST",
        f"/api/v2/showcases/fault-plaza/{urllib.parse.quote(scenario_id, safe='')}/stop",
    )
    if not verify:
        return
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        current = _scenario(_get_plaza(client), scenario_id)
        if current and not current.get("active"):
            return
        time.sleep(0.5)
    raise AcceptanceError(f"fault scenario {scenario_id} remained active after stop")


def _start_fault(
    client: Client,
    scenario_id: str,
    duration_seconds: int,
) -> dict[str, Any]:
    started = client.request(
        "POST",
        f"/api/v2/showcases/fault-plaza/{urllib.parse.quote(scenario_id, safe='')}/start",
        {"duration_seconds": duration_seconds},
    ) or {}
    _require(started.get("status") == "RUNNING", "fault did not enter RUNNING")
    _require(
        bool((started.get("scenario") or {}).get("active")),
        "fault start response is not active",
    )
    diagnosis_request = started.get("diagnosis_request") or {}
    _require(
        isinstance(diagnosis_request.get("query"), str),
        "fault start response omitted its server-owned diagnosis query",
    )
    _require(
        diagnosis_request.get("auto_scope") is True,
        "fault diagnosis request is not auto-scoped",
    )
    return started


def _stable_scope(target: Any) -> dict[str, Any]:
    target = target if isinstance(target, dict) else {}
    binding = target.get("process_binding")
    binding = binding if isinstance(binding, dict) else {}
    return {
        "agent_id": target.get("agent_id") or binding.get("agent_id"),
        "pid": target.get("pid") or binding.get("pid"),
        "boot_id": binding.get("boot_id"),
        "process_start_ticks": binding.get("process_start_ticks"),
        "pid_namespace_inode": binding.get("pid_namespace_inode"),
        "namespace_pid": binding.get("namespace_pid"),
        "executable_identity": binding.get("executable_identity"),
    }


def _scope_fingerprint(scope: dict[str, Any]) -> str:
    canonical = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _wait_for_bound_scope(
    client: Client,
    diagnosis_id: str,
    *,
    demo_agent_id: str,
    demo_pid: int,
    deadline: float,
    poll_seconds: float,
) -> dict[str, Any]:
    scope_timeout_seconds = max(
        5.0,
        float(os.getenv("MINI_DROP_ACCEPTANCE_SCOPE_TIMEOUT_SECONDS", "60")),
    )
    scope_deadline = min(deadline, time.monotonic() + scope_timeout_seconds)
    last_snapshot: dict[str, Any] = {}
    while time.monotonic() < scope_deadline:
        snapshot = client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}") or {}
        last_snapshot = snapshot
        target = snapshot.get("target") or {}
        scope = _stable_scope(target)
        if scope.get("agent_id") and scope.get("pid"):
            _require(
                scope["agent_id"] == demo_agent_id,
                f"auto-scope selected {scope['agent_id']!r}, expected demo Agent {demo_agent_id!r}",
            )
            _require(
                int(scope["pid"]) == demo_pid,
                f"auto-scope selected PID {scope['pid']}, expected demo PID {demo_pid}",
            )
            _require(
                all(scope.get(key) not in (None, "") for key in (
                    "boot_id",
                    "process_start_ticks",
                    "pid_namespace_inode",
                    "executable_identity",
                )),
                "diagnosis target lacks immutable process-binding authority",
            )
            return snapshot
        if snapshot.get("status") in TERMINAL_DIAGNOSIS:
            raise AcceptanceError(
                f"diagnosis {diagnosis_id} became {snapshot.get('status')} before scope binding"
            )
        time.sleep(poll_seconds)
    if str(last_snapshot.get("status") or "").upper() == "NEEDS_CLARIFICATION":
        question_ids = [
            str(item.get("question_id"))
            for item in (last_snapshot.get("clarification_questions") or [])
            if isinstance(item, dict) and item.get("question_id")
        ]
        detail = ", ".join(question_ids) or "unknown scope fields"
        raise AcceptanceError(
            f"diagnosis {diagnosis_id} remained NEEDS_CLARIFICATION after "
            f"{scope_timeout_seconds:g}s ({detail})"
        )
    raise TimeoutError(f"diagnosis {diagnosis_id} did not bind the demo target")


def _artifact_sample_count(artifact: dict[str, Any]) -> int:
    metadata = artifact.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    quality = metadata.get("profile_quality")
    quality = quality if isinstance(quality, dict) else {}
    # Different collectors expose the same quality fact under the field used
    # by their artifact contract (for example eBPF uses ``total_samples``).
    # Keep the verifier aligned with the runtime evidence extractor instead of
    # falsely reporting a real, non-empty capture as zero samples.
    for value in (
        metadata.get("sample_count"),
        metadata.get("total_samples"),
        metadata.get("event_count"),
        metadata.get("samples"),
        quality.get("sample_count"),
        quality.get("total_samples"),
        quality.get("event_count"),
        quality.get("samples"),
    ):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _collect_task_lineage(client: Client, task_id: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(task_id, safe="")
    task = client.request("GET", f"/api/tasks/{encoded}") or {}
    events = items_of(client.request("GET", f"/api/tasks/{encoded}/events"))
    attempts = items_of(client.request("GET", f"/api/tasks/{encoded}/attempts"))
    artifacts = items_of(client.request("GET", f"/api/tasks/{encoded}/artifacts"))
    task_status = str(task.get("status") or "").upper()
    collection_status = str(task.get("collection_status") or "").upper()
    _require(
        task_status in TERMINAL_TASK,
        f"task {task_id} is not terminal: {task.get('status')}",
    )
    _require(bool(events), f"task {task_id} has no status events")
    _require(bool(attempts), f"task {task_id} has no TaskAttempt")
    # A later route probe may fail during analysis without invalidating a
    # diagnosis that already has conclusion-grade evidence.  Collection
    # success is still a hard artifact-integrity boundary, regardless of the
    # final Task status.
    if task_status == "DONE" or collection_status == "COLLECTED":
        _require(bool(artifacts), f"task {task_id} has no Artifact")
    for artifact in artifacts:
        digest = str(artifact.get("sha256") or "")
        _require(
            len(digest) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in digest),
            f"task {task_id} artifact {artifact.get('id')} lacks a SHA-256",
        )
    return {
        "task_id": task_id,
        "collector_type": task.get("collector_type"),
        "status": task_status,
        "status_reason": task.get("status_reason"),
        "error_code": task.get("error_code"),
        "collection_status": collection_status or None,
        "analysis_status": task.get("analysis_status"),
        "event_count": len(events),
        "attempts": [
            {
                key: item.get(key)
                for key in ("id", "attempt_id", "status", "started_at", "finished_at")
                if item.get(key) is not None
            }
            for item in attempts
        ],
        "artifacts": [
            {
                key: value
                for key, value in {
                    "id": item.get("id"),
                    "artifact_type": item.get("artifact_type"),
                    "size_bytes": item.get("size_bytes"),
                    "sha256": item.get("sha256"),
                    "task_attempt_id": item.get("task_attempt_id"),
                    "integrity_status": item.get("integrity_status"),
                    "sample_count": _artifact_sample_count(item),
                }.items()
                if value is not None
            }
            for item in artifacts
        ],
        "_raw_artifacts": artifacts,
    }


def _validate_profile_content(
    client: Client,
    lineage: dict[str, Any],
    *,
    expected_hot_function: str = EXPECTED_HOT_FUNCTION,
    require_expected_hot_function: bool = True,
) -> dict[str, Any]:
    task_id = str(lineage["task_id"])
    artifacts = lineage.pop("_raw_artifacts")
    by_type = {str(item.get("artifact_type")): item for item in artifacts}
    for artifact_type in ("flamegraph_json", "top_json"):
        artifact = by_type.get(artifact_type)
        _require(
            artifact is not None,
            f"profile task {task_id} lacks {artifact_type}",
        )
        _require(
            int(artifact.get("size_bytes") or 0) > 0,
            f"profile task {task_id} has an empty {artifact_type}",
        )
        _require(
            artifact.get("integrity_status") == "VERIFIED",
            f"profile task {task_id} {artifact_type} is not integrity-verified",
        )
        _require(
            _artifact_sample_count(artifact) >= 100,
            f"profile task {task_id} {artifact_type} has fewer than 100 samples",
        )

    encoded = urllib.parse.quote(task_id, safe="")
    flame = client.request(
        "GET", f"/api/tasks/{encoded}/artifacts/flamegraph_json/content"
    )
    top = client.request("GET", f"/api/tasks/{encoded}/artifacts/top_json/content")
    _require(
        isinstance(flame, dict) and int(flame.get("value") or 0) > 0,
        "flamegraph_json has no positive root sample value",
    )
    _require(
        isinstance(flame.get("children"), list) and bool(flame["children"]),
        "flamegraph_json has no renderable child frames",
    )

    def _flamegraph_names(node: Any) -> list[str]:
        if not isinstance(node, dict):
            return []
        names = [str(node.get("name") or "")]
        for child in node.get("children") or []:
            names.extend(_flamegraph_names(child))
        return names

    flame_names = _flamegraph_names(flame)
    matching_flame_names = [
        name for name in flame_names if expected_hot_function in name
    ]
    if require_expected_hot_function:
        _require(
            bool(matching_flame_names),
            f"flamegraph_json does not contain {expected_hot_function}",
        )
    _require(isinstance(top, list) and bool(top), "top_json has no hotspot rows")
    hot_names = [str(item.get("name") or "") for item in top if isinstance(item, dict)]
    matching_names = [
        name for name in hot_names if expected_hot_function in name
    ]
    if require_expected_hot_function:
        _require(
            bool(matching_names),
            f"top_json does not contain {expected_hot_function}",
        )
    expected_hot_function_found = bool(matching_flame_names and matching_names)
    return {
        "flamegraph_root_samples": int(flame.get("value") or 0),
        "flamegraph_root_children": len(flame["children"]),
        "top_row_count": len(top),
        "expected_hot_function": expected_hot_function,
        "expected_hot_function_found": expected_hot_function_found,
        "matched_flamegraph_function": (
            matching_flame_names[0] if matching_flame_names else None
        ),
        "matched_hot_function": matching_names[0] if matching_names else None,
    }


def _validate_java_profile_content(
    client: Client,
    lineage: dict[str, Any],
    *,
    expected_hot_function: str,
    require_expected_hot_function: bool,
    require_gc_counters: bool = False,
) -> dict[str, Any]:
    task_id = str(lineage["task_id"])
    artifacts = lineage.pop("_raw_artifacts")
    artifact = next(
        (item for item in artifacts if item.get("artifact_type") == "java_flamegraph_html"),
        None,
    )
    _require(artifact is not None, f"Java profile task {task_id} lacks java_flamegraph_html")
    _require(
        int(artifact.get("size_bytes") or 0) >= 4096,
        f"Java profile task {task_id} has an empty or header-only flame graph",
    )
    _require(
        artifact.get("integrity_status") == "VERIFIED",
        f"Java profile task {task_id} is not integrity-verified",
    )
    encoded = urllib.parse.quote(task_id, safe="")
    raw = client.request_raw(
        "GET", f"/api/tasks/{encoded}/artifacts/java_flamegraph_html/content"
    )
    # The API's bounded content endpoint returns non-JSON artifacts inside the
    # standard {code,data:{text}} envelope.  Decode that envelope instead of
    # mistaking the JSON response itself for the flame-graph HTML.
    response_text = raw.decode("utf-8", errors="replace")
    html = response_text
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        envelope = None
    if isinstance(envelope, dict):
        data = envelope.get("data")
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            html = data["text"]
    _require("<html" in html.lower(), "Java flame graph is not renderable HTML")
    expected_found = expected_hot_function in html
    if require_expected_hot_function:
        _require(
            expected_found,
            f"Java flame graph does not contain {expected_hot_function}",
        )
    result = {
        "flamegraph_bytes": len(html.encode("utf-8")),
        "expected_hot_function": expected_hot_function,
        "expected_hot_function_found": expected_found,
        "matched_flamegraph_function": expected_hot_function if expected_found else None,
        "top_row_count": None,
    }
    counter_artifact = next(
        (
            item
            for item in artifacts
            if item.get("artifact_type") == "jvm_gc_metrics"
        ),
        None,
    )
    if require_gc_counters:
        _require(
            counter_artifact is not None,
            f"Java profile task {task_id} lacks jvm_gc_metrics",
        )
        _require(
            int(counter_artifact.get("size_bytes") or 0) > 0,
            f"Java profile task {task_id} has an empty JVM counter window",
        )
        _require(
            counter_artifact.get("integrity_status") == "VERIFIED",
            f"Java profile task {task_id} JVM counter window is not integrity-verified",
        )
        metadata = counter_artifact.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        delta = metadata.get("delta")
        delta = delta if isinstance(delta, dict) else {}
        allocated_delta = int(delta.get("allocated_bytes") or 0)
        gc_count_delta = int(delta.get("gc_count") or 0)
        gc_time_delta = int(delta.get("gc_time_ms") or 0)
        _require(
            allocated_delta > 0,
            "JVM counter window observed no allocation growth",
        )
        _require(
            gc_count_delta > 0 or gc_time_delta > 0,
            "JVM counter window observed no independent GC activity",
        )
        result["jvm_gc_counters"] = {
            "allocated_bytes_delta": allocated_delta,
            "gc_count_delta": gc_count_delta,
            "gc_time_ms_delta": gc_time_delta,
            "window_duration_ms": int(metadata.get("window_duration_ms") or 0),
            "artifact_sha256": counter_artifact.get("sha256"),
        }
    return result


def _validate_lineage_artifacts(lineage: dict[str, Any]) -> dict[str, Any]:
    """Validate a decisive collector without assuming a flamegraph format."""

    task_id = str(lineage["task_id"])
    artifacts = lineage.pop("_raw_artifacts")
    _require(bool(artifacts), f"decisive task {task_id} has no Artifact")
    verified = [
        item
        for item in artifacts
        if item.get("integrity_status") == "VERIFIED"
        and int(item.get("size_bytes") or 0) > 0
    ]
    _require(
        bool(verified),
        f"decisive task {task_id} has no non-empty integrity-verified Artifact",
    )
    return {
        "artifact_contract_verified": True,
        "verified_artifact_count": len(verified),
        "verified_artifact_types": sorted(
            str(item.get("artifact_type") or "unknown") for item in verified
        ),
        "expected_hot_function": None,
        "expected_hot_function_found": None,
    }


def _compact_evidence(item: dict[str, Any]) -> dict[str, Any]:
    envelope = item.get("envelope")
    envelope = envelope if isinstance(envelope, dict) else {}
    source = envelope.get("source")
    source = source if isinstance(source, dict) else {}
    quality = envelope.get("quality")
    quality = quality if isinstance(quality, dict) else {}
    classification = item.get("classification")
    classification = classification if isinstance(classification, dict) else {}
    return {
        "evidence_id": item.get("evidence_id"),
        "role": item.get("role"),
        "task_id": source.get("task_id"),
        "task_attempt_id": source.get("task_attempt_id"),
        "artifact_id": source.get("artifact_id"),
        "artifact_sha256": source.get("artifact_sha256"),
        "analysis_job_id": source.get("analysis_job_id"),
        "quality_level": quality.get("level"),
        "sample_count": quality.get("sample_count"),
        "analyzer_validated": quality.get("analyzer_validated"),
        "classification": classification.get("decision"),
        "can_support_conclusion": classification.get("can_support_conclusion"),
    }


def _exact_lats_duplicate_count(events: list[dict[str, Any]]) -> int:
    """Count only byte-for-byte semantic LATS duplicates, not legal updates."""

    seen: set[tuple[str, str, str]] = set()
    duplicates = 0
    for event in events:
        event_type = str(event.get("event_type") or "")
        if not event_type.startswith("lats."):
            continue
        key = (
            event_type,
            str(event.get("actor") or ""),
            json.dumps(
                event.get("payload") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _accepted_terminal_status(policy: str, status: str) -> bool:
    """Keep the Skill arm strict while accepting an honest weak baseline."""

    normalized_policy = str(policy or "").upper()
    normalized_status = str(status or "").upper()
    if normalized_policy == "AUTO":
        return normalized_status == "COMPLETED"
    if normalized_policy == "DISABLED":
        return normalized_status in {"COMPLETED", "INSUFFICIENT_EVIDENCE"}
    return normalized_status == "COMPLETED"


def _run_diagnosis(
    client: Client,
    diagnosis_request: dict[str, Any],
    *,
    policy: str,
    demo_agent_id: str,
    demo_pid: int,
    timeout_seconds: int,
    poll_seconds: float,
    minimum_rounds: int,
    expected_collector: str = "pyspy",
    expected_hot_function: str = EXPECTED_HOT_FUNCTION,
    profile_validation: str = "standard",
) -> dict[str, Any]:
    query = str(diagnosis_request["query"])
    scenario_budget = diagnosis_request.get("budget")
    scenario_budget = scenario_budget if isinstance(scenario_budget, dict) else {}
    requested_minimum = int(
        scenario_budget.get("min_diagnosis_rounds") or minimum_rounds
    )
    requested_maximum = int(
        scenario_budget.get("max_diagnosis_rounds") or 4
    )
    minimum_rounds = min(4, max(1, requested_minimum, minimum_rounds))
    maximum_rounds = min(4, max(minimum_rounds, requested_maximum))
    payload = {
        "query": query,
        "auto_scope": True,
        "mode": "AUTONOMOUS",
        "skill_policy": policy,
        "target": {},
        "budget": {
            "max_duration_seconds": min(timeout_seconds, 1800),
            "max_tool_calls": 12,
            "min_diagnosis_rounds": minimum_rounds,
            "max_diagnosis_rounds": maximum_rounds,
            "max_concurrent_tasks": 3,
            "max_hosts": 1,
            "max_artifact_bytes": 524_288_000,
            "max_risk_level": "R2",
        },
    }
    created = client.request("POST", "/api/v2/diagnoses", payload) or {}
    diagnosis_id = created.get("diagnosis_id") or created.get("id")
    _require(bool(diagnosis_id), "create diagnosis did not return a diagnosis_id")
    diagnosis_id = str(diagnosis_id)
    deadline = time.monotonic() + timeout_seconds
    snapshot = _wait_for_bound_scope(
        client,
        diagnosis_id,
        demo_agent_id=demo_agent_id,
        demo_pid=demo_pid,
        deadline=deadline,
        poll_seconds=poll_seconds,
    )

    tool_calls = items_of(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/tool-calls")
    )
    planner = None
    if not tool_calls:
        try:
            planner = client.request(
                "POST",
                f"/api/v2/diagnoses/{diagnosis_id}/planner/run",
                {},
                timeout=90,
            )
        except AcceptanceError:
            # The autonomous Worker may win the race.  Accept only if a real
            # persisted call now exists; otherwise preserve the failure.
            tool_calls = items_of(
                client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/tool-calls")
            )
            if not tool_calls:
                raise

    while time.monotonic() < deadline:
        snapshot = client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}") or {}
        status = str(snapshot.get("status") or "")
        if status in TERMINAL_DIAGNOSIS:
            break
        # This endpoint is idempotent and keeps acceptance independent from an
        # open browser/SSE connection while the background Worker still runs.
        client.request(
            "POST",
            f"/api/v2/diagnoses/{diagnosis_id}/orchestrator/advance",
            {},
            # One transition may include bounded model planning, Skill
            # retrieval and LATS report effects. Keep the live verifier above
            # those internal timeouts without weakening its overall deadline.
            timeout=180,
        )
        time.sleep(poll_seconds)
    else:
        raise TimeoutError(
            f"diagnosis {diagnosis_id} did not finish in {timeout_seconds}s"
        )

    # A terminal value must remain stable across subsequent reads.  This
    # catches a report branch briefly publishing INSUFFICIENT/COMPLETED before
    # its report effects reopen the Agent search.
    terminal_status = str(snapshot.get("status") or "")
    for _ in range(2):
        time.sleep(min(max(poll_seconds, 0.1), 1.0))
        stable_snapshot = client.request(
            "GET", f"/api/v2/diagnoses/{diagnosis_id}"
        ) or {}
        _require(
            stable_snapshot.get("status") == terminal_status,
            f"diagnosis {diagnosis_id} exposed unstable terminal state "
            f"{terminal_status}->{stable_snapshot.get('status')}",
        )
        snapshot = stable_snapshot

    tool_calls = items_of(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/tool-calls")
    )
    evidence = items_of(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/evidence")
    )
    reports = items_of(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/reports")
    )
    hypotheses = items_of(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/hypotheses")
    )
    events = items_of(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/events")
    )
    tree = client.request(
        "GET", f"/api/v2/diagnoses/{diagnosis_id}/exploration-tree"
    ) or {}
    activations = items_of(
        client.request(
            "GET",
            f"/api/v2/diagnoses/{diagnosis_id}/diagnostic-skill-activations",
        )
    )
    budget = client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/budget") or {}

    completed = str(snapshot.get("status") or "").upper() == "COMPLETED"
    _require(
        _accepted_terminal_status(policy, str(snapshot.get("status") or "")),
        f"diagnosis {diagnosis_id} ended as {snapshot.get('status')}",
    )
    _require(bool(tool_calls), f"diagnosis {diagnosis_id} has no Tool Call")
    task_ids = list(dict.fromkeys(
        str(item.get("task_id")) for item in tool_calls if item.get("task_id")
    ))
    _require(bool(task_ids), f"diagnosis {diagnosis_id} has no real Task")
    lineages = [_collect_task_lineage(client, task_id) for task_id in task_ids]
    _require(
        any(item.get("status") == "DONE" for item in lineages),
        f"diagnosis {diagnosis_id} has no DONE Task",
    )
    runtime_profiles = [
        item
        for item in lineages
        if item.get("collector_type") == expected_collector
    ]
    # Prefer the successful execution when a later retry of the same
    # collector failed.  Failed attempts remain in the route observations.
    runtime_profile = next(
        (item for item in runtime_profiles if item.get("status") == "DONE"),
        runtime_profiles[0] if runtime_profiles else None,
    )
    runtime_profile_done = bool(
        runtime_profile is not None and runtime_profile.get("status") == "DONE"
    )
    expected_profile_required = policy == "AUTO"
    if runtime_profile is None:
        _require(
            not expected_profile_required,
            f"diagnosis {diagnosis_id} created no {expected_collector} Task",
        )
        profile = {
            "expected_collector": expected_collector,
            "expected_collector_found": False,
            "expected_collector_done": False,
            "expected_hot_function": expected_hot_function,
            "expected_hot_function_found": False,
            "match_status": "EXPECTED_COLLECTOR_NOT_EXECUTED",
        }
    elif not runtime_profile_done:
        _require(
            not expected_profile_required,
            f"diagnosis {diagnosis_id} {expected_collector} Task ended as "
            f"{runtime_profile.get('status')}",
        )
        profile = {
            "expected_collector": expected_collector,
            "expected_collector_found": True,
            "expected_collector_done": False,
            "expected_hot_function": expected_hot_function,
            "expected_hot_function_found": False,
            "match_status": "EXPECTED_COLLECTOR_TASK_FAILED",
        }
    else:
        if profile_validation in {"java_html", "java_gc"}:
            profile = _validate_java_profile_content(
                client,
                runtime_profile,
                expected_hot_function=expected_hot_function,
                require_expected_hot_function=expected_profile_required,
                require_gc_counters=profile_validation == "java_gc",
            )
        elif profile_validation == "lineage_only":
            profile = _validate_lineage_artifacts(runtime_profile)
        else:
            profile = _validate_profile_content(
                client,
                runtime_profile,
                expected_hot_function=expected_hot_function,
                require_expected_hot_function=expected_profile_required,
            )
        profile.update(
            {
                "expected_collector": expected_collector,
                "expected_collector_found": True,
                "expected_collector_done": True,
                "match_status": (
                    "MATCHED"
                    if profile["expected_hot_function_found"]
                    else "EXPECTED_HOT_FUNCTION_NOT_FOUND"
                ),
            }
        )
    # ``_raw_artifacts`` exists only for in-process validation and must never
    # inflate or leak into the persisted acceptance report.
    for lineage in lineages:
        lineage.pop("_raw_artifacts", None)

    _require(bool(evidence), f"diagnosis {diagnosis_id} has no Evidence")
    compact_evidence = [_compact_evidence(item) for item in evidence]
    done_task_ids = {
        str(item.get("task_id") or "")
        for item in lineages
        if item.get("status") == "DONE"
    }
    support_ids = {
        str(item.get("evidence_id"))
        for item in compact_evidence
        if str(item.get("task_id") or "") in done_task_ids
        and str(item.get("role") or "").upper().startswith("SUPPORT")
        and item.get("can_support_conclusion") is True
        and item.get("evidence_id")
    }
    if completed:
        _require(
            bool(support_ids),
            f"diagnosis {diagnosis_id} has no conclusion-supporting Evidence "
            "linked to a DONE Task",
        )
    runtime_task_id = (
        str((runtime_profile or {}).get("task_id") or "")
        if runtime_profile_done
        else ""
    )
    runtime_support_ids = {
        str(item.get("evidence_id"))
        for item in compact_evidence
        if runtime_task_id
        and str(item.get("task_id") or "") == runtime_task_id
        and str(item.get("role") or "").upper().startswith("SUPPORT")
        and item.get("can_support_conclusion") is True
        and item.get("evidence_id")
    }
    _require(bool(reports), f"diagnosis {diagnosis_id} has no Report")
    if completed:
        _require(
            any(item.get("evidence_refs") for item in reports),
            f"diagnosis {diagnosis_id} Report has no Evidence references",
        )
    report_cites_runtime_support = any(
        runtime_support_ids.intersection(
            str(item) for item in (report.get("evidence_refs") or [])
        )
        for report in reports
    )
    if runtime_profile_done and expected_profile_required:
        _require(
            bool(runtime_support_ids),
            f"diagnosis {diagnosis_id} {expected_collector} profile did not produce "
            "conclusion-supporting Evidence",
        )
        _require(
            report_cites_runtime_support,
            f"diagnosis {diagnosis_id} Report does not cite supporting "
            f"{expected_collector} Evidence",
        )
    report_cites_support = any(
        support_ids.intersection(
            str(item) for item in (report.get("evidence_refs") or [])
        )
        for report in reports
    )
    if completed:
        _require(
            report_cites_support,
            f"diagnosis {diagnosis_id} Report does not cite conclusion-supporting "
            "Evidence from a DONE Task",
        )
    _require(
        any(item.get("event_type") == "diagnosis.created" for item in events),
        f"diagnosis {diagnosis_id} lacks its creation event",
    )
    round_indexes = _report_execution_round_indexes(reports, hypotheses, events)
    _require(
        len(round_indexes) >= minimum_rounds,
        f"diagnosis {diagnosis_id} explored only {len(round_indexes)} round(s); "
        f"scenario requires at least {minimum_rounds}",
    )
    duplicate_lats_events = _exact_lats_duplicate_count(events)
    _require(
        duplicate_lats_events == 0,
        f"diagnosis {diagnosis_id} persisted {duplicate_lats_events} exact duplicate LATS event(s)",
    )
    if policy == "DISABLED":
        _require(not activations, "DISABLED diagnosis unexpectedly activated a Skill")

    scope = _stable_scope(snapshot.get("target"))
    tool_route = [
        str(item.get("tool_name"))
        for item in tool_calls
        if item.get("tool_name")
    ]
    lineage_by_task_id = {
        str(item.get("task_id")): item
        for item in lineages
        if item.get("task_id")
    }
    tool_route_observations = []
    for item in tool_calls:
        task_id = str(item.get("task_id") or "")
        lineage = lineage_by_task_id.get(task_id) or {}
        tool_route_observations.append(
            {
                key: value
                for key, value in {
                    "tool_call_id": item.get("tool_call_id"),
                    "tool_name": item.get("tool_name"),
                    "task_id": task_id or None,
                    "tool_call_status": item.get("status"),
                    "task_status": lineage.get("status"),
                    "collection_status": lineage.get("collection_status"),
                    "analysis_status": lineage.get("analysis_status"),
                    "error_code": lineage.get("error_code"),
                }.items()
                if value is not None
            }
        )
    return {
        "diagnosis_id": diagnosis_id,
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "policy": policy,
        "status": snapshot.get("status"),
        "scope": {"agent_id": scope.get("agent_id"), "pid": scope.get("pid")},
        "scope_fingerprint": _scope_fingerprint(scope),
        "planner": {
            "kind": planner.get("planner_kind") if isinstance(planner, dict) else "AUTONOMOUS_WORKER",
            "category": planner.get("category") if isinstance(planner, dict) else None,
        },
        "tool_route": tool_route,
        "tool_route_observations": tool_route_observations,
        "expected_profile_match": {
            "expected_collector": expected_collector,
            "expected_collector_found": runtime_profile is not None,
            "expected_collector_done": runtime_profile_done,
            "expected_hot_function": expected_hot_function,
            "expected_hot_function_found": bool(
                profile.get("expected_hot_function_found")
            ),
            "supporting_evidence_found": bool(runtime_support_ids),
            "report_cites_supporting_evidence": report_cites_runtime_support,
        },
        "tool_calls": [
            {
                key: item.get(key)
                for key in (
                    "tool_call_id",
                    "hypothesis_id",
                    "tool_name",
                    "task_id",
                    "status",
                    "policy_decision",
                )
                if item.get(key) is not None
            }
            for item in tool_calls
        ],
        "tasks": lineages,
        "profile": profile,
        "evidence": compact_evidence,
        "reports": [
            {
                key: item.get(key)
                for key in (
                    "report_id",
                    "hypothesis_id",
                    "confidence",
                    "evidence_refs",
                    "counter_evidence_refs",
                    "effects_status",
                )
                if item.get(key) is not None
            }
            for item in reports
        ],
        "exploration": {
            "minimum_rounds_required": minimum_rounds,
            "round_indexes": round_indexes,
            "round_count": len(round_indexes),
            "hypothesis_count": len(hypotheses),
            "exact_duplicate_lats_event_count": duplicate_lats_events,
            "lats_event_count": sum(
                str(item.get("event_type") or "").startswith("lats.")
                for item in events
            ),
        },
        "skill_activations": [
            {
                key: item.get(key)
                for key in (
                    "activation_id",
                    "skill_id",
                    "match_score",
                    "baseline_tool",
                    "selected_tool",
                    "outcome",
                    "match_reason",
                )
                if item.get(key) is not None
            }
            for item in activations
        ],
        "event_count": len(events),
        "last_event_sequence": max(
            (int(item.get("sequence") or 0) for item in events),
            default=0,
        ),
        "tree_version": tree.get("version") if isinstance(tree, dict) else None,
        "tree_node_count": len(tree.get("nodes") or []) if isinstance(tree, dict) else 0,
        "budget": budget,
    }


def run_acceptance(
    client: Client,
    *,
    scenario_id: str = EXPECTED_SCENARIO,
    demo_agent_id: str = "control-interview-demo-agent",
    fault_duration_seconds: int = 180,
    timeout_seconds: int = 300,
    warmup_seconds: float = 3,
    poll_seconds: float = 2,
) -> dict[str, Any]:
    acceptance_profile = SCENARIO_ACCEPTANCE_PROFILES.get(scenario_id)
    _require(
        acceptance_profile is not None,
        f"scenario {scenario_id!r} has no live profile acceptance contract",
    )
    process_names = tuple(acceptance_profile["process_names"])
    expected_collector = str(acceptance_profile["collector_type"])
    expected_tool_name = str(acceptance_profile["tool_name"])
    expected_hot_function = str(acceptance_profile["expected_hot_function"])
    expected_skill_family = str(acceptance_profile["expected_skill_family"])
    profile_validation = str(acceptance_profile.get("profile_validation") or "standard")
    requires_distinct_first_tool = bool(
        acceptance_profile.get("requires_distinct_first_tool", True)
    )

    health = client.request("GET", "/api/healthz")
    agents = items_of(client.request("GET", "/api/agents?limit=1000"))
    demo_agent = next((item for item in agents if item.get("id") == demo_agent_id), None)
    _require(demo_agent is not None, f"demo Agent {demo_agent_id!r} is not registered")
    _require(demo_agent.get("status") == "ONLINE", f"demo Agent {demo_agent_id!r} is not ONLINE")

    encoded_agent = urllib.parse.quote(demo_agent_id, safe="")
    processes_response = client.request(
        "GET", f"/api/top-processes?agent_id={encoded_agent}&limit=100"
    ) or {}
    _require(processes_response.get("authoritative") is not False, "demo process snapshot is not authoritative")
    _require(processes_response.get("fresh") is not False, "demo process snapshot is stale")
    demo_process = next(
        (
            item
            for item in items_of(processes_response)
            if item.get("comm") in process_names and int(item.get("pid") or 0) > 0
        ),
        None,
    )
    _require(
        demo_process is not None,
        "fresh demo snapshot does not contain any of " + ", ".join(process_names),
    )
    demo_pid = int(demo_process["pid"])

    plaza = _get_plaza(client)
    _require(plaza.get("status") == "READY", f"Fault Plaza is {plaza.get('status')}: {plaza.get('reason')}")
    _require(plaza.get("production_safe") is False, "Fault Plaza lost its demo-only safety declaration")
    _require(len(items_of(plaza.get("scenarios"))) >= 15, "Fault Plaza exposes fewer than fifteen allow-listed scenarios")
    selected = _scenario(plaza, scenario_id)
    _require(bool(selected), f"Fault Plaza has no {scenario_id!r} scenario")
    _require(selected.get("supports_skill_ab") is True, f"scenario {scenario_id!r} does not support Skill A/B")
    _require((selected.get("safety") or {}).get("allow_listed") is True, "scenario is not allow-listed")
    minimum_rounds = max(3, int(selected.get("minimum_diagnosis_rounds") or 3))
    if selected.get("active"):
        _stop_fault(client, scenario_id)

    skills = items_of(client.request("GET", "/api/v2/diagnostic-skills"))
    expected_skill = next(
        (
            item
            for item in skills
            if item.get("family_key") == expected_skill_family
            and item.get("status") == "ACTIVE"
        ),
        None,
    )
    _require(
        expected_skill is not None,
        f"active Skill {expected_skill_family!r} is missing",
    )

    arms: dict[str, dict[str, Any]] = {}
    query_hash = ""
    for policy in ("DISABLED", "AUTO"):
        fault_started = False
        try:
            started = _start_fault(client, scenario_id, fault_duration_seconds)
            fault_started = True
            current_hash = hashlib.sha256(
                str(started["diagnosis_request"]["query"]).encode("utf-8")
            ).hexdigest()
            if query_hash:
                _require(current_hash == query_hash, "server changed the scenario query between A/B replays")
            query_hash = current_hash
            if warmup_seconds:
                time.sleep(warmup_seconds)
            arms[policy.lower()] = _run_diagnosis(
                client,
                started["diagnosis_request"],
                policy=policy,
                demo_agent_id=demo_agent_id,
                demo_pid=demo_pid,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                minimum_rounds=minimum_rounds,
                expected_collector=expected_collector,
                expected_hot_function=expected_hot_function,
                profile_validation=profile_validation,
            )
        finally:
            if fault_started:
                _stop_fault(client, scenario_id)

    disabled = arms["disabled"]
    auto = arms["auto"]
    _require(
        disabled["diagnosis_id"] != auto["diagnosis_id"],
        "Skill A/B re-used a diagnosis instead of creating two fresh sessions",
    )
    _require(
        disabled["scope_fingerprint"] == auto["scope_fingerprint"],
        "Skill A/B replays did not bind the same immutable demo process",
    )
    expected_skill_id = expected_skill.get("skill_id")
    expected_activations = [
        item
        for item in auto["skill_activations"]
        if item.get("skill_id") == expected_skill_id
    ]
    _require(
        bool(expected_activations),
        f"AUTO diagnosis did not activate {expected_skill_family}",
    )
    _require(
        any(
            item.get("selected_tool") == expected_tool_name
            or any(
                application.get("selected_tool") == expected_tool_name
                for application in (
                    (item.get("match_reason") or {}).get("applications") or []
                )
                if isinstance(application, dict)
            )
            for item in expected_activations
        ),
        f"AUTO Skill activation did not select {expected_tool_name}",
    )
    disabled_first_tool = str((disabled.get("tool_calls") or [{}])[0].get("tool_name") or "")
    auto_first_tool = str((auto.get("tool_calls") or [{}])[0].get("tool_name") or "")
    _require(
        auto_first_tool == expected_tool_name,
        f"AUTO diagnosis did not execute Skill-selected {expected_tool_name} first",
    )
    disabled_route = list(disabled.get("tool_route") or [])
    auto_route = list(auto.get("tool_route") or [])
    route_treatment_observed = (
        disabled_first_tool != auto_first_tool
        if requires_distinct_first_tool
        else disabled_route != auto_route
    )
    auto_profile_match = auto["expected_profile_match"]
    _require(
        auto_profile_match["expected_collector_found"] is True,
        f"AUTO diagnosis did not execute {expected_collector}",
    )
    _require(
        auto_profile_match["expected_collector_done"] is True,
        f"AUTO {expected_collector} Task did not finish as DONE",
    )
    _require(
        auto_profile_match["expected_hot_function_found"] is True,
        f"AUTO diagnosis did not find {expected_hot_function}",
    )
    _require(
        auto_profile_match["supporting_evidence_found"] is True,
        f"AUTO diagnosis has no supporting {expected_collector} Evidence",
    )
    _require(
        auto_profile_match["report_cites_supporting_evidence"] is True,
        f"AUTO Report does not cite supporting {expected_collector} Evidence",
    )

    disabled_profile_match = disabled["expected_profile_match"]
    disabled_hit = all(
        disabled_profile_match[key]
        for key in (
            "expected_collector_found",
            "expected_collector_done",
            "expected_hot_function_found",
            "supporting_evidence_found",
            "report_cites_supporting_evidence",
        )
    )
    disabled_support_count = sum(
        item.get("can_support_conclusion") is True
        and str(item.get("role") or "").upper().startswith("SUPPORT")
        for item in disabled.get("evidence") or []
    )
    auto_support_count = sum(
        item.get("can_support_conclusion") is True
        and str(item.get("role") or "").upper().startswith("SUPPORT")
        for item in auto.get("evidence") or []
    )

    return {
        "schema": "mini-drop.interview-demo-acceptance.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "authentication": "X-API-Key_FROM_ENV_REDACTED",
        "passed": True,
        "scenario_id": scenario_id,
        "query_sha256": query_hash,
        "preflight": {
            "health": health,
            "demo_agent": {
                "agent_id": demo_agent_id,
                "status": demo_agent.get("status"),
            },
            "demo_process": {"pid": demo_pid, "comm": demo_process.get("comm")},
            "fault_plaza_status": plaza.get("status"),
            "scenario_count": len(items_of(plaza.get("scenarios"))),
            "expected_skill_id": expected_skill_id,
            "expected_collector": expected_collector,
            "expected_tool_name": expected_tool_name,
            "expected_hot_function": expected_hot_function,
        },
        "paired_target": True,
        "fresh_diagnosis_ids": [disabled["diagnosis_id"], auto["diagnosis_id"]],
        "arms": arms,
        "skill_ab_comparison": {
            "expected_collector": expected_collector,
            "expected_hot_function": expected_hot_function,
            "disabled_expected_profile_hit": disabled_hit,
            "auto_expected_profile_hit": True,
            "disabled_tool_route": disabled_route,
            "auto_tool_route": auto_route,
            "first_tool_diverged": disabled_first_tool != auto_first_tool,
            "complete_route_diverged": disabled_route != auto_route,
            "route_treatment_observed": route_treatment_observed,
            "disabled_terminal_status": disabled.get("status"),
            "auto_terminal_status": auto.get("status"),
            "disabled_supporting_evidence_count": disabled_support_count,
            "auto_supporting_evidence_count": auto_support_count,
            "supporting_evidence_delta": (
                auto_support_count - disabled_support_count
            ),
        },
        "cleanup": {"fault_active": False, "verified": True},
        "measurement_boundary": (
            "两组实验分别启动同一白名单故障，并绑定同一个不可变进程身份。"
            "每组都创建全新的诊断、任务、尝试、产物、证据和报告；AUTO 组必须产出"
            "非空运行时火焰图/TopN 并命中预期热点，DISABLED 组未走到预期采集器时按未命中记录。"
            "非关键后续 Task 的终态失败仅作为路线观测，AUTO 预期采集器仍必须 DONE。"
            "路线是否分叉作为观测结果如实记录，不再强制制造差异；基础计划器已经命中同一路线时，"
            "该 Pair 应报告无额外路线收益，而不是把有效诊断误判为验收失败。"
            "这能证明流程可重复和路线真实执行，但顺序运行的墙钟时间不能当作严格延迟基准。"
        ),
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the live Fault Plaza + AI diagnosis interview acceptance"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", default="MINI_DROP_API_KEY")
    parser.add_argument("--scenario", default=EXPECTED_SCENARIO)
    parser.add_argument("--demo-agent", default="control-interview-demo-agent")
    parser.add_argument("--fault-duration-seconds", type=int, default=180)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--warmup-seconds", type=float, default=3)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not 15 <= args.fault_duration_seconds <= 300:
        raise SystemExit("fault-duration-seconds must be between 15 and 300")
    if not 30 <= args.timeout_seconds <= 1800:
        raise SystemExit("timeout-seconds must be between 30 and 1800")
    if not 0 <= args.warmup_seconds <= 30:
        raise SystemExit("warmup-seconds must be between 0 and 30")
    if not 0.1 <= args.poll_seconds <= 30:
        raise SystemExit("poll-seconds must be between 0.1 and 30")
    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"missing API key environment variable: {args.api_key_env}")

    client = Client(args.base_url, api_key, args.insecure)
    try:
        report = run_acceptance(
            client,
            scenario_id=args.scenario,
            demo_agent_id=args.demo_agent,
            fault_duration_seconds=args.fault_duration_seconds,
            timeout_seconds=args.timeout_seconds,
            warmup_seconds=args.warmup_seconds,
            poll_seconds=args.poll_seconds,
        )
        report["base_url"] = args.base_url.rstrip("/")
        _write_report(args.output, report)
        print(json.dumps({"output": str(args.output), "passed": True}, ensure_ascii=False))
        return 0
    except (AcceptanceError, TimeoutError) as error:
        failure = {
            "schema": "mini-drop.interview-demo-acceptance.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url.rstrip("/"),
            "authentication": "X-API-Key_FROM_ENV_REDACTED",
            "passed": False,
            "error_type": type(error).__name__,
            "error": str(error)[:2000],
        }
        _write_report(args.output, failure)
        print(json.dumps({"output": str(args.output), "passed": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
