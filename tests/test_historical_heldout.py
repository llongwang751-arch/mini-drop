"""Historical snapshots do not bypass current-source drift checks."""
import importlib.util
import json
from pathlib import Path

import pytest

from scripts import evaluate_heldout_diagnosis as old
from scripts.verify_historical_heldout import (
    historical_numeric_sum, historical_test_scope, restored_inputs, verify_historical_report,
)


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


def test_original_recall_aggregation_replays_exact_arithmetic_without_tolerance():
    path = Path(__file__).resolve().parents[1] / "reports/quality/interview-release-20261002/heldout/first-run/report.json"
    report = json.loads(path.read_bytes())
    values = [row["recall_at_3"] for row in report["cases"] if row["relevant_ids"]]
    naive = 0
    for value in values:
        naive += value
    assert naive / 20 == 0.7583333333333334
    assert historical_numeric_sum(values) / 20 == report["metrics"]["recall_at_3"] == 0.7583333333333333
    assert historical_numeric_sum(values) / 20 != naive / 20
    assert type(historical_numeric_sum([True, False, 2])) is int
    assert historical_numeric_sum([2 ** 100, 1]) == 2 ** 100 + 1


def test_historical_arithmetic_binding_is_local_and_restored_on_exit():
    spec = importlib.util.spec_from_file_location("isolated_sum_replay_test", Path(old.__file__))
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    assert "sum" not in evaluator.__dict__
    with historical_test_scope(evaluator):
        assert evaluator.sum is historical_numeric_sum
    assert "sum" not in evaluator.__dict__


def test_even_one_ulp_historical_report_change_is_rejected(tmp_path):
    path = Path(__file__).resolve().parents[1] / "reports/quality/interview-release-20261002/heldout/first-run/report.json"
    report = json.loads(path.read_bytes())
    report["metrics"]["recall_at_3"] = 0.7583333333333334
    modified = tmp_path / "report.json"
    modified.write_text(json.dumps(report), encoding="utf8")
    with pytest.raises(ValueError, match="original historical report bytes changed"):
        verify_historical_report(modified)
