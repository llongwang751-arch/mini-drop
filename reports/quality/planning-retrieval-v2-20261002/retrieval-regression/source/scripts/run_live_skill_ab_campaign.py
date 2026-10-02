"""Run authenticated, full-stack Skill AUTO vs DISABLED campaigns.

The API key is read from an environment variable and is never accepted on the
command line or written to the report.  Output contains server-issued IDs and
artifact/evidence lineage, so it can be reviewed independently of screenshots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TERMINAL = {"COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"}


class Client:
    def __init__(self, base_url: str, api_key: str, insecure: bool = False):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.context = ssl._create_unverified_context() if insecure else ssl.create_default_context()

    def request(self, method: str, path: str, payload: dict | None = None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={"X-API-Key": self.key, "Content-Type": "application/json"},
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


def _items(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value.get("items") or value.get("data") or []
    return []


def _compact(items, fields):
    return [{field: item.get(field) for field in fields if item.get(field) is not None} for item in items]


def run_one(client: Client, query: str, policy: str, timeout_seconds: int) -> dict:
    created = client.request("POST", "/api/v2/diagnoses", {
        "query": query,
        "auto_scope": True,
        "mode": "AUTONOMOUS",
        "skill_policy": policy,
        "target": {},
        "budget": {
            "max_duration_seconds": min(timeout_seconds, 1800),
            "max_tool_calls": 12,
            "max_diagnosis_rounds": 6,
            "max_concurrent_tasks": 3,
            "max_hosts": 3,
            "max_artifact_bytes": 524288000,
            "max_risk_level": "R2",
        },
    })
    diagnosis_id = created.get("id") or created.get("diagnosis_id")
    if not diagnosis_id:
        raise RuntimeError("create diagnosis did not return an id")
    deadline = time.monotonic() + timeout_seconds
    snapshot = created
    while time.monotonic() < deadline:
        snapshot = client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}")
        if snapshot.get("status") in TERMINAL:
            break
        time.sleep(3)
    else:
        raise TimeoutError(f"diagnosis {diagnosis_id} did not finish in {timeout_seconds}s")

    tool_calls = _items(client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/tool-calls"))
    evidence = _items(client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/evidence"))
    reports = _items(client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/reports"))
    events = _items(client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/events"))
    tree = client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/exploration-tree")
    activations = _items(client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/diagnostic-skill-activations"))
    return {
        "diagnosis_id": diagnosis_id,
        "policy": policy,
        "status": snapshot.get("status"),
        "target": snapshot.get("target") or snapshot.get("target_json"),
        "tool_call_count": len(tool_calls),
        "evidence_count": len(evidence),
        "report_count": len(reports),
        "skill_activation_count": len(activations),
        "tool_calls": _compact(tool_calls, ("tool_call_id", "id", "tool_name", "task_id", "status")),
        "evidence": _compact(evidence, ("evidence_id", "task_id", "artifact_id", "integrity", "quality_score")),
        "reports": _compact(reports, ("report_id", "id", "status", "confidence", "evidence_ids")),
        "skill_activations": _compact(activations, ("activation_id", "skill_id", "selected_tool", "match_score", "outcome")),
        "tree_version": tree.get("version") if isinstance(tree, dict) else None,
        "tree_node_count": len(tree.get("nodes") or []) if isinstance(tree, dict) else None,
        "last_event_sequence": max((item.get("sequence", 0) for item in events), default=0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--query", action="append", required=True)
    parser.add_argument("--api-key-env", default="MINI_DROP_API_KEY")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    api_key = os.getenv(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"missing API key environment variable: {args.api_key_env}")
    client = Client(args.base_url, api_key, args.insecure)
    health = client.request("GET", "/api/healthz")
    pairs = []
    for query in args.query:
        pairs.append({
            "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
            "disabled": run_one(client, query, "DISABLED", args.timeout_seconds),
            "auto": run_one(client, query, "AUTO", args.timeout_seconds),
        })
    report = {
        "schema": "mini-drop.live-skill-ab.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "authentication": "X-API-Key_FROM_ENV_REDACTED",
        "health": health,
        "pairs": pairs,
        "measurement_boundary": "Authenticated live API and server-issued lineage; the API key and raw user query are not persisted in this report.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "pairs": len(pairs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
