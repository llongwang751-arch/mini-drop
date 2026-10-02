"""Public capability boundary regressions, not a blind quality evaluation."""
from __future__ import annotations

import json

import pytest

from server.app.agent_runtime.relevance import assess_relevance, query_profile
from server.app.agent_runtime.retrieval import build_retrieval_trace, retrieve_knowledge
from server.app.agent_runtime import semantic_retrieval as semantic
from tests.test_retrieval_abstention import Client, Provider


@pytest.mark.parametrize("query", [
    "Accelerator XYZ execution is slow; do not treat it as process CPU",
    "zephyr protocol has retransmissions; diagnose its stream flow control",
    "NebulaDB 索引分片合并与锁阻塞，请排查它的存储引擎",
    "nebulaDB 索引分片合并与锁阻塞，请排查它的存储引擎",
    "NebulaDB index lock waits; do not substitute PostgreSQL transactions",
    "NebulaDB 的分片可能堵塞，索引任务记录尚未拿到",
    "星际社交网络应该如何扩大联系圈",
    "A CPU-controlled character asks an agent for rules",
    "CUDA graph output changed; CPU is mentioned only as a comparison",
    "JVM monitor lock ownership and thread synchronization",
])
def test_unsupported_subjects_and_everyday_resource_words_do_not_admit(query):
    trace = build_retrieval_trace(query)
    assert trace["matches"] == []
    assert trace["outcome"] == "NO_RELEVANT_KNOWLEDGE"
    assert trace["health"] == "HEALTHY"
    assert trace["relevance_policy"] == "knowledge-subject-admission-v2"
    assert trace["no_match_is_normal"] is False


@pytest.mark.parametrize(("query", "expected"), [
    ("Java 排查CPU高", "linux.cpu.process_pressure"),
    ("HTTP响应慢但TCP正常，怎样区分下游处理时间", "distributed.downstream_pressure"),
    ("Java ThreadLocal leak: process RSS rises continuously", "linux.memory.process_growth"),
    ("Java ThreadLocal leak under load, how should I investigate memory", "linux.memory.process_growth"),
    ("Application NebulaDB is slow; independently, process CPU usage is high", "linux.cpu.process_pressure"),
    ("NebulaDB合并问题没有记录；另外RAG向量索引recall怎么测", "rag.quality.lifecycle"),
    ("某数据库字段 wait_event_type 中的锁等待怎么判断，当前是PostgreSQL", "postgres.waits"),
    ("JVM GC暂停和进程RSS增长，缺少同窗指标应采什么", "jvm.gc.pressure"),
    ("CPU正常 12%，TCP RTT 和丢包也正常，需要保持有界结论", "linux.network.retransmit"),
    ("我有一个Linux进程，CPU high，尚未收集profile", "linux.cpu.process_pressure"),
])
def test_supported_observations_survive_scope_and_missing_evidence(query, expected):
    assert expected in [row["knowledge_id"] for row in retrieve_knowledge(query, top_k=8)]


def test_excluded_substitution_is_auditable_without_rejecting_negative_observations():
    profile = query_profile("TCP RTT is normal; do not substitute MySQL lock evidence")
    assert profile["database_scopes"] == []
    assert profile["concepts"] == ["network"]
    assert profile["excluded_mentions"] == ["do not substitute MySQL lock evidence"]
    assert "normal" in profile["active_clauses"][0]


def test_only_primary_capabilities_admit_not_summary_alternatives():
    cpu = {"title": "Userland CPU pressure", "keywords": ["cpu", "userland"],
           "summary": "GC, network, memory, database locks are alternative causes."}
    assert not assess_relevance("JVM garbage collection pause", cpu)["accepted"]
    assert not assess_relevance("TCP retransmission RTT", cpu)["accepted"]
    assert assess_relevance("Java CPU high", cpu)["accepted"]


def test_quota_and_gc_require_their_specific_capability():
    quota = {"title": "Container CPU quota", "keywords": ["cpu.max", "cgroup"]}
    assert not assess_relevance("Java CPU high", quota)["accepted"]
    assert assess_relevance("cpu.max quota throttled", quota)["accepted"]
    gc = {"title": "JVM GC pause", "keywords": ["jvm", "gc"]}
    assert not assess_relevance("Java monitor locks", gc)["accepted"]
    assert assess_relevance("Java garbage collection pause", gc)["accepted"]


def test_catalog_anchor_admits_new_technology_without_policy_changes(tmp_path):
    rows = [{"knowledge_id": "new.subject", "title": "NebulaDB storage waits",
             "keywords": ["NebulaDB", "index merge", "lock"], "document": "new.md"}]
    (tmp_path / "new.md").write_text("# Storage waits\nInspect merge tasks.", encoding="utf-8")
    (tmp_path / "catalog.json").write_text(json.dumps(rows), encoding="utf-8")
    query = "NebulaDB index merge lock contention"
    assert [row["knowledge_id"] for row in retrieve_knowledge(query, knowledge_root=tmp_path)] == ["new.subject"]


