from datetime import datetime, timedelta, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.evidence import EvidenceEnvelope, classify_evidence
from server.app.drop_insight.builtin_skills import seed_repository_skills
from server.app.drop_insight.skill_evolution import (
    _activation_summary,
    _match_score,
    _rank_hybrid_skills,
    _rank_with_observed_reliability,
    _select_ranked_skill,
    apply_active_skill,
    create_candidate_from_diagnosis,
    evaluate_skill,
    get_persisted_skill_context,
    get_skill,
    list_activations,
    publish_skill,
    record_campaign_validation,
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
    _record_passing_campaign(candidate["skill_id"], diagnosis_id)
    return publish_skill(candidate["skill_id"])


def _record_passing_campaign(skill_id: str, suffix: str = "default") -> dict:
    return record_campaign_validation(
        skill_id,
        {
            "campaign_id": f"campaign-{suffix}",
            "benchmark_kind": "CROSS_ENVIRONMENT_SKILL_CAMPAIGN",
            "report_sha256": "b" * 64,
            "environment_fingerprints": ["python-3.11-container", "python-3.12-host"],
            "collector_kinds": ["perf_cpu", "sys_metrics"],
            "positive_cases": 1,
            "misleading_negative_cases": 1,
            "environment_drift_cases": 1,
            "paired_skill_no_skill_cases": 1,
            "passed_cases": 4,
            "total_cases": 4,
            "false_activation_rate": 0.0,
            "policy_violation_count": 0,
            "report_ref": "artifacts/skill-campaign.json",
        },
        recorded_by="campaign-runner",
    )


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
    with pytest.raises(ValueError, match="Campaign"):
        publish_skill(first["skill_id"])
    campaign = _record_passing_campaign(first["skill_id"], "verified-trajectory")
    assert campaign["gate_metrics"]["campaign_validation"]["passed"] is True
    assert publish_skill(first["skill_id"])["status"] == "ACTIVE"


def test_failed_cross_environment_campaign_blocks_publish():
    diagnosis_id = "diagnosis-failed-campaign"
    _seed_verified_trajectory(diagnosis_id)
    candidate = create_candidate_from_diagnosis(diagnosis_id, created_by="reviewer")
    evaluate_skill(candidate["skill_id"])
    result = record_campaign_validation(
        candidate["skill_id"],
        {
            "campaign_id": "campaign-failed",
            "benchmark_kind": "CROSS_ENVIRONMENT_SKILL_CAMPAIGN",
            "report_sha256": "c" * 64,
            "environment_fingerprints": ["python-3.11-container", "python-3.12-host"],
            "collector_kinds": ["perf_cpu"],
            "positive_cases": 1,
            "misleading_negative_cases": 1,
            "environment_drift_cases": 1,
            "paired_skill_no_skill_cases": 1,
            "passed_cases": 3,
            "total_cases": 4,
            "false_activation_rate": 0.25,
            "policy_violation_count": 0,
        },
        recorded_by="campaign-runner",
    )
    campaign = next(
        item
        for item in result["evaluations"]
        if item["case_kind"] == "CROSS_ENVIRONMENT_CAMPAIGN"
    )
    assert campaign["passed"] is False
    with pytest.raises(ValueError, match="未通过"):
        publish_skill(candidate["skill_id"])


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


def test_hybrid_retrieval_breaks_the_legacy_context_score_tie():
    timestamp = datetime.now(timezone.utc)
    common = {
        "family_key": "cpu_hotspot:staging",
        "category": "CPU_HOTSPOT",
        "version": 1,
        "status": "ACTIVE",
        "source_diagnosis_ids_json": [],
        "strategy_json": {"probe_order": ["start_perf_profile"]},
        "gate_metrics_json": {"eligible": True},
        "created_by": "reviewer",
        "created_at": timestamp,
        "updated_at": timestamp,
        "published_at": timestamp,
    }
    cpu_skill = DiagnosticSkillModel(
        id="skill-cpu-query",
        trigger_json={
            "environment": "staging",
            "service": "order-service",
            "source_query": "order service CPU hotspot calculate price",
        },
        **common,
    )
    unrelated_skill = DiagnosticSkillModel(
        id="skill-unrelated-query",
        trigger_json={
            "environment": "staging",
            "service": "order-service",
            "source_query": "batch export disk queue latency",
        },
        **common,
    )
    target = {
        "environment": "staging",
        "service": "order-service",
        "_baseline_tool": "collect_sys_metrics",
    }

    assert _match_score(cpu_skill, "CPU_HOTSPOT", target)[0] == _match_score(
        unrelated_skill, "CPU_HOTSPOT", target
    )[0]
    ranked = _rank_hybrid_skills(
        [unrelated_skill, cpu_skill],
        "CPU_HOTSPOT",
        target,
        "order service CPU is high in calculate price",
    )

    assert ranked[0][2].id == "skill-cpu-query"
    assert ranked[0][1]["retrieval"] == "HYBRID_BM25_VECTOR"
    assert ranked[0][1]["bm25"] > ranked[1][1]["bm25"]


def test_cross_category_correction_keeps_ambiguity_and_subsystem_hard_gates():
    timestamp = datetime.now(timezone.utc)
    common = {
        "version": 1,
        "status": "ACTIVE",
        "source_diagnosis_ids_json": [],
        "trigger_json": {
            "environment": "*",
            "source_query": "runtime worker blocked latency",
            "query_terms": ["runtime", "worker", "blocked", "latency"],
        },
        "strategy_json": {"probe_order": ["collect_sys_metrics"]},
        "gate_metrics_json": {"eligible": True},
        "created_by": "reviewer",
        "created_at": timestamp,
        "updated_at": timestamp,
        "published_at": timestamp,
    }
    python_skill = DiagnosticSkillModel(
        id="ambiguous-python",
        family_key="python:*",
        category="PYTHON_RUNTIME",
        **common,
    )
    go_skill = DiagnosticSkillModel(
        id="ambiguous-go",
        family_key="go:*",
        category="GO_RUNTIME",
        **common,
    )
    query = "runtime worker blocked latency"
    target = {"environment": "production", "_baseline_tool": "collect_sys_metrics"}

    ranked = _rank_hybrid_skills(
        [python_skill, go_skill], "UNKNOWN", target, query
    )
    assert len(ranked) == 2
    assert _select_ranked_skill(ranked) is None

    hard_gated = _rank_hybrid_skills(
        [python_skill, go_skill],
        "UNKNOWN",
        {**target, "suspected_subsystem": "network"},
        query,
    )
    assert hard_gated == []


def test_known_category_cannot_be_overridden_by_runtime_skill():
    seed_repository_skills()
    session = new_session()
    skills = (
        session.query(DiagnosticSkillModel)
        .filter(DiagnosticSkillModel.status == "ACTIVE")
        .all()
    )
    session.close()

    ranked = _rank_hybrid_skills(
        skills,
        "IO_LATENCY",
        {
            "environment": "demo",
            "service": "cpp-hotspot",
            "_baseline_tool": "start_ebpf_io_profile",
            "collector_capabilities": ["perf_cpu", "ebpf_io", "sys_metrics"],
        },
        "C++ 服务同步文件写入导致 I/O 延迟，请定位磁盘路径",
    )

    assert all(skill.category == "IO_LATENCY" for _, _, skill in ranked)


def test_repository_skill_unknown_category_recovery_loads_full_instructions_and_traces_rounds():
    seed_repository_skills()
    timestamp = datetime.now(timezone.utc)
    session = new_session()
    session.add(
        DropInsightSessionModel(
            id="diagnosis-cross-category",
            query="Python py-spy 显示 GIL 与事件循环阻塞，请定位用户态热点",
            target_json={"service": "api", "environment": "production"},
            time_range_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            mode="AUTONOMOUS",
            budget_json={},
            status="RUNNING",
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    first_plan = {"tool_name": "collect_sys_metrics"}
    first = apply_active_skill(
        "diagnosis-cross-category",
        "UNKNOWN",
        first_plan,
        {"service": "api", "environment": "production"},
        round_index=1,
        phase="INITIAL_PLAN",
        available_tools=["start_pyspy_profile", "collect_sys_metrics"],
    )

    assert first is not None
    assert first["applied"] is True
    assert first["state"] == "ACTIVATED"
    assert first["skill_name"] == "python-runtime-diagnosis"
    assert first["baseline_category"] == "UNKNOWN"
    assert first["selected_category"] == "PYTHON_RUNTIME"
    assert first["load_mode"] == "FULL_SKILL_MD"
    assert first["skill_category"] == "PYTHON_RUNTIME"
    assert first["category_correction"] == {
        "from": "UNKNOWN",
        "to": "PYTHON_RUNTIME",
        "guard": "STRONG_TEXT_SIGNAL",
    }
    assert first_plan["tool_name"] == "start_pyspy_profile"
    assert first["skill_instructions"]["name"] == "python-runtime-diagnosis"
    assert "## 必需证据与反证" in first["skill_instructions"]["body"]
    assert first["skill_instructions"]["sections"]["安全边界"]
    assert first["reuse_step"]["round_index"] == 1
    assert first["reuse_step"]["category_corrected"] is True

    first_retry = apply_active_skill(
        "diagnosis-cross-category",
        "UNKNOWN",
        {"tool_name": "collect_sys_metrics"},
        {"service": "api", "environment": "production"},
        round_index=1,
        phase="INITIAL_PLAN",
        available_tools=["start_pyspy_profile", "collect_sys_metrics"],
    )
    assert first_retry is not None
    assert first_retry["state"] == "REUSED"
    assert first_retry["reuse_trace"][0]["state"] == "ACTIVATED"

    session = new_session()
    session.add(
        DropInsightToolCallModel(
            id="tool-cross-category-pyspy",
            diagnosis_id="diagnosis-cross-category",
            tool_name="start_pyspy_profile",
            arguments_json={},
            policy_decision="ALLOW",
            policy_checks_json=[],
            policy_reason="completed first Skill step",
            status="COMPLETED",
            result_json={},
            requested_by="planner",
            created_at=timestamp,
        )
    )
    session.commit()
    session.close()

    second_plan = {"tool_name": "collect_sys_metrics"}
    second = apply_active_skill(
        "diagnosis-cross-category",
        "PYTHON_RUNTIME",
        second_plan,
        {"service": "api", "environment": "production"},
        round_index=2,
        phase="INSUFFICIENT_EVIDENCE_REPLAN",
        attempted_tools=["start_pyspy_profile"],
        available_tools=["collect_sys_metrics"],
    )
    assert second is not None
    assert second["selected_tool"] == "collect_sys_metrics"
    assert [item["round_index"] for item in second["reuse_trace"]] == [1, 2]

    # A retry of the same planner phase replaces that round rather than
    # manufacturing a third application.
    retry = apply_active_skill(
        "diagnosis-cross-category",
        "PYTHON_RUNTIME",
        second_plan,
        {"service": "api", "environment": "production"},
        round_index=2,
        phase="INSUFFICIENT_EVIDENCE_REPLAN",
        attempted_tools=["start_pyspy_profile"],
        available_tools=["collect_sys_metrics"],
    )
    assert retry is not None
    assert len(retry["reuse_trace"]) == 2
    persisted = list_activations("diagnosis-cross-category")
    assert len(persisted) == 1
    assert len(persisted[0]["match_reason"]["reuse_trace"]) == 2

    exhausted = apply_active_skill(
        "diagnosis-cross-category",
        "PYTHON_RUNTIME",
        {"tool_name": "collect_sys_metrics"},
        {
            "service": "api",
            "environment": "production",
            # The previous route is now filtered before ranking.  Its trusted
            # stop/refutation context must still survive this exhausted round.
            "collector_capabilities": [],
        },
        round_index=3,
        phase="COUNTER_EVIDENCE_REPLAN",
        attempted_tools=["start_pyspy_profile", "collect_sys_metrics", "start_perf_profile"],
        available_tools=["collect_sys_metrics"],
        return_decision=True,
    )
    assert exhausted is not None
    assert exhausted["applied"] is False
    assert exhausted["state"] == "EXHAUSTED"
    assert exhausted["selected_tool"] is None
    assert exhausted["skill_instructions"]["sections"]["适用与停止条件"]
    assert exhausted["reuse_trace"][-1]["state"] == "EXHAUSTED"

    persisted_context = get_persisted_skill_context("diagnosis-cross-category")
    assert persisted_context is not None
    assert persisted_context["skill_id"] == first["skill_id"]
    assert persisted_context["skill_instructions"]["body"] == first["skill_instructions"]["body"]
    assert persisted_context["reuse_trace"][-1]["state"] == "EXHAUSTED"


def test_follow_up_round_keeps_active_gc_skill_when_cpu_words_are_only_an_exclusion():
    seed_repository_skills()
    timestamp = datetime.now(timezone.utc)
    diagnosis_id = "diagnosis-java-gc-continuity"
    session = new_session()
    session.add(
        DropInsightSessionModel(
            id=diagnosis_id,
            query=(
                "诊断 demo 环境中进程名为 java 的 Java 服务出现 GC 和延迟抖动。"
                "先确认系统与堆趋势，再采集 JVM 调用栈定位分配路径，"
                "最后排除对象长期保留、锁等待和纯 CPU 热点，并验证停止后的恢复。"
            ),
            target_json={"service": "java-hotspot", "environment": "demo"},
            time_range_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            mode="AUTONOMOUS",
            budget_json={},
            status="RUNNING",
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    available = [
        "collect_sys_metrics",
        "collect_memory_profile",
        "start_jvm_profile",
        "start_perf_profile",
        "start_continuous_profile",
    ]
    first_plan = {"tool_name": "collect_sys_metrics"}
    first = apply_active_skill(
        diagnosis_id,
        "JVM_GC",
        first_plan,
        {"service": "java-hotspot", "environment": "demo"},
        round_index=1,
        phase="INITIAL_PLAN",
        available_tools=available,
    )
    assert first is not None
    assert first["skill_name"] == "gc-pressure-diagnosis"
    assert first["selected_tool"] == "collect_sys_metrics"

    session = new_session()
    session.add(
        DropInsightToolCallModel(
            id="tool-java-gc-baseline",
            diagnosis_id=diagnosis_id,
            tool_name="collect_sys_metrics",
            arguments_json={},
            policy_decision="ALLOW",
            policy_checks_json=[],
            policy_reason="completed GC baseline",
            status="COMPLETED",
            result_json={},
            requested_by="planner",
            created_at=timestamp,
        )
    )
    session.commit()
    session.close()

    # The fallback planner temporarily leans toward CPU after baseline
    # evidence is inconclusive.  The active GC Skill must continue with its
    # remaining route instead of treating the negated CPU phrase as intent.
    follow_up_plan = {"tool_name": "start_perf_profile"}
    follow_up = apply_active_skill(
        diagnosis_id,
        "CPU_HOTSPOT",
        follow_up_plan,
        {"service": "java-hotspot", "environment": "demo"},
        round_index=2,
        phase="INSUFFICIENT_EVIDENCE_REPLAN",
        attempted_tools=["collect_sys_metrics"],
        available_tools=available,
    )

    assert follow_up is not None
    assert follow_up["state"] == "REUSED"
    assert follow_up["skill_id"] == first["skill_id"]
    assert follow_up["skill_name"] == "gc-pressure-diagnosis"
    assert follow_up["selected_tool"] == "collect_memory_profile"
    assert follow_up["match_reason"]["continuity_guard"] == (
        "PERSISTED_ROUTE_HAS_REMAINING_SAFE_TOOL"
    )
    assert follow_up_plan["tool_name"] == "collect_memory_profile"


def test_real_reuse_reliability_breaks_close_retrieval_tie_without_bypassing_gates():
    timestamp = datetime.now(timezone.utc)
    common = {
        "category": "CPU_HOTSPOT",
        "version": 1,
        "status": "ACTIVE",
        "source_diagnosis_ids_json": [],
        "trigger_json": {"environment": "staging", "service": "order-service"},
        "strategy_json": {"probe_order": ["start_perf_profile"]},
        "gate_metrics_json": {"eligible": True},
        "created_by": "reviewer",
        "created_at": timestamp,
        "updated_at": timestamp,
        "published_at": timestamp,
    }
    reliable = DiagnosticSkillModel(id="skill-reliable", family_key="cpu:reliable", **common)
    weak = DiagnosticSkillModel(id="skill-weak", family_key="cpu:weak", **common)
    ranked = [
        (0.6, {"retrieval": "STRUCTURED_FALLBACK"}, weak),
        (0.6, {"retrieval": "STRUCTURED_FALLBACK"}, reliable),
    ]
    activations = [
        DiagnosticSkillActivationModel(
            id=f"activation-good-{index}", skill_id=reliable.id,
            diagnosis_id=f"diagnosis-good-{index}", match_score=800,
            match_reason_json={}, baseline_tool="collect_sys_metrics",
            selected_tool="start_perf_profile", outcome="CORRECT",
            created_at=timestamp, updated_at=timestamp,
        )
        for index in range(3)
    ] + [
        DiagnosticSkillActivationModel(
            id="activation-weak", skill_id=weak.id, diagnosis_id="diagnosis-weak",
            match_score=800, match_reason_json={}, baseline_tool="collect_sys_metrics",
            selected_tool="start_perf_profile", outcome="WRONG",
            created_at=timestamp, updated_at=timestamp,
        )
    ]

    adjusted = _rank_with_observed_reliability(ranked, activations)

    assert adjusted[0][2].id == reliable.id
    assert adjusted[0][1]["posterior_reliability"] > adjusted[1][1]["posterior_reliability"]
    assert adjusted[0][1]["retrieval_score_before_reliability"] == 0.6
    summary = _activation_summary(activations[:3])
    assert summary == {
        "activation_count": 3,
        "labeled_outcome_count": 3,
        "pending_outcome_count": 0,
        "correct_outcome_count": 3,
        "partial_outcome_count": 0,
        "wrong_outcome_count": 0,
        "outcome_coverage": 1.0,
        "observed_success_rate": 1.0,
        "posterior_reliability": 0.7143,
    }


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
    _record_passing_campaign(second["skill_id"], "diagnosis-v2")
    publish_skill(second["skill_id"])

    assert get_skill(first["skill_id"])["status"] == "RETIRED"
    restored = rollback_skill(second["skill_id"])
    assert restored["skill_id"] == first["skill_id"]
    assert restored["status"] == "ACTIVE"
    assert get_skill(second["skill_id"])["status"] == "QUARANTINED"
