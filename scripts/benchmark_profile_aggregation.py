"""Compare raw sampled stacks with lossless folded-stack preaggregation.

This is a synthetic transport/storage benchmark, not an overhead claim about a
production profiler.  Both encodings are decoded and compared exactly before a
report is written for the teaching UI/docs.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "web/public/report-assets/profiling/aggregation-report.json"


STACKS = [
    "main;http;router;orders;json_encode",
    "main;http;router;orders;database_query",
    "main;worker;queue;deserialize",
    "main;worker;queue;lock_wait",
    "main;runtime;gc_scan",
    "main;metrics;flush",
]
WEIGHTS = [32, 24, 18, 12, 9, 5]


def run(sample_count: int = 200_000, seed: int = 20260905) -> dict:
    rng = random.Random(seed)
    started = time.perf_counter()
    raw = [rng.choices(STACKS, weights=WEIGHTS, k=1)[0] for _ in range(sample_count)]
    raw_encode_ms = (time.perf_counter() - started) * 1000
    raw_bytes = json.dumps(raw, separators=(",", ":")).encode()

    started = time.perf_counter()
    folded = Counter(raw)
    aggregate_ms = (time.perf_counter() - started) * 1000
    folded_text = "".join(f"{stack} {count}\n" for stack, count in sorted(folded.items()))
    folded_bytes = folded_text.encode()
    reconstructed = Counter()
    for line in folded_text.splitlines():
        stack, _, count = line.rpartition(" ")
        reconstructed[stack] += int(count)
    exact = reconstructed == folded and sum(reconstructed.values()) == sample_count
    return {
        "schema": "mini-drop.profile-aggregation-benchmark.v1",
        "kind": "SYNTHETIC_LOSSLESS_TRANSPORT_COMPARISON",
        "seed": seed,
        "sample_count": sample_count,
        "unique_stack_count": len(folded),
        "correctness": {"exact_stack_counts_preserved": exact},
        "raw": {
            "bytes": len(raw_bytes),
            "gzip_bytes": len(gzip.compress(raw_bytes)),
            "encode_ms": round(raw_encode_ms, 3),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        },
        "preaggregated_folded": {
            "bytes": len(folded_bytes),
            "gzip_bytes": len(gzip.compress(folded_bytes)),
            "aggregate_ms": round(aggregate_ms, 3),
            "sha256": hashlib.sha256(folded_bytes).hexdigest(),
        },
        "ratios": {
            "plain_size_reduction": round(1 - len(folded_bytes) / len(raw_bytes), 6),
            "gzip_size_reduction": round(
                1 - len(gzip.compress(folded_bytes)) / len(gzip.compress(raw_bytes)), 6
            ),
        },
        "measurement_boundary": (
            "Synthetic repeated-stack encoding only; does not measure perf, eBPF, "
            "Pyroscope or Parca production CPU overhead."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=200_000)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not 1_000 <= args.samples <= 5_000_000:
        raise SystemExit("--samples must be between 1000 and 5000000")
    report = run(args.samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), **report["ratios"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
