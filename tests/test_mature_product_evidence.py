from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_otel_upstream_replay_is_stable_and_hash_bound() -> None:
    evidence = ROOT / "reports/benchmark/real-world/RW-OTELPY-4224"
    report = _json(evidence / "report.json")

    assert report["verdict"] == "STABLE_REPLAY_PASS"
    assert report["harness"]["repetitions"] == 3
    assert report["baseline_fault"]["stable_failure"] is True
    assert report["verification_fix"]["stable_success"] is True
    assert report["baseline_fault"]["median_retained_readers"] == 250
    assert report["verification_fix"]["median_retained_readers"] == 0
    assert report["harness"]["sha256"] == _sha256(evidence / "otel_gc_harness.py")
    for filename, expected_hash in report["artifacts"].items():
        assert expected_hash == _sha256(evidence / filename)
        assert len((evidence / filename).read_text(encoding="utf-8").splitlines()) == 3


def test_pyroscope_result_is_real_capability_evidence_not_rca_score() -> None:
    evidence = ROOT / "reports/benchmark/mature-products/pyroscope-2.2.1"
    report = _json(evidence / "report.json")
    flamegraph = (evidence / "flamegraph.json").read_text(encoding="utf-8")

    assert report["verdict"] == "EXECUTED_CAPABILITY_PASS"
    assert report["result"]["profile_ingested"] is True
    assert report["result"]["hot_function_found"] is True
    assert report["result"]["query_repetitions"] == 3
    assert report["result"]["median_query_seconds"] > 0
    assert "order_compute" in flamegraph
    assert any("not an AI root-cause agent" in item for item in report["limitations"])


def test_holmes_provider_failure_is_not_misreported_as_quality_score() -> None:
    report = _json(
        ROOT / "reports/benchmark/mature-products/holmesgpt-0.39.0/report.json"
    )

    assert report["verdict"] == "EXECUTED_PROVIDER_AUTH_BLOCKED"
    assert report["result"]["installation_verified"] is True
    assert report["result"]["diagnosis_completed"] is False
    assert report["result"]["provider_preflight_http_status"] == 401
    assert "score" not in report["result"]


def test_comparator_catalog_matches_archived_execution_evidence() -> None:
    catalog = _json(ROOT / "benchmarks/real_world/comparators.json")
    by_id = {item["id"]: item for item in catalog["comparators"]}

    assert by_id["grafana-pyroscope"]["execution_status"] == "EXECUTED_CAPABILITY_PASS"
    assert by_id["holmesgpt"]["execution_status"] == "EXECUTED_PROVIDER_AUTH_BLOCKED"
    assert by_id["rcaeval"]["execution_status"] == "NOT_EXECUTED_IN_THIS_WORKSPACE"
    assert by_id["openrca"]["execution_status"] == "NOT_EXECUTED_IN_THIS_WORKSPACE"
    assert "same incident window" in catalog["fair_comparison_rule"]
