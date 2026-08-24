from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from server.app.drop_insight import service as drop_insight_service
from server.app.models import (
    Base,
    DropInsightEventModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
)


NOW = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


def _postgres_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture
def postgres_sessions(monkeypatch):
    if not _postgres_enabled(os.getenv("RUN_POSTGRES_TESTS")):
        pytest.skip("set RUN_POSTGRES_TESTS=1 to run PostgreSQL integration tests")
    raw_url = os.getenv("MINI_DROP_TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("MINI_DROP_TEST_POSTGRES_URL is not configured")
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        pytest.fail("MINI_DROP_TEST_POSTGRES_URL must use PostgreSQL")
    if not url.database or "test" not in url.database.lower():
        pytest.fail(
            "MINI_DROP_TEST_POSTGRES_URL must name a dedicated test database"
        )

    schema = f"mini_drop_test_{uuid4().hex}"
    admin_engine = create_engine(url, pool_pre_ping=True)
    test_engine = None
    schema_created = False
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        schema_created = True
        test_engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "options": (
                    f"-csearch_path={schema} "
                    "-cstatement_timeout=5000"
                )
            },
        )
        Base.metadata.create_all(test_engine)
        factory = sessionmaker(
            bind=test_engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        monkeypatch.setattr(drop_insight_service, "new_session", factory)
        yield factory
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


def _seed_report(factory, report_id: str):
    diagnosis_id = f"diagnosis-{report_id}"
    hypothesis_id = f"hypothesis-{report_id}"
    with factory.begin() as session:
        session.add(
            DropInsightSessionModel(
                id=diagnosis_id,
                query="PostgreSQL report authority test",
                target_json={},
                time_range_json={},
                requested_time_range_json={},
                effective_time_range_json={},
                mode="OBSERVE_ONLY",
                budget_json={},
                status="COLLECTING_EVIDENCE",
                version=1,
                clarification_questions_json=[],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            DropInsightHypothesisModel(
                id=hypothesis_id,
                diagnosis_id=diagnosis_id,
                statement="PostgreSQL preserves report-effect authority",
                expected_observations_json=[],
                falsification_criteria_json=[],
                status="OPEN",
                source="DETERMINISTIC_RULE",
                round_index=1,
                generation_reason="postgres_integration_test",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            DropInsightReportModel(
                id=report_id,
                diagnosis_id=diagnosis_id,
                hypothesis_id=hypothesis_id,
                conclusion="Evidence is insufficient.",
                confidence=0,
                evidence_refs_json=[],
                counter_evidence_refs_json=[],
                assumptions_json=[],
                limitations_json=[],
                next_actions_json=[],
                claims_json=[],
                verification_json={"status": "INSUFFICIENT_EVIDENCE"},
                effects_status="PENDING",
                effects_fencing_token=0,
                created_at=NOW,
            )
        )
    return diagnosis_id, hypothesis_id


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def test_postgres_claim_lease_takeover_and_fencing(postgres_sessions):
    report_id = "report-postgres-authority"
    _seed_report(postgres_sessions, report_id)
    barrier = Barrier(2)

    def claim(owner):
        barrier.wait(timeout=5)
        return drop_insight_service._claim_report_effects(report_id, owner)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("owner-a", "owner-b")))

    winners = [report for report, claimed in results if claimed]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.effects_fencing_token == 1
    assert winner.effects_owner in {"owner-a", "owner-b"}
    stale_owner = "owner-b" if winner.effects_owner == "owner-a" else "owner-a"

    not_stolen, claimed = drop_insight_service._claim_report_effects(
        report_id,
        stale_owner,
    )
    assert claimed is False
    assert not_stolen.effects_owner == winner.effects_owner
    assert not_stolen.effects_fencing_token == 1

    with postgres_sessions.begin() as session:
        current = session.get(DropInsightReportModel, report_id)
        current.effects_lease_expires_at = NOW - timedelta(seconds=1)

    replacement, claimed = drop_insight_service._claim_report_effects(
        report_id,
        "replacement-owner",
    )
    assert claimed is True
    assert replacement.effects_fencing_token == 2

    with pytest.raises(drop_insight_service._StaleReportEffectAuthority):
        drop_insight_service._renew_report_effect_lease(
            report_id,
            winner.effects_owner,
            winner.effects_fencing_token,
        )
    with pytest.raises(drop_insight_service._StaleReportEffectAuthority):
        drop_insight_service._start_report_effect_execution(
            report_id,
            winner.effects_owner,
            winner.effects_fencing_token,
        )
    with pytest.raises(drop_insight_service._StaleReportEffectAuthority):
        drop_insight_service._complete_report_effects(
            report_id,
            winner.effects_owner,
            winner.effects_fencing_token,
        )
    drop_insight_service._release_report_effects(
        report_id,
        winner.effects_owner,
        winner.effects_fencing_token,
    )

    with postgres_sessions() as session:
        current = session.get(DropInsightReportModel, report_id)
        assert current.effects_status == "APPLYING"
        assert current.effects_owner == "replacement-owner"
        assert current.effects_fencing_token == 2

    completed = drop_insight_service._complete_report_effects(
        report_id,
        "replacement-owner",
        replacement.effects_fencing_token,
    )
    assert completed.effects_status == "APPLIED"
    assert completed.effects_phase == "EFFECTS_COMPLETED"


def test_postgres_session_lock_serializes_event_effect_identity(postgres_sessions):
    diagnosis_id, _ = _seed_report(
        postgres_sessions,
        "report-postgres-event-lock",
    )
    first_locked = Event()
    release_first = Event()
    second_started = Event()
    effect_key = "report:report-postgres-event-lock:route:event"

    def append(owner: str, hold_lock: bool) -> bool:
        with postgres_sessions() as session:
            diagnosis = (
                session.execute(
                    select(DropInsightSessionModel)
                    .where(DropInsightSessionModel.id == diagnosis_id)
                    .with_for_update()
                )
                .scalar_one()
            )
            if hold_lock:
                first_locked.set()
                assert release_first.wait(timeout=5)
            else:
                second_started.set()
            created = drop_insight_service._append_event(
                session,
                diagnosis.id,
                "diagnosis.route_learned",
                "SYSTEM",
                {"owner": owner},
                NOW,
                effect_key=effect_key,
            )
            session.commit()
            return created

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(append, "owner-a", True)
        assert first_locked.wait(timeout=5)
        second = executor.submit(append, "owner-b", False)
        assert second_started.wait(timeout=5)
        with pytest.raises(FutureTimeoutError):
            second.result(timeout=0.2)
        release_first.set()
        assert first.result(timeout=5) is True
        assert second.result(timeout=5) is False

    with postgres_sessions() as session:
        events = session.execute(
            select(DropInsightEventModel).where(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.effect_key == effect_key,
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].payload_json == {"owner": "owner-a"}


def test_postgres_report_and_event_constraints_reject_concurrent_duplicates(
    postgres_sessions,
):
    diagnosis_id, hypothesis_id = _seed_report(
        postgres_sessions,
        "report-postgres-identity-seed",
    )

    def insert_report(report_id: str):
        try:
            with postgres_sessions.begin() as session:
                session.add(
                    DropInsightReportModel(
                        id=report_id,
                        diagnosis_id=diagnosis_id,
                        hypothesis_id=hypothesis_id,
                        conclusion="Concurrent duplicate.",
                        confidence=0,
                        evidence_refs_json=[],
                        counter_evidence_refs_json=[],
                        assumptions_json=[],
                        limitations_json=[],
                        next_actions_json=[],
                        claims_json=[],
                        verification_json={"status": "INSUFFICIENT_EVIDENCE"},
                        effects_status="PENDING",
                        effects_fencing_token=0,
                        created_at=NOW,
                    )
                )
        except IntegrityError as error:
            return _constraint_name(error)
        return None

    barrier = Barrier(2)

    def insert_event(event_id: str, sequence: int):
        try:
            with postgres_sessions.begin() as session:
                barrier.wait(timeout=5)
                session.add(
                    DropInsightEventModel(
                        id=event_id,
                        diagnosis_id=diagnosis_id,
                        sequence=sequence,
                        event_type="diagnosis.route_learned",
                        actor="SYSTEM",
                        payload_json={},
                        effect_key="report:postgres:route:event",
                        occurred_at=NOW,
                    )
                )
        except IntegrityError as error:
            return _constraint_name(error)
        return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        report_constraints = list(
            executor.map(
                insert_report,
                ("report-postgres-duplicate-a", "report-postgres-duplicate-b"),
            )
        )
    assert report_constraints == [
        "uq_drop_insight_report_identity",
        "uq_drop_insight_report_identity",
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        event_constraints = list(
            executor.map(
                lambda item: insert_event(*item),
                (("event-postgres-a", 10), ("event-postgres-b", 11)),
            )
        )
    assert event_constraints.count(None) == 1
    assert event_constraints.count("uq_drop_insight_event_effect") == 1

    with postgres_sessions() as session:
        events = session.execute(
            select(DropInsightEventModel).where(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.effect_key == "report:postgres:route:event",
            )
        ).scalars().all()
        assert len(events) == 1
