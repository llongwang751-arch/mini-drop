"""Restore exact committed public runtime/evaluator blobs in an owned directory."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    assert destination.is_relative_to(ROOT) and not destination.exists(), "fresh owned source destination required"
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", args.head], cwd=ROOT, text=True).splitlines()
    selected = [name for name in names if name.startswith(("server/", "knowledge/", "benchmarks/retrieval/")) or name in {
        "scripts/evaluate_heldout_diagnosis.py", "scripts/evaluate_planning_retrieval_v2.py", "scripts/evaluate_planning_boundary_v3.py"}]
    assert "server/app/agent_runtime/planning_request.py" in selected
    assert "scripts/evaluate_planning_boundary_v3.py" in selected
    rows = {}
    for name in selected:
        raw = subprocess.check_output(["git", "show", args.head + ":" + name], cwd=ROOT)
        target = (destination / name).resolve()
        assert target.is_relative_to(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        rows[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    manifest = {"schema": "mini-drop.exact-v3-source-capture.v1", "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_head": args.head, "source_tree": subprocess.check_output(["git", "rev-parse", args.head + "^{tree}"], cwd=ROOT, text=True).strip(),
                "source_kind": "EXACT_COMMITTED_GIT_BLOBS", "provider_calls_attempted": 0,
                "file_count": len(rows), "files": rows}
    output = destination / "git-source-capture.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "CAPTURED", "source_head": args.head, "file_count": len(rows),
                      "manifest_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "destination": str(destination)}))


if __name__ == "__main__":
    main()
