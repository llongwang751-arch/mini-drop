import json
from scripts.build_fault_plaza_acceptance_index import verified_json
from server.app.drop_insight.fault_acceptance import latest_acceptance
import pytest


def test_projection_does_not_promote_partial_or_malformed_results(tmp_path, monkeypatch):
    path = tmp_path / "index.json"
    monkeypatch.setenv("MINI_DROP_FAULT_ACCEPTANCE_INDEX", str(path))
    item = {"passed": True, "lineage_verified": True, "root_cause_accepted": False,
            "recovery_observed": True, "cleanup_verified": True, "fix_verified": False,
            "finished_at": "2026-09-10T09:00:00Z", "tested_release": "release", "case_sha256": "a" * 64}
    def write():
        path.write_text(json.dumps({"schema": "mini-drop.fault-plaza-acceptance-index.v1", "scenarios": {"cpu-hotspot": item}}), encoding="utf-8")
    write()
    assert latest_acceptance("cpu-hotspot") is None
    item["passed"] = False
    write()
    assert latest_acceptance("cpu-hotspot")["passed"] is False
    path.write_text("[]", encoding="utf-8")
    assert latest_acceptance("cpu-hotspot") is None
    path.unlink()
    assert latest_acceptance("cpu-hotspot") is None


def test_changed_raw_evidence_is_rejected(tmp_path):
    path = tmp_path / "case.json"
    path.write_text(json.dumps({"passed": True, "report_sha256": "f" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verified_json(path)
