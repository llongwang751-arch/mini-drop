"""Verify the public, page-driven FULL_LATS frozen replay showcase.

The showcase is deliberately separate from the live Fault Plaza.  This
acceptance client creates two fresh replay diagnoses and proves that they use
the same immutable observation snapshot while retaining session-unique nodes
and durable, incrementally visible search events.

The API key is read from ``MINI_DROP_API_KEY`` only.  It is never accepted on
the command line, printed, or persisted in the report.
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
from uuid import uuid4


TERMINAL = {"COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"}
REQUIRED_LATS_EVENTS = {
    "lats.search_started",
    "lats.candidates_expanded",
    "lats.candidates_evaluated",
    "lats.node_selected",
    "lats.simulation_started",
    "lats.observation_recorded",
    "lats.reflection_recorded",
    "lats.backpropagated",
    "lats.node_pruned",
    "lats.search_terminated",
}


class AcceptanceError(RuntimeError):
    """A public frozen-replay invariant was not satisfied."""


class Client:
    def __init__(self, base_url: str, api_key: str, *, insecure: bool = False):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._ssl_context = (
            ssl._create_unverified_context()  # noqa: SLF001 - explicit CLI opt-in
            if insecure
            else ssl.create_default_context()
        )
        self.last_headers: dict[str, str] = {}

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
        allow_not_found: bool = False,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "X-API-Key": self._api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=self._ssl_context,
            ) as response:
                self.last_headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
                raw = response.read()
        except urllib.error.HTTPError as error:
            if allow_not_found and error.code == 404:
                return None
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
                    f"{method} {path}: {value.get('message') or 'request failed'}"
                )
            return value.get("data")
        return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "scenarios", "data"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
    return []


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or "")


def _node_ids(tree: dict[str, Any]) -> set[str]:
    return {
        str(item.get("id"))
        for item in _items(tree.get("nodes"))
        if item.get("kind") == "hypothesis" and item.get("id")
    }


def _snapshot(tree: dict[str, Any], created: dict[str, Any]) -> dict[str, Any]:
    search = tree.get("search") or {}
    semantics = search.get("semantics") or {}
    search_snapshot = search.get("snapshot") or {}
    target = tree.get("target") or {}
    return {
        "snapshot_id": (
            search.get("snapshot_id")
            or semantics.get("snapshot_id")
            or search_snapshot.get("snapshot_id")
            or (created.get("snapshot") or {}).get("snapshot_id")
            or (target.get("snapshot") or {}).get("snapshot_id")
        ),
        "snapshot_digest": (
            search.get("snapshot_digest")
            or semantics.get("snapshot_digest")
            or search_snapshot.get("snapshot_digest")
            or (created.get("snapshot") or {}).get("snapshot_digest")
            or (target.get("snapshot") or {}).get("snapshot_digest")
        ),
        "reset_count": (
            search.get("reset_count")
            or semantics.get("reset_count")
            or search_snapshot.get("reset_count")
            or (created.get("snapshot") or {}).get("reset_count")
            or 0
        ),
        "frozen": (
            semantics.get("frozen_observations") is True
            or search_snapshot.get("frozen") is True
            or (created.get("snapshot") or {}).get("frozen") is True
            or search.get("environment_semantics") == "FROZEN_REPLAY"
        ),
    }


def _run_once(
    client: Client,
    scenario_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    encoded = urllib.parse.quote(scenario_id, safe="")
    client_run_id = str(uuid4())
    created = client.request(
        "POST",
        f"/api/v2/showcases/lats-replays/{encoded}/runs",
        {"client_run_id": client_run_id},
    ) or {}
    diagnosis_id = str(created.get("diagnosis_id") or "")
    _require(bool(diagnosis_id), "run response omitted diagnosis_id")
    _require(created.get("mode") == "REPLAY", "run response is not REPLAY")
    _require(
        created.get("execution_mode") == "FULL_LATS",
        "run response did not declare FULL_LATS",
    )

    deadline = time.monotonic() + timeout_seconds
    revisions: list[int] = []
    phases: list[str] = []
    detail: dict[str, Any] = {}
    tree: dict[str, Any] = {}
    while time.monotonic() < deadline:
        detail = client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}") or {}
        candidate_tree = client.request(
            "GET",
            f"/api/v2/diagnoses/{diagnosis_id}/exploration-tree",
            allow_not_found=True,
        )
        if isinstance(candidate_tree, dict):
            tree = candidate_tree
            revision = int(tree.get("revision") or 0)
            if not revisions or revisions[-1] != revision:
                revisions.append(revision)
                phase = str((tree.get("search") or {}).get("phase") or "")
                if phase and (not phases or phases[-1] != phase):
                    phases.append(phase)
        if str(detail.get("status") or "").upper() in TERMINAL:
            break
        time.sleep(poll_seconds)
    else:
        raise AcceptanceError(
            f"frozen replay {diagnosis_id} did not finish in {timeout_seconds:g}s"
        )

    _require(detail.get("status") == "COMPLETED", f"replay ended as {detail.get('status')}")
    tree = client.request(
        "GET", f"/api/v2/diagnoses/{diagnosis_id}/exploration-tree"
    ) or {}
    events = _items(client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/events"))
    hypotheses = _items(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/hypotheses")
    )
    tool_calls = _items(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/tool-calls")
    )
    evidence = _items(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/evidence")
    )
    reports = _items(
        client.request("GET", f"/api/v2/diagnoses/{diagnosis_id}/reports")
    )
    search = tree.get("search") or {}
    semantics = search.get("semantics") or {}
    budget = search.get("budget") or {}
    event_types = {_event_type(event) for event in events}
    snapshot = _snapshot(tree, created)

    _require(
        search.get("execution_mode") == "FULL_LATS",
        "persisted tree is not FULL_LATS",
    )
    _require(
        search.get("environment_semantics") == "FROZEN_REPLAY",
        "persisted tree is not a frozen environment",
    )
    _require(
        search.get("strict_environment_reversibility") is True,
        "strict replay did not prove environment reversibility",
    )
    _require(snapshot["frozen"] is True, "snapshot is not marked frozen")
    digest = str(snapshot["snapshot_digest"] or "")
    _require(
        len(digest) == 64 and all(char in "0123456789abcdef" for char in digest.lower()),
        "snapshot digest is not a SHA-256",
    )
    missing = sorted(REQUIRED_LATS_EVENTS - event_types)
    _require(not missing, f"replay omitted LATS events: {', '.join(missing)}")
    _require(
        "lats.action_dispatched" not in event_types,
        "frozen replay falsely dispatched a live action",
    )
    _require(len(hypotheses) >= 4, "replay did not persist a branching hypothesis tree")
    _require(not tool_calls, "frozen replay created live ToolCall rows")
    _require(not evidence, "frozen replay created live Evidence rows")
    _require(not reports, "frozen replay created a trusted Report row")
    _require(int(budget.get("used_tool_calls") or 0) == 0, "tool budget is not zero")
    used_simulations = int(budget.get("used_simulations") or 0)
    _require(used_simulations >= 2, "replay did not execute sibling simulations")
    _require(
        int(snapshot.get("reset_count") or 0) >= used_simulations,
        "snapshot was not reset before every simulation",
    )
    _require(len(revisions) >= 3, "tree did not expose at least three durable revisions")

    return {
        "diagnosis_id": diagnosis_id,
        "client_run_id": client_run_id,
        "status": detail.get("status"),
        "snapshot": snapshot,
        "tree_revisions_observed": revisions,
        "search_phases_observed": phases,
        "tree_node_count": len(_items(tree.get("nodes"))),
        "hypothesis_count": len(hypotheses),
        "hypothesis_node_ids": sorted(_node_ids(tree)),
        "event_count": len(events),
        "event_types": sorted(event_types),
        "search": {
            "algorithm": search.get("algorithm"),
            "selection_policy": (search.get("search_config") or {}).get("selection_policy"),
            "execution_mode": search.get("execution_mode"),
            "environment_semantics": search.get("environment_semantics"),
            "strict_environment_reversibility": search.get(
                "strict_environment_reversibility"
            ),
            "best_path_node_ids": search.get("best_path_node_ids") or [],
            "termination": search.get("termination") or {},
            "budget": budget,
            "snapshot_proof": {
                "snapshot_id": semantics.get("snapshot_id") or snapshot["snapshot_id"],
                "snapshot_digest": semantics.get("snapshot_digest") or digest,
                "reset_count": semantics.get("reset_count") or snapshot["reset_count"],
            },
        },
        "forbidden_live_rows": {
            "tool_calls": len(tool_calls),
            "evidence": len(evidence),
            "reports": len(reports),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the public FULL_LATS frozen-replay showcase"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scenario", default="")
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    api_key = os.getenv("MINI_DROP_API_KEY", "")
    if not api_key:
        raise SystemExit("MINI_DROP_API_KEY is required")
    client = Client(args.base_url, api_key, insecure=args.insecure)
    catalog = client.request("GET", "/api/v2/showcases/lats-replays") or {}
    scenarios = _items(catalog)
    _require(bool(scenarios), "frozen replay catalog is empty")
    scenario_id = args.scenario or str(scenarios[0].get("scenario_id") or "")
    selected = next(
        (item for item in scenarios if item.get("scenario_id") == scenario_id), None
    )
    _require(selected is not None, f"scenario {scenario_id!r} is not allow-listed")
    _require(selected.get("execution_mode") == "FULL_LATS", "catalog mode is not FULL_LATS")
    _require(
        selected.get("environment_semantics") == "FROZEN_REPLAY",
        "catalog environment is not FROZEN_REPLAY",
    )

    first = _run_once(
        client,
        scenario_id,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=max(0.05, args.poll_seconds),
    )
    second = _run_once(
        client,
        scenario_id,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=max(0.05, args.poll_seconds),
    )
    _require(
        first["diagnosis_id"] != second["diagnosis_id"],
        "two clicks reused the same diagnosis",
    )
    _require(
        first["snapshot"]["snapshot_digest"] == second["snapshot"]["snapshot_digest"],
        "identical showcase runs did not use the same snapshot digest",
    )
    _require(
        set(first["hypothesis_node_ids"]).isdisjoint(second["hypothesis_node_ids"]),
        "two diagnoses reused globally keyed hypothesis nodes",
    )

    report = {
        "schema_version": "mini-drop-lats-replay-acceptance-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url.rstrip("/"),
        "scenario": {
            key: selected.get(key)
            for key in (
                "scenario_id",
                "title",
                "execution_mode",
                "environment_semantics",
                "selection_policy",
                "truth_boundary",
            )
        },
        "transport": client.last_headers.get("x-mini-drop-ai-transport"),
        "runs": [first, second],
        "cross_run_proof": {
            "fresh_diagnoses": True,
            "same_snapshot_digest": True,
            "disjoint_hypothesis_node_ids": True,
        },
        "result": "PASS",
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest().upper()
        print(f"PASS report={args.output} sha256={digest}")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
