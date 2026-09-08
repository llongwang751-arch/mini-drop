from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight import service
from server.app.drop_insight.schemas import GenerateReportRequest
from server.app.models import (
    DropInsightEventModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
)


NOW = datetime(2026, 9, 6, 3, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _seed_diagnosis(
    diagnosis_id: str,
    *,
    status: str = "COLLECTING_EVIDENCE",
    budget: dict | None = None,
) -> str:
    hypothesis_id = f"hypothesis-{diagnosis_id}"
    with new_session() as session:
        session.add(
            DropInsightSessionModel(
                id=diagnosis_id,
                query="诊断真实采样中的性能根因",
                target_json={},
                time_range_json={},
                requested_time_range_json={},
                effective_time_range_json={},
                mode="AUTONOMOUS",
                skill_policy="AUTO",
                budget_json=budget or {},
                status=status,
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
                statement="目标进程可能存在用户态 CPU 热点",
                expected_observations_json=["采样集中在少数函数"],
                falsification_criteria_json=["采样均匀分布"],
                status="OPEN",
                source="MODEL",
                round_index=1,
                generation_reason="回归测试",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
    return hypothesis_id


def _start_lats_search(diagnosis_id: str) -> None:
    with new_session() as session:
        service._append_event(
            session,
            diagnosis_id,
            "lats.search_started",
            "SYSTEM",
            {"round_index": 1},
            NOW,
            effect_key=f"{diagnosis_id}:lats-started",
        )
        session.commit()


def test_unverified_report_never_commits_a_transient_terminal_state(monkeypatch):
    diagnosis_id = "insight-nonterminal-report"
    hypothesis_id = _seed_diagnosis(diagnosis_id)
    observed_statuses: list[str] = []

    def capture_before_effects(report_id: str):
        with new_session() as session:
            observed_statuses.append(
                session.get(DropInsightSessionModel, diagnosis_id).status
            )
            return session.get(DropInsightReportModel, report_id)

    monkeypatch.setattr(service, "_apply_report_effects", capture_before_effects)
    report = service.generate_report(
        diagnosis_id,
        GenerateReportRequest(hypothesis_id=hypothesis_id),
    )

    assert report is not None
    assert observed_statuses == ["COLLECTING_EVIDENCE"]
    with new_session() as session:
        assert session.get(DropInsightSessionModel, diagnosis_id).status == (
            "COLLECTING_EVIDENCE"
        )


def test_search_exhaustion_without_support_finalizes_as_insufficient_once():
    diagnosis_id = "insight-final-insufficient"
    _seed_diagnosis(diagnosis_id)
    _start_lats_search(diagnosis_id)

    for _ in range(2):
        service._record_lats_termination(
            diagnosis_id,
            reason="COVERAGE_EXHAUSTED",
            detail="全部安全证据域已覆盖。",
            effect_key=f"{diagnosis_id}:coverage-terminated",
        )

    with new_session() as session:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        assert diagnosis.status == "INSUFFICIENT_EVIDENCE"
        assert diagnosis.version == 2
        events = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .all()
        )
        assert sum(row.event_type == "lats.search_terminated" for row in events) == 1
        assert sum(
            row.event_type == "diagnosis.insufficient_evidence_finalized"
            for row in events
        ) == 1


def test_search_exhaustion_uses_best_supported_report_instead_of_overwriting_it():
    diagnosis_id = "insight-final-best-report"
    hypothesis_id = _seed_diagnosis(diagnosis_id, status="HYPOTHESIZING")
    _start_lats_search(diagnosis_id)
    with new_session() as session:
        session.add(
            DropInsightReportModel(
                id="report-best-supported",
                diagnosis_id=diagnosis_id,
                hypothesis_id=hypothesis_id,
                conclusion="真实采样支持用户态 CPU 热点。",
                confidence=780,
                evidence_refs_json=["evidence-real-profile"],
                counter_evidence_refs_json=[],
                assumptions_json=[],
                limitations_json=["尚缺少独立对照证据"],
                next_actions_json=["修复后在相同负载下复测"],
                claims_json=[],
                verification_json={"status": "PARTIAL_WITHOUT_COUNTER"},
                effects_status="APPLIED",
                effects_fencing_token=1,
                created_at=NOW,
            )
        )
        session.commit()

    service._record_lats_termination(
        diagnosis_id,
        reason="BUDGET_EXHAUSTED",
        detail="已达到本次自动探索预算。",
        effect_key=f"{diagnosis_id}:budget-terminated",
    )

    with new_session() as session:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        assert diagnosis.status == "COMPLETED"
        # HYPOTHESIZING -> COLLECTING_EVIDENCE -> COMPLETED is committed as one
        # transaction, so clients never observe the intermediate state.
        assert diagnosis.version == 3
        completion = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type
                == "diagnosis.completed_from_supported_report",
            )
            .one()
        )
        assert completion.payload_json["report_id"] == "report-best-supported"
        assert completion.payload_json["limitations_preserved"] is True


