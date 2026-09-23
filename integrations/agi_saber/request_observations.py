"""Request-scoped, content-free RAG timings for the deployed AGI-saber app."""
from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from functools import wraps
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from uuid import UUID, uuid4


_ACTIVE: ContextVar["_Window | None"] = ContextVar("mini_drop_rag_window", default=None)
_EXERCISE: ContextVar[str] = ContextVar("mini_drop_rag_exercise", default="")
_INGEST: ContextVar["_IngestWindow | None"] = ContextVar("mini_drop_ingest_window", default=None)
_INSTALLED = False
EXERCISE_QUESTION = "员工年假申请须提前多久提交？"
EXERCISE_DELAY_MS = 2500
_OBSERVATION_KEYS = frozenset({
    "request_id", "service_id", "operation", "method", "version", "status",
    "started_at_unix", "ended_at_unix", "duration_ms", "pid", "stage_ms",
    "retrieval_mode", "result", "exercise_phase", "injected_delay_ms",
    "content_chars", "chunk_count", "embed_calls", "embed_failures",
    "vector_indexed_count", "process_cpu_ms", "rss_peak_mib", "service_cpu_ms",
    "service_memory_peak_mib", "service_memory_limit_mib",
})
_OBSERVATION_STAGES = frozenset({
    "rewrite_ms", "embedding_ms", "retrieval_ms", "rerank_ms", "generation_ms",
    "split_ms", "index_ms", "vector_write_ms", "ingest_ms",
    "document_write_ms", "parse_and_http_ms",
})


class ExerciseScope:
    """A request-scoped fault. No process-wide switch or cleanup timer exists."""

    def __init__(self, app):
        self.app = app
        self.store = None

    def with_store(self, store):
        self.store = store
        return self

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") == "/api/upload" and self.store is not None:
            window = _IngestWindow()
            token = _INGEST.set(window)
            status = 500

            async def observed_send(message):
                nonlocal status
                if message.get("type") == "http.response.start":
                    status = int(message.get("status", 500))
                    message["headers"] = list(message.get("headers") or []) + [
                        (b"x-mini-drop-request-id", window.request_id.encode("ascii"))]
                await send(message)

            try:
                await self.app(scope, receive, observed_send)
            finally:
                _INGEST.reset(token)
                window.finish()
                record = window.record(status)
                try:
                    self.store.append(record)
                except OSError:
                    pass
            return
        if scope.get("path") != "/api/chat":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        phase = headers.get(b"x-mini-drop-exercise", b"").decode("ascii", "ignore")
        if phase not in {"baseline", "fault", "recovery"}:
            phase = ""
        token = _EXERCISE.set(phase)
        try:
            await self.app(scope, receive, send)
        finally:
            _EXERCISE.reset(token)


class _Window:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.started = time.perf_counter()
        self.stages: dict[str, float] = {}
        self.lock = threading.Lock()
        self.exercise_phase = ""
        self.injected_delay_ms = 0

    def add(self, name: str, elapsed_ms: float) -> None:
        with self.lock:
            self.stages[name] = self.stages.get(name, 0.0) + elapsed_ms


def _rss_mib() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _service_cgroup_sample() -> tuple[float | None, float | None, float | None]:
    """Sample the actual systemd service cgroup, including Milvus Lite child processes."""
    try:
        lines = Path("/proc/self/cgroup").read_text().splitlines()
        relative = next(line.split("::", 1)[1] for line in lines if line.startswith("0::"))
        root = Path("/sys/fs/cgroup")
        group = (root / relative.lstrip("/")).resolve()
        if not group.is_relative_to(root):
            return None, None, None
        memory = int((group / "memory.current").read_text().strip()) / (1024 * 1024)
        limit_text = (group / "memory.max").read_text().strip()
        limit = None if limit_text == "max" else int(limit_text) / (1024 * 1024)
        cpu_line = next(line for line in (group / "cpu.stat").read_text().splitlines()
                        if line.startswith("usage_usec "))
        cpu_ms = int(cpu_line.split()[1]) / 1000
        return memory, cpu_ms, limit
    except (OSError, ValueError, IndexError, StopIteration):
        return None, None, None


