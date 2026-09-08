import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from langchain_core.messages import AIMessage, ToolMessage

from server.app.ai_provider import AISettings
from server.app.drop_insight import adaptive_planner
from server.app.drop_insight.diagnosis_agent import (
    AGENT_FRAMEWORK,
    DIAGNOSIS_OUTPUT_LANGUAGE_REQUIREMENT,
    SKILL_PROGRESSIVE_DISCLOSURE_REQUIREMENT,
    DiagnosisAgentContext,
    _accepted_tool_payload,
    _database_url_for_psycopg,
    _normalize_trusted_context,
    _provider_circuit_open,
    _record_provider_failure,
    _trusted_context_json,
    get_agent_runtime_status,
    normalize_diagnosis_plan_for_display,
    plan_with_diagnosis_agent,
    request_diagnostic_probe,
    reset_agent_runtime_for_tests,
)
from server.app.agent_runtime.harness import (
    safe_scope_candidates,
    selected_authorized_candidate,
)


def _context(**overrides):
    values = {
        "diagnosis_id": "insight_agent_test",
        "query": "Python 服务 CPU 升高",
        "target": {"agent_id": "agent-a", "pid": 123},
        "category": "PYTHON_RUNTIME",
        "rule_plan": {"tool_name": "start_pyspy_profile"},
        "allowed_tools": ("start_pyspy_profile",),
        "active_skill": {"skill_id": "skill-v1", "probe_order": ["start_pyspy_profile"]},
    }
    values.update(overrides)
    return DiagnosisAgentContext(**values)


def _proposal(tool_name="start_pyspy_profile"):
    return {
        "reasoning_summary": "先验证 Python 用户态热点是否集中",
        "tool_name": tool_name,
        "hypotheses": [
            {
                "statement": "CPU 由少数 Python 热点函数主导",
                "expected_observations": ["采样集中在少数调用栈"],
                "falsification_criteria": ["样本分散且无显著热点"],
                "rationale": "规则基线和服务运行时均指向 Python",
            }
        ],
    }


def test_nonretryable_provider_error_opens_fast_fallback_circuit(monkeypatch):
    class PaymentRequired(RuntimeError):
        status_code = 402

    settings = AISettings(
        enabled="full",
        provider="test",
        base_url="https://example.invalid",
        api_key="test-circuit-key",
        model="test-model",
        nlp_enabled=True,
        rca_enabled=True,
        summarize_enabled=True,
    )
    monkeypatch.setenv("MINI_DROP_AI_PROVIDER_COOLDOWN_SECONDS", "30")
    reset_agent_runtime_for_tests()
    try:
        assert _provider_circuit_open(settings) == (False, None)
        _record_provider_failure(settings, PaymentRequired("balance exhausted"))
        assert _provider_circuit_open(settings) == (True, 402)
    finally:
        reset_agent_runtime_for_tests()


def test_probe_tool_accepts_only_server_allowlisted_tools():
    runtime = SimpleNamespace(context=_context())
    accepted = request_diagnostic_probe.func(
        **_proposal(),
        runtime=runtime,
    )
    rejected = request_diagnostic_probe.func(
        **_proposal("start_perf_profile"),
        runtime=runtime,
    )

    assert json.loads(accepted)["accepted"] is True
    assert json.loads(rejected)["accepted"] is False


def test_agent_result_is_read_from_validated_tool_call():
    proposal = _proposal()
    messages = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "request_diagnostic_probe",
                "args": proposal,
                "id": "call-1",
                "type": "tool_call",
            }],
        ),
        ToolMessage(
            content=json.dumps({"accepted": True, "proposal": proposal}),
            tool_call_id="call-1",
            name="request_diagnostic_probe",
        ),
    ]

    parsed = _accepted_tool_payload(messages)

    assert parsed is not None
    assert parsed["tool_name"] == "start_pyspy_profile"
    assert parsed["hypotheses"][0]["falsification_criteria"]


def test_agent_result_rejects_collection_failure_as_counter_evidence():
    proposal = _proposal()
    proposal["hypotheses"][0]["falsification_criteria"] = [
        "py-spy 因权限不足采集失败"
    ]
    messages = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "request_diagnostic_probe",
                "args": proposal,
                "id": "call-unsafe-counter",
                "type": "tool_call",
            }],
        ),
        ToolMessage(
            content=json.dumps({"accepted": True, "proposal": proposal}),
            tool_call_id="call-unsafe-counter",
            name="request_diagnostic_probe",
        ),
    ]

    assert _accepted_tool_payload(messages) is None


