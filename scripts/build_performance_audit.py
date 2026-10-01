"""Generate the performance history view from pinned, unchanged campaign records."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from scripts.audit_fault_plaza_failures import build_audit
except ModuleNotFoundError:
    from audit_fault_plaza_failures import build_audit

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "contracts/performance_audit.json"
OUTPUT = ROOT / "web/public/report-assets/performance-audit/index.json"


def generate(root=ROOT):
    plan = json.loads((root / "contracts/performance_audit.json").read_text(encoding="utf-8"))
    directory = (root / plan["case_directory"]).resolve()
    if not directory.is_relative_to(root.resolve()):
        raise ValueError("case directory outside workspace")
    audit = build_audit(directory)
    if audit["source_sha256"] != plan["source_sha256"]:
        raise ValueError("historical evidence differs from pinned contract")
    if audit["historical_case_count"] != 21 or audit["historical_root_passes"] != 0:
        raise ValueError("this view must not replace the historical 21-case baseline")
    audit["campaign_date"] = "2026-09-30"
    audit["download_url"] = "/report-assets/performance-audit/index.json"
    audit["new_live_acceptance"] = "NOT_EVALUATED"
    return json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = generate()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("performance history index is stale; run its generator")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected, encoding="utf-8")
    print("Performance history evidence and generated view verified")


if __name__ == "__main__":
    main()
