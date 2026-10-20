"""固定 FTS5 行为、SQL/租户隔离和源码修改边界的契约测试。

原始 tests/test_fts5_index.py 同样原样执行。新增判定保留 Unicode casefold
及 64-token 排序等已有行为，不通过削弱测试让优化补丁过关。
"""
import ast
import os
from pathlib import Path
import re
import sqlite3

from saber_component import RELATIVE_SOURCE, load_component, typed


SOURCE = Path(os.environ.get("DROP_SABER_SOURCE", ".")).resolve()
BASELINE = Path(os.environ.get("DROP_SABER_BASELINE", "/workspace/baseline")).resolve()
MODULE = load_component(SOURCE)


def _reference_tokenize(text):
    if not text:
        return ""
    tokens = []
    ascii_buf = ""
    for char in text.casefold():
        if "\u4e00" <= char <= "\u9fff":
            if ascii_buf:
                tokens.append(ascii_buf)
                ascii_buf = ""
            tokens.append(char)
        elif char.isalnum():
            ascii_buf += char
        elif ascii_buf:
            tokens.append(ascii_buf)
            ascii_buf = ""
    if ascii_buf:
        tokens.append(ascii_buf)
    return " ".join(tokens)


def _reference_query(query):
    tokens = []
    for part in re.findall(r"[\u4e00-\u9fff]+|[^\W_]+", query.casefold()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part) and len(part) > 1:
            tokens.extend(part[index] + " " + part[index + 1] for index in range(len(part) - 1))
        else:
            tokens.append(part)
    tokens = list(dict.fromkeys(tokens))[:64]
    safe = [f'"{token}"' for token in tokens if token not in {"AND", "OR", "NOT", "NEAR", "*"}]
    return " OR ".join(safe)


def test_query_unicode_quotes_duplicates_and_empty_contracts():
    cases = ["", " \t\r\n", "___()'\"*", "知识库", "知", "RAG知识库Tencent",
             "alpha alpha ALPHA Beta beta", "A_B_C", "Straße İ CAFÉ Ω Σίσυφος １２３",
             "emoji🙂RAG𠀀混合", '"hello" OR NOT NEAR * AND', "a' OR 1=1 --",
             "数据库数据库 数据库", "一二三四五六七八九十", "a\x00b\n引号\"检索"]
    for text in cases:
        query = MODULE.build_fts_query(text)
        tokenized = MODULE.fts_tokenize(text)
        assert type(query) is str
        assert type(tokenized) is str
        assert query == _reference_query(text)
        assert tokenized == _reference_tokenize(text)


def test_first_64_unique_tokens_preserve_exact_order_and_phrases():
    texts = [" ".join(f"term{index}" for index in range(120)),
             "repeat " * 100 + " ".join(f"term{index}" for index in range(100)),
             " ".join(f"term{index}" for index in range(62)) + " 知识库全文检索 alpha beta",
             "知识库 " * 100 + " ".join(f"item{index}" for index in range(90)),
             " ".join(chr(0x4e00 + index) for index in range(90)),
             "".join(chr(0x4e00 + index) for index in range(90))]
    for text in texts:
        result = MODULE.build_fts_query(text)
        assert result == _reference_query(text)
        assert len(result.split(" OR ")) <= 64


def test_queries_have_no_shared_mutable_state():
    before = MODULE.build_fts_query("知识库 alpha ALPHA")
    for text in ("beta", "", "不同用户查询", "alpha beta gamma"):
        MODULE.build_fts_query(text)
    assert MODULE.build_fts_query("知识库 alpha ALPHA") == before


def test_real_sqlite_tenant_scope_replacement_and_deletion():
    manager = MODULE.FTS5IndexManager
    with sqlite3.connect(":memory:") as conn:
        assert manager.ensure_table(conn) is True
        for pg_id, user in ((1, "tenant_a"), (2, "tenant_b"), (3, "tenant_a")):
            assert manager.index_chunk(conn, pg_id, user, "same_hash", "知识库 sqlite performance", "共享父章节") is True
        for user, expected_ids in (("tenant_a", {1, 3}), ("tenant_b", {2}), ("unknown", set())):
            hits = manager.search(conn, "知识库 sqlite", top_k=999, user_id=user)
            assert type(hits) is list
            assert {row["pg_id"] for row in hits} == expected_ids
            for row in hits:
                assert type(row) is dict and type(row["pg_id"]) is int and type(row["score"]) is float
                assert row["score"] > 0 and row["source"] == "local_keyword" and row["engine"] == "fts5"
        assert manager.index_chunk(conn, 1, "tenant_a", "replacement", "完全不同内容") is True
        assert [row["pg_id"] for row in manager.search(conn, "sqlite", user_id="tenant_a")] == [3]
        manager.delete_by_doc_hash(conn, "same_hash", "tenant_a")
        assert manager.search(conn, "sqlite", user_id="tenant_a") == []
        assert [row["pg_id"] for row in manager.search(conn, "sqlite", user_id="tenant_b")] == [2]
        manager.delete_by_pg_ids(conn, [2])
        assert manager.search(conn, "sqlite", user_id="tenant_b") == []
        assert manager.search(conn, '" OR NOT NEAR *', user_id="tenant_a") == []


def test_sql_query_results_and_scores_match_original_component():
    baseline = load_component(BASELINE)
    conn_original, conn_candidate = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    try:
        for module, conn in ((baseline, conn_original), (MODULE, conn_candidate)):
            for index in range(1, 15):
                assert module.FTS5IndexManager.index_chunk(conn, index, "tenant_a" if index % 2 else "tenant_b",
                                                          "synthetic", "知识库 " * (index % 4 + 1) + f"sqlite term{index}", "全文检索") is True
        for query in ("知识库", "sqlite", "term1", "全文检索", '"sqlite" OR term3', "不存在", ""):
            for user in ("tenant_a", "tenant_b", "absent_user"):
                for top_k in (0, 1, 5, 200):
                    expected = baseline.FTS5IndexManager.search(conn_original, query, top_k, user)
                    actual = MODULE.FTS5IndexManager.search(conn_candidate, query, top_k, user)
                    assert typed(actual) == typed(expected)
    finally:
        conn_original.close()
        conn_candidate.close()


def test_only_registered_functions_can_change_and_sql_class_is_frozen():
    def frozen(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        body = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ("fts_tokenize", "build_fts_query"):
                continue
            if isinstance(node, ast.Import) and all(name.name == "functools" for name in node.names):
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "functools":
                continue
            body.append(node)
        tree.body = body
        return ast.dump(tree, include_attributes=False)
    assert frozen(SOURCE / RELATIVE_SOURCE) == frozen(BASELINE / RELATIVE_SOURCE)
