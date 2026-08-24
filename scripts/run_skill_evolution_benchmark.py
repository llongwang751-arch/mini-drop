#!/usr/bin/env python3
"""Run and persist the independent diagnostic-strategy evolution benchmark."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_benchmark_module():
    path = ROOT / "tests" / "test_diagnostic_skill_benchmark.py"
    spec = importlib.util.spec_from_file_location("skill_evolution_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load skill evolution benchmark")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "skill-evolution" / "benchmark-report.json",
    )
    args = parser.parse_args()
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"

    benchmark = _load_benchmark_module()
    benchmark.reset_engine()
    benchmark.init_db()
    try:
        cases = benchmark._load_cases()
        result = benchmark.compare_benchmark_runs(
            cases,
            benchmark._baseline_observations(cases),
            benchmark._skill_enabled_observations(cases),
        )
        # The ordered campaign must start from a clean registry. Otherwise the
        # independent replay above would leak skill versions into its state.
        benchmark.reset_engine()
        benchmark.init_db()
        longitudinal = benchmark.run_longitudinal_adversarial_campaign()
    finally:
        benchmark.reset_engine()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"independent_replay": result, "longitudinal_campaign": longitudinal},
            ensure_ascii=False,
            indent=2,
            default=str,
        ) + "\n",
        encoding="utf-8",
    )
    summary = {
        "benchmark_kind": result["benchmark_kind"],
        "case_count": result["skill_enabled"]["total"],
        "baseline_passed": result["baseline"]["passed"],
        "skill_enabled_passed": result["skill_enabled"]["passed"],
        "delta": result["delta"],
        "metrics": result["skill_enabled"]["metrics"],
        "claim_boundary": result["claim_boundary"],
        "longitudinal_campaign": {
            "case_count": longitudinal["case_count"],
            "passed": longitudinal["passed"],
            "pass_rate": longitudinal["pass_rate"],
            "capabilities": longitudinal["capabilities"],
            "claim_boundary": longitudinal["claim_boundary"],
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"REPORT={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
