"""Read-only adapter for the shared ``ai_ops_v2`` benchmark archive.

The shared archive is intentionally kept outside the diagnosis planner.  This
module exposes public cases and *completed historical evaluation* summaries to
the Web UI, while keeping private Oracle data behind an explicit evaluator
view.  Nothing in this module is used as diagnosis context.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXTERNAL_ROOT = ROOT / "artifacts" / "benchmarks" / "external"
BUNDLED_EXTERNAL_ROOT = ROOT / "benchmarks" / "external"
MOUNTED_EXTERNAL_ROOT = Path("/workspace-source/artifacts/benchmarks/external")


class ExternalBenchmarkUnavailable(RuntimeError):
    """Raised when the optional shared benchmark archive is absent or invalid."""


def locate_external_benchmark(path: str | Path | None = None) -> Path:
    configured = path or os.getenv("MINI_DROP_EXTERNAL_BENCHMARK_ARCHIVE")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError as exc:
            raise ExternalBenchmarkUnavailable(
                f"external benchmark archive is not readable: {candidate}"
            ) from exc
        raise ExternalBenchmarkUnavailable(
            f"external benchmark archive not found: {candidate}"
        )

    candidates: list[Path] = []
    # Bind-mounted source trees can be intentionally unreadable to the
    # unprivileged server user.  An optional benchmark must not turn that into
    # a 500 response: skip inaccessible roots and continue with the bundled
    # copy shipped in the image.
    for root in (
        BUNDLED_EXTERNAL_ROOT,
        DEFAULT_EXTERNAL_ROOT,
        MOUNTED_EXTERNAL_ROOT,
    ):
        try:
            if root.is_dir():
                candidates.extend(root.glob("ai_ops_v2*.zip"))
        except (OSError, PermissionError):
            continue

    readable_candidates: list[tuple[int, str, Path]] = []
    for candidate in candidates:
        try:
            readable_candidates.append(
                (candidate.stat().st_mtime_ns, candidate.name, candidate)
            )
        except OSError:
            continue
    candidates = [
        item[2] for item in sorted(readable_candidates, reverse=True)
    ]
    if not candidates:
        raise ExternalBenchmarkUnavailable(
            "ai_ops_v2 archive is not installed; put it under "
            "benchmarks/external, artifacts/benchmarks/external, or set "
            "MINI_DROP_EXTERNAL_BENCHMARK_ARCHIVE"
        )
    return candidates[0].resolve()


def _normalise_member(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def _member_by_suffix(archive: ZipFile, suffix: str) -> str:
    wanted = suffix.replace("\\", "/")
    matches = [
        item.filename
        for item in archive.infolist()
        if _normalise_member(item.filename).endswith(wanted)
    ]
    if len(matches) != 1:
        raise ExternalBenchmarkUnavailable(
            f"expected one archive member ending with {wanted!r}, found {len(matches)}"
        )
    return matches[0]


def _json_member(archive: ZipFile, suffix: str) -> Any:
    member = _member_by_suffix(archive, suffix)
    try:
        return json.loads(archive.read(member).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalBenchmarkUnavailable(
            f"invalid JSON in external benchmark member: {member}"
        ) from exc


def _archive_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dimension_summary(result: dict[str, Any]) -> dict[str, Any]:
    dimensions = result.get("dimensions") or {}
    root = dimensions.get("root_cause") or {}
    evidence = dimensions.get("evidence") or {}
    trace = dimensions.get("trace") or {}
    safety = dimensions.get("safety") or {}
    recovery = dimensions.get("recovery") or {}
    return {
        "root_score": root.get("score"),
        "root_maximum": root.get("maximum"),
        "root_checks": root.get("checks") or [],
        "evidence_score": evidence.get("score"),
        "evidence_maximum": evidence.get("maximum"),
        "citation_valid": evidence.get("citation_valid"),
        "required_collectors": evidence.get("required_collectors") or [],
        "observed_collectors": evidence.get("observed_collectors") or [],
        "runtime_step_count": trace.get("runtime_step_count", 0),
        "present_stages": trace.get("present_stages") or [],
        "missing_stages": trace.get("missing_stages") or [],
        "unsafe_actions": safety.get("unsafe_actions") or [],
        "recovery_applicable": recovery.get("applicable", False),
        "recovery_passed": recovery.get("passed", False),
    }


def _run_summary(result: dict[str, Any], audit: dict[str, Any] | None) -> dict[str, Any]:
    trace = (audit or {}).get("trace") or []
    conclusion = (audit or {}).get("conclusion") or {}
    evidence_manifest = (audit or {}).get("evidence_manifest") or []
    run = (audit or {}).get("run") or {}
    return {
        "diagnosis_id": result.get("diagnosis_id"),
        "repetition": result.get("repetition"),
        "status": run.get("status"),
        "score": result.get("score"),
        "exact_root_match": bool(result.get("exact_root_match")),
        "expected_abstention": bool(result.get("expected_abstention")),
        "correct_abstention": bool(result.get("correct_abstention")),
        "actual": result.get("actual") or {},
        "dimensions": _dimension_summary(result),
        "model_version": run.get("model_version"),
        "planner_version": run.get("planner_version"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "conclusion_summary": conclusion.get("summary"),
        "confidence_level": conclusion.get("confidence_level"),
        "evidence_count": len(evidence_manifest),
        "probe_count": len((audit or {}).get("probes") or []),
        "trace": [
            {
                "sequence": item.get("sequence"),
                "stage": item.get("stage"),
                "component": item.get("component"),
                "decision": item.get("decision"),
                "summary": item.get("summary"),
                "evidence_refs": item.get("evidence_refs") or [],
                "recorded_at": item.get("recorded_at"),
            }
            for item in trace
        ],
    }


def _audit_members(archive: ZipFile) -> dict[str, str]:
    members: dict[str, str] = {}
    for item in archive.infolist():
        name = _normalise_member(item.filename)
        if "__r" not in name or not name.endswith(".json"):
            continue
        try:
            payload = json.loads(archive.read(item).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        diagnosis_id = payload.get("diagnosis_id")
        if diagnosis_id:
            members[str(diagnosis_id)] = item.filename
    return members


@lru_cache(maxsize=4)
def _load_cached(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    del mtime_ns, size  # only used to invalidate the cache key
    path = Path(path_text)
    try:
        with ZipFile(path) as archive:
            manifest = _json_member(archive, "ai_ops_v2/manifest.json")
            public = _json_member(archive, "ai_ops_v2/public/cases.json")
            private = _json_member(archive, "ai_ops_v2/private/oracles.json")
            evaluation = _json_member(archive, "evaluation.json")
            summary = _json_member(archive, "summary.json")
            audit_members = _audit_members(archive)

            public_cases = {
                str(item["case_id"]): item for item in public.get("cases", [])
            }
            oracles = {
                str(item["case_id"]): item for item in private.get("cases", [])
            }
            aggregate = evaluation.get("aggregate") or {}
            results = aggregate.get("results") or []
            results_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
            audit_payloads: dict[str, dict[str, Any]] = {}
            for diagnosis_id, member in audit_members.items():
                audit_payloads[diagnosis_id] = json.loads(
                    archive.read(member).decode("utf-8-sig")
                )
            for result in results:
                diagnosis_id = str(result.get("diagnosis_id") or "")
                results_by_case[str(result.get("case_id"))].append(
                    _run_summary(result, audit_payloads.get(diagnosis_id))
                )
    except (BadZipFile, KeyError, OSError) as exc:
        raise ExternalBenchmarkUnavailable(
            f"cannot read external benchmark archive: {path}"
        ) from exc

    declared_cases = manifest.get("cases") or []
    tracks = manifest.get("tracks") or {}
    catalog: list[dict[str, Any]] = []
    for declared in declared_cases:
        case_id = str(declared.get("case_id"))
        public_case = public_cases.get(case_id, {})
        runs = sorted(
            results_by_case.get(case_id, []),
            key=lambda item: int(item.get("repetition") or 0),
        )
        catalog.append({
            "case_id": case_id,
            "track": declared.get("track"),
            "query": public_case.get("query"),
            "service_hint": public_case.get("service_hint"),
            "environment": public_case.get("environment"),
            "run_count": len(runs),
            "exact_match_count": sum(bool(item["exact_root_match"]) for item in runs),
            "mean_score": round(
                sum(float(item.get("score") or 0) for item in runs) / len(runs), 2
            ) if runs else None,
            "oracle_available_after_evaluation": case_id in oracles,
        })

    return {
        "available": True,
        "archive": {
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _archive_fingerprint(path),
        },
        "dataset": manifest.get("dataset"),
        "version": manifest.get("version"),
        "schema_version": manifest.get("schema_version"),
        "policy": manifest.get("policy") or {},
        "tracks": tracks,
        "metrics": manifest.get("metrics") or [],
        "execution": summary,
        "evaluation": {
            key: value for key, value in aggregate.items() if key != "results"
        },
        "cases": catalog,
        "_oracles": oracles,
        "_runs": dict(results_by_case),
    }


def load_external_benchmark(path: str | Path | None = None) -> dict[str, Any]:
    archive_path = locate_external_benchmark(path)
    stat = archive_path.stat()
    return _load_cached(str(archive_path), stat.st_mtime_ns, stat.st_size)


def external_benchmark_summary(path: str | Path | None = None) -> dict[str, Any]:
    payload = load_external_benchmark(path)
    return {key: value for key, value in payload.items() if not key.startswith("_")}


def external_benchmark_case(
    case_id: str,
    *,
    reveal_oracle: bool = True,
    path: str | Path | None = None,
) -> dict[str, Any]:
    payload = load_external_benchmark(path)
    catalog_case = next(
        (item for item in payload["cases"] if item["case_id"] == case_id), None
    )
    if catalog_case is None:
        raise KeyError(case_id)
    response = {
        **catalog_case,
        "runs": payload["_runs"].get(case_id, []),
        "oracle_revealed": reveal_oracle,
    }
    if reveal_oracle:
        response["oracle"] = payload["_oracles"].get(case_id)
    return response
