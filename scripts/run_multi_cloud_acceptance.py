"""Run an authenticated end-to-end collection on every named Agent.

This is deliberately stricter than a heartbeat check: every Agent must expose a
fresh process snapshot, accept a real ``sys_metrics`` Task, reach ``DONE`` and
publish a contract-recognised artifact.  Secrets and artifact bodies are never
written to the report.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TERMINAL = {"DONE", "FAILED", "CANCELLED"}


class Client:
    def __init__(self, base_url: str, api_key: str, insecure: bool = False):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.context = ssl._create_unverified_context() if insecure else ssl.create_default_context()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        idempotency_key: str = "",
    ):
        headers = {"X-API-Key": self.key, "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            self.base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=30, context=self.context) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:1000]
            raise RuntimeError(f"{method} {path}: HTTP {error.code}: {detail}") from error
        if isinstance(value, dict) and "code" in value:
            if value.get("code") != 0:
                raise RuntimeError(f"{method} {path}: {value.get('message')}")
            return value.get("data")
        return value


def items_of(value) -> list[dict]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        items = value.get("items") or value.get("data") or []
        return items if isinstance(items, list) else []
    return []


def compact(items: list[dict], fields: tuple[str, ...]) -> list[dict]:
    return [
        {field: item.get(field) for field in fields if item.get(field) is not None}
        for item in items
    ]


def select_process(candidates: list[dict]) -> dict:
    usable = [item for item in candidates if int(item.get("pid") or 0) > 0]
    if not usable:
        raise RuntimeError("no usable process in fresh authoritative snapshot")
    # Prefer an ordinary long-running process; PID 1 is retained as a fallback.
    return next((item for item in usable if int(item["pid"]) != 1), usable[0])


def run_agent(client, agent_id: str, timeout_seconds: int, duration_seconds: int) -> dict:
    encoded = urllib.parse.quote(agent_id, safe="")
    snapshot = client.request("GET", f"/api/top-processes?agent_id={encoded}&limit=30")
    if isinstance(snapshot, dict):
        if snapshot.get("authoritative") is False or snapshot.get("fresh") is False:
            raise RuntimeError(f"{agent_id}: process snapshot is not fresh and authoritative")
    target = select_process(items_of(snapshot))
    pid = int(target["pid"])
    # A new script invocation is a new acceptance run.  The key still protects
    # this individual create request from accidental duplicate delivery.
    request_fingerprint = secrets.token_hex(12)
    created = client.request(
        "POST",
        "/api/tasks",
        {
            "name": f"multi-cloud-acceptance:{agent_id}",
            "agent_id": agent_id,
            "target_pid": pid,
            "collector_type": "sys_metrics",
            "sample_rate": 1,
            "duration_sec": duration_seconds,
            "options": {"interval_ms": 1000, "source": "multi_cloud_acceptance"},
            "resource_budget": {
                "max_cpu_percent": 25,
                "max_memory_mb": 256,
                "max_output_mb": 16,
                "max_duration_sec": duration_seconds,
            },
        },
        idempotency_key=f"multi-cloud-{request_fingerprint}",
    )
    task_id = (created or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"{agent_id}: create task returned no task_id")
    deadline = time.monotonic() + timeout_seconds
    task = {}
    while time.monotonic() < deadline:
        task = client.request("GET", f"/api/tasks/{task_id}") or {}
        if task.get("status") in TERMINAL:
            break
        time.sleep(2)
    else:
        raise TimeoutError(f"{agent_id}: task {task_id} did not finish")

    events = items_of(client.request("GET", f"/api/tasks/{task_id}/events"))
    attempts = items_of(client.request("GET", f"/api/tasks/{task_id}/attempts"))
    artifacts = items_of(client.request("GET", f"/api/tasks/{task_id}/artifacts"))
    artifact_types = {str(item.get("artifact_type")) for item in artifacts}
    if task.get("status") != "DONE":
        raise RuntimeError(f"{agent_id}: task {task_id} ended as {task.get('status')}")
    if "sys_metrics" not in artifact_types:
        raise RuntimeError(f"{agent_id}: task {task_id} has no sys_metrics artifact")
    return {
        "agent_id": agent_id,
        "hostname": task.get("agent_hostname"),
        "task_id": task_id,
        "target": {"pid": pid, "comm": target.get("comm")},
        "status": task.get("status"),
        "collection_status": task.get("collection_status"),
        "analysis_status": task.get("analysis_status"),
        "events": compact(events, ("id", "sequence", "from_status", "to_status", "reason")),
        "attempts": compact(attempts, ("id", "attempt_id", "status", "started_at", "finished_at")),
        "artifacts": compact(
            artifacts,
            ("id", "artifact_type", "size_bytes", "sha256", "task_attempt_id"),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--agent", action="append", required=True, dest="agents")
    parser.add_argument("--api-key-env", default="MINI_DROP_API_KEY")
    parser.add_argument("--duration-seconds", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.duration_seconds <= 60:
        raise SystemExit("duration-seconds must be between 1 and 60")
    api_key = os.getenv(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"missing API key environment variable: {args.api_key_env}")
    client = Client(args.base_url, api_key, args.insecure)
    health = client.request("GET", "/api/healthz")
    registered = {str(item.get("id")): item for item in items_of(client.request("GET", "/api/agents?limit=1000"))}
    missing = [agent_id for agent_id in args.agents if agent_id not in registered]
    offline = [agent_id for agent_id in args.agents if registered.get(agent_id, {}).get("status") != "ONLINE"]
    if missing or offline:
        raise SystemExit(f"agents not ready; missing={missing}, offline={offline}")
    results = [run_agent(client, agent_id, args.timeout_seconds, args.duration_seconds) for agent_id in args.agents]
    report = {
        "schema": "mini-drop.multi-cloud-acceptance.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "authentication": "X-API-Key_FROM_ENV_REDACTED",
        "health": health,
        "agents": results,
        "passed": len(results),
        "measurement_boundary": (
            "Authenticated live Go API; each named Agent completed a real sys_metrics Task "
            "with server-issued event, attempt and artifact lineage. Artifact bodies and credentials are excluded."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "passed": len(results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
