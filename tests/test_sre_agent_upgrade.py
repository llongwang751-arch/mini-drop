from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

import pytest

from server.app.agent_runtime import semantic_retrieval as semantic
from server.app.agent_runtime.investigation_strategy import select_react_candidate
from server.app.drop_insight.claim_verifier import generate_sre_remediation_advice, _verification_result
from server.app.drop_insight.diagnosis_agent import DiagnosisAgentContext, _lookup


@pytest.fixture
def knowledge(tmp_path):
    (tmp_path / "cpu.md").write_text("# CPU\nPython GIL hotspot sampling py-spy", encoding="utf-8")
    (tmp_path / "io.md").write_text("# I/O\n磁盘等待 fsync 延迟", encoding="utf-8")
    (tmp_path / "catalog.json").write_text(json.dumps([
        {"knowledge_id": "cpu", "title": "CPU GIL", "document": "cpu.md"},
        {"knowledge_id": "io", "title": "磁盘", "document": "io.md"},
    ]), encoding="utf-8")
    return tmp_path


class Collection:
    def __init__(self):
        self.metadata = {"ready": False}
        self.rows = {}

    def count(self):
        return len(self.rows)

    def upsert(self, ids, embeddings, metadatas):
        self.rows.update(zip(ids, embeddings))

    def modify(self, metadata):
        self.metadata = metadata

    def query(self, **kwargs):
        return {"ids": [list(self.rows)], "distances": [[0.2] * len(self.rows)]}


class Client:
    def __init__(self):
        self.collections = {}

    def get_or_create_collection(self, name, **kwargs):
        return self.collections.setdefault(name, Collection())

    def get_collection(self, name, **kwargs):
        return self.collections[name]


class Provider:
    settings = semantic.RetrievalSettings(dimensions=2)
    calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[1.0, 0.1] for _ in texts]

    def rerank(self, query, docs):
        return [(i, 0.9 if "fsync" in text else 0.2) for i, text in enumerate(docs)]


def test_real_chroma_snapshot_and_retrieval(knowledge, tmp_path):
    chromadb = pytest.importorskip("chromadb")
    client = chromadb.PersistentClient(path=str(tmp_path / "index"))
    provider = Provider()
    built = semantic.build_index(knowledge, provider, client)
    assert built["chunks"] == 2
    assert semantic.build_index(knowledge, provider, client)["reused"]
    result = semantic.search("fsync", knowledge, provider=provider, client=client)
    assert result["actual_backend"] == "BM25_ENTITY_CHROMA_RRF_RERANK"
    assert result["matches"][0]["knowledge_id"] == "io"


def test_lexical_chunk_can_be_read(knowledge):
    from server.app.agent_runtime.retrieval import retrieve_knowledge
    match = retrieve_knowledge("GIL", knowledge_root=knowledge)[0]
    assert "GIL" in semantic.read_chunk(knowledge, match["chunk_id"])["excerpt"]


def test_chroma_http_requests_are_bounded_from_startup(monkeypatch):
    pytest.importorskip("chromadb")
    import httpx
    from server.app.agent_runtime.chroma_http import bounded_http_client
    requests = []
    def respond(client, method, url, **kwargs):
        assert client.timeout.connect == 2 and client.timeout.read == 5
        requests.append(url)
        if url.endswith("/auth/identity"):
            body = {"user_id": "test", "tenant": "default_tenant", "databases": ["default_database"]}
        elif "/databases/" in url:
            body = {"id": "00000000-0000-0000-0000-000000000001", "name": "default_database", "tenant": "default_tenant"}
        else:
            body = {"name": "default_tenant"}
        return httpx.Response(200, json=body, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.Client, "request", respond)
    bounded_http_client.cache_clear()
    client = bounded_http_client("test-chroma.invalid", 8000, False)
    assert client.tenant == "default_tenant" and len(requests) >= 3
    client.close()
    bounded_http_client.cache_clear()


def test_profile_percentage_alone_does_not_imply_cpu():
    advice = generate_sre_remediation_advice([{"claim_type": "TOP_FUNCTION_PERCENT", "direction": "SUPPORT"}], "VERIFIED")
    assert not advice["root_cause_fixes"]