def test_maintenance_does_not_finish_partial_report_before_replanning():
    diagnosis_id = "insight-maintenance-partial"
    hypothesis_id = _seed_diagnosis(diagnosis_id)
    with new_session() as session:
        session.add(
            DropInsightReportModel(
                id="report-partial-in-flight",
                diagnosis_id=diagnosis_id,
                hypothesis_id=hypothesis_id,
                conclusion="当前分支有支持，但仍需交叉验证。",
                confidence=800,
                evidence_refs_json=["evidence-partial"],
                counter_evidence_refs_json=[],
                assumptions_json=[],
                limitations_json=["缺少独立证据域"],
                next_actions_json=[],
                claims_json=[],
                verification_json={"status": "PARTIAL_WITHOUT_COUNTER"},
                effects_status="PENDING",
                effects_fencing_token=0,
                created_at=NOW,
            )
        )
        session.commit()

    result = service.maintain_drop_insight_sessions(timestamp=NOW)

    assert result["completed_diagnoses"] == []
    with new_session() as session:
        assert session.get(DropInsightSessionModel, diagnosis_id).status == (
            "COLLECTING_EVIDENCE"
        )


def test_low_confidence_verified_report_replans_instead_of_spinning(monkeypatch):
    diagnosis_id = "insight-verified-low-confidence"
    hypothesis_id = _seed_diagnosis(diagnosis_id)
    with new_session() as session:
        session.add(
            DropInsightReportModel(
                id="report-verified-below-gate",
                diagnosis_id=diagnosis_id,
                hypothesis_id=hypothesis_id,
                conclusion="结构化条件覆盖，但综合置信度仍不足。",
                confidence=590,
                evidence_refs_json=["evidence-support"],
                counter_evidence_refs_json=["evidence-counter"],
                assumptions_json=[],
                limitations_json=["证据冲突使综合置信度低于门槛"],
                next_actions_json=[],
                claims_json=[],
                verification_json={
                    "status": "VERIFIED",
                    "coverage_ratio": 1.0,
                },
                effects_status="PENDING",
                effects_fencing_token=0,
                created_at=NOW,
            )
        )
        session.commit()

    replans: list[tuple] = []
    learned_routes: list[str] = []
    monkeypatch.setattr(service, "_record_lats_report_outcome", lambda *_: None)
    monkeypatch.setattr(
        service,
        "_replan_after_insufficient_evidence",
        lambda *args, **kwargs: replans.append((args, kwargs)) or object(),
    )
    monkeypatch.setattr(
        service,
        "_record_successful_route",
        lambda *_args: learned_routes.append("called"),
    )

    report = service._apply_report_effects("report-verified-below-gate")

    assert report is not None
    assert len(replans) == 1
    assert "综合置信度" in replans[0][1]["continuation_reason"]
    assert learned_routes == []
    with new_session() as session:
        persisted = session.get(
            DropInsightReportModel,
            "report-verified-below-gate",
        )
        assert persisted.effects_status == "APPLIED"


