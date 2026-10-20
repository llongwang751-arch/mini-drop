"""只在外层沙箱运行已登记、从源码构建的合成 BOLT 对照程序。

这是运维验收 harness，不是任意 ELF 执行器。调用者必须固定两侧路径，
并将原始 ELF 的 SHA 与刚编译的受控夹具核对；子进程输出只取摘要，不进入日志。
"""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import subprocess
import time


def execute(path: Path) -> dict:
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic_ns()
    result = subprocess.run(
        [str(path), "--once"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=20,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    wall_ns = time.monotonic_ns() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if result.returncode or result.stderr or len(result.stdout) > 128:
        raise RuntimeError("CONTROLLED_BOLT_FUNCTIONAL_CHECK_FAILED")
    return {
        "output_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "cpu_ns": round(
            (after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime)
            * 1e9
        ),
        "wall_ns": wall_ns,
        "exit_code": result.returncode,
    }


def measure(original: Path, optimized: Path, expected_sha256: str) -> dict:
    for path in (original, optimized):
        if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise RuntimeError("CONTROLLED_BOLT_BINARY_BUDGET_EXCEEDED")
        if path.read_bytes()[:4] != b"\x7fELF":
            raise RuntimeError("CONTROLLED_BOLT_NOT_ELF")
    if hashlib.sha256(original.read_bytes()).hexdigest() != expected_sha256:
        raise RuntimeError("CONTROLLED_BOLT_SOURCE_BINARY_MISMATCH")
    # 先预热，避免把首次页读取带来的磁盘差异误算为优化收益。
    baseline = execute(original)
    candidate = execute(optimized)
    if baseline["output_sha256"] != candidate["output_sha256"]:
        raise RuntimeError("CONTROLLED_BOLT_OUTPUT_MISMATCH")
    rows = []
    for index in range(6):
        pair = {}
        for label, path in (
            (("original", original), ("optimized", optimized))
            if index % 2 == 0
            else (("optimized", optimized), ("original", original))
        ):
            observation = execute(path)
            if observation["output_sha256"] != baseline["output_sha256"]:
                raise RuntimeError("CONTROLLED_BOLT_OUTPUT_MISMATCH")
            pair[label] = observation
        rows.append(pair)
    cpu_ratio = statistics.median(x["original"]["cpu_ns"] for x in rows) / max(
        1, statistics.median(x["optimized"]["cpu_ns"] for x in rows)
    )
    wall_ratio = statistics.median(x["original"]["wall_ns"] for x in rows) / max(
        1, statistics.median(x["optimized"]["wall_ns"] for x in rows)
    )
    return {
        "source": "freshly compiled controlled integer workload, live perf fdata and actual llvm-bolt output; no imported profile",
        "original_sha256": expected_sha256,
        "optimized_sha256": hashlib.sha256(optimized.read_bytes()).hexdigest(),
        "functional_equivalence_for_frozen_input": True,
        "paired_runs": rows,
        "cpu_median_ratio": cpu_ratio,
        "wall_median_ratio": wall_ratio,
        "performance_claim": "descriptive six-pair measurements only; no whole-service or statistical benefit guarantee",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--optimized", type=Path, required=True)
    parser.add_argument("--original-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(measure(args.original, args.optimized, args.original_sha256)))
