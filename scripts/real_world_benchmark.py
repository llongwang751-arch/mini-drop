"""Validate and score the real-world Mini-Drop evidence challenge.

This suite is intentionally separate from the historical synthetic 90-run
campaign.  Public diagnosis inputs and evaluator-only oracles are stored in
different files so the planner cannot receive the answer by accident.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from server.app.evaluation.real_world_admission import validate_formal_admission
from server.app.evaluation.real_world_oracle import (
    PublicRealWorldScore,
    RealWorldOracleRepositoryV1,
    RealWorldOracleScorerV1,
)


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "benchmarks" / "real_world"
_EVIDENCE_FIELDS = {
    "evidence_id", "run_id", "case_id", "role", "evidence_type",
    "recorded_at", "producer", "observed", "integrity_hash",
}
_SNAPSHOT_ROLES = {"baseline", "incident", "verification"}
_COMMITMENT_KEY_ENV = "MINI_DROP_REAL_WORLD_COMMITMENT_KEY"
_PUBLIC_SCORE_FIELDS = frozenset(PublicRealWorldScore.model_fields)
_PUBLIC_GATE_FIELDS = frozenset({
    "formal_admission",
    "terminal_expectation",
    "root_cause",
    "source_location",
    "evidence_cited",
    "counter_evidence_cited",
})


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_evidence_hash(evidence: dict[str, Any]) -> str:
    canonical = {key: value for key, value in evidence.items() if key != "integrity_hash"}
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validated_evidence(run: dict[str, Any], case_id: str) -> dict[str, dict[str, Any]]:
    """Return current-run evidence records whose required fields and hash validate."""
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return {}
    evidence = run.get("evidence")
    if not isinstance(evidence, list):
        return {}
    valid: dict[str, dict[str, Any]] = {}
    duplicate_ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict) or not _EVIDENCE_FIELDS.issubset(item):
            continue
        evidence_id = item.get("evidence_id")
        string_fields = ("evidence_id", "run_id", "case_id", "role", "evidence_type", "recorded_at", "producer")
        if any(not isinstance(item.get(field), str) or not item[field].strip() for field in string_fields):
            continue
        if item["run_id"] != run_id or item["case_id"] != case_id:
            continue
        if item.get("integrity_hash") != _canonical_evidence_hash(item):
            continue
        if evidence_id in valid:
            duplicate_ids.add(evidence_id)
        else:
            valid[evidence_id] = item
    for evidence_id in duplicate_ids:
        valid.pop(evidence_id, None)
    return valid


def _refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [ref for ref in value if isinstance(ref, str) and ref]


def _validate_run_metrics(run: dict[str, Any], case_id: str) -> None:
    confidence = run.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        raise ValueError(f"invalid confidence for {case_id}")

    duration = run.get("duration_seconds")
    if duration is not None and (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or duration < 0
    ):
        raise ValueError(f"invalid duration_seconds for {case_id}")

    tool_calls = run.get("tool_calls")
    if tool_calls is not None and (
        isinstance(tool_calls, bool) or not isinstance(tool_calls, int) or tool_calls < 0
    ):
        raise ValueError(f"invalid tool_calls for {case_id}")

    locations = run.get("predicted_locations")
    if (
        not isinstance(locations, list)
        or not locations
        or any(not isinstance(location, str) or not location.strip() for location in locations)
    ):
        raise ValueError(f"empty or invalid predicted_locations for {case_id}")


def validate_suite() -> dict[str, Any]:
    """Validate the public suite contract without opening evaluator-only data."""
    manifest = _load(SUITE / "manifest.json")
    public_path = Path(manifest["public_cases"])
    comparator_path = Path(manifest["comparators"])
    oracle_path = Path(manifest["private_oracles"])
    if (
        oracle_path.is_absolute()
        or ".." in oracle_path.parts
        or oracle_path in {public_path, comparator_path}
        or "private" not in {part.lower() for part in oracle_path.parts}
    ):
        raise ValueError("private Oracle path is not isolated")
    public = _load(SUITE / public_path)
    comparators = _load(SUITE / comparator_path)
    public_by_id = {item["case_id"]: item for item in public["cases"]}
    declared = manifest["case_ids"]
    if len(declared) != len(set(declared)):
        raise ValueError("duplicate case_id in real-world manifest")
    if set(declared) != set(public_by_id):
        raise ValueError("manifest and public cases are not aligned")
    forbidden = {"root_cause_id", "expected_summary", "fix_sha", "base_sha"}
    leaked = {
        case_id: sorted(forbidden & set(case))
        for case_id, case in public_by_id.items()
        if forbidden & set(case)
    }
    if leaked:
        raise ValueError(f"oracle fields leaked into public cases: {leaked}")
    for case in public_by_id.values():
        if case["minimum_repetitions"] < manifest["policy"]["minimum_repetitions"]:
            raise ValueError(f"too few repetitions: {case['case_id']}")
        if not case["required_evidence"]:
            raise ValueError(f"missing evidence contract: {case['case_id']}")
    return {
        "valid": True,
        "dataset": manifest["dataset"],
        "version": manifest["version"],
        "status": manifest["status"],
        "case_count": len(declared),
        "comparator_count": len(comparators["comparators"]),
        "oracle_isolated": True,
        "locally_replayed_count": sum(
            "NOT_REPLAYED" not in item["reproducibility"]
            for item in public_by_id.values()
        ),
    }


def public_plan() -> dict[str, Any]:
    manifest = _load(SUITE / "manifest.json")
    public = _load(SUITE / manifest["public_cases"])
    return {
        "dataset": manifest["dataset"],
        "version": manifest["version"],
        "oracle_in_plan": False,
        "cases": [
            {key: value for key, value in case.items() if key != "source_url"}
            for case in public["cases"]
        ],
    }


def _public_score(score: PublicRealWorldScore) -> dict[str, Any]:
    projected = score.model_dump(include=_PUBLIC_SCORE_FIELDS)
    projected["gates"] = {
        key: bool(projected["gates"].get(key))
        for key in sorted(_PUBLIC_GATE_FIELDS)
    }
    return projected


def _commitment_key(value: bytes | None) -> bytes:
    if value is not None:
        return bytes(value)
    configured = os.environ.get(_COMMITMENT_KEY_ENV)
    if not configured:
        raise ValueError(f"evaluator commitment key missing: {_COMMITMENT_KEY_ENV}")
    return configured.encode("utf-8")


def _isolated_oracle_path(path: Path, *, manifest: dict[str, Any]) -> Path:
    configured = Path(manifest["private_oracles"])
    if (
        configured.is_absolute()
        or ".." in configured.parts
        or "private" not in {part.lower() for part in configured.parts}
    ):
        raise ValueError("private Oracle path is not isolated")
    configured_path = (SUITE / configured).resolve()
    private_root = configured_path.parent
    candidate = path.resolve()
    public_path = (SUITE / Path(manifest["public_cases"])).resolve()
    comparator_value = manifest.get("comparators")
    comparator_path = (
        (SUITE / Path(comparator_value)).resolve()
        if isinstance(comparator_value, str)
        else None
    )
    if (
        candidate != configured_path
        or candidate == public_path
        or candidate == comparator_path
        or candidate.parent != private_root
    ):
        raise ValueError("private Oracle path is not isolated")
    return candidate


def score_results(
    path: Path,
    *,
    oracle_path: Path | None = None,
    commitment_key: bytes | None = None,
) -> dict[str, Any]:
    """Score frozen normalized outputs through the evaluator-only Oracle adapter."""
    payload = _load(path)
    if not isinstance(payload, dict):
        raise ValueError("comparison results payload must be an object")
    manifest = _load(SUITE / "manifest.json")
    public_by_id = {
        item["case_id"]: item
        for item in _load(SUITE / manifest["public_cases"])["cases"]
    }
    runs = payload.get("runs", [])
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError("comparison results runs must be a list of objects")
    case_ids = []
    for run in runs:
        case_id = run.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("comparison result case_id must be a non-empty string")
        case_ids.append(case_id)
    if len(case_ids) != len(set(case_ids)):
        duplicate = next(case_id for case_id in case_ids if case_ids.count(case_id) > 1)
        raise ValueError(f"duplicate case_id in one comparison result: {duplicate}")
    for run in runs:
        case_id = run.get("case_id")
        if case_id not in public_by_id:
            raise ValueError(f"unknown case_id: {case_id}")
        _validate_run_metrics(run, case_id)

    selected_oracle_path = oracle_path or (SUITE / manifest["private_oracles"])
    repository = RealWorldOracleRepositoryV1(
        _isolated_oracle_path(selected_oracle_path, manifest=manifest)
    )
    scorer = RealWorldOracleScorerV1(repository, _commitment_key(commitment_key))
    oracle_case_ids = repository.case_ids()
    runs = payload.get("runs", [])
    seen: set[str] = set()
    for run in runs:
        case_id = run.get("case_id")
        if case_id not in oracle_case_ids:
            raise ValueError(f"unknown case_id: {case_id}")
        if case_id in seen:
            raise ValueError(f"duplicate case_id in one comparison result: {case_id}")
        seen.add(case_id)

    rows = []
    for run in runs:
        case_id = run["case_id"]
        _validate_run_metrics(run, case_id)
        evidence_by_id = _validated_evidence(run, case_id)
        evidence = [evidence_by_id[ref] for ref in _refs(run.get("evidence_refs")) if ref in evidence_by_id]
        counter_evidence = [
            evidence_by_id[ref]
            for ref in _refs(run.get("counter_evidence_refs"))
            if ref in evidence_by_id
        ]
        roles = {item["role"] for item in evidence + counter_evidence}
        three_phase = _SNAPSHOT_ROLES.issubset(roles)

        admission_error = None
        try:
            validate_formal_admission(
                run,
                case_id=case_id,
                minimum_repetitions=public_by_id[case_id]["minimum_repetitions"],
                evidence_by_id=evidence_by_id,
            )
        except (TypeError, ValueError) as exc:
            admission_error = str(exc)
        public_score = scorer.score(
            run,
            minimum_repetitions=public_by_id[case_id]["minimum_repetitions"],
            evidence_by_id=evidence_by_id,
        )
        gates = public_score.gates
        formal_scored = (
            public_score.verdict in {"PASS", "FAIL"}
            and gates["formal_admission"]
        )
        exact = gates["root_cause"]
        location_match = gates["source_location"]
        abstention_ok = gates["terminal_expectation"]
        confidence = float(run["confidence"])
        target = 1.0 if (exact or (abstention_ok and bool(run.get("abstained")))) else 0.0
        calibrated_error = round(abs(confidence - target), 4)
        rows.append({
            "case_id": case_id,
            "scoring_status": "SCORED" if formal_scored else "UNSCORED",
            "admission_error": admission_error,
            "exact_root_match": exact,
            "source_location_match": location_match,
            "abstention_calibrated": abstention_ok,
            "has_evidence_refs": bool(evidence),
            "has_counter_evidence_refs": bool(counter_evidence),
            "three_phase_snapshot_complete": three_phase,
            "confidence_absolute_error": calibrated_error,
            "duration_seconds": run.get("duration_seconds"),
            "tool_calls": run.get("tool_calls"),
            "evaluator_score": _public_score(public_score),
        })

    scored_rows = [row for row in rows if row["scoring_status"] == "SCORED"]
    count = len(scored_rows)
    def rate(field: str) -> float:
        return sum(row[field] for row in scored_rows) / count if count else 0

    exact_rate = rate("exact_root_match")
    location_rate = rate("source_location_match")
    evidence_rate = rate("has_evidence_refs")
    counter_rate = rate("has_counter_evidence_refs")
    snapshot_rate = rate("three_phase_snapshot_complete")
    abstention_rate = rate("abstention_calibrated")
    return {
        "product": payload.get("product"),
        "dataset": manifest["dataset"],
        "dataset_version": manifest["version"],
        "submitted_cases": len(rows),
        "unscored_cases": len(rows) - count,
        "evaluated_cases": count,
        "coverage_rate": round(count / len(oracle_case_ids), 4),
        "top1_exact_rate": round(exact_rate, 4),
        "source_location_rate": round(location_rate, 4),
        "evidence_citation_rate": round(evidence_rate, 4),
        "counter_evidence_rate": round(counter_rate, 4),
        "three_phase_snapshot_rate": round(snapshot_rate, 4),
        "abstention_calibration_rate": round(abstention_rate, 4),
        "mean_confidence_absolute_error": round(
            sum(row["confidence_absolute_error"] for row in scored_rows) / count, 4
        ) if count else None,
        "evidence_first_score": round(
            100 * (
                0.35 * exact_rate
                + 0.15 * location_rate
                + 0.15 * evidence_rate
                + 0.10 * counter_rate
                + 0.15 * snapshot_rate
                + 0.10 * abstention_rate
            ),
            2,
        ),
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini-Drop real-world benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    plan = commands.add_parser("plan")
    plan.add_argument("--output", type=Path)
    score = commands.add_parser("score")
    score.add_argument("results", type=Path)
    score.add_argument("--output", type=Path)
    score.add_argument(
        "--oracle",
        type=Path,
        help="Evaluator-only versioned Oracle envelope",
    )
    args = parser.parse_args()
    result = (
        validate_suite() if args.command == "validate"
        else public_plan() if args.command == "plan"
        else score_results(args.results, oracle_path=args.oracle)
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    output = getattr(args, "output", None)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(output.resolve())
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
