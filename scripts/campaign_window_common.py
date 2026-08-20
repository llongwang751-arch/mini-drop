"""Case-neutral helpers for orchestrator-backed live campaign windows."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.otel_experiment_common import atomic_write_json
from server.app.diagnosis.benchmark_runner import (
    CAMPAIGN_TERMINAL_STATUSES,
    upsert_submission,
)


JsonPublisher = Callable[..., dict[str, Any]]


def _redact_secret(value: str, secret: str) -> str:
    return value.replace(secret, "[REDACTED]") if secret else value


def api_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    retries: int = 6,
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("MINI_DROP_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = _redact_secret(
                exc.read().decode("utf-8", errors="replace"), api_key
            )
            if exc.code not in {502, 503, 504} or attempt == retries - 1:
                raise RuntimeError(
                    f"{method} {path} returned HTTP {exc.code}: {body}"
                ) from exc
        except URLError as exc:
            if attempt == retries - 1:
                message = _redact_secret(str(exc), api_key)
                raise RuntimeError(f"{method} {path} failed: {message}") from exc
        time.sleep(min(8, 2**attempt))
    raise RuntimeError(f"{method} {path} exhausted retries")


def wait_for_terminal(
    base_url: str,
    diagnosis_id: str,
    *,
    approve_r2: bool,
    timeout_seconds: float,
    fixture_check: Callable[[], None] | None = None,
    api: Callable[..., dict] = api_json,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    approved: set[str] = set()
    approval_errors: dict[str, str] = {}
    while time.monotonic() < deadline:
        if fixture_check is not None:
            fixture_check()
        response = api(base_url, "GET", f"/api/v1/diagnoses/{diagnosis_id}")
        detail = response["data"]
        if detail.get("status") in CAMPAIGN_TERMINAL_STATUSES:
            return response
        if detail.get("status") == "WAITING_APPROVAL" and approve_r2:
            pending = [
                item
                for item in detail.get("probes", [])
                if item.get("requires_approval")
                and item.get("status") == "WAITING_APPROVAL"
                and item.get("step_id") not in approved
            ]
            for probe in pending:
                step_id = str(probe["step_id"])
                try:
                    api(
                        base_url,
                        "POST",
                        f"/api/v1/diagnoses/{diagnosis_id}/approvals",
                        {
                            "step_id": step_id,
                            "decision": "approve",
                            "scope": "single_execution",
                            "approver_id": "benchmark-operator",
                        },
                        retries=2,
                    )
                except RuntimeError as exc:
                    message = str(exc)
                    if approval_errors.get(step_id) == message:
                        raise RuntimeError(
                            f"R2 approval repeatedly failed for {step_id}: {message}"
                        ) from exc
                    approval_errors[step_id] = message
                    continue
                approved.add(step_id)
                approval_errors.pop(step_id, None)
        time.sleep(2)
    raise TimeoutError(f"diagnosis did not reach terminal state: {diagnosis_id}")


def wait_task_terminal(
    base_url: str,
    task_id: str,
    *,
    timeout_seconds: float = 90,
    api: Callable[..., dict] = api_json,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = api(base_url, "GET", f"/api/tasks/{task_id}")
        task = response.get("data", response)
        if task.get("status") in {"DONE", "FAILED", "CANCELLED"}:
            return task
        time.sleep(1)
    raise TimeoutError(f"task did not reach terminal state: {task_id}")


def resolve_agent_host(
    base_url: str,
    agent_id: str,
    *,
    api: Callable[..., dict] = api_json,
) -> str:
    response = api(base_url, "GET", "/api/agents?limit=1000")
    for item in response.get("data", {}).get("items", []):
        if item.get("id") == agent_id and item.get("status") == "ONLINE":
            return str(item["hostname"])
    raise ValueError(f"online agent not found: {agent_id}")


def publish_submissions(
    path: Path,
    submissions: list[dict[str, Any]],
    *,
    overwrite: bool,
    publisher: JsonPublisher = upsert_submission,
) -> list[dict[str, Any]]:
    """Validate a complete window in staging before replacing its target."""

    staging = path.with_suffix(path.suffix + ".window.tmp")
    staging.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, staging)
    else:
        atomic_write_json(staging, [])
    staging_write = staging.with_suffix(staging.suffix + ".tmp")
    recorded: list[dict[str, Any]] = []
    try:
        for submission in submissions:
            recorded.append(publisher(staging, submission, overwrite=overwrite))
        staging.replace(path)
    finally:
        for temporary in (staging_write, staging):
            if temporary.exists():
                temporary.unlink()
    for item in recorded:
        item["path"] = str(path.resolve())
    return recorded
