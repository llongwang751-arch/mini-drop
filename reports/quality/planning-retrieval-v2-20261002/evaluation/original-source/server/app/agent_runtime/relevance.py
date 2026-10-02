"""Public knowledge relevance admission, independent of ranking or incident truth.

The small reviewed vocabulary describes technical observation domains, not
answers or case identifiers. It makes synonyms auditable and applies equally
to local, dense and reranked candidates. A match remains only a planning prior.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


POLICY_VERSION = "knowledge-domain-admission-v1"

# Runtime names and generic product words deliberately are not observation
# domains. A Java lock query must not qualify a MySQL/Go lock guide merely
# because they all mention waiting; generic Linux CPU guides remain portable.
_CONCEPTS = {
    "cpu": ("cpu", "processor", "core", "cores", "userland", "用户态", "核心", "核利用", "核满", "计算压力"),
    "kernel": ("kernel", "syscall", "system call", "cpu system", "sys时间", "系统态", "内核", "系统调用"),
    "sampling": ("profile", "profiling", "flamegraph", "flame graph", "stack trace", "stacktrace", "py spy", "gil", "speedscope", "采样", "剖面", "火焰图", "热点", "调用栈"),
    "memory": ("rss", "allocation", "allocations", "leak", "内存", "堆内存", "堆分配", "泄漏"),
    "memory_pressure": ("oom", "oom kill", "oom killer", "memory events", "memory pressure", "内存回收", "内存压力"),
    "block_io": ("i/o", "iowait", "fsync", "fdatasync", "block device", "disk", "storage synchronization", "磁盘", "块设备", "同步写", "同步落", "慢写", "写入", "文件导出"),
    "network": ("tcp", "retransmit", "retransmission", "retransmissions", "packet loss", "rtt", "网络", "重传", "丢包", "链路"),
    "dependency": ("downstream", "dependency", "upstream", "http", "下游", "上游", "依赖", "接口", "调用链", "症状传播"),
    "lock_wait": ("lock", "locks", "locking", "mutex", "monitor", "wait event", "pg stat activity", "transaction", "transactions", "锁等待", "锁竞争", "持锁", "互斥", "长事务", "阻塞"),
    "gc": ("gc", "garbage collection", "garbage collector", "垃圾回收", "停顿", "分代"),
    "cpu_quota": ("cgroup", "cpu.max", "cpu.stat", "quota", "throttling", "throttled", "nr throttled", "配额", "节流", "受限资源组"),
    "host_contention": ("noisy neighbor", "same host", "co located", "噪声邻居", "同宿主", "邻居", "旁边进程", "共享资源", "共享设备"),
    "latency_measurement": ("p95", "p99", "percentile", "histogram", "summary", "分位数", "同负载", "复测", "延迟分布"),
    "tool_governance": ("tool budget", "tool call", "tool timeout", "probe budget", "duplicate call", "工具", "探针预算", "重复调用", "采集预算", "采样预算", "证据门禁"),
    "strategy_evaluation": ("react", "lats", "ground truth", "评测", "真值", "对照实验", "独立实验"),
    "retrieval_quality": ("rag", "recall", "mrr", "rerank", "embedding", "检索", "向量", "重排", "索引", "知识原文"),
    "recovery": ("lease", "idempotency", "backup", "worker", "checkpoint", "租约", "幂等", "备份", "中途重启", "容量验收"),
}

_RUNTIME_SCOPES = {
    "PYTHON": ("python", "py spy", "gil", "cpython"),
    "GO": ("go", "golang", "goroutine", "pprof"),
    "JVM": ("java", "jvm", "jfr", "jstack"),
    "CPP": ("c++", "cpp", "cxx"),
}
_DATABASE_SCOPES = {
    "MYSQL": ("mysql", "innodb"),
    "POSTGRES": ("postgres", "postgresql", "pg stat activity", "pg locks"),
    "REDIS": ("redis",),
    "MONGODB": ("mongodb",),
    "SQLITE": ("sqlite", "sqlite3"),
}
_GENERIC_ANCHORS = {
    "agent", "container", "runtime", "linux", "system", "service", "server",
    "database", "performance", "problem", "wait", "waiting", "block", "pressure",
    "cpu", "memory", "network", "io", "go", "python", "java", "jvm", "cpp",
    "工具", "容器", "恢复", "容量", "实验", "对照", "增长", "共享", "暂停",
    "内存", "磁盘", "网络", "阻塞", "热点", "采样", "数据库", "记忆", "预算", "超时",
}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or "")).casefold().replace("_", " ").replace("-", " ")).strip()


def _contains(text: str, phrase: str) -> bool:
    phrase = _normalize(phrase)
    pattern = re.escape(phrase)
    if re.search(r"[a-z0-9]", phrase):
        pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _labels(text: str, vocabulary: dict[str, tuple[str, ...]]) -> list[str]:
    return sorted(label for label, phrases in vocabulary.items() if any(_contains(text, phrase) for phrase in phrases))


def query_profile(query: str) -> dict[str, Any]:
    normalized = _normalize(query)
    concepts = _labels(normalized, _CONCEPTS)
    # 'Memory' also means recollection; a resource observation needs a
    # technical qualifier. This is domain disambiguation, not an off-topic
    # topic blacklist. Bare runtime/product nouns remain insufficient.
    if any(_contains(normalized, term) for term in ("memory", "heap")) and any(
        _contains(normalized, term) for term in (
            "process", "rss", "allocation", "leak", "pressure", "growth", "usage",
            "system", "service", "server", "container", "application", "free",
            "limit", "limits", "database", "pod", "cpu", "swap", "cache", "mib", "gib",
        )
    ):
        concepts = sorted(set(concepts) | {"memory"})
    return {"concepts": concepts,
            "runtime_scopes": _labels(normalized, _RUNTIME_SCOPES),
            "database_scopes": _labels(normalized, _DATABASE_SCOPES)}


def curated_anchor_text(item: dict[str, Any]) -> str:
    values = [str(item.get("title") or ""), str(item.get("summary") or "")]
    for field in ("keywords", "applies_to"):
        if isinstance(item.get(field), list):
            values.extend(str(value) for value in item[field])
    return " ".join(values)


def concept_score(query: str, item: dict[str, Any]) -> float:
    """Local alias recall without interpreting similarity as a probability."""
    shared = set(query_profile(query)["concepts"]) & set(query_profile(curated_anchor_text(item))["concepts"])
    return 0.4 * len(shared)


def assess_relevance(query: str, item: dict[str, Any]) -> dict[str, Any]:
    """Admit a catalog entry from the genuine query, never its inferred category.

    A distinctive curated technical identifier can admit new domains without
    adding them to this vocabulary. Generic aliases still need an observation
    concept. Instructions in Markdown or evidence/caveat text cannot admit it.
    """
    query_text = _normalize(query)
    source = query_profile(curated_anchor_text(item))
    profile = query_profile(query)
    shared = sorted(set(profile["concepts"]) & set(source["concepts"]))
    anchors = []
    for field in ("keywords", "applies_to"):
        values = item.get(field) if isinstance(item.get(field), list) else []
        for value in values:
            value = str(value).strip()
            normalized = _normalize(value)
            if len(normalized) < 3 or normalized in _GENERIC_ANCHORS:
                continue
            if _contains(query_text, value):
                anchors.append(value)
    reason = "DOMAIN_ANCHOR_MATCH"
    accepted = bool(shared or anchors)
    # Multi-domain queries may intentionally discuss more than one runtime;
    # a specialized source must match at least one explicit runtime scope.
    query_runtime, source_runtime = profile["runtime_scopes"], source["runtime_scopes"]
    query_database, source_database = profile["database_scopes"], source["database_scopes"]
    if not accepted:
        reason = "NO_DISTINCTIVE_DOMAIN_ANCHOR"
    elif query_runtime and source_runtime and not set(query_runtime) & set(source_runtime):
        accepted, reason = False, "RUNTIME_SCOPE_CONFLICT"
    elif query_runtime and source_database and not query_database and not any(
        _contains(query_text, term) for term in ("database", "sql", "数据库", "事务")
    ):
        accepted, reason = False, "DATABASE_SCOPE_NOT_REQUESTED"
    elif query_database and source_database and not set(query_database) & set(source_database):
        accepted, reason = False, "DATABASE_SCOPE_CONFLICT"
    return {"accepted": accepted, "reason": reason, "shared_concepts": shared,
            "matched_anchors": sorted(set(anchors)), "source_profile": source}


def relevance_audit(query: str, decisions: list[dict[str, Any]], *, degraded: bool = False) -> dict[str, Any]:
    accepted = [item for item in decisions if item["accepted"]]
    rejected = [item for item in decisions if not item["accepted"]]
    return {"relevance_policy": POLICY_VERSION, "relevance_query": str(query or "").strip(),
            "query_profile": query_profile(query), "accepted": accepted, "rejected": rejected,
            "outcome": "MATCHED" if accepted else "NO_RELEVANT_KNOWLEDGE",
            "health": "DEGRADED" if degraded else "HEALTHY",
            "health_scope": "RETRIEVAL_ONLY", "no_match_is_normal": False}
