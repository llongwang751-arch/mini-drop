"""Convert an actual verbose unittest log into an auditable JSON report."""

import argparse
import hashlib
import json
from pathlib import Path
import re


parser = argparse.ArgumentParser()
parser.add_argument("log", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--image", required=True)
args = parser.parse_args()
raw = args.log.read_bytes()
text = raw.decode("utf8")
summary = re.search(r"Ran (\d+) tests in ([\d.]+)s", text)
cases = [
    {"name": match[1], "result": match[2]}
    for match in re.finditer(
        r"^(test_\S+ \([^)]+\)) \.\.\. (ok|FAIL|ERROR|skipped.*)$",
        text,
        flags=re.MULTILINE,
    )
]
report = {
    "provenance": "Actual isolated Linux test run; no production database or data volumes.",
    "image": args.image,
    "python": "3.12.12",
    "uid": 10001,
    "postgres": "17.11",
    "minio": "RELEASE.2025-07-23T15-54-02Z",
    "log_file": args.log.name,
    "log_sha256": hashlib.sha256(raw).hexdigest(),
    "tests_run": int(summary[1]) if summary else None,
    "duration_seconds": float(summary[2]) if summary else None,
    "passed": sum(case["result"] == "ok" for case in cases),
    "skipped": sum(case["result"].startswith("skipped") for case in cases),
    "failed": sum(case["result"] in {"FAIL", "ERROR"} for case in cases),
    "success": text.rstrip().endswith("OK"),
    "cases": cases,
}
args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
if not report["success"] or len(cases) != report["tests_run"]:
    raise SystemExit("Incomplete or unsuccessful test evidence")
print(json.dumps({key: value for key, value in report.items() if key != "cases"}))