def test_runtime_or_database_name_without_capability_is_insufficient():
    assert assess_relevance("PostgreSQL logical replication slots", {
        "title": "PostgreSQL transaction waits", "keywords": ["postgresql", "lock"]
    })["reason"] == "SUBJECT_WITHOUT_CAPABILITY"
    assert assess_relevance("An unknown engine lock waits", {
        "title": "Go mutex profiles", "keywords": ["go", "mutex"]
    })["reason"] == "RUNTIME_SCOPE_NOT_REQUESTED"


def test_subject_in_another_public_entry_cannot_expand_this_candidates_capability():
    new_engine = {"title": "NebulaDB transaction waits", "keywords": ["NebulaDB", "lock"]}
    rag = {"title": "RAG index recall", "keywords": ["rag", "index", "recall"]}
    decision = assess_relevance("NebulaDB index compaction waits", rag, catalog_entries=[rag, new_engine])
    assert not decision["accepted"]
    assert decision["reason"] == "TECHNICAL_SUBJECT_NOT_COVERED"
    assert decision["unregistered_subjects"] == []
    assert decision["unsupported_subjects"] == ["nebuladb"]
    assert not assess_relevance("PostgreSQL index storage settings", rag)["accepted"]


def test_invalid_catalog_paths_cannot_register_technical_subjects(tmp_path):
    rows = [{"knowledge_id": "rag", "title": "RAG index recall", "keywords": ["rag", "index"], "document": "rag.md"},
            {"knowledge_id": "bad", "title": "NebulaDB locks", "keywords": ["NebulaDB"], "document": "../bad.md"}]
    (tmp_path / "rag.md").write_text("# RAG\nIndex recall quality.", encoding="utf-8")
    (tmp_path.parent / "bad.md").write_text("NebulaDB", encoding="utf-8")
    (tmp_path / "catalog.json").write_text(json.dumps(rows), encoding="utf-8")
    trace = build_retrieval_trace("NebulaDB index compaction", knowledge_root=tmp_path)
    assert trace["matches"] == []
    decision = next(row for row in trace["rejected"] if row["knowledge_id"] == "rag")
    assert decision["unregistered_subjects"] == ["nebuladb"]


@pytest.mark.parametrize("mode", ["dense_rerank", "dense_only", "missing_index", "rerank_failure"])
def test_unsupported_named_subject_cannot_bypass_any_semantic_route(tmp_path, monkeypatch, mode):
    rows = [{"knowledge_id": "rag", "title": "RAG vector index recall",
             "keywords": ["rag", "index", "recall"], "document": "rag.md"}]
    (tmp_path / "rag.md").write_text("# RAG\nIndex updates require recall quality checks.", encoding="utf-8")
    (tmp_path / "catalog.json").write_text(json.dumps(rows), encoding="utf-8")
    provider = Provider(failure=mode == "rerank_failure")
    chunks = semantic.corpus(tmp_path)
    client = Client(chunks)
    if mode == "missing_index":
        class Missing:
            def get_collection(self, *args, **kwargs):
                raise RuntimeError("missing")
        client = Missing()
    monkeypatch.setenv("MINI_DROP_RERANK_ENABLED", "false" if mode == "dense_only" else "true")
    trace = semantic.search("NebulaDB index compaction wait", tmp_path, provider=provider, client=client)
    assert trace["matches"] == []
    assert trace["outcome"] == "NO_RELEVANT_KNOWLEDGE"
    assert trace["health"] == ("DEGRADED" if mode == "missing_index" else "HEALTHY")
    assert trace["rejected"][0]["reason"] == "TECHNICAL_SUBJECT_NOT_COVERED"
    assert provider.rerank_docs == []


def test_unknown_subject_gate_applies_to_outer_hybrid_fallback(monkeypatch):
    monkeypatch.setenv("MINI_DROP_RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(semantic, "search", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    trace = build_retrieval_trace("NebulaDB index compaction wait")
    assert trace["matches"] == []
    assert trace["health"] == "DEGRADED"
    assert trace["outcome"] == "NO_RELEVANT_KNOWLEDGE"


def test_unknown_backend_keeps_the_unsupported_subject_gate(monkeypatch):
    monkeypatch.setenv("MINI_DROP_RETRIEVAL_MODE", "unsupported-configuration")
    trace = build_retrieval_trace("NebulaDB index compaction wait")
    assert trace["matches"] == []
    assert trace["requested_backend"] == "UNSUPPORTED"
    assert trace["actual_backend"] == "BM25"
    assert trace["degraded_reasons"] == ["RETRIEVAL_MODE_UNSUPPORTED"]
