from __future__ import annotations

import hashlib
import json

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.diagnostic_ai_rpc import dispatch
from server.app.drop_insight import adaptive_planner, diagnosis_agent
from server.app.drop_insight.schemas import CreateDiagnosisRequestV2
from server.app.drop_insight.service import (
    _record_planner_knowledge_retrieval,
    create_diagnosis,
    list_knowledge_retrievals,
)
from server.app.models import DropInsightEvidenceModel
from server.app.agent_runtime.retrieval import (
    build_retrieval_trace,
    retrieve_knowledge,
)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _knowledge_fixture(tmp_path):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    document = (
        "# CPU 初筛\n\n先看系统指标，不要直接归因。\n\n"
        "## Python 热点\n\npy-spy 能定位 GIL 和 Python 热点函数。"
    )
    raw = document.encode("utf-8")
    (knowledge_root / "cpu.md").write_bytes(raw)
    (knowledge_root / "catalog.json").write_text(
        json.dumps(
            [
                {
                    "knowledge_id": "python.cpu.hotspot",
                    "title": "Python CPU 热点",
                    "summary": "用 py-spy 检查 Python 用户态热点和 GIL。",
                    "keywords": ["python", "cpu", "py-spy", "gil", "热点"],
                    "applies_to": ["python_runtime", "cpu_hotspot"],
                    "required_evidence": ["py-spy Profile", "CPU 时间序列"],
                    "caveats": ["知识命中不能证明本次根因"],
                    "document": "cpu.md",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return knowledge_root, raw


def test_hybrid_retrieval_returns_auditable_source_and_best_markdown_chunk(tmp_path):
    knowledge_root, raw = _knowledge_fixture(tmp_path)

    matches = retrieve_knowledge(
        "Python 服务 CPU 高，检查 GIL 热点",
        knowledge_root=knowledge_root,
    )

    assert len(matches) == 1
    match = matches[0]
    assert match["query"] == "Python 服务 CPU 高，检查 GIL 热点"
    assert match["knowledge_id"] == "python.cpu.hotspot"
    assert match["title"] == "Python CPU 热点"
    assert match["document"] == "knowledge/cpu.md"
    assert match["content_hash"] == hashlib.sha256(raw).hexdigest()
    assert match["score"] > 0
    assert {"python", "cpu", "gil"}.issubset(set(match["matched_terms"]))
    assert match["required_evidence"] == ["py-spy Profile", "CPU 时间序列"]
    assert match["caveats"] == ["知识命中不能证明本次根因"]
    assert "Python 热点" in match["excerpt"]


def test_retrieval_returns_empty_for_unrelated_query(tmp_path):
    knowledge_root, _ = _knowledge_fixture(tmp_path)

    assert retrieve_knowledge("今天天气好吗", knowledge_root=knowledge_root) == []


def test_retrieval_trace_explicitly_refuses_to_be_incident_evidence(tmp_path):
    knowledge_root, _ = _knowledge_fixture(tmp_path)

    trace = build_retrieval_trace(
        "Python CPU 热点",
        knowledge_root=knowledge_root,
    )

    assert trace["matched_count"] == 1
    assert trace["evidence_contract"]["is_evidence"] is False
    assert trace["evidence_contract"]["status"] == "KNOWLEDGE_PRIOR_ONLY"
    assert "evidence_id" not in trace["matches"][0]


def test_langgraph_planner_receives_and_returns_the_same_retrieval_trace(monkeypatch):
    captured = {}
    trace = build_retrieval_trace("Python CPU 热点")
    monkeypatch.setenv("MINI_DROP_AGENT_FRAMEWORK", "langgraph")
    monkeypatch.setattr(adaptive_planner, "is_feature_enabled", lambda _feature: True)

    def fake_plan(context, _settings):
        captured["context"] = context
        return {
            "reasoning_summary": "先采集 Python 调用栈",
            "tool_name": "start_pyspy_profile",
            "hypotheses": [
                {
                    "statement": "Python 用户态存在热点",
                    "expected_observations": ["样本集中"],
                    "falsification_criteria": ["样本分散"],
                    "rationale": "知识只提示取证方向",
                }
            ],
        }

    monkeypatch.setattr(diagnosis_agent, "plan_with_diagnosis_agent", fake_plan)

    proposal = adaptive_planner.propose_hypothesis_plan(
        diagnosis_id="insight-rag-planner",
        query="Python CPU 热点",
        target={"agent_id": "agent-a", "pid": 101},
        category="PYTHON_RUNTIME",
        rule_plan={"tool_name": "start_pyspy_profile"},
        allowed_tools=["start_pyspy_profile"],
        retrieval_trace=trace,
    )

    assert captured["context"].retrieval_trace == trace
    assert proposal is not None
    assert proposal["retrieval_trace"] == trace


def test_persisted_retrievals_are_diagnosis_scoped_and_do_not_create_evidence():
    first = create_diagnosis(
        CreateDiagnosisRequestV2(query="检查 Python 服务 CPU 热点")
    )
    second = create_diagnosis(
        CreateDiagnosisRequestV2(query="检查 TCP 重传")
    )

    _record_planner_knowledge_retrieval(
        first.id,
        query=first.query,
        category="PYTHON_RUNTIME",
        phase="INITIAL_PLAN",
        effect_key=f"diagnosis:{first.id}:knowledge:test",
        round_index=1,
    )
    _record_planner_knowledge_retrieval(
        second.id,
        query=second.query,
        category="NETWORK_DEGRADATION",
        phase="INITIAL_PLAN",
        effect_key=f"diagnosis:{second.id}:knowledge:test",
        round_index=1,
    )

    first_rows = list_knowledge_retrievals(first.id)
    second_rows = list_knowledge_retrievals(second.id)
    assert len(first_rows) == 1
    assert len(second_rows) == 1
    assert first_rows[0]["diagnosis_id"] == first.id
    assert second_rows[0]["diagnosis_id"] == second.id
    assert all(
        row["retrieval_trace"]["evidence_contract"]["is_evidence"] is False
        for row in [*first_rows, *second_rows]
    )
    with new_session() as session:
        assert session.query(DropInsightEvidenceModel).count() == 0


def test_internal_rpc_exposes_only_requested_diagnosis_retrievals():
    first = create_diagnosis(
        CreateDiagnosisRequestV2(query="检查 Linux CPU 用户态热点")
    )
    second = create_diagnosis(
        CreateDiagnosisRequestV2(query="检查 MySQL 锁等待")
    )
    for diagnosis, category in (
        (first, "CPU_HOTSPOT"),
        (second, "DATABASE_LOCK"),
    ):
        _record_planner_knowledge_retrieval(
            diagnosis.id,
            query=diagnosis.query,
            category=category,
            phase="INITIAL_PLAN",
            effect_key=f"diagnosis:{diagnosis.id}:knowledge:rpc",
        )

    response = dispatch(
        "GET",
        f"/diagnoses/{first.id}/retrievals",
        "",
        "",
        "test:reader",
    )

    assert response.status == 200
    rows = response.body["data"]
    assert len(rows) == 1
    assert rows[0]["diagnosis_id"] == first.id
    assert rows[0]["retrieval_trace"]["matches"]
    assert all(row["diagnosis_id"] != second.id for row in rows)


def test_internal_rpc_returns_not_found_for_unknown_diagnosis():
    response = dispatch(
        "GET",
        "/diagnoses/insight-missing/retrievals",
        "",
        "",
        "test:reader",
    )

    assert response.status == 404