class _IngestWindow:
    def __init__(self):
        self.request_id = uuid4().hex
        self.started_at = time.time()
        self.started = time.perf_counter()
        self.cpu_started = time.process_time()
        self.stages = {}
        self.content_chars = 0
        self.chunk_count = 0
        self.embed_calls = 0
        self.embed_failures = 0
        self.vector_indexed_count = 0
        self.rss_peak_mib = _rss_mib()
        self.service_memory_peak_mib, self.service_cpu_started, self.service_memory_limit_mib = _service_cgroup_sample()
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.sampler = threading.Thread(target=self._sample, daemon=True)
        self.sampler.start()

    def _sample(self):
        while not self.done.wait(0.2):
            self.rss_peak_mib = max(self.rss_peak_mib, _rss_mib())
            memory, _, _ = _service_cgroup_sample()
            if memory is not None:
                self.service_memory_peak_mib = max(self.service_memory_peak_mib or 0, memory)

    def add(self, name, elapsed_ms):
        with self.lock:
            self.stages[name] = self.stages.get(name, 0.0) + elapsed_ms

    def finish(self):
        self.done.set()
        self.sampler.join(timeout=1)
        self.rss_peak_mib = max(self.rss_peak_mib, _rss_mib())
        memory, service_cpu_ended, limit = _service_cgroup_sample()
        if memory is not None:
            self.service_memory_peak_mib = max(self.service_memory_peak_mib or 0, memory)
        self.service_memory_limit_mib = limit or self.service_memory_limit_mib
        self.service_cpu_ms = (max(0, service_cpu_ended - self.service_cpu_started)
                               if service_cpu_ended is not None and self.service_cpu_started is not None else None)
        self.ended_at = time.time()
        self.duration_ms = (time.perf_counter() - self.started) * 1000
        self.process_cpu_ms = (time.process_time() - self.cpu_started) * 1000

    def record(self, status):
        stages = {name: round(value, 3) for name, value in self.stages.items()}
        write_total = stages.pop("document_write_total_ms", 0)
        stages["document_write_ms"] = round(max(0, write_total - stages.get("ingest_ms", 0)), 3)
        stages["parse_and_http_ms"] = round(max(0, self.duration_ms - write_total), 3)
        return {
            "request_id": self.request_id, "service_id": "agi-office-backend",
            "operation": "rag.ingest", "method": "POST",
            "version": Path(__file__).resolve().parent.name[:32],
            "status": status, "started_at_unix": round(self.started_at, 3),
            "ended_at_unix": round(self.ended_at, 3),
            "duration_ms": round(self.duration_ms, 3), "pid": os.getpid(),
            "stage_ms": stages, "retrieval_mode": "",
            "result": "COMPLETED" if status < 400 and self.chunk_count > 0 else "FAILED",
            "content_chars": self.content_chars, "chunk_count": self.chunk_count,
            "embed_calls": self.embed_calls, "embed_failures": self.embed_failures,
            "vector_indexed_count": self.vector_indexed_count,
            "process_cpu_ms": round(self.process_cpu_ms, 3),
            "rss_peak_mib": round(self.rss_peak_mib, 3),
            "service_cpu_ms": round(self.service_cpu_ms, 3) if self.service_cpu_ms is not None else None,
            "service_memory_peak_mib": round(self.service_memory_peak_mib, 3) if self.service_memory_peak_mib is not None else None,
            "service_memory_limit_mib": round(self.service_memory_limit_mib, 3) if self.service_memory_limit_mib is not None else None,
            "exercise_phase": "", "injected_delay_ms": 0,
        }


def _measure_ingest(cls, method_name, stage):
    original = getattr(cls, method_name)

    @wraps(original)
    def measured(self, *args, **kwargs):
        window = _INGEST.get()
        if window is None:
            return original(self, *args, **kwargs)
        if stage == "ingest_ms" and args:
            window.content_chars = len(args[0])
        if stage == "embedding_ms":
            window.embed_calls += 1
        started = time.perf_counter()
        try:
            result = original(self, *args, **kwargs)
            if stage == "ingest_ms":
                window.chunk_count = int(result or 0)
            return result
        except Exception:
            if stage == "embedding_ms":
                window.embed_failures += 1
            raise
        finally:
            window.add(stage, (time.perf_counter() - started) * 1000)

    setattr(cls, method_name, measured)


def _measure_vector_upsert(cls):
    original = cls.upsert

    @wraps(original)
    def measured(self, collection_name, data):
        window = _INGEST.get()
        if window is None:
            return original(self, collection_name, data)
        started = time.perf_counter()
        try:
            result = original(self, collection_name, data)
            if result is True:
                window.vector_indexed_count += len(data)
            return result
        finally:
            window.add("vector_write_ms", (time.perf_counter() - started) * 1000)

    cls.upsert = measured


