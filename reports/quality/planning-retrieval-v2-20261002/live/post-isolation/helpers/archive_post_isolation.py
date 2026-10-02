"""Archive actual read-only post-isolation evidence without changing first-run grades."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
DEST = ROOT / "reports/quality/planning-retrieval-v2-20261002/live/post-isolation"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not DEST.exists(), "preserve immutable attempts; choose a fresh subtree"
    DEST.mkdir(parents=True)
    shutil.copytree(STAGE / "live-smoke-r4", DEST / "normal-smoke")
    shutil.copytree(STAGE / "live-browser-r3", DEST / "browser")
    for source, name in [("live-runtime-audit-r4.json", "checkpoint-audit.json"),
                         ("old-checkpoints-preserved-r4.json", "old-checkpoints-preserved.json"),
                         ("normal-timeout-attribution.json", "timeout-attribution-raw.json"),
                         ("r5-publication-verification.json", "publication-verification.json")]:
        shutil.copyfile(STAGE / source, DEST / name)
    attribution = json.loads((STAGE / "normal-timeout-attribution.json").read_text(encoding="utf-8"))
    tails = {row["attempt"]: row["checkpoints"][-1] for row in attribution["attempts"]}
    assert all("branch:to:model" in row["channel_names"] and row["pending_error_types"] for row in tails.values())
    attribution["raw_receipt"] = {"path": "timeout-attribution-raw.json", "sha256": digest(DEST / "timeout-attribution-raw.json")}
    attribution["attribution"].update({
        "r3": "Two completed AI responses executed search_knowledge and search_incident_memory with matching ToolMessages. The final checkpoint passed DeadlineSummarizationMiddleware.before_model and has branch:to:model with a pending error; failure is at the main model node, not the summarization node. Initial immutable worker audit captured OpenAITimeoutError; no accepted finish or probe.",
        "r4": "First AI finish_diagnosis_plan proposed INVESTIGATE; the genuine validator rejected INVALID_PLANNING_OUTPUT because collection failure was used as refutation. A second graph invoke submitted corrective context. Its final checkpoint passed summarization and reached branch:to:model with a pending error; current worker logged OpenAITimeoutError. No accepted disposition or scheduled tool/task.",
        "r4_milestone_interval": {"start": "2026-10-02T10:09:00.039146+00:00", "end": "2026-10-02T10:09:37Z", "approximately_seconds": 37,
                                  "scope": "Checkpoint-to-log graph milestone interval; not an instrumented HTTP latency. Network/provider generation/remaining-timeout causes cannot be distinguished."},
    })
    (DEST / "timeout-attribution.json").write_text(json.dumps(attribution, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = STAGE / "final-release-r5/manifest.json"
    shutil.copyfile(manifest, DEST / "release-manifest.json")
    smoke = json.loads((DEST / "normal-smoke/summary.json").read_text(encoding="utf-8"))
    browser = json.loads((DEST / "browser/result.json").read_text(encoding="utf-8"))
    source_pins = {"planner_source_head": smoke["source_head"], "planner_release": smoke["release"],
                   "release_manifest": {"path": "release-manifest.json", "sha256": digest(DEST / "release-manifest.json")},
                   "publication_verification": {"path": "publication-verification.json", "sha256": digest(DEST / "publication-verification.json")},
                   "case_producers": browser["smoke_attempts"], "browser_ui_source_head": browser["source_head"],
                   "browser_ui_release": browser["release"], "normal_outcome": "FAILED_NO_ACCEPTED_DISPOSITION",
                   "ui_planning_cards": "Two genuine earlier missing/refused cards; no NORMAL card was persisted or fabricated."}
    (DEST / "source-pins.json").write_text(json.dumps(source_pins, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    helpers = DEST / "helpers"
    helpers.mkdir()
    for name in ("live_planning_smoke.py", "live_runtime_audit.py", "verify_legacy_checkpoint_preservation.py",
                 "attribute_normal_timeouts.py", "browser_planning_smoke.py", "browser_planning_smoke.mjs", "archive_post_isolation.py"):
        shutil.copyfile(STAGE / name, helpers / name)
    rows = [{"path": str(path.relative_to(DEST)).replace("\\", "/"), "sha256": digest(path), "bytes": path.stat().st_size}
            for path in sorted(DEST.rglob("*")) if path.is_file()]
    receipt = {"schema": "mini-drop.live-post-isolation-evidence.v1", "created_at": datetime.now(timezone.utc).isoformat(),
               "normal_smoke_status": smoke["status"], "normal_passed_cases": 0, "normal_total_cases": 1,
               "post_isolation_planner_invocations": 1, "all_completed_live_planner_invocations": 4,
               "provider_http_call_count": None, "provider_http_count_scope": "Planner turns are not equated to SDK/model HTTP calls; partial checkpoint counts are retained separately.",
               "heldout_evaluation": False, "frozen_24_calls_in_this_subtree": 0,
               "browser_status": browser["status"], "browser_planning_cards": 2, "browser_requested_states": 3,
               "browser_no_answer_notices": len(browser["no_answer_checks"]), "screenshots": len(browser["screenshots"]),
               "writes_performed_by_browser": browser["writes_performed"], "new_persisted_tasks": 0,
               "health_check_performed": False, "causal_root_cause_verified": False,
               "scope": "Actual r4 NORMAL failure, actual versioned PostgreSQL key and retained old keys, current read-only UI with source per case. Earlier raw attempts and grades remain immutable.",
               "files": rows}
    (DEST / "manifest.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(DEST), "files": len(rows), "manifest_sha256": digest(DEST / "manifest.json")}))


if __name__ == "__main__":
    main()
