"""真实 Saber SQLite FTS5 组件的固定适配器，不代表整个应用。

按精确路径加载已登记源码，跳过会加载外部引擎的 rag 包初始化；
文档、用户和查询字符串全部是本地合成测试数据。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import random
import sqlite3
import sys
import types


RELATIVE_SOURCE = "internal/rag/fts5_index.py"


def load_component(source, *, original_namespace=False):
    source = Path(source).resolve()
    name = "drop_saber_fts5_component"
    spec = importlib.util.spec_from_file_location(name, source / RELATIVE_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("SABER_COMPONENT_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if original_namespace:
        # 仅安装这些包占位符，不读取应用入口、环境文件、持久数据库或引擎初始化器。
        for package in ("internal", "internal.rag"):
            stub = types.ModuleType(package)
            stub.__path__ = [str(source.joinpath(*package.split(".")))]
            sys.modules[package] = stub
        sys.modules["internal.rag.fts5_index"] = module
    return module


class ObservedConnection(sqlite3.Connection):
    """观察实际 SELECT 的绑定参数，不重新拼装查询，因此保留真实 SQL 行为。"""
    query_context = None

    def execute(self, sql, parameters=()):
        if "bm25(" in sql and " MATCH ?" in sql:
            self.query_context = {"match": parameters[0], "user_id": parameters[1], "limit": parameters[2]}
        return super().execute(sql, parameters)


def synthetic_database(module):
    conn = sqlite3.connect(":memory:", factory=ObservedConnection)
    manager = module.FTS5IndexManager
    if manager.ensure_table(conn) is not True:
        raise RuntimeError("SABER_SQLITE_FTS5_REQUIRED")
    for tenant, offset in (("user_a", 0), ("user_b", 1000)):
        for index in range(32):
            content = ("知识库 性能分析 检索 RAG performance sqlite query "
                       + f"term{index:03d} term{(index + 32):03d} "
                       + ("可观测性 火焰图 " if index % 2 else "身份隔离 内存分配 "))
            if manager.index_chunk(conn, pg_id=offset + index + 1, user_id=tenant,
                                   doc_hash=f"synthetic-{index // 4}", content=content,
                                   parent_content=f"Synthetic section {index % 4}") is not True:
                raise RuntimeError("SABER_SYNTHETIC_INDEX_FAILED")
    return conn


def query_cases(seed):
    rng = random.Random(seed)
    queries = ["", " \t\n", "知识库", "RAG performance", '"sqlite" OR NOT NEAR *',
               "Straße CAFÉ Σίσυφος １２３ 数据", "性能 性能 性能", "知识库_fts5 quote'\"()"]
    # 长自然语言或关键词请求可能包含大量不同词项；源码已有最多 64 个唯一 token 的契约，
    # harness 只触发这一既有行为，不植入缺陷或额外 sleep。
    for _ in range(56):
        head = ["rag", "performance", "sqlite", "知识库"]
        head.extend(f"term{value:03d}" for value in rng.sample(range(256), 80))
        tail = " ".join(f"词元{rng.randrange(10000)} suffix{rng.randrange(100000)}" for _ in range(300))
        target = rng.randrange(1024, 8193)
        query = " ".join(head) + " " + tail
        queries.append(query[:target])
    return [{"query": query, "user_id": ("user_a", "user_b", "absent_user")[index % 3],
             "top_k": (1, 3, 5, 100, 0, 999)[index % 6]} for index, query in enumerate(queries)]


def execute_case(module, conn, case):
    conn.query_context = None
    hits = module.FTS5IndexManager.search(conn, query=case["query"], top_k=case["top_k"], user_id=case["user_id"])
    return {"query": case["query"], "requested_user_id": case["user_id"], "requested_top_k": case["top_k"],
            "executed_query": conn.query_context, "hits": hits}


def typed(value):
    """保留 Python 容器和标量的精确类型，包括浮点评分。"""
    if type(value) is dict:
        return ["dict", [[typed(key), typed(item)] for key, item in sorted(value.items())]]
    if type(value) in (list, tuple):
        return [type(value).__name__, [typed(item) for item in value]]
    if type(value) in (str, int, float, bool, type(None)):
        return [type(value).__name__, value]
    raise RuntimeError("SABER_OUTPUT_TYPE_UNSUPPORTED")