class OfficeObservationStore:
    def __init__(self, path: str | Path, *, limit: int = 100) -> None:
        self.path = Path(path)
        self.records: deque[dict] = deque(maxlen=limit)
        self.lock = threading.Lock()
        self.producer_started_at_unix = time.time()
        self._load_previous()

    def _load_previous(self) -> None:
        """Carry forward only bounded, content-free rows from a prior process."""
        try:
            if self.path.stat().st_size > 256 * 1024:
                return
            previous = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if (not isinstance(previous, dict)
                or previous.get("schema_version") not in {"mini-drop.office-observations.v1", "mini-drop.office-observations.v2"}
                or previous.get("service_id") != "agi-office-backend"
                or not isinstance(previous.get("pid"), int)
                or not isinstance(previous.get("records"), list)
                or len(previous["records"]) > 100):
            return
        previous_started = previous.get("producer_started_at_unix")
        if previous["schema_version"].endswith(".v2") and (
                not isinstance(previous_started, (int, float))
                or isinstance(previous_started, bool)
                or not math.isfinite(previous_started)
                or previous_started < 0
                or previous_started > self.producer_started_at_unix + 5):
            return
        valid = []
        for row in previous["records"]:
            if not isinstance(row, dict) or not set(row).issubset(_OBSERVATION_KEYS):
                continue
            started, ended = row.get("started_at_unix"), row.get("ended_at_unix")
            stages = row.get("stage_ms")
            if (not isinstance(row.get("request_id"), str)
                    or re.fullmatch(r"[a-f0-9]{32}", row["request_id"]) is None
                    or row.get("service_id") != "agi-office-backend"
                    or not isinstance(row.get("pid"), int) or row["pid"] <= 0
                    or not isinstance(started, (int, float)) or not math.isfinite(started)
                    or not isinstance(ended, (int, float)) or not math.isfinite(ended)
                    or ended < started or ended > self.producer_started_at_unix + 5
                    or self.producer_started_at_unix - ended > 24 * 3600
                    or not isinstance(stages, dict)
                    or any(key not in _OBSERVATION_STAGES or not isinstance(value, (int, float))
                           or not math.isfinite(value) or value < 0 for key, value in stages.items())):
                continue
            if previous["schema_version"].endswith(".v1") and row["pid"] != previous["pid"]:
                continue
            if (previous["schema_version"].endswith(".v2")
                    and row["pid"] != previous["pid"] and ended > previous_started + 5):
                continue
            valid.append(row)
        for row in sorted(valid, key=lambda item: item["ended_at_unix"])[-self.records.maxlen:]:
            self.records.append(row)

    def append(self, record: dict) -> None:
        with self.lock:
            self.records.append(record)
            payload = {
                "schema_version": "mini-drop.office-observations.v2",
                "service_id": "agi-office-backend",
                "pid": os.getpid(),
                "producer_started_at_unix": round(self.producer_started_at_unix, 3),
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
            if stage == "search_total_ms" and window.exercise_phase == "fault":
                with window.lock:
                    inject = window.injected_delay_ms == 0
                    if inject:
                        window.injected_delay_ms = EXERCISE_DELAY_MS
                if inject:
                    time.sleep(EXERCISE_DELAY_MS / 1000)
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
    from internal.infra.infra import _MilvusAdapter
    from internal.llm.llm import Client as LLMClient
    from internal.rag.hybrid import HybridStore
    from internal.rag.rag import Engine
    from internal.rag.rewriter import LLMRewriter
    from internal.rag.splitter import RecursiveSplitter

    _measure_ingest(UnifiedAgent, "write_document", "document_write_total_ms")
    _measure_ingest(Engine, "ingest", "ingest_ms")
    _measure_ingest(RecursiveSplitter, "split", "split_ms")
    _measure_ingest(LLMClient, "embed", "embedding_ms")
    _measure_ingest(LLMClient, "embed_batch", "embedding_ms")
    _measure_ingest(HybridStore, "index_with_parents", "index_ms")
    _measure_vector_upsert(_MilvusAdapter)

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
                if query == EXERCISE_QUESTION:
                    window.exercise_phase = _EXERCISE.get()
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
                        "exercise_phase": window.exercise_phase,
                        "injected_delay_ms": window.injected_delay_ms,
                    }
                    try:
                        store.append(record)
                    except OSError:
                        # Telemetry failures never fail a user question.
                        pass

            return observed

        setattr(UnifiedAgent, method_name, wrap_process(original))
    _INSTALLED = True
