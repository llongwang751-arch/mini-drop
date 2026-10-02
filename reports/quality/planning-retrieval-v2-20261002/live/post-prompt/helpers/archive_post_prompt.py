"""Archive the single actual post-prompt live trial, including failures honestly."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
DEST = ROOT / "reports/quality/planning-retrieval-v2-20261002/live/post-prompt"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    args = parser.parse_args()
    assert not DEST.exists(), "preserve earlier receipts; never rewrite attempts"
    smoke = json.loads((STAGE / "live-smoke-r5/summary.json").read_text(encoding="utf-8"))
    browser = json.loads((STAGE / "live-browser-r4/result.json").read_text(encoding="utf-8"))
    audit = json.loads((STAGE / "live-runtime-audit-r5.json").read_text(encoding="utf-8"))
    release = json.loads(args.manifest.read_text(encoding="utf-8"))
    verification = json.loads(args.verification.read_text(encoding="utf-8"))
    assert smoke["source_head"] == browser["source_head"] == release["git_head"] == verification["source_head"]
    assert smoke["release"] == browser["release"] == verification["release"]
    assert smoke["selected_cases"] == ["normal"] and smoke["planner_invocations"] <= 1
    assert all(row["name"] == "normal" for row in smoke["cases"])
    DEST.mkdir(parents=True)
    shutil.copytree(STAGE / "live-smoke-r5", DEST / "normal-smoke")
    shutil.copytree(STAGE / "live-browser-r4", DEST / "browser")
    shutil.copyfile(STAGE / "live-runtime-audit-r5.json", DEST / "checkpoint-audit.json")
    shutil.copyfile(args.manifest, DEST / "release-manifest.json")
    shutil.copyfile(args.verification, DEST / "publication-verification.json")
    pins = {"source_head": smoke["source_head"], "release": smoke["release"], "agent_version": "diagnosis-agent-v7-four-state-prompts",
            "release_manifest": {"path": "release-manifest.json", "sha256": digest(DEST / "release-manifest.json")},
            "publication_verification": {"path": "publication-verification.json", "sha256": digest(DEST / "publication-verification.json")},
            "case_producers": browser["smoke_attempts"], "browser_ui_source_head": browser["source_head"],
            "normal_outcome": smoke["status"], "normal_expected_disposition": "NORMAL",
            "actual_dispositions": [row.get("actual_disposition") for row in smoke["cases"]],
            "accepted_planning_output": [row.get("accepted_proposal") for row in audit["checkpoint_audit"]],
            "schema": "mini-drop.planning-output.v2", "heldout_evaluation": False}
    (DEST / "source-pins.json").write_text(json.dumps(pins, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    helpers = DEST / "helpers"
    helpers.mkdir()
    for name in ("live_planning_smoke.py", "live_runtime_audit.py", "browser_planning_smoke.py", "browser_planning_smoke.mjs", "archive_post_prompt.py"):
        shutil.copyfile(STAGE / name, helpers / name)
    all_turns = sum(json.loads((STAGE / ("live-smoke-" + name) / "summary.json").read_text(encoding="utf-8"))["planner_invocations"] for name in ["r3", "r4", "r5"])
    rows = [{"path": str(path.relative_to(DEST)).replace("\\", "/"), "sha256": digest(path), "bytes": path.stat().st_size}
            for path in sorted(DEST.rglob("*")) if path.is_file()]
    receipt = {"schema": "mini-drop.live-post-prompt-evidence.v1", "created_at": datetime.now(timezone.utc).isoformat(),
               "normal_smoke_status": smoke["status"], "normal_passed_cases": sum(bool(row["passed"]) for row in smoke["cases"]),
               "normal_total_cases": len(smoke["cases"]), "normal_expected_disposition": "NORMAL",
               "actual_dispositions": [row.get("actual_disposition") for row in smoke["cases"]],
               "actual_valid_output_accepted": all(bool(row.get("accepted_proposal")) for row in audit["checkpoint_audit"]),
               "failure_scope": "Expected NORMAL label did not match genuine INSUFFICIENT_EVIDENCE; no timeout, invalid plan, lookup loop, or action was observed.",
               "post_prompt_planner_invocations": smoke["planner_invocations"],
               "all_completed_live_planner_invocations": all_turns, "provider_http_call_count": None,
               "provider_http_count_scope": "Planner invocations do not count underlying SDK HTTP requests; safe checkpoint structures are preserved.",
               "heldout_evaluation": False, "frozen_24_calls_in_this_subtree": 0,
               "versioned_checkpoint_verified": audit["versioned_checkpoint_verified"], "browser_status": browser["status"],
               "browser_planning_cards": browser["rendered_live_cases"], "browser_requested_states": browser["total_live_cases"],
               "browser_no_answer_notices": len(browser["no_answer_checks"]), "screenshots": len(browser["screenshots"]),
               "browser_writes": browser["writes_performed"], "new_persisted_tasks": sum(row["persisted_tasks"] for row in smoke["persisted_task_counts"]),
               "health_check_performed": False, "causal_root_cause_verified": False,
               "scope": "One fresh same-query NORMAL trial after disposition-first prompt deployment. Initial r3 and isolated r4 failures are immutable; earlier missing/refused producer sources remain explicit.", "files": rows}
    (DEST / "manifest.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(DEST), "files": len(rows), "normal_status": smoke["status"], "browser_status": browser["status"],
                      "manifest_sha256": digest(DEST / "manifest.json")}))


if __name__ == "__main__":
    main()