def test_agent_result_correlates_tool_result_by_tool_call_id():
    accepted = _proposal()
    pending = _proposal("start_perf_profile")
    messages = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "request_diagnostic_probe",
                "args": accepted,
                "id": "call-accepted",
                "type": "tool_call",
            }],
        ),
        ToolMessage(
            content=json.dumps({"accepted": True, "proposal": accepted}),
            tool_call_id="call-accepted",
            name="request_diagnostic_probe",
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "request_diagnostic_probe",
                "args": pending,
                "id": "call-not-executed",
                "type": "tool_call",
            }],
        ),
    ]

    parsed = _accepted_tool_payload(messages)

    assert parsed is not None
    assert parsed["tool_name"] == "start_pyspy_profile"


def test_agent_result_rejects_mismatched_tool_payload():
    proposal = _proposal()
    mismatched = _proposal("start_perf_profile")
    messages = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "request_diagnostic_probe",
                "args": proposal,
                "id": "call-1",
                "type": "tool_call",
            }],
        ),
        ToolMessage(
            content=json.dumps({"accepted": True, "proposal": mismatched}),
            tool_call_id="call-1",
            name="request_diagnostic_probe",
        ),
    ]

    assert _accepted_tool_payload(messages) is None


def test_visible_english_model_plan_uses_chinese_server_rule_fallback():
    english = {
        "reasoning_summary": "Continuous profiling should find a transient hotspot.",
        "tool_name": "start_continuous_profile",
        "hypotheses": [
            {
                "statement": "Synchronous writes may block the Python process.",
                "expected_observations": ["Stacks remain blocked in fsync."],
                "falsification_criteria": ["No blocked write path appears."],
                "rationale": "Earlier probes missed intermittent activity.",
            }
        ],
    }
    normalized = normalize_diagnosis_plan_for_display(
        english,
        {
            "tool_name": "start_continuous_profile",
            "statement": "异常可能是短时漂移热点，单次采样窗口没有覆盖",
            "expected": ["连续采样至少一个窗口捕获稳定热点"],
            "falsification": ["多个连续窗口均没有稳定热点"],
        },
    )

    assert "简体中文" in DIAGNOSIS_OUTPUT_LANGUAGE_REQUIREMENT
    assert "切换证据域" in DIAGNOSIS_OUTPUT_LANGUAGE_REQUIREMENT
    assert normalized["language_normalization"] == "SERVER_RULE_FALLBACK"
    assert normalized["hypotheses"][0]["statement"].startswith("异常可能")
    assert normalized["hypotheses"][0]["rationale"].endswith("真实取证验证。")
    assert "Continuous profiling" not in json.dumps(normalized, ensure_ascii=False)


def test_langgraph_english_tool_output_is_normalized_before_service_use(monkeypatch):
    english = {
        "reasoning_summary": "Inspect a different evidence domain.",
        "tool_name": "start_pyspy_profile",
        "hypotheses": [
            {
                "statement": "A Python hot function consumes the CPU.",
                "expected_observations": ["Samples converge on one stack."],
                "falsification_criteria": ["Samples remain evenly distributed."],
                "rationale": "The process is a Python runtime.",
            }
        ],
    }
    messages = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "request_diagnostic_probe",
                "args": english,
                "id": "call-english",
                "type": "tool_call",
            }],
        ),
        ToolMessage(
            content=json.dumps({"accepted": True, "proposal": english}),
            tool_call_id="call-english",
            name="request_diagnostic_probe",
        ),
    ]

    class FakeAgent:
        def invoke(self, *_args, **_kwargs):
            return {"messages": messages}

    monkeypatch.setenv("MINI_DROP_AGENT_CHECKPOINT_BACKEND", "memory")
    monkeypatch.setattr(
        "server.app.drop_insight.diagnosis_agent._agent_for",
        lambda _settings: FakeAgent(),
    )
    settings = AISettings(
        enabled="full",
        provider="test",
        base_url="https://example.invalid",
        api_key="test-key",
        model="test-model",
        nlp_enabled=True,
        rca_enabled=True,
        summarize_enabled=True,
    )
    context = _context(
        rule_plan={
            "tool_name": "start_pyspy_profile",
            "statement": "Python 运行时可能存在用户态热点",
            "expected": ["py-spy 样本集中在少数调用栈"],
            "falsification": ["Python 样本分散且没有稳定热点"],
        }
    )
    reset_agent_runtime_for_tests()
    try:
        result = plan_with_diagnosis_agent(context, settings)
    finally:
        reset_agent_runtime_for_tests()

    assert result is not None
    assert result["language_normalization"] == "SERVER_RULE_FALLBACK"
    assert result["hypotheses"][0]["statement"] == (
        "Python 运行时可能存在用户态热点"
    )
    assert "A Python hot function" not in json.dumps(result, ensure_ascii=False)


def test_postgres_checkpoint_url_uses_psycopg_native_scheme(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://mini_drop:secret@postgres:5432/mini_drop",
    )

    assert _database_url_for_psycopg() == (
        "postgresql://mini_drop:secret@postgres:5432/mini_drop"
    )


