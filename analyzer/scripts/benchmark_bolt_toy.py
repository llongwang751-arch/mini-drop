"""Optimize and execute only the checksum toy compiled by this script.

Never used by the Analyzer worker; uploaded user ELF files are never executed.
"""

import hashlib
import json
from pathlib import Path
import statistics
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from drop_analyzer.external import optimize_bolt

SOURCE = r"""
#include <cstdint>
#include <cstdio>
__attribute__((noinline)) uint64_t work(uint64_t value) {
    for (unsigned i=0;i<30000000;++i) {
        if ((i & 31u) == 7u) value ^= uint64_t(i) * 1315423911u;
        else value = value * 6364136223846793005ull + uint64_t(i);
    }
    return value;
}
int main() { std::printf("%llu\n", static_cast<unsigned long long>(work(12345))); }
"""


def run(args, cwd):
    begin = time.monotonic()
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=40,
    )
    if len(result.stdout) > 1024 * 1024 or len(result.stderr) > 1024 * 1024:
        raise ValueError("Unexpected toy tool output size")
    return result.stdout, round(time.monotonic() - begin, 6)


with tempfile.TemporaryDirectory(prefix="bolt-equivalence-") as directory:
    work = Path(directory)
    (work / "toy.cpp").write_text(SOURCE)
    run(
        [
            "g++",
            "-O2",
            "-g",
            "-fno-pie",
            "-no-pie",
            "-Wl,--emit-relocs",
            work / "toy.cpp",
            "-o",
            work / "toy",
        ],
        work,
    )
    bolt = Path(shutil.which("llvm-bolt") or shutil.which("llvm-bolt-16") or "/usr/lib/llvm-16/bin/llvm-bolt").resolve()
    runtime = bolt.resolve().parent.parent / "lib/libbolt_rt_instr.a"
    if not runtime.is_file():
        raise RuntimeError("The installed BOLT instrumentation runtime is missing")
    run(
        [
            bolt,
            work / "toy",
            "-instrument",
            "-instrumentation-file=" + str(work / "toy.fdata"),
            "-o",
            work / "toy-instrumented",
        ],
        work,
    )
    expected, _ = run([work / "toy-instrumented"], work)
    optimization = optimize_bolt(
        str(work / "toy.fdata"), str(work / "toy"), work_dir=work
    )
    (work / "optimized.elf").chmod(0o755)
    runs = []
    for index in range(3):
        for name in ("toy", "optimized.elf"):
            output, elapsed = run([work / name], work)
            if output != expected:
                raise ValueError("Optimized toy changed its checksum output")
            runs.append(
                {
                    "iteration": index + 1,
                    "binary": name,
                    "exit_code": 0,
                    "stdout": output.strip(),
                    "elapsed_seconds": elapsed,
                }
            )
    report = {
        "provenance": "Only this script's freshly compiled deterministic checksum toy was executed in an isolated network-none container; no uploaded or business executable was run.",
        "limitations": "Three alternating runs on a shared single CPU quota are a smoke benchmark, not an optimization benefit claim or an SLA. Only checksum/exit-code equivalence is established.",
        "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
        "elf_sha256": hashlib.sha256((work / "toy").read_bytes()).hexdigest(),
        "optimized_sha256": hashlib.sha256(
            (work / "optimized.elf").read_bytes()
        ).hexdigest(),
        "profile_sha256": hashlib.sha256((work / "toy.fdata").read_bytes()).hexdigest(),
        "profile_bytes": (work / "toy.fdata").stat().st_size,
        "compiler": run(["g++", "--version"], work)[0].splitlines()[0],
        "bolt_version": run([bolt, "--version"], work)[0].strip(),
        "optimization": optimization,
        "runs": runs,
        "median_seconds": {
            name: statistics.median(
                row["elapsed_seconds"] for row in runs if row["binary"] == name
            )
            for name in ("toy", "optimized.elf")
        },
        "functionally_equivalent_for_toy_input": True,
    }
    output = Path(sys.argv[1])
    output.write_text(json.dumps(report, indent=2), encoding="utf8")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "optimization"}
        ),
        flush=True,
    )