def test_agent_graph_can_request_knowledge_before_final_response(monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from server.app.drop_insight import diagnosis_agent as agent_module
    class ToolModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self
    model = ToolModel(responses=[
        *[AIMessage(content="", tool_calls=[{"name": "search_knowledge", "args": {"query": query}, "id": f"lookup-{i}", "type": "tool_call"}])
          for i, query in enumerate(["Python GIL", "CPU hotspot", "disk wait", "memory pressure"])],
        AIMessage(content="knowledge retrieved"),
    ])
    monkeypatch.setattr("server.app.agent_runtime.model_factory.create_chat_model", lambda *args, **kwargs: model)
    monkeypatch.setattr(agent_module, "_get_checkpointer", lambda: InMemorySaver())
    monkeypatch.setattr(agent_module, "_AGENTS", {})
    context = DiagnosisAgentContext(diagnosis_id="graph-test", query="CPU", target={}, category="CPU", rule_plan={}, allowed_tools=())
    settings = SimpleNamespace(provider="compatible", model="fake", base_url="https://invalid.test", api_key="synthetic")
    graph = agent_module._agent_for(settings)
    result = graph.invoke({"messages": [{"role": "user", "content": "查阅 GIL 资料"}]},
                          config={"configurable": {"thread_id": "retrieval-graph-test"}, "recursion_limit": 64}, context=context)
    messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(messages) == 4
    assert json.loads(messages[0].content)["is_evidence"] is False
    assert context.lookup_state["remaining"] == 0


def test_react_selection_is_persisted_and_idempotent(monkeypatch):
    from server.app.database import init_db, reset_engine, new_session
    from server.app.models import DropInsightSessionModel, DropInsightEventModel
    from server.app.drop_insight.service import _record_lats_expansion_and_selection
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    now = datetime.now(timezone.utc)
    try:
        with new_session() as session:
            session.add(DropInsightSessionModel(id="react", query="CPU", created_by="alice", mode="AUTONOMOUS",
                budget_json={"investigation_strategy": "REACT"}, status="HYPOTHESIZING", created_at=now, updated_at=now))
            session.commit()
        candidates = [{"node_id": "hypothesis:first", "rank": 0, "initial_value": 0.1, "prior": 0.1},
                      {"node_id": "hypothesis:second", "rank": 1, "initial_value": 0.99, "prior": 0.99}]
        kwargs = dict(phase="TEST", round_index=1, parent_hypothesis_id=None, effect_prefix="react-round-1")
        selected = _record_lats_expansion_and_selection("react", candidates, **kwargs)
        assert selected["node_id"] == "hypothesis:first" and selected["score"] is None
        assert _record_lats_expansion_and_selection("react", candidates, **kwargs) == selected
        with new_session() as session:
            events = session.query(DropInsightEventModel).all()
            start = next(e for e in events if e.event_type == "lats.search_started")
            assert start.payload_json["algorithm"] == "REACT"
            assert start.payload_json["config"]["selection_policy"] == "REACT"
            assert len([e for e in events if e.event_type == "lats.node_selected"]) == 1
    finally:
        reset_engine()


def test_failed_index_is_not_published(knowledge):
    client = Client()
    provider = Provider()
    provider.embed = lambda texts: (_ for _ in ()).throw(semantic.RetrievalUnavailable("UNAVAILABLE"))
    with pytest.raises(semantic.RetrievalUnavailable):
        semantic.build_index(knowledge, provider, client)
    assert all(not c.metadata.get("ready") for c in client.collections.values())


def test_grafana_adapter_binds_scope_and_preserves_provenance(monkeypatch):
    from server.app.agent_runtime import grafana_observations as adapter
    target = {"service": 'api"bad', "environment": "prod"}
    diagnosis = SimpleNamespace(deleted_at=None, target_json=target,
        effective_time_range_json={"start": "2026-09-19T01:00:00Z", "end": "2026-09-19T01:05:00Z"})
    monkeypatch.setattr(adapter, "new_session", lambda: SimpleNamespace(get=lambda *args: diagnosis, close=lambda: None))
    for key, value in {"URL": "https://grafana.example", "TOKEN": "synthetic", "PROMETHEUS_UID": "metrics", "ENVIRONMENT": "prod"}.items():
        monkeypatch.setenv("MINI_DROP_GRAFANA_" + key, value)
    calls = []
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size):
            yield b'{"status":"success","data":{"resultType":"matrix","result":[]}}'
    monkeypatch.setattr(adapter.requests, "get", lambda url, **kwargs: (calls.append((url, kwargs)) or Response()))
    result = adapter.query_service_observations("current", "latency_p95")
    assert result["is_evidence"] is False and result["unit"] == "seconds"
    assert 'service_name="api\\"bad"' in calls[0][1]["params"]["query"]
    assert calls[0][1]["allow_redirects"] is False
    target["environment"] = "dev"
    with pytest.raises(ValueError, match="SCOPE"):
        adapter.query_service_observations("current", "latency_p95")
    assert len(calls) == 1


def test_missing_falsification_cannot_be_averaged_away():
    result = _verification_result([{"direction": "SUPPORT"}, {"direction": "CONTROL"}], [], {0, 1, 2, 3}, set(), 4, 1)
    assert result["coverage_ratio"] == 0.8
    assert result["status"] != "VERIFIED"


@pytest.mark.parametrize("text", ["不能确认 CPU 异常", "function", "GC 内存不足", "FD 文件描述符"])
def test_unverified_text_never_emits_changes(text):
    result = generate_sre_remediation_advice([], "INSUFFICIENT_EVIDENCE", text)
    assert not result["root_cause_fixes"]
    assert all("command" not in item for item in result["mitigations"])


def test_negation_and_function_substrings_do_not_classify_remediation():
    result = generate_sre_remediation_advice([
        {"claim_type": "TOP_FUNCTION_PERCENT", "statement": "function", "direction": "COUNTER"}
    ], "VERIFIED", "CPU memory io")
    assert not result["root_cause_fixes"]


