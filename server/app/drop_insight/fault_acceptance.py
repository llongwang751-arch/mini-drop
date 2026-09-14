"""Read the generated acceptance projection from the read-only evidence mount."""
import json
import os
from pathlib import Path


def latest_acceptance(scenario_id):
    path = Path(os.getenv("MINI_DROP_FAULT_ACCEPTANCE_INDEX", "/workspace-source/reports/ai-diagnosis/fault-plaza-acceptance-index.json"))
    try:
        if path.stat().st_size > 128 * 1024:
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema") != "mini-drop.fault-plaza-acceptance-index.v1":
            return None
        if not isinstance(document.get("scenarios"), dict):
            return None
        item = document["scenarios"].get(scenario_id)
        if not isinstance(item, dict):
            return None
        fields = ("passed", "lineage_verified", "root_cause_accepted", "recovery_observed", "cleanup_verified", "fix_verified")
        if any(type(item.get(field)) is not bool for field in fields):
            return None
        if item["passed"] != all(item[field] for field in fields[1:5]):
            return None
        if item["fix_verified"] is not False:
            return None
        if not item.get("finished_at") or not item.get("tested_release") or len(item.get("case_sha256", "")) != 64:
            return None
        return item
    except (OSError, ValueError, KeyError, TypeError):
        return None
