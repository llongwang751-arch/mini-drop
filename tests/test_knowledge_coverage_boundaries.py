"""Public observation coverage with positive and negative admission boundaries.

These maintenance examples are developer regressions, never a blind benchmark.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.agent_runtime.relevance import POLICY_VERSION, assess_relevance
from server.app.agent_runtime.retrieval import build_retrieval_trace, retrieve_knowledge
from server.app.agent_runtime import semantic_retrieval as semantic
from tests.test_retrieval_abstention import Client, Provider


ROOT = Path(__file__).resolve().parents[1] / "knowledge"


def entry(knowledge_id: str) -> dict:
    rows = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    return next(row for row in rows if row["knowledge_id"] == knowledge_id)


@pytest.mark.parametrize(("query", "expected"), [
    ("Java worker CPU remains normal; describe the observation scope", "linux.cpu.process_pressure"),
    ("JVM threads have low CPU during the observation window", "jvm.cpu.thread_observation"),
    ("Java 线程 CPU 正常，ThreadMXBean 的计时未启用时能认为零CPU吗", "jvm.cpu.thread_observation"),
    ("Java getThreadCpuTime returned -1; which measurement prerequisites are missing", "jvm.cpu.thread_observation"),
    ("same-node iowait rises on a shared block device", "linux.iowait.shared_block_device"),
    ("co-located instances share storage; compare host contention in the same window", "distributed.same_host_noisy_neighbor"),
    ("同主机的邻居和目标出现块设备等待，需哪些反证", "linux.iowait.shared_block_device"),
    ("syncwrite latency is low; what can it refute in this measured window", "linux.io.synchronous_write"),
    ("synchronous write samples are below the slow-write threshold", "linux.io.synchronous_write"),
    ("fdatasync success count and low latency are recorded on tmpfs", "linux.io.synchronous_write"),
    ("同步落盘窗口低延迟，可以反驳该窗口慢写吗", "linux.io.synchronous_write"),
])
def test_public_observations_and_refutations_have_supported_prior(query, expected):
    ids = [row["knowledge_id"] for row in retrieve_knowledge(query, top_k=8)]
    assert expected in ids


@pytest.mark.parametrize("query", [
    "A CPU-controlled character asks a worker agent for game rules",
    "TCP is my team's name; write its anthem",
    "Aurora protocol has synchronous write stream flow control",
    "NebulaDB engine compacts its index; do not substitute block device iowait",
    "某未知网络的社交关系需要优化",
    "Java worker has tasks; explain its business rules without CPU observations",
    "UnknownRuntime worker CPU is normal; diagnose its proprietary scheduler",
    "UnknownRuntime supports ThreadMXBean; diagnose its proprietary event loop",
])
def test_terms_do_not_expand_unknown_products_or_everyday_meanings(query):
    trace = build_retrieval_trace(query)
    assert trace["matches"] == []
    assert trace["outcome"] == "NO_RELEVANT_KNOWLEDGE"
    assert trace["health_scope"] == "RETRIEVAL_ONLY"
    assert trace["no_match_is_normal"] is False
    assert trace["relevance_policy"] == POLICY_VERSION


@pytest.mark.parametrize(("query", "knowledge_id", "reason"), [
    ("Python worker CPU is low", "jvm.cpu.thread_observation", "RUNTIME_SCOPE_CONFLICT"),
    ("Go mutex lock ownership", "jvm.cpu.thread_observation", "NO_DISTINCTIVE_DOMAIN_ANCHOR"),
    ("Java monitor lock ownership", "jvm.cpu.thread_observation", "NO_DISTINCTIVE_DOMAIN_ANCHOR"),
    ("MySQL lock waits", "postgres.waits", "DATABASE_SCOPE_CONFLICT"),
    ("PostgreSQL transaction locking", "mysql.lock_wait", "DATABASE_SCOPE_CONFLICT"),
])
def test_new_cpu_coverage_does_not_remove_runtime_database_capability_guards(query, knowledge_id, reason):
    decision = assess_relevance(query, entry(knowledge_id))
    assert not decision["accepted"]
    assert decision["reason"] == reason


def test_explicit_negation_cannot_supply_new_io_or_runtime_anchor():
    for query in (
        "Aurora protocol stream order; do not substitute synchronous write evidence",
        "NebulaDB index management; do not use Java ThreadMXBean CPU",
        "社交网络的联系规则；不要用 same-node iowait 块设备代替",
    ):
        assert build_retrieval_trace(query)["matches"] == []
    # Ordinary negative observations retain their actual measurement topic.
    assert "linux.io.synchronous_write" in [
        row["knowledge_id"] for row in retrieve_knowledge("fsync has no slow calls in this window", top_k=8)
    ]


def test_unknown_application_still_allows_separately_named_os_observation():
    trace = build_retrieval_trace("NebulaDB compaction semantics are unknown; independently process CPU usage is high")
    assert "linux.cpu.process_pressure" in [row["knowledge_id"] for row in trace["matches"]]
    assert trace["evidence_contract"]["is_evidence"] is False
    assert trace["evidence_contract"]["status"] == "KNOWLEDGE_PRIOR_ONLY"


@pytest.mark.parametrize("mode", ["dense_rerank", "dense_only", "missing_index", "rerank_failure"])
def test_new_public_corpus_is_subject_gated_on_every_semantic_route(monkeypatch, mode):
    chunks = semantic.corpus(ROOT)
    client = Client(chunks)
    provider = Provider(failure=mode == "rerank_failure")
    if mode == "missing_index":
        class Missing:
            def get_collection(self, *args, **kwargs):
                raise RuntimeError("missing")
        client = Missing()
    monkeypatch.setenv("MINI_DROP_RERANK_ENABLED", "false" if mode == "dense_only" else "true")
    trace = semantic.search("Aurora protocol synchronous write stream flow control", ROOT,
                            provider=provider, client=client)
    assert trace["matches"] == []
    assert trace["outcome"] == "NO_RELEVANT_KNOWLEDGE"
    assert provider.rerank_docs == []


def test_public_documents_include_measurement_and_authority_limits():
    cpu = (ROOT / "java_thread_cpu.md").read_text(encoding="utf-8")
    io = (ROOT / "synchronous_io.md").read_text(encoding="utf-8")
    assert "-1" in cpu and "虚拟线程" in cpu and "测量未启用" in cpu
    assert "tmpfs" in io and "缺测" in io and "失败" in io
    assert "当前 Evidence" in cpu and "不是当前事故证据" in " ".join(entry("linux.io.synchronous_write")["caveats"])
    assert "https://docs.oracle.com/" in cpu and "https://man7.org/" in io
