"""Public knowledge relevance admission, independent of ranking or incident truth.

The small reviewed vocabulary describes technical observation domains, not
answers or case identifiers. It makes synonyms auditable and applies equally
to local, dense and reranked candidates. A match remains only a planning prior.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


POLICY_VERSION = "knowledge-subject-admission-v3-coverage"

# Runtime names and generic product words deliberately are not observation
# domains. A Java lock query must not qualify a MySQL/Go lock guide merely
# because they all mention waiting; generic Linux CPU guides remain portable.
_CONCEPTS = {
    "cpu": ("cpu", "processor", "core", "cores", "userland", "用户态", "核心", "核利用", "核满", "计算压力"),
    "kernel": ("kernel", "syscall", "system call", "cpu system", "sys时间", "系统态", "内核", "系统调用"),
    "sampling": ("profile", "profiling", "flamegraph", "flame graph", "stack trace", "stacktrace", "py spy", "gil", "speedscope", "采样", "剖面", "火焰图", "热点", "调用栈"),
    "memory": ("rss", "allocation", "allocations", "leak", "内存", "堆内存", "堆分配", "泄漏"),
    "memory_pressure": ("oom", "oom kill", "oom killer", "memory events", "memory pressure", "内存回收", "内存压力"),
    "block_io": ("i/o", "iowait", "fsync", "fdatasync", "block device", "disk", "storage synchronization", "syncwrite", "sync write", "synchronous write", "synchronous writes", "磁盘", "块设备", "同步写", "同步落", "慢写", "写入", "文件导出"),
    "network": ("tcp", "retransmit", "retransmission", "retransmissions", "packet loss", "rtt", "网络", "重传", "丢包", "链路"),
    "dependency": ("downstream", "dependency", "upstream", "http", "下游", "上游", "依赖", "接口", "调用链", "症状传播"),
    "lock_wait": ("lock", "locks", "locking", "mutex", "monitor", "wait event", "pg stat activity", "transaction", "transactions", "锁等待", "锁竞争", "持锁", "互斥", "长事务", "阻塞"),
    "gc": ("gc", "garbage collection", "garbage collector", "垃圾回收", "停顿", "分代"),
    "cpu_quota": ("cgroup", "cpu.max", "cpu.stat", "quota", "throttling", "throttled", "nr throttled", "配额", "节流", "受限资源组"),
    "host_contention": ("noisy neighbor", "same host", "same node", "co located", "colocated", "噪声邻居", "同宿主", "同节点", "同主机", "邻居", "旁边进程", "共享资源", "共享设备"),
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
    "cpu", "memory", "network", "tcp", "io", "go", "python", "java", "jvm", "cpp",
    "process", "thread", "threads", "worker", "workers", "进程", "线程",
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


def _active_clauses(query: str) -> tuple[list[str], list[str]]:
    """Keep real topics, excluding explicit substitutions and out-of-scope mentions.

    These are query semantics, not trusted instructions. Ordinary negative
    observations ("CPU is normal", "no retransmits") remain useful topics.
    """
    clauses, excluded = [], []
    for clause in re.split(r"[;；。!?！？\n]|(?<!\d)\.(?!\d|[a-zA-Z_])", str(query or "")):
        clause = clause.strip(" ,，")
        if not clause:
            continue
        parts = re.split(r"[,，]|\b(?:but|whereas)\b|(?:但是|但|而且|另外|同时)", clause, flags=re.I)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # "Do not substitute MySQL" and "不要将 GPU 当作 CPU" cannot
            # provide affirmative MySQL/CPU coverage. No technology is named
            # in this rule; arbitrary scopes obey the same comparison grammar.
            if re.search(r"\b(?:do\s+not|don't|not)\s+(?:substitute|use|treat|confuse|involve|about)\b|"
                         r"(?:不要|别|不能)(?:将|把|用|拿|当|混淆|替代)|(?:不涉及|不属于|不是讨论)", part, re.I):
                excluded.append(part)
                continue
            # Preserve the affirmative half of 'X, not Y' contrast.
            contrast = re.split(r"\bnot\s+(?=[a-zA-Z])|(?:而非|而不是)", part, maxsplit=1, flags=re.I)
            if len(contrast) == 2:
                part = contrast[0].strip()
                excluded.append(contrast[1].strip())
            if part:
                clauses.append(part)
    return clauses, excluded


def _primary_text(item: dict[str, Any]) -> str:
    """Capabilities come from the headline and reviewed anchors, not caveats.

    A summary can mention alternative diagnoses; those mentions must not turn
    a CPU guide into GC/network coverage. Body text still participates in rank.
    """
    values = [str(item.get("title") or "")]
    for field in ("keywords", "applies_to"):
        if isinstance(item.get(field), list):
            values.extend(str(value) for value in item[field])
    return " ".join(values)


def _technical_subjects(text: str) -> list[str]:
    """Recognize named subject syntax rather than a list of unsupported products.

    Acronyms, mixed-case product names, quoted names and Latin subjects in
    Chinese prose expose scope that a resource keyword must not silently erase.
    Lowercase names are recognized in explicit protocol/engine/runtime slots.
    Code-level identifiers are not automatically product scopes.
    """
    subjects = set()
    for match in re.finditer(r"(?<![a-zA-Z0-9_])[a-zA-Z][a-zA-Z0-9+]*(?![a-zA-Z0-9_])", text):
        value = match.group()
        if len(value) < 2:
            continue
        around = text[max(0, match.start() - 2):match.end() + 2]
        acronym = value.isupper() and len(value) >= 2
        mixed_case = bool(re.search(r"[a-z][A-Z]", value))
        cjk_name = bool(re.search(r"[\u3400-\u9fff]", around))
        quoted = match.start() > 0 and text[match.start() - 1] in "`\"'"
        named_slot = bool(re.match(r"\s+(?:protocol|database|engine|runtime|framework|cluster)\b|\s*(?:协议|引擎|集群|框架)", text[match.end():], re.I))
        if acronym or mixed_case or cjk_name or quoted or named_slot:
            subjects.add(_normalize(value))
    # Identifiers like request_id or a SQL column are observations rather than
    # named implementations. Unit/identity acronyms likewise do not add scope.
    return sorted(subjects - {"pid", "tid", "id", "api", "s3", "sha", "sha256", "http", "https",
                              "p95", "p99", "mib", "gib", "ms", "r1", "r2"})


def _subject_known(subject: str, entries: list[dict[str, Any]]) -> bool:
    # Aliases for resource measures and known runtimes are syntax context;
    # admission still checks the source's actual capability below.
    database = _labels(subject, _DATABASE_SCOPES)
    if database:
        return any(set(database) & set(_labels(_normalize(_primary_text(entry)), _DATABASE_SCOPES))
                   for entry in entries)
    aliases = [phrase for vocabulary in (_CONCEPTS, _RUNTIME_SCOPES)
               for phrases in vocabulary.values() for phrase in phrases]
    if subject in {_normalize(phrase) for phrase in aliases} | _GENERIC_ANCHORS | {
        "process", "application", "data", "heap", "cache", "query", "protocol",
    }:
        return True
    return any(_contains(_normalize(_primary_text(entry)), subject) for entry in entries)


def _portable_observation(text: str, shared: list[str]) -> bool:
    """An independently named OS observation can survive an unknown app brand.

    'ThreadLocal leak, process RSS rises' has portable memory guidance. A
    product's 'index wait' or a GPU question mentioning CPU does not establish
    such an observation. This does not claim the observation is verified.
    """
    if "cpu" in shared and re.search(r"(?:cpu|processor|用户态|系统态).{0,30}(?:high|hot|usage|utilization|%|高|升|占用|热点)|"
                                     r"(?:high|hot|高).{0,15}(?:cpu|processor)|用户态|核满|计算压力", text, re.I):
        return True
    if "memory" in shared and re.search(r"(?:rss|resident).{0,35}(?:grow|ris|increas|leak|增长|升|泄漏)|"
                                         r"(?:process|进程).{0,25}(?:memory|内存).{0,20}(?:leak|grow|增长|泄漏)|"
                                         r"(?:java|jvm|python|golang|c\+\+).{0,45}(?:leak|泄漏)", text, re.I):
        return True
    return ("block_io" in shared and any(_contains(_normalize(text), term) for term in ("fsync", "fdatasync", "iowait", "block device", "块设备")))


def _source_profile(item: dict[str, Any]) -> dict[str, Any]:
    text = _primary_text(item)
    profile = _profile(text, require_context=False)
    # These capabilities are more specific than the resource named in their
    # titles. CPU alone does not request quota, nor memory alone OOM controls.
    required = set(profile["concepts"]) & {"cpu_quota", "memory_pressure"}
    if required:
        profile["concepts"] = sorted(required)
    if "kernel" in profile["concepts"]:
        profile["concepts"] = ["kernel"]
    return profile


def _profile(text: str, *, require_context: bool) -> dict[str, Any]:
    normalized = _normalize(text)
    concepts = _labels(normalized, _CONCEPTS)
    # Generic 'network' includes social networks, and CPU can describe a game
    # character. Actual transport/measurement vocabulary disambiguates them.
    if require_context:
        if "network" in concepts and not any(_contains(normalized, term) for term in (
            "retransmit", "retransmission", "retransmissions", "packet", "rtt", "transport", "protocol", "counters",
            "connection", "socket", "latency", "bandwidth", "重传", "丢包", "连接", "时延", "带宽", "链路",
        )):
            concepts.remove("network")
        if "cpu" in concepts and not any(_contains(normalized, term) for term in (
            "high", "usage", "utilization", "userland", "processor", "process", "profile", "profiling",
            "hotspot", "sampling", "cores", "kernel", "throttled", "quota", "cpu.max", "cpu.stat",
            "用户态", "系统态", "高", "热点", "进程", "利用率", "占用", "采样", "核满", "计算压力",
        )) and not re.search(r"cpu.{0,20}(?:\d|%)", normalized):
            # Low/normal observations are still technical CPU topics. A
            # runtime plus worker/thread and a measured state supplies context;
            # a CPU-controlled game character or unknown engine name does not.
            runtime_worker = bool(_labels(normalized, _RUNTIME_SCOPES)) and any(
                _contains(normalized, term) for term in ("thread", "threads", "worker", "workers", "线程")
            )
            bounded_state = bool(re.search(
                r"(?:cpu|processor).{0,25}(?:low|normal|stable|idle|低|正常|稳定|空闲)|"
                r"(?:low|normal|stable|idle).{0,15}(?:cpu|processor)", normalized
            ))
            if not (runtime_worker and bounded_state):
                concepts.remove("cpu")
    if "recovery" in concepts and require_context and not any(
        _contains(normalized, term) for term in (
            "lease", "idempotency", "backup", "checkpoint", "restart", "crash", "recovery",
            "租约", "幂等", "备份", "中途重启", "恢复", "容量验收",
        )
    ):
        # Worker is an actor name; only lifecycle/control observations request
        # a recovery guide. The word alone also occurs in everyday prose.
        concepts.remove("recovery")
    if any(_contains(normalized, term) for term in ("memory", "heap")) and (
        not require_context or any(_contains(normalized, term) for term in (
            "process", "rss", "allocation", "leak", "pressure", "growth", "usage", "gc",
            "system", "service", "server", "container", "application", "free", "heap",
            "limit", "limits", "database", "pod", "cpu", "swap", "cache", "mib", "gib",
        ))
    ):
        concepts = sorted(set(concepts) | {"memory"})
    return {"concepts": concepts,
            "runtime_scopes": _labels(normalized, _RUNTIME_SCOPES),
            "database_scopes": _labels(normalized, _DATABASE_SCOPES)}


def query_profile(query: str) -> dict[str, Any]:
    clauses, excluded = _active_clauses(query)
    profile = _profile(" ".join(clauses), require_context=True)
    return {**profile, "technical_subjects": _technical_subjects(" ".join(clauses)),
            "active_clauses": clauses, "excluded_mentions": excluded}


def curated_anchor_text(item: dict[str, Any]) -> str:
    values = [str(item.get("title") or ""), str(item.get("summary") or "")]
    for field in ("keywords", "applies_to"):
        if isinstance(item.get(field), list):
            values.extend(str(value) for value in item[field])
    return " ".join(values)


def concept_score(query: str, item: dict[str, Any]) -> float:
    """Local alias recall without interpreting similarity as a probability."""
    shared = set(query_profile(query)["concepts"]) & set(_source_profile(item)["concepts"])
    return 0.4 * len(shared)


def assess_relevance(query: str, item: dict[str, Any], *,
                     catalog_entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Require supported technical subjects and primary capabilities together.

    Matching a resource noun, or a named implementation with no supported
    capability, is insufficient. Independent clauses allow an unsupported
    application question alongside a genuinely supported OS observation.
    """
    entries = catalog_entries if catalog_entries is not None else [item]
    source = _source_profile(item)
    profile = query_profile(query)
    query_runtime, source_runtime = profile["runtime_scopes"], source["runtime_scopes"]
    query_database, source_database = profile["database_scopes"], source["database_scopes"]
    query_unsupported = [subject for subject in profile["technical_subjects"]
                         if not _subject_known(subject, [item])]
    decisions = []
    for clause in profile["active_clauses"]:
        text = _normalize(clause)
        local = _profile(clause, require_context=True)
        shared = sorted(set(local["concepts"]) & set(source["concepts"]))
        anchors = []
        for field in ("keywords", "applies_to"):
            values = item.get(field) if isinstance(item.get(field), list) else []
            for value in values:
                value = str(value).strip()
                normalized = _normalize(value)
                if len(normalized) >= 3 and normalized not in _GENERIC_ANCHORS and _contains(text, value):
                    anchors.append(value)
        subjects = _technical_subjects(clause)
        local_unsupported = [subject for subject in subjects if not _subject_known(subject, [item])]
        # Commas do not reset the product being discussed. An omitted subject
        # in 'its index task' inherits the declared technology; a separately
        # named supported topic or portable OS observation can still qualify.
        named_source_topic = any(subject not in _GENERIC_ANCHORS and
                                 _contains(_normalize(_primary_text(item)), subject)
                                 for subject in subjects if subject not in local_unsupported)
        unsupported = local_unsupported if named_source_topic else query_unsupported
        accepted, reason = bool(shared or anchors), "PRIMARY_CAPABILITY_MATCH"
        if not accepted:
            reason = "NO_DISTINCTIVE_DOMAIN_ANCHOR"
        elif query_runtime and source_runtime and not set(query_runtime) & set(source_runtime):
            accepted, reason = False, "RUNTIME_SCOPE_CONFLICT"
        elif source_runtime and not query_runtime:
            accepted, reason = False, "RUNTIME_SCOPE_NOT_REQUESTED"
        elif query_database and source_database and not set(query_database) & set(source_database):
            accepted, reason = False, "DATABASE_SCOPE_CONFLICT"
        elif source_database and not query_database and not any(
            _contains(_normalize(" ".join(profile["active_clauses"])), term)
            for term in ("database", "sql", "数据库", "事务")
        ):
            accepted, reason = False, "DATABASE_SCOPE_NOT_REQUESTED"
        elif unsupported and not _portable_observation(clause, shared):
            accepted, reason = False, "TECHNICAL_SUBJECT_NOT_COVERED"
        # A runtime/database brand is scope, not a capability. New reviewed
        # identifiers such as 'evictions' can qualify without vocabulary edits.
        elif not shared and anchors and all(
            _labels(_normalize(anchor), _RUNTIME_SCOPES) or _labels(_normalize(anchor), _DATABASE_SCOPES)
            for anchor in anchors
        ):
            accepted, reason = False, "SUBJECT_WITHOUT_CAPABILITY"
        decisions.append({"accepted": accepted, "reason": reason, "shared_concepts": shared,
                          "matched_anchors": sorted(set(anchors)), "unsupported_subjects": unsupported,
                          "clause": clause})
    matching = [decision for decision in decisions if decision["accepted"]]
    chosen = matching[0] if matching else next((decision for decision in decisions
        if decision["reason"] != "NO_DISTINCTIVE_DOMAIN_ANCHOR"),
        {"accepted": False, "reason": "NO_DISTINCTIVE_DOMAIN_ANCHOR", "shared_concepts": [],
         "matched_anchors": [], "unsupported_subjects": [], "clause": ""})
    return {**chosen, "source_profile": source, "clause_decisions": decisions,
            "unregistered_subjects": [subject for subject in profile["technical_subjects"]
                                      if not _subject_known(subject, entries)]}


def relevance_audit(query: str, decisions: list[dict[str, Any]], *, degraded: bool = False) -> dict[str, Any]:
    accepted = [item for item in decisions if item["accepted"]]
    rejected = [item for item in decisions if not item["accepted"]]
    return {"relevance_policy": POLICY_VERSION, "relevance_query": str(query or "").strip(),
            "query_profile": query_profile(query), "accepted": accepted, "rejected": rejected,
            "outcome": "MATCHED" if accepted else "NO_RELEVANT_KNOWLEDGE",
            "health": "DEGRADED" if degraded else "HEALTHY",
            "health_scope": "RETRIEVAL_ONLY", "no_match_is_normal": False}
