"""Build the UI projection from completed, hash-verified campaign evidence."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_fault_plaza_closure_campaign import _canonical_sha256, _write_json_atomic
from scripts.run_fault_plaza_strict_acceptance import ORACLES


def verified_json(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {k: v for k, v in value.items() if k != "report_sha256"}
    if _canonical_sha256(unsigned) != value.get("report_sha256"):
        raise ValueError(f"evidence hash mismatch: {path}")
    return value


def build(campaigns, output):
    entries = {}
    for source, release in campaigns:
        source = Path(source)
        campaign = verified_json(source)
        if campaign.get("run_status") != "COMPLETED":
            raise ValueError("Only completed campaigns can populate acceptance badges")
        for row in campaign["results"]:
            sid = row["scenario_id"]
            if sid not in ORACLES:
                raise ValueError("unknown scenario contract")
            case = verified_json(source.parent / (source.stem + "-cases") / (sid + ".json"))
            if row.get("report_sha256") != case["report_sha256"]:
                raise ValueError("summary does not reference this case")
            item = {"diagnosis_id": case.get("diagnosis_id"), "finished_at": case["finished_at"],
                    "tested_release": release, "passed": case["passed"],
                    "lineage_verified": case["lineage_verified"],
                    "root_cause_accepted": case["root_cause_accepted"],
                    "recovery_observed": case["intervention"]["recovery_observed"],
                    "cleanup_verified": case["cleanup_verified"], "fix_verified": False,
                    "source_report": source.name, "source_sha256": campaign["report_sha256"],
                    "case_sha256": case["report_sha256"]}
            if sid not in entries or item["finished_at"] > entries[sid]["finished_at"]:
                entries[sid] = item
    _write_json_atomic(Path(output), {"schema": "mini-drop.fault-plaza-acceptance-index.v1", "scenarios": entries})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", nargs=2, action="append", required=True, metavar=("JSON", "RELEASE"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.campaign, args.output)
