from __future__ import annotations

import argparse
import json
from pathlib import Path

from server.app.drop_insight.retrieval_benchmark import load_benchmark, run_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Mini-Drop Skill retrieval")
    parser.add_argument(
        "--dataset",
        default="tests/fixtures/skill_retrieval_benchmark.json",
    )
    parser.add_argument(
        "--output",
        default="artifacts/skill-retrieval/benchmark-report.json",
    )
    args = parser.parse_args()

    report = run_benchmark(load_benchmark(args.dataset))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered + "\n", encoding="utf-8")
    print("method\taccuracy\tpositive@1\tnegative-reject")
    for method, metric in report["metrics"].items():
        print(
            f"{method}\t{metric['accuracy']:.1%}\t"
            f"{metric['positive_recall_at_1']:.1%}\t"
            f"{metric['negative_rejection_rate']:.1%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
