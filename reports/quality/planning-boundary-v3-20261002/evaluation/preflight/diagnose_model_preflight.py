"""Read only public metadata/source hashes; no provider evaluation request."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
SOURCE = ROOT / "reports/quality/planning-boundary-v3-20261002/original-source"
sys.path.insert(0, str(SOURCE))
from scripts import evaluate_planning_boundary_v3 as evaluation


def main():
    head = "cfea6744fc2395d5392440b2404ee275ada0a69f"
    contract = evaluation.candidate_contract(head)
    helper = evaluation.load_remote_helper(ROOT / "output/acceptance/deployment-20260930/run_strict.py")
    metadata = evaluation.remote_metadata(helper)
    names = [name for name in evaluation.SOURCE_PATHS if name.startswith("server/")]
    deployed = evaluation.remote_public_source_receipt(helper, names, evaluation.validate_freeze()[0]["runtime_tools"])
    expected = {"sources": {name: contract["raw_source_sha256"][name] for name in names},
                "corpus": contract["corpus_lf_sha256"], "schemas": contract["schemas"]}
    receipt = {"schema": "mini-drop.v3-model-preflight-public-diagnostic.v1", "provider_calls_attempted": 0,
               "expected_source_head": head, "metadata": metadata, "expected_prompt_sha256": contract["public_prompt_sha256"],
               "deployed_critical_source_corpus_schema_match": deployed == expected,
               "deployed_prompt_match": metadata.get("production_prompt_sha256") == contract["public_prompt_sha256"],
               "published_manifest_head_match": metadata.get("published_source_head") == head,
               "actual_deployed_source_receipt": deployed}
    output = STAGE / "model-preflight-source-diagnostic.json"
    with output.open("xb") as stream:
        stream.write((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({key: receipt[key] for key in ("provider_calls_attempted", "expected_source_head", "deployed_critical_source_corpus_schema_match", "deployed_prompt_match", "published_manifest_head_match")}
                     | {"metadata_published_source_head": metadata.get("published_source_head")}))


if __name__ == "__main__":
    main()
