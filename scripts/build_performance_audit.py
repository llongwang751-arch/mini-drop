"""Publish a score-free replacement marker for the retired performance index."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "contracts/performance_audit.json"
OUTPUT = ROOT / "web/public/report-assets/performance-audit/index.json"


def generate(root=ROOT):
    plan = json.loads((root / "contracts/performance_audit.json").read_text(encoding="utf-8"))
    publication = plan.get("publication")
    if publication != {
        "status": "RETIRED",
        "replacement_profile": "engineering-diagnosis.v1",
        "replacement_url": "/report-assets/engineering-diagnosis/index.json",
    }:
        raise ValueError("retired publication policy must point to engineering diagnosis")
    directory = (root / plan["case_directory"]).resolve()
    if not directory.is_relative_to(root.resolve()):
        raise ValueError("case directory outside workspace")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(directory.glob("*.json")) if path.is_file()}
    if hashes != plan["source_sha256"]:
        raise ValueError("historical evidence differs from pinned contract")
    marker = {"schema": "mini-drop.retired-performance-index.v1",
              "status": publication["status"],
              "replacement_profile": publication["replacement_profile"],
              "replacement_url": publication["replacement_url"]}
    return json.dumps(marker, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = generate()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("retired performance marker is stale; run its generator")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected, encoding="utf-8")
    print("Score-free performance replacement marker and pinned bytes verified")


if __name__ == "__main__":
    main()
