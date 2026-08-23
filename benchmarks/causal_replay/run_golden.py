"""Run the deterministic causal-replay Golden set.

This checks the verdict contract. It does not replace a live cloud Campaign.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from server.app.diagnosis.causal_replay import evaluate_plan, get_case


DATASET = Path(__file__).with_name("golden_cases.jsonl")


def run() -> dict:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    for row in rows:
        case = get_case(row["case_id"])
        plan = {"design_mode": row["design_mode"], "primary_metric": case["primary_metric"]}
        judgement = evaluate_plan(plan, row["measurements"])
        results.append({
            "run_id": row["run_id"],
            "expected": row["expected_verdict"],
            "actual": judgement["verdict"],
            "passed": judgement["verdict"] == row["expected_verdict"],
        })
    passed = sum(item["passed"] for item in results)
    return {
        "dataset": DATASET.name,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "verdict_coverage": dict(Counter(item["expected"] for item in results)),
        "results": results,
        "scope_note": "离线规则契约测试；真实采集与恢复能力需另跑云端 Campaign",
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
