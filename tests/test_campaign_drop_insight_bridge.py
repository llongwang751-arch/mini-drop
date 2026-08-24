from datetime import datetime, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.campaign_bridge import promote_campaign
from server.app.models import (
    DropInsightEvidenceModel,
    DropInsightReportModel,
    DropInsightSessionModel,
)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    yield
    reset_engine()


def _completed_campaign() -> dict:
    return {
        "run_id": "campaign-cpu-verified",
        "scenario_id": "cpu_hotspot",
        "title": "Python 进程 CPU 热点",
        "status": "COMPLETED",
        "started_at": datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 8, 24, 10, 1, tzinfo=timezone.utc),
        "comparison": {"passed": True, "root_cause_match": True},
        "cleanup": {"attempted": True, "succeeded": True},
        "snapshots": {
            "baseline_snapshot": {"cpu_percent": 3.2, "fault_active": False},
            "fault_snapshot": {"cpu_percent": 92.4, "fault_active": True},
            "recovery_snapshot": {"cpu_percent": 4.1, "fault_active": False},
        },
        "diagnosis": {
            "root_cause": "SELF_CODE_CPU_HOTSPOT",
            "summary": "受控忙循环导致目标进程 CPU 持续升高",
        },
        "linked_task": {
            "task_id": "task-campaign-cpu",
            "status": "DONE",
            "agent_id": "agent-control",
            "target_pid": 4321,
            "collector_type": "perf_cpu",
            "task_attempts": [{"id": "attempt-campaign-cpu", "status": "SUCCEEDED"}],
            "artifacts": [
                {
                    "id": "artifact-campaign-cpu",
                    "integrity_status": "VERIFIED",
                    "sha256": "a" * 64,
                }
            ],
            "analysis_jobs": [
                {
                    "id": "analysis-campaign-cpu",
                    "status": "SUCCEEDED",
                    "analyzer_type": "collector.perf_cpu",
                    "analyzer_version": "1.2.0",
                }
            ],
        },
    }


def test_completed_campaign_becomes_verified_diagnosis():
    result = promote_campaign(_completed_campaign())

    assert result["verification_status"] == "VERIFIED"
    assert result["confidence"] > 0
    assert result["conclusion"].startswith("SUPPORTED")

    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, result["diagnosis_id"])
        report = session.get(DropInsightReportModel, result["report_id"])
        evidence = (
            session.query(DropInsightEvidenceModel)
            .filter(DropInsightEvidenceModel.diagnosis_id == result["diagnosis_id"])
            .all()
        )

        assert diagnosis.status == "COMPLETED"
        assert report.verification_json["status"] == "VERIFIED"
        assert report.verification_json["control_claim_count"] == 1
        assert {row.role for row in evidence} == {"SUPPORT", "CONTROL"}
        assert all(row.envelope_json["source"]["artifact_sha256"] == "a" * 64 for row in evidence)
    finally:
        session.close()


@pytest.mark.parametrize(
    "mutation, expected_message",
    [
        (lambda run: run["comparison"].update(passed=False), "Oracle"),
        (lambda run: run["cleanup"].update(succeeded=False), "清理"),
        (lambda run: run["linked_task"]["artifacts"].clear(), "可信链"),
        (lambda run: run["linked_task"]["analysis_jobs"].clear(), "可信链"),
    ],
)
def test_campaign_promotion_rejects_incomplete_trust_chain(mutation, expected_message):
    run = _completed_campaign()
    mutation(run)

    with pytest.raises(ValueError, match=expected_message):
        promote_campaign(run)
