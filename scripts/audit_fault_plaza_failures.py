"""Read-only historical audit. Never updates a campaign, score or Agent evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def evaluate_recorded_lineage(records, target):
    """Check the recorded Task -> attempt -> Artifact -> Analyzer -> Evidence chain.

    This checks server integrity attestations and hash equality, not a fresh
    object-store download. Supporting a hypothesis is a separate requirement.
    """
    target = target or {}
    diagnosis_target = (records.get("diagnosis") or {}).get("target") or {}
    binding = diagnosis_target.get("process_binding") or {}
    problems = []
    if not target.get("agent_id") or not isinstance(target.get("pid"), int) or isinstance(target.get("pid"), bool):
        problems.append("expected target is missing")
    if any(diagnosis_target.get(key) != target.get(key) for key in ("agent_id", "pid")):
        problems.append("diagnosis selected a different target")
    if not binding.get("boot_id") or not binding.get("process_start_ticks"):
        problems.append("immutable process binding is missing")
    tasks = {str(task.get("task_id")): task for task in records.get("tasks", [])}
    tool_tasks = {str(tool.get("task_id")) for tool in records.get("tool-calls", []) if tool.get("task_id")}
    admitted = []
    failures = []
    for evidence in records.get("evidence", []):
        envelope = evidence.get("envelope") or {}
        source = envelope.get("source") or {}
        scope = envelope.get("scope") or {}
        task_id = str(source.get("task_id") or "")
        task = tasks.get(task_id) or {}
        artifacts = {str(a.get("id")): a for a in task.get("artifacts", [])}
        artifact = artifacts.get(str(source.get("artifact_id"))) or {}
        attempt_ids = {str(a.get("attempt_id") or a.get("id")) for a in task.get("attempts", [])}
        decision = (evidence.get("classification") or {}).get("decision")
        digest = source.get("artifact_sha256")
        valid = (task_id in tool_tasks and task.get("status") == "DONE"
                 and task.get("analysis_status") == "SUCCESS" and task.get("event_count", 0) > 0
                 and str(source.get("task_attempt_id")) in attempt_ids
                 and artifact.get("integrity_status") == "VERIFIED"
                 and isinstance(digest, str) and len(digest) == 64
                 and all(ch in "0123456789abcdefABCDEF" for ch in digest)
                 and digest == artifact.get("sha256")
                 and source.get("analysis_job_id")
                 and (envelope.get("quality") or {}).get("analyzer_validated") is True
                 and decision in {"ACCEPT_SUPPORT", "ACCEPT_LIMITED"}
                 and all(scope.get(key) == target.get(key) for key in ("agent_id", "pid")))
        (admitted if valid else failures).append(evidence.get("evidence_id"))
    return {"recorded_chain_consistent": bool(admitted) and not problems,
            "matched_evidence_ids": admitted, "unmatched_evidence_ids": failures, "problems": problems,
            "scope": "RECORDED_SERVER_ATTESTATIONS; NO_FRESH_OBJECT_DOWNLOAD; NO_ROOT_CAUSE_GRADE"}


def audit_case(case):
    error = str(case.get("error") or "")
    if "auto-scope selected" in error:
        failure = "WRONG_TARGET"
    elif "ended as INSUFFICIENT_EVIDENCE" in error:
        failure = "OUTCOME_REJECTED_BY_LINEAGE_WRAPPER"
    elif "conclusion-supporting Evidence" in error:
        failure = "SUPPORT_COVERAGE_REJECTED_BY_LINEAGE_WRAPPER"
    elif error:
        failure = "OTHER_EXECUTION_FAILURE"
    else:
        failure = "LEGACY_LINEAGE_ACCEPTED"
    records = case.get("records") or {}
    reports = records.get("reports") or []
    return {"scenario_id": case["scenario_id"], "historical_lineage_verified": case.get("lineage_verified"),
            "historical_root_cause_accepted": case.get("root_cause_accepted"), "failure_category": failure,
            "original_error": error or None, "chain_audit": evaluate_recorded_lineage(records, case.get("target")),
            "reports": [{"report_id": report.get("report_id"),
                         "status": (report.get("verification") or {}).get("status"),
                         "claim_scope": (report.get("verification") or {}).get("claim_scope"),
                         "coverage_ratio": (report.get("verification") or {}).get("coverage_ratio"),
                         "independent_control": (report.get("verification") or {}).get("has_independent_counter_or_control")}
                        for report in reports],
            "same_load_fix_verified": case.get("fix_verified") is True}


def build_audit(case_dir):
    paths = sorted(Path(case_dir).glob("*.json"))
    rows = [audit_case(json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    return {"schema": "mini-drop.performance-failure-audit.v1", "historical_case_count": len(rows),
            "historical_root_passes": sum(row["historical_root_cause_accepted"] is True for row in rows),
            "recorded_chain_consistent_count": sum(row["chain_audit"]["recorded_chain_consistent"] for row in rows),
            "failure_categories": dict(Counter(row["failure_category"] for row in rows)),
            "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
            "cases": rows,
            "boundary": "Independent audit of archived records; old campaign and grades remain unchanged."
                        " No old case has a same-load code-fix verification merely because injection was stopped."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = build_audit(args.cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(audit, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(json.dumps({key: value for key, value in audit.items() if key not in {"cases", "source_sha256"}}, ensure_ascii=True))


if __name__ == "__main__":
    main()