def test_verified_report_before_required_minimum_round_replans_cross_domain(
    monkeypatch,
):
    diagnosis_id = "insight-verified-before-minimum"
    hypothesis_id = _seed_diagnosis(
        diagnosis_id,
        budget={
            "min_diagnosis_rounds": 3,
            "max_diagnosis_rounds": 4,
        },
    )
    with new_session() as session:
        session.add(
            DropInsightReportModel(
                id="report-verified-before-minimum",
                diagnosis_id=diagnosis_id,
                hypothesis_id=hypothesis_id,
                conclusion="第一轮采样支持热点，但场景仍要求交叉验证。",
                confidence=920,
                evidence_refs_json=["evidence-first-round"],
                counter_evidence_refs_json=["evidence-control"],
                assumptions_json=[],
                limitations_json=[],
                next_actions_json=[],
                claims_json=[],
                verification_json={"status": "VERIFIED", "coverage_ratio": 1.0},
                effects_status="PENDING",
                effects_fencing_token=0,
                created_at=NOW,
            )
        )
        session.commit()

    replans: list[tuple] = []
    learned_routes: list[str] = []
    monkeypatch.setattr(service, "_record_lats_report_outcome", lambda *_: None)
    monkeypatch.setattr(
        service,
        "_replan_after_insufficient_evidence",
        lambda *args, **kwargs: replans.append((args, kwargs)) or object(),
    )
    monkeypatch.setattr(
        service,
        "_record_successful_route",
        lambda *_args: learned_routes.append("called"),
    )

    report = service._apply_report_effects("report-verified-before-minimum")

    assert report is not None
    assert len(replans) == 1
    assert "至少 3 轮真实诊断" in replans[0][1]["continuation_reason"]
    assert "跨证据域交叉验证" in replans[0][1]["continuation_reason"]
    assert learned_routes == []
    with new_session() as session:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        assert diagnosis.status == "COLLECTING_EVIDENCE"
        persisted = session.get(
            DropInsightReportModel,
            "report-verified-before-minimum",
        )
        assert persisted.effects_status == "APPLIED"


def test_search_exhaustion_before_required_minimum_never_claims_completion():
    diagnosis_id = "insight-exhausted-before-minimum"
    hypothesis_id = _seed_diagnosis(
        diagnosis_id,
        status="HYPOTHESIZING",
        budget={
            "min_diagnosis_rounds": 3,
            "max_diagnosis_rounds": 4,
        },
    )
    _start_lats_search(diagnosis_id)
    with new_session() as session:
        session.add(
            DropInsightReportModel(
                id="report-supported-too-early",
                diagnosis_id=diagnosis_id,
                hypothesis_id=hypothesis_id,
                conclusion="第一轮存在支持信号。",
                confidence=900,
                evidence_refs_json=["evidence-first-round"],
                counter_evidence_refs_json=[],
                assumptions_json=[],
                limitations_json=["尚未完成场景要求的交叉验证"],
                next_actions_json=[],
                claims_json=[],
                verification_json={"status": "VERIFIED", "coverage_ratio": 1.0},
                effects_status="APPLIED",
                effects_fencing_token=1,
                created_at=NOW,
            )
        )
        session.commit()

    service._record_lats_termination(
        diagnosis_id,
        reason="NO_ELIGIBLE_CHILD",
        detail="没有新的安全探针可用。",
        effect_key=f"{diagnosis_id}:no-child",
    )

    with new_session() as session:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        assert diagnosis.status == "INSUFFICIENT_EVIDENCE"
        event = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type
                == "diagnosis.insufficient_evidence_finalized",
            )
            .one()
        )
        assert event.payload_json["best_supported_report_id"] == (
            "report-supported-too-early"
        )
        assert event.payload_json["minimum_diagnosis_rounds"] == 3
        assert event.payload_json["observed_diagnosis_rounds"] == 1
        assert event.payload_json["minimum_rounds_satisfied"] is False


def test_lats_verified_observation_before_minimum_reflects_and_keeps_search_open():
    diagnosis_id = "insight-lats-verified-before-minimum"
    hypothesis_id = _seed_diagnosis(
        diagnosis_id,
        budget={
            "min_diagnosis_rounds": 3,
            "max_diagnosis_rounds": 4,
        },
    )
    _start_lats_search(diagnosis_id)
    with new_session() as session:
        session.add(
            DropInsightReportModel(
                id="report-lats-verified-before-minimum",
                diagnosis_id=diagnosis_id,
                hypothesis_id=hypothesis_id,
                conclusion="第一轮热点信号可信。",
                confidence=910,
                evidence_refs_json=["evidence-first-round"],
                counter_evidence_refs_json=["evidence-control"],
                assumptions_json=[],
                limitations_json=[],
                next_actions_json=[],
                claims_json=[],
                verification_json={"status": "VERIFIED", "coverage_ratio": 1.0},
                effects_status="APPLIED",
                effects_fencing_token=1,
                created_at=NOW,
            )
        )
        session.commit()

    result = service._record_lats_report_outcome(
        "report-lats-verified-before-minimum"
    )

    assert result is not None
    assert result["reflection"]["decision"] == "EXPAND_FOR_CROSS_VALIDATION"
    with new_session() as session:
        events = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .all()
        )
        reflection = next(
            row
            for row in events
            if row.event_type == "lats.reflection_recorded"
        )
        assert "至少需要 3 轮真实诊断" in reflection.payload_json["summary"]
        assert not any(
            row.event_type == "lats.search_terminated" for row in events
        )
