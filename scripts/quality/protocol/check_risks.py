"""根据真实 go test -json 和 coverprofile 检查关键场景，不伪称全仓覆盖率。"""

import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
POLICY = Path(__file__).with_name("risk-policy.json")


def assess(policy, events, functions):
    terminal = {}
    for event in events:
        name, action = event.get("Test"), event.get("Action")
        if name and action in ("pass", "skip", "fail"):
            terminal[name] = action
    required = {name: terminal.get(name, "not_run") for name in policy["required_tests"]}
    checks = []
    for definition in policy["functions"]:
        measured = functions.get((definition["file"], definition["function"]))
        checks.append({"risk_id": definition["risk_id"], "function": definition["function"], "statement_coverage_percent": measured, "minimum_percent": definition["minimum_percent"], "passed": measured is not None and measured >= definition["minimum_percent"]})
    return {"schema_version": 1, "passed": all(state == "pass" for state in required.values()) and all(check["passed"] for check in checks) and not any(event.get("Action") == "fail" for event in events), "coverage_scope": policy["coverage_scope"], "required_scenarios": required, "checks": checks, "tests_passed_including_subtests": sum(state == "pass" for state in terminal.values()), "tests_skipped_including_subtests": sum(state == "skip" for state in terminal.values()), "note": "Scenario presence plus measured production function statements; this is not proof of all branches or semantic completeness"}


def parse_functions(text):
    values = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(.+):\d+:\s+(\w+)\s+([0-9]+(?:\.[0-9]+)?)%\s*", line)
        if match:
            key = (match[1], match[2])
            if key in values:
                raise ValueError("DUPLICATE_COVERAGE_FUNCTION")
            values[key] = float(match[3])
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.events.stat().st_size > 16 * 1024 * 1024 or args.coverage.stat().st_size > 16 * 1024 * 1024:
        raise SystemExit("RISK_INPUT_BOUND_EXCEEDED")
    events = [json.loads(line) for line in args.events.read_text(encoding="utf-8").splitlines() if line.strip()]
    process = subprocess.run(["go", "tool", "cover", "-func", str(args.coverage.resolve())], cwd=ROOT / "api", capture_output=True, text=True, timeout=30)
    if process.returncode or len(process.stdout) > 1024 * 1024:
        raise SystemExit("COVERAGE_TOOL_FAILED")
    report = assess(json.loads(POLICY.read_text(encoding="utf-8")), events, parse_functions(process.stdout))
    if args.output.exists():
        raise SystemExit("REFUSING_TO_OVERWRITE_RISK_REPORT")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
