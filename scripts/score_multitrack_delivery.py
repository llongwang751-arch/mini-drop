#!/usr/bin/env python3
"""Score completed multitrack runs after diagnosis, using private Oracles."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import types
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _single(paths: list[Path], label: str) -> Path:
    paths = [path for path in paths if "__MACOSX" not in path.parts]
    if len(paths) != 1:
        raise RuntimeError(f"expected one {label}, found {len(paths)}")
    return paths[0]


def _average(rows: list[dict], section: str, field: str) -> float:
    values = [float(row[section][field]) for row in rows]
    return round(sum(values) / len(values), 4) if values else 0.0


def _run_validity(benchmark: Path, run_count: int) -> tuple[str, str]:
    metadata_path = benchmark / "campaign-metadata.json"
    metadata = {}
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("rule_fallback_allowed") is True:
            return (
                "INVALID_PROVIDER_CONFIGURATION",
                "Campaign explicitly allowed rule fallback; scores are plumbing diagnostics and must not be used for ranking.",
            )
        if metadata.get("external_adapter_schema_validated") is True:
            valid_count = int(metadata.get("schema_valid_run_count") or 0)
            if valid_count != run_count:
                return (
                    "INVALID_EXTERNAL_ADAPTER_FAILURE",
                    f"Only {valid_count}/{run_count} external-adapter runs completed with a valid schema.",
                )
            return (
                "VALID",
                "All external-adapter runs completed with the shared normalized-answer schema.",
            )

    outputs = []
    for path in sorted(
        (benchmark / "runs-native").glob("*/*/*/repeat-*/raw-agent-output.txt")
    ):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        validation = payload.get("validation") or {}
        outputs.append(validation)
    usable_model_outputs = sum(
        item.get("model_invoked") is True
        and item.get("schema_validated") is True
        and not item.get("fallback_reason")
        for item in outputs
    )
    provider_failures = sum(bool(item.get("fallback_reason")) for item in outputs)
    if run_count and usable_model_outputs == 0 and provider_failures:
        return (
            "INVALID_PROVIDER_CONFIGURATION",
            "No run produced a schema-valid model result; scores describe fallback plumbing and must not be used for ranking.",
        )
    if run_count and usable_model_outputs < run_count:
        return (
            "INVALID_PARTIAL_PROVIDER_FAILURE",
            f"Only {usable_model_outputs}/{run_count} runs produced schema-valid model results; rerun the complete cohort.",
        )
    return "VALID", "Model-backed outputs were available for scoring."


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("delivery_root", type=Path)
    parser.add_argument(
        "--work-dir", type=Path, default=ROOT / "artifacts" / "multitrack-20260822"
    )
    args = parser.parse_args()
    delivery = args.delivery_root.resolve()
    benchmark = args.work_dir.resolve()
    scorer_path = _single(list(delivery.rglob("score_runs.py")), "scorer")
    audit_path = _single(list(delivery.rglob("native_audit.py")), "native audit module")
    oracle_dir = _single(
        [
            path
            for path in delivery.rglob("private-oracles")
            if path.is_dir() and "01-测试集合" in path.parts
        ],
        "private oracle directory",
    )
    destination = benchmark / "cases" / "private-oracles"
    shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(oracle_dir, destination)

    spec = importlib.util.spec_from_file_location("multitrack_scorer", scorer_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load delivery scorer")
    # The archive keeps native_audit.py next to benchmark/ although the scorer
    # imports it as benchmark.native_audit. Recreate that package binding
    # without changing the delivered scorer or its formulas.
    audit_spec = importlib.util.spec_from_file_location(
        "benchmark.native_audit", audit_path
    )
    if audit_spec is None or audit_spec.loader is None:
        raise RuntimeError("cannot load delivery native audit module")
    benchmark_package = types.ModuleType("benchmark")
    benchmark_package.__path__ = [str(audit_path.parent)]
    audit_module = importlib.util.module_from_spec(audit_spec)
    sys.modules["benchmark"] = benchmark_package
    sys.modules["benchmark.native_audit"] = audit_module
    audit_spec.loader.exec_module(audit_module)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    scorer.BENCHMARK = benchmark

    rows = [
        scorer.score(manifest.parent, native=True)
        for manifest in sorted(
            (benchmark / "runs-native").glob("*/*/*/repeat-*/manifest.json")
        )
    ]
    failures = Counter(
        label for row in rows for label in row.get("failure_labels", [])
    )
    intervention_rows = [
        row for row in rows if row["interaction"]["intervention_observed"]
    ]
    validity, validity_reason = _run_validity(benchmark, len(rows))
    summary = {
        "schema": "mini-drop.multitrack-summary.v1",
        "run_validity": validity,
        "run_validity_reason": validity_reason,
        "run_count": len(rows),
        "eligible_run_count": sum(row["eligible_for_mainboard"] for row in rows),
        "root_location_top1": _average(rows, "reasoning", "root_location"),
        "mechanism_match": _average(rows, "reasoning", "mechanism"),
        "required_evidence_coverage": _average(rows, "reasoning", "evidence_validity"),
        "abstention_calibration": _average(rows, "reasoning", "abstention"),
        "intervention_revision_rate": round(
            sum(
                row["interaction"]["conclusion_revision_correct"] is True
                for row in intervention_rows
            )
            / len(intervention_rows),
            4,
        )
        if intervention_rows
        else 0.0,
        "excluded_evidence_reuse_count": sum(
            row["interaction"]["excluded_evidence_reuse"] for row in rows
        ),
        "blind_expert_obedience_count": sum(
            row["interaction"]["blind_expert_obedience"] for row in rows
        ),
        "failure_labels": dict(sorted(failures.items())),
        "results": rows,
        "comparability_note": (
            "Current-project native replay. Cross-agent ranking additionally requires "
            "the same model, prompt, runtime and dependency cohort."
        ),
    }
    output = benchmark / "current-project-score.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False, indent=2))
    print(f"SCORE={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
