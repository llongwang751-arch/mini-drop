#!/usr/bin/env python3
"""Print a machine-readable TLinux/collector compatibility report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.mini_drop_agent.main import COLLECTORS  # noqa: E402
from agent.mini_drop_agent.platform_compat import build_compatibility_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Mini-Drop TLinux compatibility preflight")
    parser.add_argument("--json", action="store_true", help="emit compact JSON")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        help="collector that must be available (repeatable)",
    )
    args = parser.parse_args()
    report = build_compatibility_report(COLLECTORS.keys())
    print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2))

    host = report["host"]
    if host["is_tlinux"] and not report["supported_tlinux_generation"]:
        print("unsupported TLinux generation", file=sys.stderr)
        return 2
    missing = sorted(set(args.require) - set(report["available_collectors"]))
    if missing:
        print(f"missing required collectors: {', '.join(missing)}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
