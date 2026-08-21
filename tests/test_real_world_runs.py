from __future__ import annotations

import json
import time

import pytest

from server.app.diagnosis.real_world_runs import (
    RealWorldRunManager,
    _canonical_hash,
    real_world_catalog,
)


def _wait(manager: RealWorldRunManager, run_id: str, timeout: float = 4.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = manager.get(run_id)
        assert run is not None
        if run["status"] != "RUNNING":
            return run
        time.sleep(0.05)
    raise AssertionError("real-world benchmark did not finish in time")


def _rewrite_manifest(path, mutate) -> None:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    mutate(envelope["run"])
    envelope["integrity"]["run_hash"] = _canonical_hash(envelope["run"])
    path.write_text(json.dumps(envelope), encoding="utf-8")


def test_catalog_never_counts_specification_as_runtime_proof() -> None:
    catalog = real_world_catalog()
    assert len(catalog["cases"]) == 7
    assert catalog["runnable_count"] == 4
    assert catalog["replayed_count"] == 0
    runnable = [case for case in catalog["cases"] if case["web_execution"] == "MECHANISM_REPRO_AVAILABLE"]
    assert {case["case_id"] for case in runnable} == {
        "RW-GRAFANA-123359",
        "RW-OTELPY-4224",
        "RW-K8S-138571",
        "RW-ENVOY-42752",
    }


def test_otel_mechanism_is_completed_but_unscored_with_owned_evidence(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path)
    created = manager.create("RW-OTELPY-4224")
    run = _wait(manager, created["run_id"])

    assert run["status"] == "COMPLETED"
    assert run["scoring_status"] == "UNSCORED"
    assert run["result"]["passed"] is None
    assert run["result"]["mechanism_verified"] is True
    assert run["result"]["recovery_verified"] is True
    assert "base/fix" in run["result"]["admission_reason"]
    assert run["progress"] == 100
    assert run["events"][-1]["stage"] == "COMPLETED"
    assert [snapshot["role"] for snapshot in run["snapshots"]] == [
        "baseline",
        "incident",
        "verification",
    ]
    assert run["snapshots"][1]["alive_after_gc"] == 600
    assert run["snapshots"][2]["alive_after_gc"] == 0
    assert run["result"]["predicted_root_cause_id"]
    assert run["execution_fidelity"] == "MECHANISM_REPRO"
    assert "oracle_root_cause_id" not in run["result"]
    assert "oracle_match_observed" not in run["result"]
    assert "source_location" not in run["result"]

    evidence_ids = {item["evidence_id"] for item in run["evidence"]}
    assert set(run["result"]["evidence_refs"] + run["result"]["counter_evidence_refs"]) == evidence_ids
    for evidence in run["evidence"]:
        assert evidence["run_id"] == run["run_id"]
        assert evidence["case_id"] == run["case_id"]
        meaningful = {key: value for key, value in evidence.items() if key != "integrity_hash"}
        assert evidence["integrity_hash"] == _canonical_hash(meaningful)


def test_unimplemented_upstream_replay_is_rejected_instead_of_faked(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path)
    with pytest.raises(ValueError, match="不会被伪装成已通过"):
        manager.create("RW-REDIS-15427")


@pytest.mark.parametrize(
    "case_id, expected_root",
    [
        ("RW-GRAFANA-123359", "workqueue_pointer_identity_breaks_deduplication"),
        ("RW-K8S-138571", "periodic_full_sync_cost_in_large_cluster_mode"),
        ("RW-ENVOY-42752", "per_chunk_debug_log_expression_evaluation"),
    ],
)
def test_additional_mechanism_reproductions_remain_unscored(
    tmp_path, case_id: str, expected_root: str
) -> None:
    manager = RealWorldRunManager(tmp_path)
    run = _wait(manager, manager.create(case_id)["run_id"])
    assert run["status"] == "COMPLETED"
    assert run["result"]["passed"] is None
    assert run["result"]["scoring_status"] == "UNSCORED"
    assert run["result"]["predicted_root_cause_id"] == expected_root
    assert [item["role"] for item in run["evidence"]] == ["baseline", "incident", "verification"]


def test_loaded_completed_run_rejects_role_and_scoring_promotion_tampering(
    tmp_path,
) -> None:
    manager = RealWorldRunManager(tmp_path)
    duplicate = _wait(manager, manager.create("RW-GRAFANA-123359")["run_id"])
    duplicate_path = tmp_path / f"{duplicate['run_id']}.json"

    def duplicate_role(run: dict) -> None:
        run["evidence"][1]["role"] = "baseline"
        meaningful = {
            key: value
            for key, value in run["evidence"][1].items()
            if key != "integrity_hash"
        }
        run["evidence"][1]["integrity_hash"] = _canonical_hash(meaningful)

    _rewrite_manifest(duplicate_path, duplicate_role)

    promoted = _wait(manager, manager.create("RW-OTELPY-4224")["run_id"])
    promoted_path = tmp_path / f"{promoted['run_id']}.json"
    _rewrite_manifest(
        promoted_path,
        lambda run: (
            run.update({"scoring_status": "SCORED"}),
            run["result"].update({"scoring_status": "SCORED", "passed": True}),
        ),
    )

    restored = RealWorldRunManager(tmp_path, start_workers=False)

    assert restored.get(duplicate["run_id"]) is None
    assert restored.get(promoted["run_id"]) is None
    assert len(list((tmp_path / "quarantine").glob("*.json"))) == 2


def test_complete_rejects_duplicate_and_out_of_order_evidence(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path, start_workers=False)
    created = manager.create("RW-GRAFANA-123359")
    manager._snapshot(created["run_id"], "baseline", {"value": 1})
    manager._snapshot(created["run_id"], "verification", {"value": 2})
    manager._snapshot(created["run_id"], "incident", {"value": 3})

    with pytest.raises(ValueError, match="unique and ordered"):
        manager._complete(
            created["run_id"],
            predicted="root",
            supported=True,
            recovered=True,
            summary="summary",
            limitations=[],
        )


def test_terminal_run_survives_manager_restart(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path)
    run = _wait(manager, manager.create("RW-GRAFANA-123359")["run_id"])

    restored = RealWorldRunManager(tmp_path, start_workers=False).get(run["run_id"])

    assert restored == run


def test_stale_running_run_becomes_interrupted_without_worker_restart(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path, start_workers=False)
    created = manager.create("RW-GRAFANA-123359")

    restored_manager = RealWorldRunManager(tmp_path, start_workers=False)
    restored = restored_manager.get(created["run_id"])

    assert restored is not None
    assert restored["status"] == "INTERRUPTED"
    assert restored["stage"] == "INTERRUPTED"
    assert restored["progress"] == created["progress"]
    assert restored["events"][-1]["stage"] == "INTERRUPTED"
    assert restored["result"] is None


def test_corrupt_and_tampered_manifests_are_quarantined_without_blocking_valid_run(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path)
    valid = _wait(manager, manager.create("RW-GRAFANA-123359")["run_id"])
    valid_path = tmp_path / f"{valid['run_id']}.json"

    corrupt_path = tmp_path / "rw_00000000000000000000000000000000.json"
    corrupt_path.write_text("{broken", encoding="utf-8")
    tampered_path = tmp_path / "rw_11111111111111111111111111111111.json"
    envelope = json.loads(valid_path.read_text(encoding="utf-8"))
    envelope["run"]["run_id"] = "rw_11111111111111111111111111111111"
    envelope["run"]["message"] = "tampered"
    tampered_path.write_text(json.dumps(envelope), encoding="utf-8")

    restored = RealWorldRunManager(tmp_path, start_workers=False)

    assert restored.get(valid["run_id"]) == valid
    assert not corrupt_path.exists()
    assert not tampered_path.exists()
    assert len(list((tmp_path / "quarantine").glob("*.json"))) == 2


def test_run_ids_and_manifest_files_do_not_collide(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path, start_workers=False)
    runs = [manager.create("RW-GRAFANA-123359") for _ in range(40)]

    assert len({run["run_id"] for run in runs}) == 40
    assert len(list(tmp_path.glob("rw_*.json"))) == 40


def test_failed_atomic_replace_preserves_last_valid_manifest(tmp_path, monkeypatch) -> None:
    manager = RealWorldRunManager(tmp_path, start_workers=False)
    created = manager.create("RW-GRAFANA-123359")
    manifest_path = tmp_path / f"{created['run_id']}.json"
    before = manifest_path.read_bytes()

    from server.app.diagnosis import real_world_runs

    monkeypatch.setattr(real_world_runs.os, "replace", lambda *_: (_ for _ in ()).throw(PermissionError("locked")))
    monkeypatch.setattr(real_world_runs.time, "sleep", lambda *_: None)

    with pytest.raises(PermissionError, match="locked"):
        manager._event(created["run_id"], "BASELINE", "cannot persist", 20)

    assert manifest_path.read_bytes() == before
    assert list(tmp_path.glob(f".{manifest_path.name}.*.tmp")) == []


def test_worker_failure_is_a_coherent_persisted_terminal_state(tmp_path, monkeypatch) -> None:
    manager = RealWorldRunManager(tmp_path, start_workers=False)
    created = manager.create("RW-GRAFANA-123359")

    def fail(_: str) -> None:
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(manager, "_execute_grafana_workqueue", fail)
    manager._execute(created["run_id"])
    failed = manager.get(created["run_id"])

    assert failed is not None
    assert failed["status"] == "FAILED"
    assert failed["stage"] == "FAILED"
    assert failed["progress"] == 100
    assert failed["events"][-1]["stage"] == "FAILED"
    assert failed["error"] == "adapter exploded"
    assert RealWorldRunManager(tmp_path, start_workers=False).get(created["run_id"]) == failed
