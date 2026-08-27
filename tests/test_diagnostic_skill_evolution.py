from datetime import datetime, timedelta, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.evidence import EvidenceEnvelope, classify_evidence
from server.app.drop_insight.skill_evolution import (
    apply_active_skill,
    create_candidate_from_diagnosis,
    evaluate_skill,
    get_skill,
    list_activations,
    publish_skill,
    record_activation_outcome,
    rollback_skill,
)
from server.app.models import (
    DiagnosticSkillActivationModel,
    DiagnosticSkillModel,
    DropInsightEvidenceModel,
    DropInsightFeedbackModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _valid_evidence(diagnosis_id: str, timestamp: datetime) -> tuple[dict, dict]:
    envelope = EvidenceEnvelope.model_validate(
        {
            "evidence_id": f"evidence-{diagnosis_id}",
            "diagnosis_id": diagnosis_id,
            "evidence_type": "PERF_CPU_PROFILE",
            "source": {
                "tool_name": "start_perf_profile",
                "task_id": f"task-{diagnosis_id}",
                "task_attempt_id": f"attempt-{diagnosis_id}",
                "artifact_id": f"artifact-{diagnosis_id}",
                "artifact_sha256": "a" * 64,
                "analysis_job_id": f"analysis-{diagnosis_id}",
                "analyzer_type": "perf_profile",
                "analyzer_version": "1.0.0",
                "analyzer_output_schema_version": "1.0.0",
                "observation_json_pointer": "/top_symbol",
            },
            "scope": {
                "agent_id": "agent-a",
                "service": "order-service",
                "pid": 123,
            },
            "time_range": {
                "start": timestamp - timedelta(minutes=1),
                "end": timestamp,
                "timezone": "UTC",
            },
            "observation": {"top_symbol": "calculate_price", "cpu_percent": 74},
            "quality": {
                "level": "HIGH",
                "sample_count": 200,
                "degraded": False,
                "target_match": True,
                "time_overlap": True,
            },
        }
    )
    return envelope.model_dump(mode="json"), classify_evidence(envelope)


def _seed_verified_trajectory(diagnosis_id: str, *, environment: str = "staging") -> None:
    session = new_session()
    timestamp = datetime.now(timezone.utc)
    evidence_envelope, evidence_classification = _valid_evidence(
        diagnosis_id, timestamp
    )
    session.add(
        DropInsightSessionModel(
            id=diagnosis_id,
            query="order service CPU is high",
            target_json={
                "service": "order-service",
                "environment": environment,
                "agent_id": "agent-a",
                "pid": 123,
            },
            time_range_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            mode="ASSISTED",
            budget_json={},
            status="COMPLETED",
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.add(
        DropInsightToolCallModel(
            id=f"tool-{diagnosis_id}",
            diagnosis_id=diagnosis_id,
            tool_name="start_perf_profile",
            arguments_json={"agent_id": "agent-a", "pid": 123},
            policy_decision="ALLOW",
            policy_checks_json=[],
            policy_reason="approved safe read-only probe",
            status="COMPLETED",
            result_json={"top_symbol": "calculate_price", "cpu_percent": 74},
            requested_by="planner",
            created_at=timestamp,
        )
    )
    session.add(
        DropInsightEvidenceModel(
            id=f"evidence-{diagnosis_id}",
            diagnosis_id=diagnosis_id,
            hypothesis_id=None,
            role="SUPPORT",
            envelope_json=evidence_envelope,
            classification_json=evidence_classification,
            created_at=timestamp,
        )
    )
    session.add(
        DropInsightReportModel(
            id=f"report-{diagnosis_id}",
            diagnosis_id=diagnosis_id,
            conclusion="calculate_price is the verified CPU hotspot",
            confidence=920,
            evidence_refs_json=[f"evidence-{diagnosis_id}"],
            counter_evidence_refs_json=[],
            assumptions_json=[],
            limitations_json=[],
            next_actions_json=[],
            claims_json=[],
            verification_json={"status": "VERIFIED"},
            effects_status="APPLIED",
            created_at=timestamp,
        )
    )
    session.add(
        DropInsightFeedbackModel(
            id=f"feedback-{diagnosis_id}",
            diagnosis_id=diagnosis_id,
            report_id=f"report-{diagnosis_id}",
            feedback_label="correct",
            predicted_conclusion="calculate_price is the verified CPU hotspot",
            requested_replan=False,
            created_by="reviewer",
            created_at=timestamp,
        )
    )
    session.commit()
    session.close()


def _publish_source(diagnosis_id: str = "diagnosis-source") -> dict:
    _seed_verified_trajectory(diagnosis_id)
    candidate = create_candidate_from_diagnosis(diagnosis_id, created_by="reviewer")
    evaluated = evaluate_skill(candidate["skill_id"])
    assert evaluated["gate_metrics"]["eligible"] is True
    return publish_skill(candidate["skill_id"])


def test_latest_verified_report_can_generate_candidate_before_human_publish_approval():
    diagnosis_id = "diagnosis-stale-feedback"
    _seed_verified_trajectory(diagnosis_id)
    session = new_session()
    timestamp = datetime.now(timezone.utc) + timedelta(seconds=1)
    session.add(
        DropInsightReportModel(
            id=f"report-{diagnosis_id}-new",
            diagnosis_id=diagnosis_id,
            conclusion="new report without review",
            confidence=920,
            evidence_refs_json=[f"evidence-{diagnosis_id}"],
            counter_evidence_refs_json=[],
            assumptions_json=[],
            limitations_json=[],
            next_actions_json=[],
            claims_json=[],
            verification_json={"status": "VERIFIED"},
            effects_status="APPLIED",
            created_at=timestamp,
        )
    )
    session.commit()
    session.close()

    candidate = create_candidate_from_diagnosis(diagnosis_id, created_by="system:auto")

    assert candidate["status"] == "CANDIDATE"
    assert candidate["source_diagnosis_ids"] == [diagnosis_id]


def test_verified_trajectory_becomes_versioned_active_skill_once():
    _seed_verified_trajectory("diagnosis-source")
    first = create_candidate_from_diagnosis("diagnosis-source", created_by="reviewer")
    duplicate = create_candidate_from_diagnosis("diagnosis-source", created_by="reviewer")

    assert duplicate["skill_id"] == first["skill_id"]
    assert first["strategy"]["confidence_floor"] == pytest.approx(0.92)
    evaluated = evaluate_skill(first["skill_id"])
    assert evaluated["gate_metrics"]["eligible"] is True
    assert evaluated["gate_metrics"]["passed"] == 3
    assert evaluated["gate_metrics"]["total"] == 3
    assert evaluated["gate_metrics"]["evaluation_mode"] == "DETERMINISTIC_CONTRACT_GATE"
    assert evaluated["gate_metrics"]["requires_campaign_validation"] is True
    assert evaluated["gate_metrics"]["negative_transfer_guard"] is True
    assert evaluated["gate_metrics"]["environment_drift_guard"] is True
    assert {item["case_kind"] for item in evaluated["evaluations"]} == {
        "POSITIVE_REPLAY",
        "MISLEADING_NEGATIVE",
        "ENVIRONMENT_DRIFT",
    }
    assert publish_skill(first["skill_id"])["status"] == "ACTIVE"


def test_verified_campaign_trust_chain_can_become_candidate_without_tool_call():
    diagnosis_id = "diagnosis-campaign-source"
    _seed_verified_trajectory(diagnosis_id)
    session = new_session()
    diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
    diagnosis.mode = "REPRODUCTION"
    session.query(DropInsightToolCallModel).filter(
        DropInsightToolCallModel.diagnosis_id == diagnosis_id
    ).delete()
    evidence = session.get(DropInsightEvidenceModel, f"evidence-{diagnosis_id}")
    envelope = dict(evidence.envelope_json or {})
    source = dict(envelope.get("source") or {})
    source["tool_name"] = "perf_cpu"
    envelope["source"] = source
    observation = dict(envelope.get("observation") or {})
    observation["metadata"] = {"campaign_run_id": "campaign-run-1"}
    envelope["observation"] = observation
    evidence.envelope_json = envelope
    evidence.classification_json = classify_evidence(
        EvidenceEnvelope.model_validate(envelope)
    )
    session.commit()
    session.close()

    candidate = create_candidate_from_diagnosis(diagnosis_id, created_by="reviewer")

    assert candidate["strategy"]["probe_order"] == ["start_perf_profile"]
    assert candidate["strategy"]["route_source"] == "CAMPAIGN_TRUST_CHAIN"
    assert candidate["category"] == "CPU_HOTSPOT"


def test_assisted_diagnosis_cannot_replace_tool_call_with_campaign_metadata():
    diagnosis_id = "diagnosis-assisted-no-tool"
    _seed_verified_trajectory(diagnosis_id)
    session = new_session()
    session.query(DropInsightToolCallModel).filter(
        DropInsightToolCallModel.diagnosis_id == diagnosis_id
    ).delete()
    evidence = session.get(DropInsightEvidenceModel, f"evidence-{diagnosis_id}")
    envelope = dict(evidence.envelope_json or {})
    observation = dict(envelope.get("observation") or {})
    observation["metadata"] = {"campaign_run_id": "campaign-run-2"}
    envelope["observation"] = observation
    evidence.envelope_json = envelope
    session.commit()
    session.close()

    with pytest.raises(ValueError, match="真实工具调用"):
        create_candidate_from_diagnosis(diagnosis_id, created_by="reviewer")


def test_active_skill_reuses_route_only_for_matching_context():
    published = _publish_source()
    plan = {"tool_name": "collect_sys_metrics"}
    activation = apply_active_skill(
        "diagnosis-reuse",
        "CPU_HOTSPOT",
        plan,
        {"service": "order-service", "environment": "staging"},
    )

    assert activation is not None
    assert activation["skill_id"] == published["skill_id"]
    assert activation["baseline_tool"] == "collect_sys_metrics"
    assert activation["selected_tool"] == "start_perf_profile"
    assert plan["tool_name"] == "start_perf_profile"

    drift_plan = {"tool_name": "collect_sys_metrics"}
    assert apply_active_skill(
        "diagnosis-drift",
        "CPU_HOTSPOT",
        drift_plan,
        {"service": "order-service", "environment": "production"},
    ) is None
    assert drift_plan["tool_name"] == "collect_sys_metrics"

    misleading_plan = {"tool_name": "collect_network_diagnostics"}
    assert apply_active_skill(
        "diagnosis-misleading",
        "CPU_HOTSPOT",
        misleading_plan,
        {
            "service": "order-service",
            "environment": "staging",
            "suspected_subsystem": "network",
        },
    ) is None
    assert misleading_plan["tool_name"] == "collect_network_diagnostics"

    capability_plan = {"tool_name": "collect_sys_metrics"}
    assert apply_active_skill(
        "diagnosis-capability-drift",
        "CPU_HOTSPOT",
        capability_plan,
        {
            "service": "order-service",
            "environment": "staging",
            "collector_capabilities": ["sys_metrics"],
            "permission_denied": ["perf_cpu"],
        },
    ) is None
    assert capability_plan["tool_name"] == "collect_sys_metrics"


def test_active_skill_advances_through_verified_probe_order():
    published = _publish_source()
    session = new_session()
    skill = session.get(DiagnosticSkillModel, published["skill_id"])
    skill.strategy_json = {
        **(skill.strategy_json or {}),
        "probe_order": ["start_perf_profile", "collect_sys_metrics"],
    }
    session.commit()
    session.close()

    target = {"service": "order-service", "environment": "staging"}
    first_plan = {"tool_name": "collect_sys_metrics"}
    first = apply_active_skill("diagnosis-route", "CPU_HOTSPOT", first_plan, target)
    assert first["selected_tool"] == "start_perf_profile"

    session = new_session()
    session.add(
        DropInsightToolCallModel(
            id="tool-diagnosis-route-perf",
            diagnosis_id="diagnosis-route",
            tool_name="start_perf_profile",
            arguments_json={},
            policy_decision="ALLOW",
            policy_checks_json=[],
            policy_reason="approved",
            status="COMPLETED",
            result_json={},
            requested_by="planner",
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    session.close()

    second_plan = {"tool_name": "collect_sys_metrics"}
    second = apply_active_skill("diagnosis-route", "CPU_HOTSPOT", second_plan, target)
    assert second["selected_tool"] == "collect_sys_metrics"
    assert second_plan["tool_name"] == "collect_sys_metrics"


def test_one_diagnosis_can_compose_multiple_active_skills():
    cpu_skill = _publish_source()
    session = new_session()
    timestamp = datetime.now(timezone.utc)
    session.add(
        DiagnosticSkillModel(
            id="skill-io-active",
            family_key="io_latency:staging",
            category="IO_LATENCY",
            version=1,
            status="ACTIVE",
            source_diagnosis_ids_json=["diagnosis-source"],
            trigger_json={"environment": "staging", "service": "order-service"},
            strategy_json={"probe_order": ["start_ebpf_io_profile"]},
            gate_metrics_json={"eligible": True},
            parent_skill_id=None,
            created_by="reviewer",
            created_at=timestamp,
            updated_at=timestamp,
            published_at=timestamp,
        )
    )
    session.add(
        DropInsightSessionModel(
            id="diagnosis-composed",
            query="CPU and I/O latency rise together",
            target_json={"service": "order-service", "environment": "staging"},
            time_range_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            mode="ASSISTED",
            budget_json={},
            status="RUNNING",
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    target = {"service": "order-service", "environment": "staging"}
    cpu_plan = {"tool_name": "collect_sys_metrics"}
    io_plan = {"tool_name": "collect_sys_metrics"}

    first = apply_active_skill("diagnosis-composed", "CPU_HOTSPOT", cpu_plan, target)
    second = apply_active_skill("diagnosis-composed", "IO_LATENCY", io_plan, target)

    assert first["skill_id"] == cpu_skill["skill_id"]
    assert second["skill_id"] == "skill-io-active"
    assert {item["skill_id"] for item in list_activations("diagnosis-composed")} == {
        cpu_skill["skill_id"],
        "skill-io-active",
    }


def test_repeated_wrong_feedback_quarantines_skill():
    published = _publish_source()
    target = {"service": "order-service", "environment": "staging"}
    for diagnosis_id in ("diagnosis-wrong-1", "diagnosis-wrong-2"):
        plan = {"tool_name": "collect_sys_metrics"}
        assert apply_active_skill(diagnosis_id, "CPU_HOTSPOT", plan, target)
        record_activation_outcome(diagnosis_id, "wrong")

    skill = get_skill(published["skill_id"])
    assert skill["status"] == "QUARANTINED"
    assert skill["gate_metrics"]["negative_transfer_count"] == 2


def test_new_version_can_roll_back_to_previous_published_strategy():
    first = _publish_source("diagnosis-v1")
    _seed_verified_trajectory("diagnosis-v2")
    second = create_candidate_from_diagnosis("diagnosis-v2", created_by="reviewer")
    assert second["version"] == 2
    evaluate_skill(second["skill_id"])
    publish_skill(second["skill_id"])

    assert get_skill(first["skill_id"])["status"] == "RETIRED"
    restored = rollback_skill(second["skill_id"])
    assert restored["skill_id"] == first["skill_id"]
    assert restored["status"] == "ACTIVE"
    assert get_skill(second["skill_id"])["status"] == "QUARANTINED"