def test_trusted_context_serializes_database_datetimes_as_iso8601():
    observed_at = datetime(2026, 9, 3, 11, 5, tzinfo=timezone.utc)

    encoded = _trusted_context_json(
        {"target": {"observed_at": observed_at}, "history": [observed_at]}
    )

    decoded = json.loads(encoded)
    assert decoded["target"]["observed_at"] == "2026-09-03T11:05:00+00:00"
    assert decoded["history"] == ["2026-09-03T11:05:00+00:00"]


def test_trusted_context_rejects_unknown_application_objects():
    with pytest.raises(TypeError, match="not JSON serializable"):
        _trusted_context_json({"unsafe": object()})


def test_normalize_trusted_context_makes_nested_datetimes_json_native():
    normalized = _normalize_trusted_context(
        {
            "prior_hypotheses": [
                {"created_at": datetime(2026, 9, 3, 11, 12, 13)}
            ]
        }
    )

    assert normalized == {
        "prior_hypotheses": [{"created_at": "2026-09-03T11:12:13"}]
    }
    json.dumps(normalized)


def test_adaptive_planner_delegates_context_and_skill_to_langgraph(monkeypatch):
    captured = {}
    monkeypatch.setenv("MINI_DROP_AGENT_FRAMEWORK", "langgraph")
    monkeypatch.setattr(adaptive_planner, "is_feature_enabled", lambda feature: True)

    def fake_plan(context, settings):
        captured["context"] = context
        captured["settings"] = settings
        return {
            **_proposal(),
            "agent_framework": AGENT_FRAMEWORK,
            "agent_version": "diagnosis-agent-v1",
            "checkpoint_backend": "memory",
        }

    from server.app.drop_insight import diagnosis_agent

    monkeypatch.setattr(diagnosis_agent, "plan_with_diagnosis_agent", fake_plan)
    active_skill = {
        "skill_id": "skill-v1",
        "state": "REUSED",
        "skill_instructions": {
            "body": "## 证据要求\n必须重新采集本次证据。",
            "sections": {"证据要求": "必须重新采集本次证据。"},
            "content_sha256": "a" * 64,
        },
    }
    result = adaptive_planner.propose_hypothesis_plan(
        diagnosis_id="insight_agent_test",
        query="Python 服务 CPU 升高",
        target={"agent_id": "agent-a", "pid": 123},
        category="PYTHON_RUNTIME",
        rule_plan={"tool_name": "start_pyspy_profile"},
        allowed_tools=["start_pyspy_profile"],
        active_skill=active_skill,
    )

    assert result is not None and result["agent_framework"] == AGENT_FRAMEWORK
    assert captured["context"].diagnosis_id == "insight_agent_test"
    assert captured["context"].active_skill == active_skill
    assert "完整 Skill 正文" in SKILL_PROGRESSIVE_DISCLOSURE_REQUIREMENT
    assert "不能扩大 allowed_tools" in SKILL_PROGRESSIVE_DISCLOSURE_REQUIREMENT
    assert isinstance(captured["settings"], AISettings)


def test_adaptive_planner_never_readds_rule_tool_outside_explicit_allowlist(monkeypatch):
    captured = {}
    monkeypatch.setenv("MINI_DROP_AGENT_FRAMEWORK", "langgraph")
    monkeypatch.setattr(adaptive_planner, "is_feature_enabled", lambda feature: True)

    def fake_plan(context, settings):
        captured["allowed_tools"] = context.allowed_tools
        return None

    from server.app.drop_insight import diagnosis_agent

    monkeypatch.setattr(diagnosis_agent, "plan_with_diagnosis_agent", fake_plan)
    result = adaptive_planner.propose_hypothesis_plan(
        diagnosis_id="insight_go_policy",
        query="Go 服务 CPU 升高",
        target={"agent_id": "agent-a", "pid": 123},
        category="GO_RUNTIME",
        rule_plan={"tool_name": "start_pyspy_profile"},
        allowed_tools=["collect_go_profile"],
    )

    assert result is None
    assert captured["allowed_tools"] == ("collect_go_profile",)


def test_scope_harness_hides_raw_process_authority_and_rejects_unknown_binding():
    candidates = [
        {
            "binding_id": "binding-safe",
            "service": "payments",
            "environment": "production",
            "process": "python",
            "collector_capabilities": ["pyspy"],
            "eligible": True,
            "pid": 4242,
            "agent_id": "agent-secret",
            "process_binding": {"boot_id": "secret"},
        }
    ]

    safe = safe_scope_candidates(candidates)

    assert safe[0]["binding_id"] == "binding-safe"
    assert "pid" not in safe[0]
    assert "agent_id" not in safe[0]
    assert "process_binding" not in safe[0]
    assert selected_authorized_candidate("binding-safe", safe) == safe[0]
    assert selected_authorized_candidate("binding-invented", safe) is None


