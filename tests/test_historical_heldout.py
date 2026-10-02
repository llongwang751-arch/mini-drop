"""Historical snapshots do not bypass current-source drift checks."""
import importlib.util
from pathlib import Path

import pytest

from scripts import evaluate_heldout_diagnosis as old
from scripts.verify_historical_heldout import historical_test_scope, restored_inputs, verify_historical_report


def test_snapshot_contains_only_original_26_registered_inputs():
    inputs = restored_inputs()
    assert len(inputs) == 26
    assert all(not name.startswith((".env", "output/", "private/")) for name in inputs)


def test_historical_scope_restores_globals_and_keeps_current_source_drift_protection():
    # Use a private module so existing legacy test globals remain independent.
    spec = importlib.util.spec_from_file_location("isolated_historical_test", Path(old.__file__))
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    before = evaluator.ROOT
    with historical_test_scope(evaluator) as owned:
        assert owned != before
        evaluator.validate_freeze()
        target = owned / "server/app/agent_runtime/retrieval.py"
        target.write_bytes(target.read_bytes() + b"\nDRIFT=True\n")
        with pytest.raises(ValueError, match="changed"):
            evaluator.validate_freeze()
    assert evaluator.ROOT == before


def test_archived_first_report_keeps_original_scores_and_explicit_historical_scope():
    path = Path(__file__).resolve().parents[1] / "reports/quality/interview-release-20261002/heldout/first-run/report.json"
    result = verify_historical_report(path)
    assert result["current_production_equivalence_claimed"] is False
    assert result["provider_calls"] == 0
    assert result["metrics"]["structure_valid_count"] == 15
    assert result["metrics"]["disposition_correct_count"] == 11
    assert result["metrics"]["no_answer_false_positive_rate"] == 0.75
