"""Reproduce retrieval correctness and profiling against frozen external sources.

Uses only a new local SQLite fixture. It does not import production settings,
access user documents, or treat profiler timing as a performance acceptance.
Run this separately for each source revision to avoid Python import caching.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.agi_saber.service import source_fingerprint


def verify(source: Path, output: Path):
    source, output = source.resolve(), output.resolve()
    if not (source / "internal/application/local_repos.py").is_file():
        raise ValueError("expected a frozen AGI-saber source directory")
    output.mkdir(parents=True, exist_ok=False)
    original_hash = source_fingerprint(source)
    report = {"schema": "mini-drop.actual-rag-search-regression.v1", "status": "RUNNING",
              "started_at": datetime.now(timezone.utc).isoformat(), "source_sha256": original_hash,
              "scope": "FROZEN_EXTERNAL_SOURCE; ISOLATED_SQLITE; SYNTHETIC_DATA; NO_LIVE_AI",
              "checks": []}

    def save():
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    sys.path.insert(0, str(source))
    store = None
    try:
        from internal.application.store import ApplicationStore
        from internal.application.models import AgentRagChunkRecord
        from internal.application.local_repos import LocalRagChunkRepo

        store = ApplicationStore("sqlite+pysqlite:///" + (output / "fixture.db").as_posix())
        owner = store.create_user("regression-owner", "login-disabled")["id"]
        other = store.create_user("regression-other", "login-disabled")["id"]
        repo = LocalRagChunkRepo(store)
        embedding = [(i % 17) / 17 for i in range(1536)]

        def insert(user, key, content):
            with store.transaction() as session:
                row = AgentRagChunkRecord(user_id=user, doc_hash=key, chunk_idx=0,
                    content=content, parent_content=content, embedding=embedding, document_id=key)
                session.add(row)
                session.flush()
                return row.id

        mixed = insert(owner, "mixed", "alpha beta")
        first = insert(owner, "first", "alpha")
        second = insert(owner, "second", "alpha")
        foreign = insert(other, "foreign", "alpha")
        with store.transaction() as session:
            session.add_all(AgentRagChunkRecord(user_id=owner, doc_hash=f"background-{i}", chunk_idx=0,
                content=f"archive record {i}", parent_content=f"archive record {i}",
                embedding=embedding, document_id=f"background-{i}") for i in range(600))

        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(6):
            hits = repo.search_local("alpha", 3, user_id=owner)
        profiler.disable()
        profiler.dump_stats(str(output / "search.prof"))
        stats = pstats.Stats(profiler)
        rows = [{"file": Path(file).name, "line": line, "function": function,
                 "primitive_calls": data[0], "calls": data[1], "self_seconds": data[2],
                 "cumulative_seconds": data[3]}
                for (file, line, function), data in stats.stats.items()]
        report["profile"] = {"queries": 6, "owner_rows": 603, "embedding_dimensions": 1536,
                             "total_seconds": stats.total_tt,
                             "top_cumulative": sorted(rows, key=lambda row: -row["cumulative_seconds"])[:20],
                             "json_decode": [row for row in rows if row["file"] in {"decoder.py", "__init__.py"}
                                             and row["function"] in {"loads", "decode", "raw_decode"}]}
        assert [h["pg_id"] for h in hits] == [first, second, mixed], "score / id tie-break regressed"
        assert abs(hits[0]["score"] - 1) < 1e-12
        assert abs(hits[2]["score"] - 2 ** -.5) < 1e-12
        report["checks"].append("independent score oracle and deterministic id tie-break")
        assert foreign not in [h["pg_id"] for h in hits]
        assert [h["pg_id"] for h in repo.search_local("alpha", 3, user_id=other)] == [foreign]
        repo.delete("foreign", user_id=owner)
        assert [h["pg_id"] for h in repo.search_local("alpha", 3, user_id=other)] == [foreign]
        report["checks"].append("cross-tenant search and delete isolation")
        assert repo.search_local("", 3, user_id=owner) == []
        assert repo.search_local("nevermatchedtoken", 3, user_id=owner) == []
        assert [h["pg_id"] for h in repo.search_local("alpha", 0, user_id=owner)] == [first]
        report["checks"].append("empty / unmatched query and top-k lower bound")
        fresh = insert(owner, "fresh", "newtoken")
        assert [h["pg_id"] for h in repo.search_local("newtoken", 3, user_id=owner)] == [fresh]
        with store.transaction() as session:
            record = session.get(AgentRagChunkRecord, fresh)
            record.content = record.parent_content = "changedtoken"
        assert repo.search_local("newtoken", 3, user_id=owner) == []
        assert [h["pg_id"] for h in repo.search_local("changedtoken", 3, user_id=owner)] == [fresh]
        repo.delete("fresh", user_id=owner)
        assert repo.search_local("changedtoken", 3, user_id=owner) == []
        report["checks"].append("insert / update / delete freshness without rebuilding service")
        assert original_hash == source_fingerprint(source), "source changed during verification"
        report["status"] = "PASSED"
    except Exception as exc:
        report["status"] = "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if store is not None:
            store.engine.dispose()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
        save()
    print(json.dumps({"status": report["status"], "checks": report["checks"], "output": str(output)}))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.source, args.output)
