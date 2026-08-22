from __future__ import annotations

import json

import pytest

from server.app.diagnosis.comparator_runs import (
    ComparisonInputStore,
    ComparatorSubmissionStore,
    build_comparison_input_bundle,
)
from server.app.diagnosis.real_world_runs import RealWorldRunManager, _canonical_hash


def _payload(
    product: str = "holmesgpt",
    *,
    case_id: str = "RW-OTELPY-4224",
    source_run_id: str = "source-run-1",
    input_hash: str = "sha256:" + "0" * 64,
) -> dict:
    return {
        "product": product,
        "runs": [
            {
                "run_id": "external-run-1",
                "case_id": case_id,
                "source_run_id": source_run_id,
                "comparison_input_hash": input_hash,
                "predicted_root_cause_id": "candidate-root",
                "predicted_locations": [],
                "evidence": [],
                "evidence_refs": [],
                "counter_evidence_refs": [],
                "abstained": True,
                "confidence": 0.2,
                "duration_seconds": 3.0,
                "tool_calls": 2,
            }
        ],
    }


def _bound_store_and_payload(tmp_path, product: str = "holmesgpt") -> tuple[ComparatorSubmissionStore, dict]:
    manager = RealWorldRunManager(tmp_path / "runs", start_workers=False)
    created = manager.create("RW-OTELPY-4224")
    manager._execute(created["run_id"])
    run = manager.get(created["run_id"])
    assert run is not None and run["status"] == "COMPLETED"
    bundle = build_comparison_input_bundle(run)
    input_store = ComparisonInputStore(tmp_path / "inputs")
    input_store.register(bundle)
    store = ComparatorSubmissionStore(tmp_path / "results", input_store=input_store)
    payload = _payload(
        product,
        source_run_id=run["run_id"],
        input_hash=bundle["input_hash"],
    )
    return store, payload


def test_submission_is_not_reported_as_executed_or_scored_without_evaluator_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("MINI_DROP_REAL_WORLD_COMMITMENT_KEY", raising=False)
    store, payload = _bound_store_and_payload(tmp_path)

    result = store.submit("holmesgpt", payload)
    summary = store.list()

    assert result["status"] == "RECEIVED_AWAITING_EVALUATOR"
    assert result["report"] is None
    assert summary["actual_submission_count"] == 1
    assert summary["scored_submission_count"] == 0
    assert summary["latest_by_comparator"]["holmesgpt"]["input_hash"].startswith(
        "sha256:"
    )


def test_product_must_match_registered_comparator(tmp_path) -> None:
    store, payload = _bound_store_and_payload(tmp_path)
    with pytest.raises(ValueError, match="product"):
        payload["product"] = "openrca"
        store.submit("holmesgpt", payload)
    with pytest.raises(ValueError, match="未登记"):
        store.submit("unknown-product", _payload("unknown-product"))


def test_oracle_fields_are_rejected_before_persistence(tmp_path) -> None:
    store, payload = _bound_store_and_payload(tmp_path)
    payload["runs"][0]["ground_truth"] = "secret"

    with pytest.raises(ValueError, match="Oracle"):
        store.submit("holmesgpt", payload)

    assert list(tmp_path.glob("*.json")) == []


def test_tampered_persisted_record_is_not_listed(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MINI_DROP_REAL_WORLD_COMMITMENT_KEY", raising=False)
    store, payload = _bound_store_and_payload(tmp_path)
    result = store.submit("holmesgpt", payload)
    path = tmp_path / "results" / f"{result['submission_id']}.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["record"]["status"] = "SCORED"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    assert store.list()["items"] == []


def test_comparison_input_is_frozen_and_does_not_leak_prediction_or_oracle(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path / "runs", start_workers=False)
    created = manager.create("RW-OTELPY-4224")
    manager._execute(created["run_id"])
    run = manager.get(created["run_id"])
    assert run is not None and run["status"] == "COMPLETED"

    bundle = build_comparison_input_bundle(run)
    rendered = json.dumps(bundle, ensure_ascii=False).lower()
    assert bundle["case"]["case_id"] == "RW-OTELPY-4224"
    assert [item["role"] for item in bundle["frozen_telemetry"]] == [
        "baseline",
        "incident",
        "verification",
    ]
    assert "predicted_root_cause_id" not in bundle["source_run"]
    assert "oracle_root_cause_id" not in rendered
    assert "ground_truth" not in rendered
    meaningful = {key: value for key, value in bundle.items() if key != "input_hash"}
    assert bundle["input_hash"] == _canonical_hash(meaningful)


def test_comparison_input_rejects_unfinished_runs(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path / "runs", start_workers=False)
    run = manager.create("RW-OTELPY-4224")
    with pytest.raises(ValueError, match="已完成"):
        build_comparison_input_bundle(run)


def test_submission_must_bind_to_exported_input_and_budget(tmp_path) -> None:
    store, payload = _bound_store_and_payload(tmp_path)
    payload["runs"][0]["comparison_input_hash"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="未由本服务导出"):
        store.submit("holmesgpt", payload)

    store, payload = _bound_store_and_payload(tmp_path / "budget")
    payload["runs"][0]["tool_calls"] = 13
    with pytest.raises(ValueError, match="工具调用数"):
        store.submit("holmesgpt", payload)


def test_submission_uses_registered_evidence_not_product_copy(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MINI_DROP_REAL_WORLD_COMMITMENT_KEY", raising=False)
    store, payload = _bound_store_and_payload(tmp_path)
    payload["runs"][0]["evidence"] = [{"evidence_id": "forged"}]
    result = store.submit("holmesgpt", payload)
    envelope = json.loads(
        (tmp_path / "results" / f"{result['submission_id']}.json").read_text(encoding="utf-8")
    )
    evidence = envelope["frozen_input"]["runs"][0]["evidence"]
    assert [item["role"] for item in evidence] == ["baseline", "incident", "verification"]
    assert all(item["evidence_id"] != "forged" for item in evidence)
