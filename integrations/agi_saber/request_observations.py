"""Request-scoped, content-free RAG timings for the deployed AGI-saber app."""
from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from functools import wraps
import json
import os
from pathlib import Path
import threading
import time
from uuid import UUID, uuid4


_ACTIVE: ContextVar["_Window | None"] = ContextVar("mini_drop_rag_window", default=None)
_INSTALLED = False


class _Window:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.started = time.perf_counter()
        self.stages: dict[str, float] = {}
        self.lock = threading.Lock()

    def add(self, name: str, elapsed_ms: float) -> None:
        with self.lock:
            self.stages[name] = self.stages.get(name, 0.0) + elapsed_ms


class OfficeObservationStore:
    def __init__(self, path: str | Path, *, limit: int = 100) -> None:
        self.path = Path(path)
        self.records: deque[dict] = deque(maxlen=limit)
        self.lock = threading.Lock()

    def append(self, record: dict) -> None:
        with self.lock:
            self.records.append(record)
            payload = {
                "schema_version": "mini-drop.office-observations.v1",
                "service_id": "agi-office-backend",
                "pid": os.getpid(),
                "records": list(self.records),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # The host parent stays 0700; only this bind-mounted telemetry
            # directory is traversable by the unprivileged worker.
            self.path.parent.chmod(0o755)
            temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            # The business-data parent is 0700 on the host. This file is
            # bind-mounted read-only for the unprivileged worker (uid 1000).
            temporary.chmod(0o644)
            os.replace(temporary, self.path)


def _measure(cls: type, method_name: str, stage: str, *, enabled=None) -> None:
    original = getattr(cls, method_name)

    @wraps(original)
    def measured(self, *args, **kwargs):
        window = _ACTIVE.get()
        if window is None or (enabled is not None and not enabled(self)):
            return original(self, *args, **kwargs)
        start = time.perf_counter()
        try:
            return original(self, *args, **kwargs)
        finally:
            window.add(stage, (time.perf_counter() - start) * 1000)

    setattr(cls, method_name, measured)


def install(store: OfficeObservationStore) -> None:
    """Install before build_deps so bound RAG callbacks use measured methods."""
    global _INSTALLED
    if _INSTALLED:
        return
    from internal.agent.agent import UnifiedAgent
    from internal.rag.hybrid import HybridStore
    from internal.rag.rewriter import LLMRewriter

    _measure(LLMRewriter, "rewrite", "rewrite_ms")
    _measure(HybridStore, "search_multi", "search_total_ms")
    _measure(HybridStore, "_finalize", "rerank_ms", enabled=lambda self: self._reranker is not None)
    _measure(UnifiedAgent, "_llm_generate", "generation_ms")

    original_hybrid_init = HybridStore.__init__

    @wraps(original_hybrid_init)
    def measured_hybrid_init(self, *args, **kwargs):
        original_hybrid_init(self, *args, **kwargs)
        original_embed = self._embed_fn
        if original_embed is None:
            return

        @wraps(original_embed)
        def measured_embed(*embed_args, **embed_kwargs):
            window = _ACTIVE.get()
            if window is None:
                return original_embed(*embed_args, **embed_kwargs)
            start = time.perf_counter()
            try:
                return original_embed(*embed_args, **embed_kwargs)
            finally:
                window.add("embedding_ms", (time.perf_counter() - start) * 1000)

        self._embed_fn = measured_embed

    HybridStore.__init__ = measured_hybrid_init

    for method_name in ("process_with_options", "process_stream"):
        original = getattr(UnifiedAgent, method_name)

        def wrap_process(original_method):
            @wraps(original_method)
            def observed(self, query, opts, *args, **kwargs):
                if not getattr(opts, "use_rag", False) or _ACTIVE.get() is not None:
                    return original_method(self, query, opts, *args, **kwargs)
                window = _Window()
                token = _ACTIVE.set(window)
                response = None
                failed = False
                try:
                    response = original_method(self, query, opts, *args, **kwargs)
                    return response
                except Exception:
                    failed = True
                    raise
                finally:
                    _ACTIVE.reset(token)
                    trace = getattr(response, "rag_trace", None) or {}
                    if not isinstance(trace, dict):
                        trace = {}
                    identifier = str(getattr(response, "trace_id", "") or "")
                    try:
                        request_id = UUID(identifier).hex
                    except ValueError:
                        request_id = uuid4().hex
                    stages = {name: round(value, 3) for name, value in window.stages.items()}
                    if "search_total_ms" in stages:
                        stages["retrieval_ms"] = round(max(0.0, stages.pop("search_total_ms") - stages.get("embedding_ms", 0.0) - stages.get("rerank_ms", 0.0)), 3)
                    retrieval = trace.get("retrieval") or {}
                    paths = retrieval.get("query_paths") or [] if isinstance(retrieval, dict) else []
                    mode = ""
                    if paths and isinstance(paths[0], dict):
                        mode = str(paths[0].get("mode") or "")[:40]
                    record = {
                        "request_id": request_id,
                        "service_id": "agi-office-backend",
                        "operation": "rag.question",
                        "method": "POST",
                        "version": Path(__file__).resolve().parent.name[:32],
                        "status": 500 if failed else 200,
                        "started_at_unix": round(window.started_at, 3),
                        "ended_at_unix": round(time.time(), 3),
                        "duration_ms": round((time.perf_counter() - window.started) * 1000, 3),
                        "pid": os.getpid(),
                        "stage_ms": stages,
                        "retrieval_mode": mode,
                        "result": "FAILED" if failed or getattr(response, "error", None) else "INTERRUPTED" if getattr(response, "interrupted", False) else "COMPLETED",
                    }
                    try:
                        store.append(record)
                    except OSError:
                        # Telemetry failures never fail a user question.
                        pass

            return observed

        setattr(UnifiedAgent, method_name, wrap_process(original))
    _INSTALLED = True
