"""Loopback-only acceptance adapter for an existing AGI-saber Python checkout.

Uses its real Engine, HybridStore and LocalRagChunkRepo. The caller owns a new
test database. No user documents, provider keys or production configuration are
loaded. HTTP output omits question text and retrieved content from observations.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextvars import ContextVar
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

QUESTIONS = [
    ("annual leave", "LEAVE-20"), ("expense reimbursement", "EXPENSE-30"),
    ("incident response", "INCIDENT-15"), ("password reset", "PASSWORD-24"),
    ("预算审批", "BUDGET-320"), ("项目启动", "PROJECT-2026"),
]
DOCUMENTS = [
    "annual leave policy allows LEAVE-20 paid days each year",
    "expense reimbursement must use EXPENSE-30 submission days",
    "incident response acknowledges incidents within INCIDENT-15 minutes",
    "password reset tickets require PASSWORD-24 hour verification",
    "预算审批制度：一期预算为 BUDGET-320 万元。",
    "项目启动计划：启动年份为 PROJECT-2026 年。",
]
STAGES: ContextVar[dict | None] = ContextVar("mini_drop_business_stages", default=None)
TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})\Z")


def trace_identity(value):
    match = TRACEPARENT.fullmatch(value or "")
    if match and int(match[1], 16) and int(match[2], 16):
        return match[1], match[2], match[3]
    return uuid4().hex, None, "01"


def corpus(rows=2000, dimensions=1536):
    embedding = [(i % 17) / 17 for i in range(dimensions)]
    for i in range(rows):
        text = DOCUMENTS[i] if i < len(DOCUMENTS) else f"archive memorandum record {i} storage inventory reference"
        yield dict(doc_hash=f"fixture-{i}", chunk_idx=0, content=text,
                   parent_content=text, embedding=embedding, document_id=f"document-{i}")


def source_fingerprint(root):
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for folder in ("config", "internal") for p in (root/folder).rglob("*.py")}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


class ActualRagService:
    def __init__(self, rag_root, database, rows=2000, dimensions=1536):
        if database.exists():
            raise ValueError("acceptance database must be new")
        if not (rag_root / "internal/rag/rag.py").is_file():
            raise ValueError("expected an AGI-saber final source directory")
        sys.path.insert(0, str(rag_root))
        from config.config import APIConfig
        from internal.application.store import ApplicationStore
        from internal.application.models import AgentRagChunkRecord
        from internal.application.local_repos import LocalRagChunkRepo
        from internal.rag.rag import Engine

        database.parent.mkdir(parents=True, exist_ok=True)
        self.store = ApplicationStore("sqlite+pysqlite:///" + database.resolve().as_posix())
        user = self.store.create_user("mini-drop-isolated-benchmark", "login-disabled")
        with self.store.transaction() as session:
            session.add_all(AgentRagChunkRecord(user_id=user["id"], **item) for item in corpus(rows, dimensions))
        repo = LocalRagChunkRepo(self.store)
        inf = SimpleNamespace(repo=SimpleNamespace(ragchunk=repo), ready=SimpleNamespace(
            postgresql="disconnected", milvus="disconnected", elasticsearch="disconnected"))
        cfg = APIConfig()
        cfg.top_k = 3
        # Local retrieval never invokes this dependency. Fail loudly if mode
        # unexpectedly changes; do not substitute fake vectors or model calls.
        def no_embedding(*args, **kwargs):
            raise RuntimeError("remote embedding disabled in this acceptance adapter")
        self.engine = Engine(cfg, inf, llm=SimpleNamespace(embed=no_embedding), user_id=user["id"])
        self._observe(repo, "search_local", "local_search")
        self._observe(self.engine, "_compose_answer", "answer_composition")
        self.rows = rows
        self.source_sha256 = source_fingerprint(rag_root)
        self.instance_id = uuid4().hex
        self.records = deque(maxlen=512)
        self.lock = threading.Lock()
        self.total = 0

    @staticmethod
    def _observe(obj, method, stage):
        original = getattr(obj, method)
        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                stages = STAGES.get()
                if stages is not None:
                    stages[stage] = stages.get(stage, 0) + (time.perf_counter()-start)*1000
        # Keep the callable's signature, including user_id. HybridStore checks
        # it to preserve tenant identity when delegating to repository adapters.
        import functools
        setattr(obj, method, functools.wraps(original)(measured))

    def query(self, question, parent=""):
        trace_id, parent_id, flags = trace_identity(parent)
        span_id = uuid4().hex[:16]
        start_wall = time.time_ns()
        start = time.perf_counter()
        stages = {}
        token = STAGES.set(stages)
        response = {}
        succeeded = False
        try:
            answer, hits, trace = self.engine.query_with_history_trace(question)
            succeeded = True
            response = {"answer": answer, "citations": [hit["pg_id"] for hit in hits],
                        "retrieval_mode": [x.get("mode") for x in trace.get("retrieval", {}).get("query_paths", [])],
                        "generator": "EXTRACTIVE_LOCAL"}
        finally:
            STAGES.reset(token)
            record = {"trace_id": trace_id, "span_id": span_id, "parent_span_id": parent_id,
                      "started_at_unix_ns": start_wall, "duration_ms": (time.perf_counter()-start)*1000,
                      "stage_ms": stages, "success": succeeded, "pid": os.getpid(),
                      "service": "agi-saber-rag", "instance_id": self.instance_id,
                      "source_sha256": self.source_sha256}
            with self.lock:
                self.records.append(record)
                self.total += 1
        return {**response, "observation": record, "traceparent": f"00-{trace_id}-{span_id}-{flags}"}

    def observations(self):
        with self.lock:
            return {"schema": "mini-drop.business-observations.v1", "service": "agi-saber-rag",
                    "instance_id": self.instance_id, "pid": os.getpid(), "source_sha256": self.source_sha256,
                    "total_requests": self.total, "retained_requests": len(self.records),
                    "retention_limit": 512, "records": list(self.records),
                    "scope": "REQUEST_TIMING_ONLY; NOT_ROOT_CAUSE_PROOF"}


def serve(service, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, data):
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.send(200, {"service": "agi-saber-rag", "pid": os.getpid(), "rows": service.rows,
                                "instance_id": service.instance_id, "source_sha256": service.source_sha256})
            elif self.path == "/observations":
                self.send(200, service.observations())
            else:
                self.send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/query":
                return self.send(404, {"error": "not_found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    return self.send(413, {"error": "body_limit"})
                value = json.loads(self.rfile.read(length))
                if (not isinstance(value, dict) or set(value) != {"question"}
                        or not isinstance(value["question"], str) or not 0 < len(value["question"]) <= 500):
                    return self.send(400, {"error": "invalid_question"})
                result = service.query(value["question"], self.headers.get("traceparent", ""))
                return self.send(200, result)
            except (ValueError, UnicodeError):
                return self.send(400, {"error": "invalid_request"})
            except Exception:
                # Detailed application failures stay local; no traceback,
                # document text or provider credential is sent to the client.
                return self.send(500, {"error": "query_failed"})
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rag-root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--rows", type=int, default=2000)
    args = parser.parse_args()
    service = ActualRagService(args.rag_root.resolve(), args.database, args.rows)
    http = serve(service, args.port)
    try:
        http.serve_forever()
    finally:
        http.server_close()
        service.store.close()
