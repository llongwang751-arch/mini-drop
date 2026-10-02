import json
from pathlib import Path

import pytest

from scripts.build_performance_audit import generate

ROOT = Path(__file__).resolve().parents[1]


def test_retired_public_index_contains_only_the_new_engineering_pointer():
    index = json.loads(generate())
    assert index == {
        "schema": "mini-drop.retired-performance-index.v1",
        "status": "RETIRED",
        "replacement_profile": "engineering-diagnosis.v1",
        "replacement_url": "/report-assets/engineering-diagnosis/index.json",
    }


def test_changed_archived_evidence_is_rejected_instead_of_changing_the_score(tmp_path):
    plan = json.loads((ROOT / "contracts/performance_audit.json").read_text(encoding="utf-8"))
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/performance_audit.json").write_text(json.dumps(plan), encoding="utf-8")
    cases = tmp_path / plan["case_directory"]
    cases.mkdir(parents=True)
    for original in (ROOT / plan["case_directory"]).glob("*.json"):
        (cases / original.name).write_bytes(original.read_bytes())
    modified = cases / "cpu-hotspot.json"
    modified.write_bytes(modified.read_bytes() + b" ")
    with pytest.raises(ValueError, match="pinned"):
        generate(tmp_path)


@pytest.mark.parametrize("mutation", ["missing", "active", "wrong_replacement"])
def test_old_scores_cannot_be_republished_by_changing_the_policy(tmp_path, mutation):
    plan = json.loads((ROOT / "contracts/performance_audit.json").read_text(encoding="utf-8"))
    if mutation == "missing":
        plan.pop("publication")
    elif mutation == "active":
        plan["publication"]["status"] = "ACTIVE"
    else:
        plan["publication"]["replacement_url"] = "/old-score.json"
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/performance_audit.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="retired publication"):
        generate(tmp_path)
