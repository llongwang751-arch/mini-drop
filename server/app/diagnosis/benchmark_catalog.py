"""统一诊断测试集目录读取与最小质量校验。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.diagnosis.benchmark_cases import (
    BenchmarkCase,
    load_benchmark_case,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "benchmarks" / "unified_manifest.json"


def load_benchmark_catalog(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or DEFAULT_MANIFEST
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_benchmark_catalog(payload, manifest_path.parent)
    return payload


def validate_benchmark_catalog(
    payload: dict[str, Any],
    manifest_root: Path | None = None,
) -> None:
    required_root = {
        "dataset", "version", "policy", "sources", "references", "core_cases",
    }
    missing_root = required_root - payload.keys()
    if missing_root:
        raise ValueError(f"benchmark manifest missing fields: {sorted(missing_root)}")

    references = {
        item["reference_id"]: item for item in payload["references"]
    }
    if len(references) != len(payload["references"]):
        raise ValueError("benchmark references contain duplicate reference_id")
    sources = {item["source_id"] for item in payload["sources"]}
    for source in payload["sources"]:
        for ref in source.get("reference_ids", []):
            if ref not in references:
                raise ValueError(
                    f"benchmark source {source['source_id']} references unknown citation {ref}"
                )
    cases = payload["core_cases"]
    if len(cases) != 10:
        raise ValueError("benchmark core_cases must contain exactly 10 cases")

    case_ids: set[str] = set()
    required_case = {
        "case_id",
        "source_id",
        "fault_type",
        "trigger",
        "expected_scope",
        "expected_root_cause",
        "required_evidence",
        "expected_terminal_class",
        "snapshot_roles",
        "topology_mode",
        "agent_deployment",
        "minimum_repetitions",
        "oracle_visibility",
        "case_file",
    }
    for case in cases:
        missing = required_case - case.keys()
        if missing:
            raise ValueError(
                f"benchmark case {case.get('case_id', '<unknown>')} missing: {sorted(missing)}"
            )
        if case["case_id"] in case_ids:
            raise ValueError(f"duplicate benchmark case_id: {case['case_id']}")
        if case["source_id"] not in sources:
            raise ValueError(
                f"benchmark case {case['case_id']} references unknown source"
            )
        if not case["required_evidence"]:
            raise ValueError(
                f"benchmark case {case['case_id']} requires evidence expectations"
            )
        if case["oracle_visibility"] != "evaluation_only":
            raise ValueError(
                f"benchmark case {case['case_id']} leaks oracle into diagnosis context"
            )
        if case["agent_deployment"] != "one_agent_per_host":
            raise ValueError(
                f"benchmark case {case['case_id']} violates one-agent-per-host deployment"
            )
        if case["minimum_repetitions"] < payload["policy"]["minimum_repetitions"]:
            raise ValueError(
                f"benchmark case {case['case_id']} has too few repetitions"
            )
        for ref in case.get("reference_ids", []):
            if ref not in references:
                raise ValueError(
                    f"benchmark case {case['case_id']} references unknown citation {ref}"
                )

        case_path = Path(case["case_file"])
        if not case_path.is_absolute():
            # Production manifest stores repository-relative paths.  Custom
            # manifests used by tests may store paths relative to the manifest.
            repository_path = ROOT / case_path
            case_path = (
                repository_path
                if repository_path.exists()
                else (manifest_root or ROOT) / case_path
            )
        executable = BenchmarkCase.model_validate_json(
            case_path.read_text(encoding="utf-8")
        ).model_dump(mode="json")
        _validate_executable_case_alignment(case, executable)
        case_ids.add(case["case_id"])

    if not any(
        case["topology_mode"] == "multi_host_single_agent_each"
        for case in cases
    ):
        raise ValueError("benchmark must include a multi-host one-agent-per-host case")


def _validate_executable_case_alignment(
    catalog_case: dict[str, Any],
    executable: dict[str, Any],
) -> None:
    """Prevent the readable catalog and executable oracle from drifting."""

    comparisons = {
        "case_id": executable["case_id"],
        "source_id": executable["source_id"],
        "fault_type": executable["fault_type"],
        "expected_scope": executable["oracle"]["expected_scope"],
        "expected_root_cause": executable["oracle"]["expected_root_cause"],
        "expected_terminal_class": executable["oracle"]["expected_terminal_class"],
        "topology_mode": executable["topology"]["mode"],
        "agent_deployment": executable["topology"]["agent_deployment"],
        "required_evidence": executable["evidence_plan"]["required_evidence"],
        "snapshot_roles": executable["evidence_plan"]["snapshot_roles"],
    }
    for field, executable_value in comparisons.items():
        if catalog_case[field] != executable_value:
            raise ValueError(
                f"benchmark case {catalog_case['case_id']} field {field} "
                "does not match its executable case"
            )
