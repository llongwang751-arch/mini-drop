import json
from pathlib import Path

import pytest

from scripts.build_performance_audit import generate

ROOT = Path(__file__).resolve().parents[1]


def test_generated_history_does_not_turn_engineering_regressions_into_root_passes():
    index = json.loads(generate())
    assert index["historical_case_count"] == len(index["cases"]) == 21
    assert index["historical_root_passes"] == 0
    assert index["new_live_acceptance"] == "NOT_EVALUATED"
    assert sum(index["failure_categories"].values()) == 21


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