def test_index_is_immutable_reusable_and_changed_source_fails_closed(knowledge):
    provider, client = Provider(), Client()
    first = semantic.build_index(knowledge, provider, client)
    assert not first["reused"]
    assert semantic.build_index(knowledge, provider, client)["reused"]
    result = semantic.search("磁盘延迟", knowledge, provider=provider, client=client)
    assert result["actual_backend"].endswith("RERANK")
    assert result["matches"][0]["knowledge_id"] == "io"
    chunk = result["matches"][0]["chunk_id"]
    (knowledge / "io.md").write_text("内容已撤销", encoding="utf-8")
    changed = semantic.search("fsync", knowledge, provider=provider, client=client)
    assert changed["actual_backend"] == "BM25_ENTITY_RRF"
    assert changed["degraded_reasons"] == ["INDEX_UNAVAILABLE"]
    with pytest.raises(semantic.RetrievalUnavailable):
        semantic.read_chunk(knowledge, chunk)


def test_private_and_path_escape_documents_are_not_embedded(knowledge):
    catalog = json.loads((knowledge / "catalog.json").read_text())
    catalog[0]["visibility"] = "PRIVATE"
    catalog[1]["document"] = "../secret.md"
    (knowledge / "catalog.json").write_text(json.dumps(catalog))
    assert semantic.corpus(knowledge) == []


def test_model_or_dimensions_change_index_identity(knowledge):
    corpus = semantic.corpus(knowledge)
    assert semantic.snapshot_name(corpus, semantic.RetrievalSettings(dimensions=2)) != semantic.snapshot_name(corpus, semantic.RetrievalSettings(dimensions=3))


def test_missing_index_does_not_call_remote_provider(knowledge):
    provider = Provider()
    result = semantic.search("GIL", knowledge, provider=provider, client=Client())
    assert result["matches"] and result["actual_backend"] == "BM25_ENTITY_RRF"
    assert provider.calls == 0


@pytest.mark.parametrize("data", [
    [{"index": 1, "embedding": [1, 2]}],
    [{"index": 0, "embedding": [float("nan"), 1]}],
    [{"index": 0, "embedding": [0, 0]}],
    [{"index": 0, "embedding": [1]}],
])
def test_bad_vectors_are_rejected(monkeypatch, data):
    provider = semantic.SemanticProvider(semantic.RetrievalSettings(dimensions=2))
    monkeypatch.setattr(provider, "_post", lambda *_: {"data": data})
    with pytest.raises(semantic.RetrievalUnavailable):
        provider.embed(["query"])


def test_react_does_not_use_search_reward_or_revisit_executed_nodes():
    candidates = [{"node_id": "old", "rank": 0}, {"node_id": "new", "rank": 1}, {"node_id": "other", "rank": 2}]
    choice = select_react_candidate(candidates, {"old": {"visits": 1}, "other": {"mean_value": 999}})
    assert choice["node_id"] == "new"
    assert choice["score"] is None


def test_lookup_budget_and_repeats_are_enforced_outside_model():
    context = DiagnosisAgentContext("d", "q", {}, "CPU", {}, ())
    assert json.loads(_lookup(context, "search", "one", lambda: {"ok": True}))["ok"]
    assert json.loads(_lookup(context, "search", "one", lambda: {}))["error"] == "DUPLICATE_LOOKUP"
    for i in range(3):
        _lookup(context, "search", str(i), lambda: {})
    assert json.loads(_lookup(context, "search", "extra", lambda: {}))["error"] == "LOOKUP_BUDGET_EXHAUSTED"
    assert len(context.lookup_state["traces"]) == 4


def test_incident_recall_is_scoped_and_archives_revoke_memory(monkeypatch):
    from server.app.database import init_db, reset_engine, new_session
    from server.app.models import DropInsightSessionModel, DropInsightReportModel
    from server.app.agent_runtime.incident_memory import recall_incidents
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    now = datetime.now(timezone.utc)
    try:
        with new_session() as session:
            for name, owner, env in [("current", "alice", "prod"), ("good", "alice", "prod"), ("other-user", "bob", "prod"), ("other-env", "alice", "dev")]:
                session.add(DropInsightSessionModel(id=name, query="GIL", created_by=owner,
                    target_json={"service": "api", "environment": env}, mode="AUTONOMOUS", status="COMPLETED", created_at=now, updated_at=now))
                session.flush()
                if name != "current":
                    session.add(DropInsightReportModel(id="r-"+name, diagnosis_id=name, conclusion="GIL hotspot", confidence=800,
                        evidence_refs_json=["old-evidence"], verification_json={"status": "VERIFIED", "coverage_ratio": 1.0,
                        "has_independent_counter_or_control": True}, created_at=now))
            session.commit()
        result = recall_incidents("current", "GIL")
        assert [r["diagnosis_id"] for r in result] == ["good"]
        assert result[0]["is_evidence"] is False
        with new_session() as session:
            session.get(DropInsightSessionModel, "good").deleted_at = now
            session.commit()
        assert recall_incidents("current", "GIL") == []
    finally:
        reset_engine()
