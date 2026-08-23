from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from pydantic import ValidationError
import pytest

from server.app.diagnosis.real_world_runs import RealWorldRunManager
from server.app.evaluation.real_world_admission import validate_formal_admission
from server.app.evaluation.real_world_local_contract import (
    BASE_SHA,
    CASE_ID,
    FIX_SHA,
    REPOSITORY_URL,
    LocalReplayObservationV1,
    evaluate_local_replay_contract,
)


ROOT = Path(__file__).resolve().parents[1]
_SHA256 = "sha256:" + "a" * 64


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _phase(
    phase: str,
    revision_sha: str,
    started_at: datetime,
    *,
    iterations: int,
    retained: dict[str, int],
    all_collected: bool,
) -> dict:
    return {
        "phase": phase,
        "revision_sha": revision_sha,
        "iterations": iterations,
        "retained": retained,
        "all_collected": all_collected,
        "terminal_status": "COMPLETED",
        "comparator_exit_code": 0,
        "started_at": _timestamp(started_at),
        "finished_at": _timestamp(started_at + timedelta(minutes=1)),
    }


def _observation() -> dict:
    origin = datetime(2026, 8, 22, 1, 0, tzinfo=timezone.utc)
    repetitions = []
    for index in range(3):
        started_at = origin + timedelta(hours=index)
        repetitions.append({
            "repetition_id": f"repetition-{index + 1}",
            "phases": [
                _phase(
                    "baseline",
                    BASE_SHA,
                    started_at,
                    iterations=0,
                    retained={"reader": 0, "exporter": 0, "provider": 0},
                    all_collected=True,
                ),
                _phase(
                    "incident",
                    BASE_SHA,
                    started_at + timedelta(minutes=2),
                    iterations=250,
                    retained={"reader": 250, "exporter": 250, "provider": 0},
                    all_collected=False,
                ),
                _phase(
                    "verification",
                    FIX_SHA,
                    started_at + timedelta(minutes=4),
                    iterations=250,
                    retained={"reader": 0, "exporter": 0, "provider": 0},
                    all_collected=True,
                ),
            ],
        })
    return {
        "schema_version": "local-replay-observation-v1",
        "run_id": "local-run-otel-4224",
        "case_id": CASE_ID,
        "repository_url": REPOSITORY_URL,
        "base_sha": BASE_SHA,
        "fix_sha": FIX_SHA,
        "source_integrity_hash": _SHA256,
        "dependency_lock_hash": "sha256:" + "b" * 64,
        "harness_integrity_hash": "sha256:" + "c" * 64,
        "environment": {
            "os": "linux",
            "architecture": "amd64",
            "runtime": "python3.12.3",
            "container_image_digest": "sha256:" + "d" * 64,
        },
        "repetitions": repetitions,
        "diagnosis_receipt": {
            "schema_version": "local-diagnosis-freeze-receipt-v1",
            "diagnosis_id": "diagnosis-otel-4224",
            "run_id": "local-run-otel-4224",
            "case_id": CASE_ID,
            "artifact_hash": "sha256:" + "e" * 64,
            "frozen_at": "2026-08-22T04:00:00Z",
        },
        "oracle_accessed_at": "2026-08-22T04:01:00Z",
    }


def _wait(manager: RealWorldRunManager, run_id: str) -> dict:
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        run = manager.get(run_id)
        assert run is not None
        if run["status"] != "RUNNING":
            return run
        time.sleep(0.05)
    raise AssertionError("real-world mechanism run did not finish")


def test_valid_local_observation_derives_gates_but_remains_unscored() -> None:
    payload = _observation()

    first = evaluate_local_replay_contract(payload)
    second = evaluate_local_replay_contract(deepcopy(payload))

    assert all(first.gates.model_dump().values())
    assert first.failure_codes == []
    assert first == second
    assert first.observation_hash == second.observation_hash
    assert first.reporting_tier == "LOCAL_SYNTHETIC_CONTRACT"
    assert first.scoring_status == "UNSCORED"
    assert first.formal_admission_eligible is False
    rendered = first.model_dump()
    for forbidden in (
        "admission_hash",
        "execution_fidelity",
        "score",
        "verdict",
    ):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    ("mutate", "gate", "failure_code"),
    [
        (
            lambda value: value["repetitions"][0]["phases"][1]["retained"].update(
                reader=0, exporter=0
            ),
            "incident_reproduced",
            "INCIDENT_NOT_REPRODUCED",
        ),
        (
            lambda value: value["repetitions"][1]["phases"][2]["retained"].update(
                reader=1
            ),
            "verification_recovered",
            "VERIFICATION_NOT_RECOVERED",
        ),
        (
            lambda value: value["repetitions"][2]["phases"][2].update(
                comparator_exit_code=1
            ),
            "comparator_passed",
            "COMPARATOR_FAILED",
        ),
        (
            lambda value: value["repetitions"][0].update(
                phases=list(reversed(value["repetitions"][0]["phases"]))
            ),
            "phase_order",
            "PHASE_ORDER_INVALID",
        ),
        (
            lambda value: value["repetitions"][0]["phases"][2].update(
                revision_sha=BASE_SHA
            ),
            "phase_revision_identity",
            "PHASE_REVISION_IDENTITY_INVALID",
        ),
        (
            lambda value: value.update(repetitions=value["repetitions"][:2]),
            "repetitions_sufficient",
            "REPETITIONS_INSUFFICIENT",
        ),
        (
            lambda value: value["repetitions"][1].update(
                repetition_id=value["repetitions"][0]["repetition_id"]
            ),
            "repetitions_sufficient",
            "REPETITIONS_INSUFFICIENT",
        ),
        (
            lambda value: value["repetitions"][0]["phases"][1].update(
                started_at="2026-08-22T01:00:30Z"
            ),
            "phase_order",
            "PHASE_ORDER_INVALID",
        ),
    ],
)
def test_raw_failures_cannot_be_promoted_by_claims(mutate, gate, failure_code) -> None:
    payload = _observation()
    mutate(payload)

    report = evaluate_local_replay_contract(payload)

    assert getattr(report.gates, gate) is False
    assert failure_code in report.failure_codes
    assert report.scoring_status == "UNSCORED"
    assert report.formal_admission_eligible is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(repository_url="https://example.invalid/repository"),
        lambda value: value.update(base_sha="1" * 40),
        lambda value: value.update(fix_sha="2" * 40),
    ],
)
def test_fixed_upstream_identity_fails_closed(mutate) -> None:
    payload = _observation()
    mutate(payload)

    report = evaluate_local_replay_contract(payload)

    assert report.gates.source_identity is False
    assert "SOURCE_IDENTITY_INVALID" in report.failure_codes


