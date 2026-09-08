from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight import service
from server.app.models import (
    DropInsightEventModel,
    DropInsightEvidenceModel,
    DropInsightHypothesisModel,
    DropInsightSessionModel,
    OutboxMessageModel,
)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _seed_diagnosis(identifier: str) -> None:
    timestamp = datetime(2026, 9, 6, tzinfo=timezone.utc)
    with new_session() as session:
        session.add(
            DropInsightSessionModel(
                id=identifier,
                query="diagnose repeated event persistence",
                target_json={},
                time_range_json={},
                requested_time_range_json={},
                effective_time_range_json={},
                mode="AUTONOMOUS",
                skill_policy="AUTO",
                budget_json={},
                status="COLLECTING_EVIDENCE",
                version=1,
                clarification_questions_json=[],
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.commit()


def test_append_event_deduplicates_same_iteration_payload_but_keeps_changes() -> None:
    diagnosis_id = "insight-semantic-event-dedupe"
    _seed_diagnosis(diagnosis_id)
    timestamp = datetime(2026, 9, 6, 1, tzinfo=timezone.utc)
    base_payload = {
        "iteration": 2,
        "round_index": 1,
        "node_id": "hypothesis:cpu",
        "score": 0.75,
    }

    with new_session() as session:
        assert service._append_event(
            session,
            diagnosis_id,
            "lats.node_selected",
            "AGENT",
            base_payload,
            timestamp,
            effect_key="poll:first",
        )
        assert not service._append_event(
            session,
            diagnosis_id,
            "lats.node_selected",
            "AGENT",
            dict(base_payload),
            timestamp + timedelta(seconds=1),
            effect_key="poll:retry-with-different-key",
        )
        assert service._append_event(
            session,
            diagnosis_id,
            "lats.node_selected",
            "AGENT",
            {**base_payload, "score": 0.8},
            timestamp + timedelta(seconds=2),
            effect_key="poll:changed-score",
        )
        assert service._append_event(
            session,
            diagnosis_id,
            "lats.node_selected",
            "AGENT",
            dict(base_payload),
            timestamp + timedelta(seconds=3),
            effect_key="poll:score-reverted",
        )
        assert service._append_event(
            session,
            diagnosis_id,
            "lats.node_selected",
            "AGENT",
            {**base_payload, "iteration": 3},
            timestamp + timedelta(seconds=4),
            effect_key="poll:next-iteration",
        )
        session.commit()

    with new_session() as session:
        events = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
        assert [event.sequence for event in events] == [1, 2, 3, 4]
        assert [event.payload_json["score"] for event in events] == [
            0.75,
            0.8,
            0.75,
            0.75,
        ]
        assert session.query(OutboxMessageModel).count() == 4


def test_candidate_scoring_is_noop_until_status_really_changes(monkeypatch) -> None:
    diagnosis_id = "insight-hypothesis-score-dedupe"
    hypothesis_id = "hypothesis-score-dedupe"
    _seed_diagnosis(diagnosis_id)
    timestamp = datetime(2026, 9, 6, 2, tzinfo=timezone.utc)
    envelope = {
        "evidence_id": "evidence-score-dedupe",
        "diagnosis_id": diagnosis_id,
        "evidence_type": "CPU_PROFILE",
        "source": {
            "tool_name": "perf_cpu",
            "task_id": "task-score-dedupe",
            "task_attempt_id": "attempt-score-dedupe",
            "artifact_id": "artifact-score-dedupe",
            "artifact_sha256": "a" * 64,
            "analysis_job_id": "analysis-score-dedupe",
            "analyzer_type": "perf",
            "analyzer_version": "test",
            "analyzer_output_schema_version": "v1",
            "observation_json_pointer": "/metadata/top_functions",
        },
        "scope": {"agent_id": "agent-score-dedupe", "pid": 42},
        "time_range": {
            "start": "2026-09-06T02:00:00Z",
            "end": "2026-09-06T02:01:00Z",
            "timezone": "UTC",
        },
        "observation": {
            "metadata": {"top_functions": [{"name": "work", "percent": 70.0}]}
        },
        "quality": {
            "level": "HIGH",
            "sample_count": 100,
            "degraded": False,
            "target_match": True,
            "time_overlap": True,
        },
        "limitations": [],
    }
    with new_session() as session:
        session.add(
            DropInsightHypothesisModel(
                id=hypothesis_id,
                diagnosis_id=diagnosis_id,
                statement="candidate hypothesis",
                expected_observations_json=[],
                falsification_criteria_json=[],
                status="OPEN",
                source="DETERMINISTIC_RULE",
                round_index=2,
                generation_reason="dedupe regression",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.add(
            DropInsightEvidenceModel(
                id="evidence-score-dedupe",
                diagnosis_id=diagnosis_id,
                hypothesis_id=hypothesis_id,
                role="SUPPORT",
                envelope_json=envelope,
                classification_json={"decision": "ACCEPT_SUPPORT"},
                created_at=timestamp,
            )
        )
        session.commit()

    monkeypatch.setattr(service, "_compute_hypothesis_predicate", lambda *_: None)
    service._score_candidate_hypotheses(diagnosis_id)
    service._score_candidate_hypotheses(diagnosis_id)

    monkeypatch.setattr(
        service,
        "_compute_hypothesis_predicate",
        lambda *_: {"outcome": "SUPPORT"},
    )
    service._score_candidate_hypotheses(diagnosis_id)

    with new_session() as session:
        events = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type == "hypotheses.scored",
            )
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
        assert len(events) == 2
        assert [event.payload_json["hypotheses"][0]["status"] for event in events] == [
            "INCONCLUSIVE",
            "SUPPORTED",
        ]
        assert all(
            event.payload_json["hypotheses"][0]["round_index"] == 2
            for event in events
        )
        assert session.get(DropInsightHypothesisModel, hypothesis_id).status == "SUPPORTED"
