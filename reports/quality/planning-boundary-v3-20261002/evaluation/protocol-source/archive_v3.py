"""Archive successful/failed v3 raw outcomes without changing reports or old suites."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
BASE = ROOT / "reports/quality/planning-boundary-v3-20261002"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def copy_exact(source, target):
    assert source.is_file() and target.resolve().is_relative_to(BASE.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    assert not target.exists(), "archive cannot overwrite an existing receipt"
    shutil.copyfile(source, target)
    assert source.read_bytes() == target.read_bytes()


def copy_tree(source, target):
    assert not target.exists(), "archive subtree already exists"
    for path in sorted(source.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            copy_exact(path, target / path.relative_to(source))


def manifest(directory, head, scope):
    files = {path.relative_to(directory).as_posix(): {"sha256": sha(path.read_bytes()), "bytes": path.stat().st_size}
             for path in sorted(directory.rglob("*")) if path.is_file() and path.name != "archive-manifest.json"}
    result = {"schema": "mini-drop.immutable-v3-evidence-subarchive.v1", "archived_at_utc": datetime.now(timezone.utc).isoformat(),
              "source_head": head, "scope": scope, "third_party_independent_author": False,
              "question_or_truth_tuned_after_run": False, "reports_overwritten": False,
              "old_evidence_changed": False, "file_count": len(files), "files": files}
    destination = directory / "archive-manifest.json"
    with destination.open("xb") as stream:
        stream.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    return {"path": destination.relative_to(ROOT).as_posix(), "sha256": sha(destination.read_bytes()), "file_count": len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True)
    parser.add_argument("--first-run", type=Path, required=True)
    parser.add_argument("--hybrid", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_bytes())
    assert audit["status"] == "VERIFIED" and audit["source_head"] == args.head
    evaluation = BASE / "evaluation"
    retrieval = BASE / "retrieval"
    original = BASE / "original-source"
    copy_tree(args.first_run, evaluation / "first-run")
    copy_tree(args.hybrid, retrieval / "hybrid")
    copy_exact(args.audit, evaluation / "independent-audit.json")
    for name in ("freeze_v3.py", "capture_v3_source.py", "audit_v3.py", "archive_v3.py", "summarize_v3_outcomes.py"):
        copy_exact(STAGE / name, evaluation / "protocol-source" / name)
    # Preserve the zero-budget preflight failure and its real source repair
    # separately from the only model batch. No receipt is silently replaced.
    for name in ("model-first-run.log", "model-preflight-source-diagnostic.json",
                 "diagnose_model_preflight.py", "v3-publication-metadata-compatibility.json"):
        copy_exact(STAGE / name, evaluation / "preflight" / name)
    for name in ("model-after-provenance.log", "model-replay-verification.json", "model-replay-verification.log",
                 "independent-audit.log", "failure-summary.json"):
        copy_exact(STAGE / name, evaluation / "receipts" / name)
    copy_exact(STAGE / "budget/chat-first-run.json", evaluation / "receipts/chat-budget-ledger.json")
    copy_exact(STAGE / "hybrid-first-run.log", retrieval / "receipts/hybrid-first-run.log")
    copy_exact(STAGE / "budget/hybrid-first-run.json", retrieval / "receipts/hybrid-budget-ledger.json")
    if args.source.resolve() != original.resolve():
        copy_tree(args.source, original)
    source_capture = json.loads((original / "git-source-capture.json").read_bytes())
    assert source_capture["source_head"] == args.head
    for name, row in source_capture["files"].items():
        raw = (original / name).read_bytes()
        assert sha(raw) == row["sha256"] and len(raw) == row["bytes"]
    receipts = [manifest(evaluation, args.head, "FROZEN_32_REAL_CHAT_JSON_DTO_AND_OFFLINE_BM25_NO_AGENT_EXECUTION"),
                manifest(retrieval, args.head, "SAME_FROZEN_32_ACTUAL_READ_ONLY_HYBRID_NO_CHAT_OR_INDEX_WRITES"),
                manifest(original, args.head, "EXACT_GIT_RUNTIME_EVALUATOR_KNOWLEDGE_AND_FROZEN_QUESTIONS_FOR_OFFLINE_REPLAY")]
    for receipt in receipts:
        path = ROOT / receipt["path"]
        assert sha(path.read_bytes()) == receipt["sha256"]
        captured = json.loads(path.read_bytes())
        assert captured["file_count"] == receipt["file_count"] == len(captured["files"])
        assert set(captured["files"]) == {p.relative_to(path.parent).as_posix() for p in path.parent.rglob("*")
                                           if p.is_file() and p.name != "archive-manifest.json"}
        for name, row in captured["files"].items():
            evidence = (path.parent / name).resolve()
            assert evidence.is_relative_to(path.parent.resolve())
            raw = evidence.read_bytes()
            assert sha(raw) == row["sha256"] and len(raw) == row["bytes"]
    print(json.dumps({"status": "ARCHIVED", "source_head": args.head,
                      "byte_integrity_verified": True, "subarchives": receipts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