@pytest.mark.parametrize(
    "path",
    [
        ("source_integrity_hash",),
        ("dependency_lock_hash",),
        ("harness_integrity_hash",),
        ("environment", "container_image_digest"),
        ("diagnosis_receipt", "artifact_hash"),
    ],
)
def test_missing_hash_identity_is_rejected(path) -> None:
    payload = _observation()
    target = payload
    for component in path[:-1]:
        target = target[component]
    del target[path[-1]]

    with pytest.raises(ValidationError):
        evaluate_local_replay_contract(payload)


@pytest.mark.parametrize(
    "path",
    [
        ("source_integrity_hash",),
        ("dependency_lock_hash",),
        ("harness_integrity_hash",),
        ("environment", "container_image_digest"),
        ("diagnosis_receipt", "artifact_hash"),
    ],
)
def test_malformed_hash_identity_is_rejected(path) -> None:
    payload = _observation()
    target = payload
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = "sha256:not-a-digest"

    with pytest.raises(ValidationError):
        evaluate_local_replay_contract(payload)


def test_diagnosis_must_be_bound_and_frozen_after_observations() -> None:
    ownership = _observation()
    ownership["diagnosis_receipt"]["run_id"] = "different-run"
    early = _observation()
    early["diagnosis_receipt"]["frozen_at"] = "2026-08-22T03:04:30Z"

    ownership_report = evaluate_local_replay_contract(ownership)
    early_report = evaluate_local_replay_contract(early)

    assert ownership_report.gates.diagnosis_frozen is False
    assert early_report.gates.diagnosis_frozen is False
    assert "DIAGNOSIS_FREEZE_INVALID" in ownership_report.failure_codes
    assert "DIAGNOSIS_FREEZE_INVALID" in early_report.failure_codes


def test_oracle_access_must_follow_diagnosis_freeze() -> None:
    payload = _observation()
    payload["oracle_accessed_at"] = "2026-08-22T03:59:59Z"

    report = evaluate_local_replay_contract(payload)

    assert report.gates.oracle_after_freeze is False
    assert "ORACLE_ACCESSED_BEFORE_FREEZE" in report.failure_codes
    assert report.formal_admission_eligible is False


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("root", "execution_fidelity", "FULL_UPSTREAM_REPLAY"),
        ("root", "scoring_status", "SCORED"),
        ("root", "formal_admission_eligible", True),
        ("root", "gates", {"comparator_passed": True}),
        ("root", "oracle_root_cause_id", "private-answer"),
        ("phase", "incident_reproduced", True),
        ("receipt", "expected_root_cause", "private-answer"),
    ],
)
def test_caller_cannot_supply_promotions_gates_or_oracle_answers(
    target, field, value
) -> None:
    payload = _observation()
    destination = (
        payload
        if target == "root"
        else payload["repetitions"][0]["phases"][1]
        if target == "phase"
        else payload["diagnosis_receipt"]
    )
    destination[field] = value

    with pytest.raises(ValidationError):
        evaluate_local_replay_contract(payload)


def test_local_report_cannot_satisfy_formal_admission() -> None:
    report = evaluate_local_replay_contract(_observation()).model_dump(mode="json")
    fake_run = {
        "run_id": report["run_id"],
        "case_id": report["case_id"],
        "status": "COMPLETED",
        "execution_fidelity": "FULL_UPSTREAM_REPLAY",
        "admission": report,
    }

    with pytest.raises(ValueError, match="schema invalid"):
        validate_formal_admission(
            fake_run,
            case_id=CASE_ID,
            minimum_repetitions=3,
            evidence_by_id={},
        )


def test_historical_archive_is_not_a_fresh_local_observation() -> None:
    archive = json.loads((
        ROOT / "reports/benchmark/real-world/RW-OTELPY-4224/report.json"
    ).read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        LocalReplayObservationV1.model_validate(archive)


def test_existing_otel_mechanism_run_remains_unscored(tmp_path) -> None:
    manager = RealWorldRunManager(tmp_path)
    run = _wait(manager, manager.create(CASE_ID)["run_id"])

    assert run["status"] == "COMPLETED"
    assert run["execution_fidelity"] == "MECHANISM_REPRO"
    assert run["scoring_status"] == "UNSCORED"
    assert run["result"]["passed"] is None
    assert "admission" not in run
