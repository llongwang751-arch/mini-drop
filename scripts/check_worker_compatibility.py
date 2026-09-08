"""Emit a worker-side compatibility report without changing kernel policy."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
from pathlib import Path

from server.app.kernel_compatibility import evaluate_kernel_support


def _integer(path: str, default: int) -> int:
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--target-uid", type=int)
    parser.add_argument("--gperftools-opt-in", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    known_tools = {name for name in ("perf", "bpftrace", "py-spy", "mini-drop-gperftools-bridge") if shutil.which(name)}
    report = evaluate_kernel_support(
        release=platform.release(),
        perf_event_paranoid=_integer("/proc/sys/kernel/perf_event_paranoid", 99),
        capabilities=set(args.capability),
        tools=known_tools,
        same_uid=args.target_uid is not None and args.target_uid == os.geteuid(),
        ptrace_scope=_integer("/proc/sys/kernel/yama/ptrace_scope", 99),
        btf_available=Path("/sys/kernel/btf/vmlinux").is_file(),
        gperftools_opt_in=args.gperftools_opt_in,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