def test_memory_checkpoint_runtime_reports_truthful_process_local_health(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AGENT_CHECKPOINT_BACKEND", "memory")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://mini_drop:must-not-appear@postgres:5432/mini_drop",
    )
    reset_agent_runtime_for_tests()
    try:
        status = get_agent_runtime_status()
    finally:
        reset_agent_runtime_for_tests()

    assert status["requested_backend"] == "memory"
    assert status["actual_backend"] == "memory"
    assert status["status"] == "HEALTHY"
    assert status["healthy"] is True
    assert status["degraded"] is False
    assert status["checkpoint_setup_status"] == "NOT_REQUIRED"
    assert status["checkpoint_schema_ready"] is None
    assert status["survives_process_restart"] is False
    assert "must-not-appear" not in json.dumps(status)


def test_postgres_checkpoint_runtime_reports_ready_schema(monkeypatch):
    from langgraph.checkpoint.postgres import PostgresSaver

    observed = {"setup": False, "closed": False}

    class FakeSaver:
        def setup(self):
            observed["setup"] = True

    class ReadyContext:
        def __enter__(self):
            return FakeSaver()

        def __exit__(self, *_args):
            observed["closed"] = True
            return False

    monkeypatch.setenv("MINI_DROP_AGENT_CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://mini_drop:secret@postgres:5432/mini_drop",
    )
    monkeypatch.setattr(
        PostgresSaver,
        "from_conn_string",
        lambda *_args, **_kwargs: ReadyContext(),
    )
    reset_agent_runtime_for_tests()
    try:
        status = get_agent_runtime_status()
    finally:
        reset_agent_runtime_for_tests()

    assert observed == {"setup": True, "closed": True}
    assert status["requested_backend"] == "postgres"
    assert status["actual_backend"] == "postgres"
    assert status["status"] == "HEALTHY"
    assert status["checkpoint_setup_status"] == "READY"
    assert status["checkpoint_schema_ready"] is True
    assert status["survives_process_restart"] is True


def test_postgres_checkpoint_failure_reports_degraded_memory_without_secrets(
    monkeypatch,
):
    from langgraph.checkpoint.postgres import PostgresSaver

    secret = "runtime-password-never-return"

    class FailingContext:
        def __enter__(self):
            raise RuntimeError(
                f"could not connect to postgresql://mini_drop:{secret}@db/mini_drop"
            )

        def __exit__(self, *_args):
            return False

    monkeypatch.setenv("MINI_DROP_AGENT_CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://mini_drop:{secret}@db:5432/mini_drop",
    )
    monkeypatch.setattr(
        PostgresSaver,
        "from_conn_string",
        lambda *_args, **_kwargs: FailingContext(),
    )
    reset_agent_runtime_for_tests()
    try:
        status = get_agent_runtime_status()
    finally:
        reset_agent_runtime_for_tests()

    serialized = json.dumps(status)
    assert status["requested_backend"] == "postgres"
    assert status["actual_backend"] == "memory"
    assert status["status"] == "DEGRADED"
    assert status["healthy"] is False
    assert status["degraded"] is True
    assert status["checkpoint_setup_status"] == "FAILED"
    assert status["checkpoint_schema_ready"] is False
    assert status["survives_process_restart"] is False
    assert "RuntimeError" in status["fallback_reason"]
    assert secret not in serialized
    assert "postgresql://" not in serialized


def test_probe_proposal_reports_actual_checkpoint_backend(monkeypatch):
    proposal = _proposal()
    messages = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "request_diagnostic_probe",
                "args": proposal,
                "id": "call-runtime-backend",
                "type": "tool_call",
            }],
        ),
        ToolMessage(
            content=json.dumps({"accepted": True, "proposal": proposal}),
            tool_call_id="call-runtime-backend",
            name="request_diagnostic_probe",
        ),
    ]

    class FakeAgent:
        def invoke(self, *_args, **_kwargs):
            return {"messages": messages}

    monkeypatch.setenv("MINI_DROP_AGENT_CHECKPOINT_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "server.app.drop_insight.diagnosis_agent._agent_for",
        lambda _settings: FakeAgent(),
    )
    settings = AISettings(
        enabled="full",
        provider="test",
        base_url="https://example.invalid",
        api_key="test-key",
        model="test-model",
        nlp_enabled=True,
        rca_enabled=True,
        summarize_enabled=True,
    )
    reset_agent_runtime_for_tests()
    try:
        result = plan_with_diagnosis_agent(_context(), settings)
        status = get_agent_runtime_status()
    finally:
        reset_agent_runtime_for_tests()

    assert result is not None
    assert result["checkpoint_backend"] == "memory"
    assert status["requested_backend"] == "postgres"
    assert status["actual_backend"] == "memory"
    assert status["degraded"] is True
