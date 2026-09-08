from __future__ import annotations

from copy import deepcopy

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.exploration_tree import get_live_exploration_tree
from server.app.drop_insight.frozen_replay_showcase import (
    SNAPSHOT_EVENT,
    advance_frozen_replay_run,
    get_frozen_replay_catalog,
    start_frozen_replay_run,
)
from server.app.drop_insight.lats import LATSConfig
from server.app.drop_insight.schemas import DiagnosisBudget
from server.app.models import (
    ArtifactModel,
    DropInsightEventModel,
    DropInsightEvidenceModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
    OutboxMessageModel,
    TaskModel,
)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    database_path = tmp_path / "frozen-replay.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    reset_engine()
    init_db()
    yield
    reset_engine()


def test_catalog_is_allowlisted_static_and_truthfully_non_live() -> None:
    catalog = get_frozen_replay_catalog()

    assert catalog["status"] == "READY"
    assert catalog["showcase_kind"] == "FROZEN_LATS_SHOWCASE"
    scenario = catalog["scenarios"][0]
    assert scenario["scenario_id"] == "python-hotspot-tree-v1"
    assert scenario["execution_mode"] == "FULL_LATS"
    assert scenario["environment_semantics"] == "FROZEN_REPLAY"
    assert scenario["estimated_frames"] >= 4
    assert scenario["safety"] == {
        "allow_listed": True,
        "live_collection": False,
        "creates_operational_tasks": False,
    }
    assert len(scenario["snapshot"]["manifest_digest"]) == 64


def test_budget_schema_exposes_lats_controls_and_null_inherits_round_budget() -> None:
    budget = DiagnosisBudget.model_validate(
        {"max_diagnosis_rounds": 9, "max_lats_iterations": None}
    )
    dumped = budget.model_dump(mode="json")

    assert dumped["lats_top_k"] == 3
    assert dumped["max_lats_iterations"] is None
    assert dumped["lats_selection_policy"] == "UCT"
    assert LATSConfig.from_budget(dumped).max_iterations == 9


def test_create_is_idempotent_and_snapshot_manifest_is_self_contained() -> None:
    first = start_frozen_replay_run(
        "python-hotspot-tree-v1", "browser-run-0001", principal="interviewer"
    )
    repeated = start_frozen_replay_run(
        "python-hotspot-tree-v1", "browser-run-0001", principal="interviewer"
    )
    fresh = start_frozen_replay_run(
        "python-hotspot-tree-v1", "browser-run-0002", principal="interviewer"
    )

    assert first["created"] is True
    assert repeated["created"] is False
    assert repeated["diagnosis_id"] == first["diagnosis_id"]
    assert fresh["diagnosis_id"] != first["diagnosis_id"]
    assert first["mode"] == "REPLAY"
    assert first["skill_policy"] == "DISABLED"
    assert first["status"] == "PLANNING"

    with new_session() as session:
        events = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == first["diagnosis_id"])
            .all()
        )
        assert len(events) == 1
        assert events[0].event_type == SNAPSHOT_EVENT
        payload = events[0].payload_json
        assert payload["immutable"] is True
        assert payload["live_collection"] is False
        assert payload["manifest"]["initial_candidates"]
        assert payload["manifest"]["observations"]
        assert payload["manifest"]["config"]["max_simulations"] == 6
        assert len(payload["manifest_digest"]) == 64
        assert session.query(OutboxMessageModel).count() == 2


