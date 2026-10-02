#!/usr/bin/env python3
"""Run benchmark v2 and publish the exact report consumed by the Web UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from server.app.drop_insight.benchmark_v2 import evaluate_skill_reuse, load_json


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "benchmarks" / "diagnosis-v2"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "web"
        / "public"
        / "report-assets"
        / "skill-evolution"
        / "benchmark-report.json",
    )
    args = parser.parse_args()
    catalog = load_json(ROOT / "skills" / "catalog.json")
    public_set = load_json(args.dataset / "public" / "cases.json")
    oracles = load_json(args.dataset / "private" / "oracles.json")
    report = evaluate_skill_reuse(catalog, public_set, oracles)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "cases": report["dataset"]["case_count"],
                "baseline_accuracy": report["baseline_no_skill"]["accuracy"],
                "skill_accuracy": report["skill_enabled"]["accuracy"],
                "false_activation_rate": report["skill_enabled"]["false_activation_rate"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
