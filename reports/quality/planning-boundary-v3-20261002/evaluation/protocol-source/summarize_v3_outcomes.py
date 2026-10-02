"""Emit a public failure breakdown without queries or private retrieval labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    from server.app.agent_runtime.planning_output import validate_planning_output
    frozen = args.source / "benchmarks/retrieval"
    questions = {row["case_id"]: row for row in json.loads((frozen / "planning_boundary_v3_public.json").read_bytes())["cases"]}
    oracle = {row["case_id"]: row for row in json.loads((frozen / "planning_boundary_v3_private.json").read_bytes())["cases"]}
    manifest = json.loads((frozen / "planning_boundary_v3_manifest.json").read_bytes())
    report = json.loads((args.run / "report.json").read_bytes())
    failures = []
    for score in report["cases"]:
        if score["structure_valid"] and score["disposition_correct"] and score["tool_choice_expected"]:
            continue
        cid = score["case_id"]
        record = json.loads((args.run / "records" / (cid + ".json")).read_bytes())
        row = {"case_id": cid, "status": score["status"],
               "expected_dispositions": oracle[cid]["acceptable_dispositions"],
               "structure_valid": score["structure_valid"], "disposition_correct": score["disposition_correct"],
               "tool_choice_expected": score["tool_choice_expected"],
               "validated_disposition": score["output"]["disposition"] if score["output"] else None}
        if score["status"] == "OK":
            try:
                value = json.loads(record["response"]["choices"][0]["message"]["content"])
                validate_planning_output(value, manifest["runtime_tools"][questions[cid]["runtime"]])
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                row["invalid_dto_reason"] = str(exc)
        else:
            row["error_type"] = record.get("error_type")
        failures.append(row)
    result = {"schema": "mini-drop.v3-public-failure-summary.v1", "source_head": report["source_head"],
              "case_count": 32, "queries_disclosed": False, "private_retrieval_labels_disclosed": False,
              "provider_calls_attempted": 0, "original_report_or_scores_changed": False,
              "failures": failures}
    with args.output.open("xb") as stream:
        stream.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