def test_worker_persists_one_frame_per_tick_and_resumes_after_engine_restart() -> None:
    created = start_frozen_replay_run(
        "python-hotspot-tree-v1", "browser-run-restart", principal="interviewer"
    )
    diagnosis_id = created["diagnosis_id"]
    previous_sequences = 1
    ticks = 0
    while ticks < 12:
        assert advance_frozen_replay_run(diagnosis_id) is True
        ticks += 1
        with new_session() as session:
            diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
            rows = (
                session.query(DropInsightEventModel)
                .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
                .order_by(DropInsightEventModel.sequence.asc())
                .all()
            )
            assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
            delta = rows[previous_sequences:]
            iterations = {
                int((row.payload_json or {}).get("iteration") or 0)
                for row in delta
                if row.event_type == "lats.node_selected"
            }
            assert len(iterations) <= 1
            previous_sequences = len(rows)
            terminal = diagnosis.status == "COMPLETED"
        if terminal:
            break
        # Simulate a worker process restart: the next step must be reconstructed
        # from the persisted manifest/events, not process memory or source data.
        reset_engine()
        init_db()

    assert ticks >= 4
    assert terminal is True
    assert advance_frozen_replay_run(diagnosis_id) is False

    with new_session() as session:
        events = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
        event_types = [row.event_type for row in events]
        assert event_types.count("lats.search_started") == 1
        assert event_types.count("lats.simulation_started") >= 4
        assert event_types[-1] == "lats.search_terminated"
        assert session.query(DropInsightToolCallModel).count() == 0
        assert session.query(TaskModel).count() == 0
        assert session.query(ArtifactModel).count() == 0
        assert session.query(DropInsightEvidenceModel).count() == 0
        assert session.query(DropInsightReportModel).count() == 0

        hypotheses = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .all()
        )
        assert len(hypotheses) >= 5
        assert all(not item.id.startswith("hypothesis:") for item in hypotheses)
        assert all(
            item.parent_hypothesis_id is None
            or not item.parent_hypothesis_id.startswith("hypothesis:")
            for item in hypotheses
        )
        supported = [item for item in hypotheses if item.status == "SUPPORTED"]
        falsified = [item for item in hypotheses if item.status == "FALSIFIED"]
        assert len(supported) == 1
        assert supported[0].parent_hypothesis_id is not None
        assert len(falsified) >= 2
        assert session.query(OutboxMessageModel).count() == len(events)

    tree = get_live_exploration_tree(diagnosis_id)
    assert tree is not None
    assert tree["status"] == "COMPLETED"
    assert tree["search"]["execution_mode"] == "FULL_LATS"
    assert tree["search"]["budget"]["used_tool_calls"] == 0
    assert tree["search"]["budget"]["used_simulations"] >= 4
    assert all("hypothesis:hypothesis:" not in item["id"] for item in tree["nodes"])


def test_fresh_run_has_disjoint_persisted_node_namespace() -> None:
    first = start_frozen_replay_run(
        "python-hotspot-tree-v1", "fresh-click-0001", principal="interviewer"
    )
    second = start_frozen_replay_run(
        "python-hotspot-tree-v1", "fresh-click-0002", principal="interviewer"
    )
    assert advance_frozen_replay_run(first["diagnosis_id"])
    assert advance_frozen_replay_run(second["diagnosis_id"])

    with new_session() as session:
        first_ids = {
            row[0]
            for row in session.query(DropInsightHypothesisModel.id)
            .filter(
                DropInsightHypothesisModel.diagnosis_id == first["diagnosis_id"]
            )
            .all()
        }
        second_ids = {
            row[0]
            for row in session.query(DropInsightHypothesisModel.id)
            .filter(
                DropInsightHypothesisModel.diagnosis_id == second["diagnosis_id"]
            )
            .all()
        }
    assert first_ids
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


def test_tampered_manifest_terminates_without_materializing_fake_facts() -> None:
    created = start_frozen_replay_run(
        "python-hotspot-tree-v1", "tamper-run-0001", principal="interviewer"
    )
    diagnosis_id = created["diagnosis_id"]
    with new_session() as session:
        event = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type == SNAPSHOT_EVENT,
            )
            .one()
        )
        payload = dict(event.payload_json)
        manifest = deepcopy(payload["manifest"])
        manifest["title"] = "tampered"
        payload["manifest"] = manifest
        event.payload_json = payload
        session.commit()

    assert advance_frozen_replay_run(diagnosis_id) is True
    with new_session() as session:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        assert diagnosis.status == "INSUFFICIENT_EVIDENCE"
        terminal = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type == "lats.search_terminated",
            )
            .one()
        )
        assert terminal.payload_json["reason"] == "REPLAY_MANIFEST_INTEGRITY_FAILED"
        assert session.query(DropInsightHypothesisModel).count() == 0
        assert session.query(DropInsightToolCallModel).count() == 0
