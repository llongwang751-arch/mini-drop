"""Mini-Drop HTTP API 入口。

生产部署中，本模块只承载 HTTP API。Agent gRPC 控制面和诊断推进循环分别由
``server.app.grpc_main``、``server.app.diagnosis_worker`` 独立运行。

为兼容单进程开发和现有测试，可通过 ``MINI_DROP_EMBED_GRPC=1`` 与
``MINI_DROP_EMBED_MAINTENANCE=1`` 恢复旧的内嵌运行方式。
"""

from __future__ import annotations

import server.app._env  # noqa: F401 — 自动加载 .env

import json as _json_mod
import hashlib
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path as _Path
from urllib.parse import quote as _url_quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import asyncio
import json as _json
import queue as _queue
import threading
from typing import Optional

from server.app.common_utils import status_value
from server.app.ai_provider import get_ai_settings
from server.app.ai_validation import AIValidationBusy, run_ai_validation_suite
from server.app.database import init_db, new_session
from server.app.event_bus import BUS, notify_diagnosis_complete
from server.app.prometheus_metrics import (
    REGISTRY,
    record_diagnosis,
    record_golden_evaluation,
    record_http_request,
    set_analysis_job_count,
)
from server.app.grpc_server import serve_in_background
from server.app.logging_utils import log_event
from server.app.nlp.intent_parser import parse_intent
from server.app.nlp.process_resolver import resolve_pid
from server.app.nlp.summarizer import summarize, suggest_followup
from server.app.diagnosis import DiagnosisOrchestrator
from server.app.diagnosis.eval_harness import run_evaluation as run_golden_evaluation
from server.app.diagnosis.evaluation_runs import create_evaluation_run, get_evaluation_run
from server.app.diagnosis.campaign_runs import get_campaign_manager
from server.app.diagnosis.benchmark_catalog import load_benchmark_catalog
from server.app.diagnosis.benchmark_runner import build_run_plan
from server.app.diagnosis.external_benchmark import (
    ExternalBenchmarkUnavailable,
    external_benchmark_case,
    external_benchmark_summary,
)
from server.app.diagnosis.real_world_runs import (
    get_real_world_run_manager,
    real_world_catalog,
)
from server.app.diagnosis.probe_registry import list_probes as list_registered_probes
from server.app.diagnosis.schemas import ApprovalRequest, CreateDiagnosisRequest
from server.app.evaluation.evaluator import (
    ArtifactEvaluationError,
    DiagnosisArtifactEvaluator,
)
from server.app.evaluation.oracle_repository import EvaluationOracleRepository
from server.app.evaluation.schemas import EvaluationRequest
from server.app.rca.report import run_diagnosis_context
from server.app.schemas import (
    APIResponse,
    CancelTaskRequest,
    CompositeTaskRequest,
    CreateTaskRequest,
    MAX_SAMPLE_RATE,
    MAX_TASK_DURATION_SEC,
    RCAFeedbackRequest,
    ScheduleRequest,
    TaskView,
)
from server.app.sql_repository import SqlRepository
from server.app.state_machine import Actor
from server.app import storage as store
from server.app.drop_insight.router import router as drop_insight_router
from server.app.drop_insight.service import (
    get_diagnosis as get_drop_insight_diagnosis,
    list_diagnoses as list_drop_insight_diagnoses,
)
from server.app.diagnostic_case_adapter import (
    adapt_cluster_diagnosis,
    adapt_drop_insight,
    adapt_legacy_rca,
    merge_diagnostic_cases,
)

repo = SqlRepository()
diagnosis_orchestrator = DiagnosisOrchestrator(repo)


def _production_fail_closed_check() -> None:
    """Refuse to start in production with insecure defaults (assessment §3.4)."""
    if os.getenv("MINI_DROP_ENV", "development").lower() != "production":
        return
    cors = os.getenv("MINI_DROP_CORS_ORIGINS", "http://localhost:5173")
    if "*" in [part.strip() for part in cors.split(",")]:
        raise RuntimeError(
            "生产模式禁止 CORS 通配符 (*)：请显式设置 MINI_DROP_CORS_ORIGINS"
        )
    if not _env_bool("MINI_DROP_API_AUTH_ENABLED", False):
        raise RuntimeError(
            "生产模式要求启用认证：MINI_DROP_API_AUTH_ENABLED=true"
        )
    gateway_token = os.getenv("MINI_DROP_INTERNAL_GATEWAY_TOKEN", "").strip()
    if gateway_token in {"", "mini-drop-internal-dev"}:
        raise RuntimeError("生产模式要求配置非默认 MINI_DROP_INTERNAL_GATEWAY_TOKEN")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """应用生命周期：默认兼容旧单进程模式，生产由环境变量关闭内嵌组件。"""
    _production_fail_closed_check()
    init_db()
    # Probe the AI provider's structured-output capabilities once at startup
    # (assessment §4.6). No-op when AI is disabled or no key is configured.
    try:
        from server.app.ai_provider import preflight_provider

        preflight_provider()
    except Exception:
        pass
    if os.getenv("MINIO_AUTO_CREATE_BUCKET", "0") == "1":
        _ensure_minio_bucket_with_retry(os.getenv("MINIO_BUCKET", "mini-drop"))
    grpc_server = serve_in_background(repo) if _env_bool("MINI_DROP_EMBED_GRPC", True) else None
    maintenance_task = (
        asyncio.create_task(_offline_sweeper())
        if _env_bool("MINI_DROP_EMBED_MAINTENANCE", True)
        else None
    )
    outbox_threads: list[threading.Thread] = []
    outbox_stop_event: threading.Event | None = None
    if _env_bool("MINI_DROP_OUTBOX_DISPATCH_ENABLED", False):
        # Consume both transactional outboxes in-process so SSE delivery shares
        # this server's event bus. Each stream has an independent worker lease.
        from server.app.diagnosis.store import DiagnosisStore
        from server.app.outbox_dispatcher import run_artifact_worker, run_worker

        worker_prefix = os.getenv(
            "MINI_DROP_OUTBOX_WORKER_ID", f"server-{os.getpid()}"
        ).strip() or f"server-{os.getpid()}"
        outbox_stop_event = threading.Event()
        outbox_threads = [
            threading.Thread(
                target=run_worker,
                args=(repo, f"{worker_prefix}:task"),
                kwargs={
                    "poll_seconds": 2.0,
                    "stop_event": outbox_stop_event,
                },
                daemon=True,
            ),
            threading.Thread(
                target=run_artifact_worker,
                args=(DiagnosisStore(), f"{worker_prefix}:artifact"),
                kwargs={
                    "poll_seconds": 2.0,
                    "stop_event": outbox_stop_event,
                },
                daemon=True,
            ),
        ]
        for thread in outbox_threads:
            thread.start()
    try:
        yield
    finally:
        if maintenance_task is not None:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
        if outbox_stop_event is not None:
            outbox_stop_event.set()
        for thread in outbox_threads:
            thread.join(timeout=5)
        if grpc_server is not None:
            grpc_server.stop(grace=None).wait(timeout=5)


def _run_maintenance_once() -> None:
    timeout_sec = int(os.getenv("AGENT_OFFLINE_TIMEOUT_SEC", "30"))
    maintenance_steps = (
        ("mark_offline_agents", lambda: repo.mark_offline_agents(timeout_sec=timeout_sec)),
        (
            "persist_agent_metric_snapshots",
            lambda: repo.persist_agent_metric_snapshots()
            if hasattr(repo, "persist_agent_metric_snapshots")
            else None,
        ),
        ("advance_active_diagnoses", diagnosis_orchestrator.advance_active),
        (
            "reconcile_terminal_artifacts",
            diagnosis_orchestrator.reconcile_terminal_artifacts,
        ),
    )
    for step_name, step in maintenance_steps:
        try:
            step()
        except Exception as exc:
            log_event(
                "error",
                "maintenance_step_failed",
                step=step_name,
                error=str(exc)[:1000],
            )


async def _offline_sweeper() -> None:
    timeout_sec = int(os.getenv("AGENT_OFFLINE_TIMEOUT_SEC", "30"))
    interval_sec = max(1, min(timeout_sec // 2, 15))
    while True:
        _run_maintenance_once()
        await asyncio.sleep(interval_sec)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(int(default))).strip().lower() in {"1", "true", "yes", "on"}


def _ensure_minio_bucket_with_retry(bucket: str) -> None:
    attempts = max(1, int(os.getenv("MINI_DROP_MINIO_READY_RETRIES", "5")))
    delay_sec = max(0.0, float(os.getenv("MINI_DROP_MINIO_READY_DELAY_SEC", "1")))
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            store.ensure_bucket(bucket)
            return
        except Exception as exc:
            last_exc = exc
            log_event(
                "warning",
                "minio_bucket_init_retry",
                bucket=bucket,
                attempt=attempt,
                attempts=attempts,
                error=type(exc).__name__,
            )
            if attempt < attempts and delay_sec > 0:
                time.sleep(delay_sec)

    if last_exc is None:
        raise RuntimeError("minio_bucket_init_failed: all retries exhausted with no exception")
    raise last_exc


app = FastAPI(title="Mini-Drop Server", version="0.1.0", lifespan=_lifespan)
app.include_router(drop_insight_router)

# CORS 中间件：允许前端跨域开发访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("MINI_DROP_CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# request-id 中间件：为每个 HTTP 请求生成唯一 ID，注入响应头、请求状态和结构化日志
@app.middleware("http")
async def _request_id(request: Request, call_next):
    import uuid
    rid = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    return response


@app.middleware("http")
async def _access_log(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        log_event(
            "error",
            "http_request_failed",
            request_id=getattr(request.state, "request_id", ""),
            method=request.method,
            path=request.url.path,
            error=type(exc).__name__,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        raise

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    log_event(
        "info",
        "http_request",
        request_id=getattr(request.state, "request_id", ""),
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        latency_ms=latency_ms,
    )
    record_http_request(request.method, request.url.path, response.status_code, latency_ms)
    return response


@app.middleware("http")
async def _api_key_auth(request: Request, call_next):
    request.state.authenticated_principal = "local-anonymous"
    if _requires_api_auth(request):
        gateway_token = os.getenv("MINI_DROP_INTERNAL_GATEWAY_TOKEN", "").strip()
        provided_gateway_token = request.headers.get("x-mini-drop-gateway-token", "").strip()
        trusted_gateway = bool(
            gateway_token
            and provided_gateway_token
            and secrets.compare_digest(provided_gateway_token, gateway_token)
        )
        if trusted_gateway:
            principal = request.headers.get("x-mini-drop-principal", "").strip()
            if not principal:
                return JSONResponse(status_code=401, content={"detail": "可信网关未提供 Principal"})
            request.state.authenticated_principal = principal
            request.state.authenticated_roles = _split_trusted_scope_header(
                request.headers.get("x-mini-drop-roles", "")
            )
            request.state.authenticated_agent_scope = _split_trusted_scope_header(
                request.headers.get("x-mini-drop-agent-scope", "")
            )
            request.state.authenticated_service_scope = _split_trusted_scope_header(
                request.headers.get("x-mini-drop-service-scope", "")
            )
            request.state.authenticated_environment_scope = _split_trusted_scope_header(
                request.headers.get("x-mini-drop-environment-scope", "")
            )
            return await call_next(request)
        expected = os.getenv("MINI_DROP_API_KEY", "")
        token = _extract_api_token(request)
        if token:
            fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
            request.state.authenticated_principal = f"api-key:{fingerprint}"
        if not expected and gateway_token:
            return JSONResponse(status_code=401, content={"detail": "无效可信网关凭证"})
        if not expected:
            return JSONResponse(
                status_code=500,
                content={"detail": "API auth enabled but MINI_DROP_API_KEY is empty"},
            )
        if not token or not secrets.compare_digest(token, expected):
            return JSONResponse(status_code=401, content={"detail": "无效 API Key"})
    return await call_next(request)


def _split_trusted_scope_header(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _require_trusted_role(request: Request, role: str) -> None:
    roles = getattr(request.state, "authenticated_roles", ())
    if role not in roles:
        raise HTTPException(status_code=403, detail="权限不足")


def _artifact_evaluator() -> DiagnosisArtifactEvaluator:
    oracle_path = os.getenv("MINI_DROP_EVALUATOR_ORACLE_PATH", "").strip()
    if not oracle_path:
        raise HTTPException(status_code=503, detail="EVALUATOR_UNAVAILABLE")
    from server.app.diagnosis.store import DiagnosisStore

    return DiagnosisArtifactEvaluator(
        DiagnosisStore(),
        EvaluationOracleRepository(oracle_path),
    )


def _task_view(record) -> TaskView:
    """将 TaskRecord 转为前端模型。"""
    return TaskView(
        id=record.id,
        name=record.name,
        agent_id=record.agent_id,
        target_pid=record.target_pid,
        collector_type=record.collector_type,
        sample_rate=record.sample_rate,
        duration_sec=record.duration_sec,
        status=status_value(record.status),
        status_reason=record.status_reason,
        collection_status=getattr(record, "collection_status", None) or "QUEUED",
        analysis_status=getattr(record, "analysis_status", None) or "NOT_STARTED",
        request_params=record.request_params,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _requires_api_auth(request: Request) -> bool:
    if os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    path = request.url.path
    return path.startswith("/api/") and path not in {"/api/healthz", "/api/metrics", "/api/auth/set-cookie", "/api/auth/clear-cookie"}


def _extract_api_token(request: Request) -> str | None:
    # 1. Authorization: Bearer <token> header
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 2. X-API-Key header
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    # 3. HttpOnly cookie (preferred for browser clients — resists XSS exfiltration)
    cookie = request.cookies.get("mini_drop_api_key")
    if cookie:
        return cookie.strip()
    return None


# ── 通用 ──────────────────────────────────────────────────────


@app.get("/api/events/stream")
async def sse_stream(request: Request, since: str = ""):
    """Server-Sent Events 实时推送。

    客户端通过 EventSource 连接此端点，接收任务状态变更、
    Agent 上下线、诊断完成等实时事件。

    用法：const es = new EventSource('/api/events/stream');
          es.onmessage = (e) => console.log(JSON.parse(e.data));
    """
    from fastapi.responses import StreamingResponse

    async def event_generator():
        queue = BUS.subscribe()
        try:
            # 先发送历史事件（如果客户端提供了 since 时间戳）
            for event in BUS.get_history(since if since else None):
                yield f"event: {event['event']}\ndata: {_json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"

            # 持续推送新事件
            while True:
                try:
                    event = await asyncio.to_thread(queue.get, True, 30.0)
                    yield f"event: {event['event']}\ndata: {_json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
                except _queue.Empty:
                    # 每 30 秒发一个注释行保活
                    yield ":keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            BUS.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 禁用缓冲
        },
    )


@app.get("/api/metrics")
def prometheus_metrics() -> Any:
    """Prometheus 指标端点。

    返回 text/plain 格式的指标数据，可被 Prometheus server 抓取。
    无需鉴权（抓取时 Prometheus 通常不带自定义 header）。
    """
    from fastapi.responses import PlainTextResponse
    for status, analyzer_type, count in repo.analysis_job_counts():
        set_analysis_job_count(status, analyzer_type, count)
    return PlainTextResponse(content=REGISTRY.generate(), media_type="text/plain; charset=utf-8")


@app.get("/api/top-processes")
def top_processes_api(limit: int = 20) -> APIResponse:
    """宿主顶层进程（供采集预设选忙 PID）。需要 server 容器 pid: host。"""
    from server.app.process_discovery import top_processes

    return APIResponse(data={"items": top_processes(limit)})


@app.get("/api/healthz")
def healthz() -> APIResponse:
    """健康检查端点：验证服务自身及关键依赖（数据库、对象存储）的状态。

    Kubernetes liveness/readiness probe 可通过此端点区分：
      - 200 + healthy=true  → 服务完全可用
      - 200 + healthy=false → 服务存活但依赖不可用（readiness 应标记为未就绪）
      - 非 200               → 服务未存活
    """
    checks: dict[str, dict] = {}

    # 数据库连通性检查
    try:
        from sqlalchemy import text as _sa_text
        session = new_session()
        try:
            session.execute(_sa_text("SELECT 1"))
        finally:
            session.close()
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        checks["database"] = {"status": "unavailable", "error": str(exc)[:200]}

    # 对象存储连通性检查
    try:
        store.ensure_bucket(os.getenv("MINIO_BUCKET", "mini-drop"))
        checks["storage"] = {"status": "ok"}
    except Exception as exc:
        checks["storage"] = {"status": "unavailable", "error": str(exc)[:200]}

    all_ok = all(c["status"] == "ok" for c in checks.values())
    return APIResponse(data={
        "service": "mini-drop-server",
        "version": "0.1.0",
        "healthy": all_ok,
        "checks": checks,
    })


@app.get("/api/ai-config")
def ai_config() -> APIResponse:
    """Return safe AI configuration metadata without exposing the API key."""
    settings = get_ai_settings()
    return APIResponse(data={
        "enabled": settings.enabled,
        "provider": settings.provider,
        "base_url": settings.base_url,
        "model": settings.model,
        "has_api_key": bool(settings.api_key),
        "features": {
            "nlp": settings.nlp_enabled,
            "rca": settings.rca_enabled,
            "summarize": settings.summarize_enabled,
        },
    })


@app.post("/api/ai-validation/runs")
def run_ai_validation() -> APIResponse:
    """Run the complete provider + Drop AI validation suite on demand."""
    try:
        result = run_ai_validation_suite()
    except AIValidationBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=result)


@app.get("/api/me")
def current_user() -> APIResponse:
    return APIResponse(data={
        "user_id": "demo_user",
        "name": "Mini-Drop Demo User",
        "role": "admin",
    })


@app.post("/api/auth/set-cookie")
def auth_set_cookie(request: Request, body: dict) -> APIResponse:
    """通过 HttpOnly cookie 设置 API Key（比 localStorage 更安全）。

    POST /api/auth/set-cookie
    {"api_key": "sk-..."}

    浏览器将自动在后续请求中携带该 cookie，
    JavaScript 无法通过 document.cookie 读取（HttpOnly）。
    """
    from fastapi.responses import JSONResponse as _JsonResp
    api_key = (body or {}).get("api_key", "").strip()
    if not api_key:
        return APIResponse(code=400, message="api_key 不能为空")
    resp = _JsonResp(content={"code": 0, "message": "ok", "data": None})
    resp.set_cookie(
        key="mini_drop_api_key",
        value=api_key,
        httponly=True,
        samesite="lax",
        secure=(
            os.getenv("MINI_DROP_ENV", "development").strip().lower() == "production"
            or _env_bool("MINI_DROP_COOKIE_SECURE", False)
        ),
        max_age=7 * 24 * 3600,  # 7 天
        path="/api",
    )
    return resp


@app.post("/api/auth/clear-cookie")
def auth_clear_cookie() -> APIResponse:
    """清除 HttpOnly cookie。"""
    from fastapi.responses import JSONResponse as _JsonResp
    resp = _JsonResp(content={"code": 0, "message": "ok", "data": None})
    resp.delete_cookie(key="mini_drop_api_key", path="/api")
    return resp


# ── Agent（查询面） ────────────────────────────────────────────


@app.get("/api/agents")
def list_agents(
    limit: int = 1000,
    offset: int = 0,
) -> APIResponse:
    """返回 Agent 列表。支持分页。

    调用前自动检查离线。可通过 ?limit=50&offset=0 分页。
    """
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    repo.mark_offline_agents()
    all_items = []
    for agent in repo.agents.values():
        item = repo.as_dict(agent)
        item["latest_metrics"] = getattr(repo, "agent_metrics", {}).get(agent.id, {})
        all_items.append(item)
    total = len(all_items)
    page = all_items[offset:offset + limit] if offset < total else []
    return APIResponse(data={"items": page, "total": total, "offset": offset, "limit": limit})


@app.get("/api/audit-logs")
def list_audit_logs(
    limit: int = 1000,
    offset: int = 0,
) -> APIResponse:
    """返回审计日志列表。支持分页。"""
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    all_items = [repo.as_dict(log) for log in repo.audit_logs]
    total = len(all_items)
    page = all_items[offset:offset + limit] if offset < total else []
    return APIResponse(data={"items": page, "total": total, "offset": offset, "limit": limit})


# ── 任务 ──────────────────────────────────────────────────────


@app.post("/api/tasks")
def create_task(payload: CreateTaskRequest, request: Request) -> APIResponse:
    if payload.target_pid <= 0:
        raise HTTPException(status_code=400, detail="target_pid 必须为正整数")
    if payload.target_pid > 4194304:  # Linux pid_max 上限
        raise HTTPException(status_code=400, detail=f"target_pid 超出有效范围: {payload.target_pid}")
    if payload.duration_sec <= 0:
        raise HTTPException(status_code=400, detail="duration_sec 必须为正整数")
    if payload.duration_sec > MAX_TASK_DURATION_SEC:
        raise HTTPException(status_code=400, detail=f"duration_sec 不能超过 {MAX_TASK_DURATION_SEC}")
    if payload.sample_rate <= 0:
        raise HTTPException(status_code=400, detail="sample_rate 必须为正整数")
    if payload.sample_rate > MAX_SAMPLE_RATE:
        raise HTTPException(status_code=400, detail=f"sample_rate 不能超过 {MAX_SAMPLE_RATE}")
    idempotency_key = _read_idempotency_key(request)
    creator_id = getattr(request.state, "authenticated_principal", None) or "python-api"
    try:
        task = repo.create_task(
            payload,
            idempotency_key=idempotency_key,
            creator_id=creator_id,
        )
    except ValueError as exc:
        if "Idempotency-Key" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # A replayed request returns the SAME task_id; the replay is observable
    # through the idempotent (creator_id, idempotency_key) lookup.
    return APIResponse(data={"task_id": task.id, "status": status_value(task.status)})


def _read_idempotency_key(request: Request) -> str | None:
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key:
        return None
    if len(key) < 8 or len(key) > 128 or any(ch.isspace() for ch in key):
        raise HTTPException(status_code=400, detail="Idempotency-Key 格式不合法")
    return key


@app.get("/api/tasks")
def list_tasks(
    limit: int = 1000,
    offset: int = 0,
    search: str = "",
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> APIResponse:
    """返回任务列表。支持分页、搜索、排序。

    可通过 ?limit=50&offset=0&search=perf&sort_by=name&sort_order=asc 过滤。
    """
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)

    all_items = [_task_view(t).model_dump() for t in repo.tasks.values()]

    # 搜索：按任务名称模糊匹配
    if search:
        q = search.lower()
        all_items = [t for t in all_items if q in (t.get("name") or "").lower() or q in (t.get("id") or "").lower()]

    # 排序
    sort_keys = {"name", "status", "created_at", "agent_id", "collector_type", "target_pid"}
    by = sort_by if sort_by in sort_keys else "created_at"
    reverse = sort_order.lower() == "desc"
    all_items.sort(key=lambda x: x.get(by, "") or "", reverse=reverse)

    total = len(all_items)
    page = all_items[offset:offset + limit] if offset < total else []
    return APIResponse(data={"items": page, "total": total, "offset": offset, "limit": limit})


@app.get("/api/analysis-jobs")
def list_analysis_jobs(
    task_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> APIResponse:
    """返回独立 Analyzer 执行记录，便于观察租约、重试和失败原因。"""

    jobs = repo.list_analysis_jobs(task_id=task_id, status=status, limit=limit)
    return APIResponse(data=[job.to_dict() for job in jobs])


@app.get("/api/analysis-jobs/{job_id}")
def get_analysis_job(job_id: str) -> APIResponse:
    job = repo.get_analysis_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return APIResponse(data=job.to_dict())


@app.post("/api/analysis-jobs/{job_id}/replay")
def replay_analysis_job(job_id: str) -> APIResponse:
    """人工重放死信或待重试分析任务。"""

    try:
        job = repo.replay_analysis_job(job_id)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=404 if "不存在" in message else 409,
            detail=message,
        ) from exc
    return APIResponse(data=job.to_dict())


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> APIResponse:
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=_task_view(task).model_dump())


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str, payload: CancelTaskRequest) -> APIResponse:
    """Cancel a queued or running task; a running Agent receives it via heartbeat."""
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        cancelled = repo.cancel_task(task_id, payload.reason.strip(), Actor.WEB)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(
        data={
            "task_id": task_id,
            "status": status_value(cancelled.status),
            "reason": cancelled.status_reason,
        }
    )


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> APIResponse:
    """归档终态任务；事件、产物和 AI 诊断证据继续保留。"""
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    # 终态保护：RUNNING/ANALYZING 不允许删除
    active_statuses = {"PENDING", "RUNNING", "UPLOADING", "ANALYZING"}
    if status_value(task.status) in active_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"任务状态为 {status_value(task.status)}，请等待任务完成或失败后再删除",
        )
    deleted = repo.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data={"task_id": task_id, "deleted": True})


@app.get("/api/tasks/{task_id}/events")
def get_task_events(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    items = [repo.as_dict(e) for e in repo.events if e.task_id == task_id]
    return APIResponse(data=items)


@app.get("/api/tasks/{task_id}/attempts")
def get_task_attempts(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    items = [repo.as_dict(item) for item in repo.get_task_attempts(task_id)]
    return APIResponse(data=items)


@app.get("/api/tasks/{task_id}/artifacts")
def get_task_artifacts(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=repo.artifacts.get(task_id, []))


@app.get("/api/tasks/{task_id}/artifacts/{artifact_type}/content")
def get_task_artifact_content(task_id: str, artifact_type: str, index: Optional[int] = None) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    for artifact in repo.artifacts.get(task_id, []):
        if artifact.get("artifact_type") != artifact_type:
            continue
        if index is not None and artifact.get("metadata", {}).get("window_index") != index:
            continue
        local_path = artifact.get("local_path")
        path = _resolve_artifact_path_or_none(local_path)
        if path is None and artifact.get("object_key"):
            text = _read_artifact_object_text(artifact)
            if artifact_type.endswith("_json") or artifact.get("content_type") == "application/json":
                return APIResponse(data=_json_mod.loads(text))
            return APIResponse(data={"text": text})
        if path is None:
            raise HTTPException(status_code=404, detail="本地产物不存在")
        if artifact_type.endswith("_json") or artifact.get("content_type") == "application/json":
            return APIResponse(data=_json_mod.loads(path.read_text(encoding="utf-8")))
        return APIResponse(data={"text": path.read_text(encoding="utf-8", errors="replace")})
    raise HTTPException(status_code=404, detail="产物不存在")


@app.get("/api/tasks/{task_id}/artifacts/{artifact_type}/download")
def download_task_artifact(task_id: str, artifact_type: str, index: Optional[int] = None):
    """经 Server 流式下载产物，使浏览器无需直接访问 MinIO 9000 端口。"""
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    for artifact in repo.artifacts.get(task_id, []):
        if artifact.get("artifact_type") != artifact_type:
            continue
        if index is not None and artifact.get("metadata", {}).get("window_index") != index:
            continue

        filename = _safe_download_filename(
            artifact.get("filename") or artifact.get("object_key") or f"{artifact_type}.bin"
        )
        media_type = artifact.get("content_type") or "application/octet-stream"
        path = _resolve_artifact_path_or_none(artifact.get("local_path"))
        if path is not None:
            return FileResponse(path, media_type=media_type, filename=filename)

        bucket = artifact.get("bucket") or os.getenv("MINIO_BUCKET", "mini-drop")
        key = _validate_presign_request(bucket, artifact.get("object_key", ""))
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        }
        return StreamingResponse(
            store.stream_object(bucket, key),
            media_type=media_type,
            headers=headers,
        )
    raise HTTPException(status_code=404, detail="产物不存在")


# ── Schedule / Cron（指南 §3.9）─────────────────────────────────


@app.post("/api/schedules")
def create_schedule(payload: ScheduleRequest) -> APIResponse:
    """Create a cron schedule over an immutable task template."""
    try:
        model = repo.create_schedule(
            name=payload.name,
            cron_expression=payload.cron_expression,
            timezone=payload.timezone,
            task_template=payload.task_template,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(data=_schedule_view(model))


@app.get("/api/schedules")
def list_schedules() -> APIResponse:
    return APIResponse(data={"items": [_schedule_view(m) for m in repo.list_schedules()]})


@app.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, payload: ScheduleRequest) -> APIResponse:
    try:
        model = repo.update_schedule(
            schedule_id,
            name=payload.name,
            cron_expression=payload.cron_expression,
            timezone=payload.timezone,
            task_template=payload.task_template,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if model is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    return APIResponse(data=_schedule_view(model))


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str) -> APIResponse:
    if not repo.delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="计划不存在")
    return APIResponse(data={"deleted": True, "schedule_id": schedule_id})


@app.post("/api/schedules/{schedule_id}/trigger")
def trigger_schedule(schedule_id: str) -> APIResponse:
    """Fire a schedule immediately (manual trigger), then advance it."""
    schedule = repo.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    from server.app.cron import next_schedule_fire

    now = _now_utc()
    next_run = next_schedule_fire(schedule.cron_expression, schedule.timezone, now)
    try:
        task = repo.fire_schedule(
            schedule,
            scheduled_at=now,
            next_run_at=next_run,
            payload=CreateTaskRequest(**schedule.task_template_json),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
    return APIResponse(data={"task_id": task.id, "next_run_at": next_run})


@app.get("/api/schedules/{schedule_id}/records")
def list_schedule_records(schedule_id: str) -> APIResponse:
    records = repo.list_schedule_records(schedule_id)
    return APIResponse(data={"items": [{
        "id": r.id,
        "schedule_id": r.schedule_id,
        "scheduled_at": r.scheduled_at,
        "task_id": r.task_id,
        "status": r.status,
        "error_message": r.error_message,
        "created_at": r.created_at,
    } for r in records]})


def _schedule_view(model) -> dict:
    return {
        "id": model.id,
        "name": model.name,
        "cron_expression": model.cron_expression,
        "timezone": model.timezone,
        "task_template": model.task_template_json or {},
        "enabled": bool(model.enabled),
        "next_run_at": model.next_run_at,
        "created_at": model.created_at,
    }


def _now_utc():
    from server.app.state_machine import now_utc

    return now_utc()


# ── Composite Task / DAG（指南 §3.8）─────────────────────────────


@app.post("/api/composite-tasks")
def create_composite_task(payload: CompositeTaskRequest) -> APIResponse:
    try:
        model = repo.create_composite_task(
            name=payload.name,
            strategy=payload.strategy,
            children=[
                {
                    "task_template": child.task_template,
                    "role": child.role,
                }
                for child in payload.children
            ],
            required_success_count=payload.required_success_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
    status = repo.aggregate_composite(model.id)
    return APIResponse(data=_composite_view(model, status=status))


@app.get("/api/composite-tasks")
def list_composite_tasks() -> APIResponse:
    return APIResponse(data={"items": [
        _composite_view(m) for m in repo.list_composite_tasks()
    ]})


@app.get("/api/composite-tasks/{composite_id}")
def get_composite_task(composite_id: str) -> APIResponse:
    model = repo.get_composite_task(composite_id)
    if model is None:
        raise HTTPException(status_code=404, detail="复合任务不存在")
    items = repo.list_composite_items(composite_id)
    return APIResponse(data=_composite_view(model, items=items))


@app.post("/api/composite-tasks/{composite_id}/aggregate")
def aggregate_composite_task(composite_id: str) -> APIResponse:
    status = repo.aggregate_composite(composite_id)
    if status is None:
        raise HTTPException(status_code=404, detail="复合任务不存在")
    return APIResponse(data={"composite_id": composite_id, "status": status})


@app.post("/api/composite-tasks/{composite_id}/cancel")
def cancel_composite_task(composite_id: str) -> APIResponse:
    model = repo.cancel_composite_task(composite_id)
    if model is None:
        raise HTTPException(status_code=404, detail="复合任务不存在")
    return APIResponse(data=_composite_view(model))


def _composite_view(model, *, items: list | None = None, status: str | None = None) -> dict:
    return {
        "id": model.id,
        "name": model.name,
        "strategy": model.strategy,
        "required_success_count": model.required_success_count,
        "status": status or model.status,
        "created_at": model.created_at,
        "items": [
            {
                "id": item.id,
                "task_id": item.task_id,
                "role": item.role,
                "sort_order": item.sort_order,
                "status": item.status,
                "error_message": item.error_message,
            }
            for item in (items or [])
        ],
    }


@app.post("/api/tasks/{task_id}/diagnose")
def diagnose_task(task_id: str) -> APIResponse:
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 收集已有 artifacts 中的结构化数据
    artifacts = repo.artifacts.get(task_id, [])
    top_functions = _extract_artifact_json(artifacts, "top_json")
    ebpf_metrics = _extract_artifact_json(artifacts, "ebpf_metrics")
    sys_metrics = _extract_artifact_json(artifacts, "sys_metrics")

    task_events = [repo.as_dict(e) for e in repo.events if e.task_id == task_id]
    agent_record = repo.agents.get(task.agent_id)
    model_name = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    diagnosis_id = repo.create_diagnosis_run(task_id, model_name)

    outcome = run_diagnosis_context(
        task_id=task_id,
        task_record=task,
        top_functions=top_functions,
        ebpf_metrics=ebpf_metrics,
        sys_metrics=sys_metrics,
        failure_events=[event.get("reason", "") for event in task_events if event.get("reason")],
        feedback_priors=repo.get_feedback_priors(),
        task_events=task_events,
        agent_record=agent_record,
        repo=repo,
    )
    report = outcome.report
    ranked_causes = [c.model_dump() for c in report.report.ranked_causes]
    confidence = ranked_causes[0]["confidence"] if ranked_causes else 0.0

    for tool_result in outcome.tool_results:
        repo.add_diagnosis_tool_result(
            diagnosis_id=diagnosis_id,
            tool_name=tool_result.tool_name,
            status=tool_result.status,
            evidence_ref=tool_result.evidence_ref,
            input_json=tool_result.input,
            output_json=tool_result.output,
            error_message=tool_result.error_message,
        )

    report_doc = report.report.model_dump()
    report_doc["verification"] = _verify_legacy_report(report, outcome.tool_results)
    # Persist the trust layers alongside the report so the detail endpoint can
    # show generation mode / validation level instead of a single `validated`.
    report_doc["generation_mode"] = report.generation_mode
    report_doc["schema_validated"] = report.schema_validated
    report_doc["reference_validated"] = report.reference_validated
    report_doc["semantic_validated"] = report.semantic_validated
    report_doc["model_invoked"] = report.model_invoked
    report_doc["fallback_reason"] = report.fallback_reason

    report_id = repo.add_diagnosis_report(
        diagnosis_id=diagnosis_id,
        report_json=report_doc,
        ranked_causes=ranked_causes,
        confidence=confidence,
        not_enough_evidence=report.report.not_enough_evidence,
    )

    repair_plan_data = None
    if outcome.repair_plan is not None:
        repair_plan_data = outcome.repair_plan.model_dump()
        repo.add_repair_plan(
            diagnosis_id=diagnosis_id,
            plan_id=outcome.repair_plan.plan_id,
            cause_id=outcome.repair_plan.cause_id,
            risk_level=outcome.repair_plan.risk_level,
            actions=[action.model_dump() for action in outcome.repair_plan.actions],
            executed_actions=[
                action.model_dump() for action in outcome.repair_plan.actions
                if action.status == "executed"
            ],
            requires_user_confirm=outcome.repair_plan.requires_user_confirm,
            status=outcome.repair_plan.status,
        )

    report_generated = (
        report.generation_mode != "MODEL_FAILED"
        and report.schema_validated
        and report.reference_validated
    )
    trusted = (
        report_generated
        and report.semantic_validated
        and not report.report.not_enough_evidence
    )
    diag_status = "DONE" if report_generated else "FAILED"
    repo.finish_diagnosis_run(
        diagnosis_id=diagnosis_id,
        status=diag_status,
        summary=report.report.summary,
        validated=trusted,
        retry_count=report.retry_count,
    )
    record_diagnosis(diag_status)

    notify_diagnosis_complete(task_id, diagnosis_id, diag_status)

    return APIResponse(data={
        "diagnosis_id": diagnosis_id,
        "report_id": report_id,
        "task_id": task_id,
        "model": report.model_name,
        "validated": trusted,
        # Trust transparency (assessment §4.2): expose the generation mode and
        # each validation layer so the frontend never equates "format valid"
        # with "semantically trustworthy".
        "generation_mode": report.generation_mode,
        "schema_validated": report.schema_validated,
        "reference_validated": report.reference_validated,
        "semantic_validated": report.semantic_validated,
        "model_invoked": report.model_invoked,
        "fallback_reason": report.fallback_reason,
        "validation_issues": report.validation_issues,
        "summary": report.report.summary,
        "ranked_causes": ranked_causes,
        "facts": report.report.facts,
        "not_enough_evidence": report.report.not_enough_evidence,
        "tool_results": [item.model_dump() for item in outcome.tool_results],
        "repair_plan": repair_plan_data,
        "verification": report_doc.get("verification"),
    })


def _verify_legacy_report(report, tool_results) -> dict:
    """Run deterministic Claim-Evidence verification over a legacy RCA report.

    The legacy model may rank causes, but every accepted claim is re-checked
    against the resolved evidence document. Failures are recorded per claim so
    a report cannot claim numbers the evidence does not contain; any hard error
    degrades to an explicit UNVERIFIED marker instead of crashing the endpoint.
    """
    from server.app.drop_insight.claim_verifier import verify_legacy_report_claims

    evidence_document = {
        "tool_results": [item.model_dump() for item in tool_results]
    }
    try:
        return verify_legacy_report_claims(report.report, evidence_document)
    except Exception:
        return {
            "status": "UNVERIFIED",
            "claims": [],
            "rejected_claims": [],
            "coverage_ratio": 0.0,
            "has_independent_counter_or_control": False,
        }


@app.get("/api/tasks/{task_id}/diagnoses")
def list_task_diagnoses(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=repo.list_diagnoses_for_task(task_id))


@app.get("/api/diagnoses/{diagnosis_id}")
def get_diagnosis(diagnosis_id: str) -> APIResponse:
    item = repo.get_diagnosis(diagnosis_id)
    if item is None:
        raise HTTPException(status_code=404, detail="诊断不存在")
    return APIResponse(data=item)


@app.post("/api/diagnoses/{diagnosis_id}/feedback")
def submit_diagnosis_feedback(diagnosis_id: str, payload: RCAFeedbackRequest) -> APIResponse:
    item = repo.get_diagnosis(diagnosis_id)
    if item is None:
        raise HTTPException(status_code=404, detail="诊断不存在")
    task_id = item["run"]["task_id"]
    repo.record_rca_feedback(
        diagnosis_id=diagnosis_id,
        task_id=task_id,
        predicted_cause_id=payload.predicted_cause_id,
        feedback_label=payload.feedback_label,
        corrected_cause_id=payload.corrected_cause_id,
        feedback_note=payload.feedback_note,
    )
    return APIResponse(data={"diagnosis_id": diagnosis_id, "feedback_saved": True})


# ── AI 集群诊断会话（v1）──────────────────────────────────────


@app.post("/api/v1/diagnoses")
def create_diagnosis_session(payload: CreateDiagnosisRequest) -> APIResponse:
    """创建独立诊断会话，并只编排注册表中的受控探针。"""
    try:
        data = diagnosis_orchestrator.create(payload, creator_id="demo_user")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(data=data)


@app.get("/api/v1/diagnoses")
def list_diagnosis_sessions(limit: int = 100, offset: int = 0) -> APIResponse:
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    items = diagnosis_orchestrator.list(limit=limit, offset=offset)
    return APIResponse(data={
        "items": items,
        "total": diagnosis_orchestrator.store.count_sessions(),
        "offset": offset,
        "limit": limit,
    })


@app.get("/api/v1/continuous-diagnosis-triggers")
def list_continuous_diagnosis_triggers(
    limit: int = 100,
    offset: int = 0,
) -> APIResponse:
    """Expose continuous-profile anomaly promotions for audit and UI."""
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    store = diagnosis_orchestrator.store
    return APIResponse(data={
        "items": store.list_continuous_triggers(limit=limit, offset=offset),
        "total": store.count_continuous_triggers(),
        "offset": offset,
        "limit": limit,
    })


@app.get("/api/v1/diagnoses/{diagnosis_id}")
def get_diagnosis_session(diagnosis_id: str) -> APIResponse:
    data = diagnosis_orchestrator.get(diagnosis_id, advance=True)
    if data is None:
        raise HTTPException(status_code=404, detail="诊断会话不存在")
    return APIResponse(data=data)


@app.post("/api/v1/diagnoses/{diagnosis_id}/approvals")
def approve_diagnosis_probe(diagnosis_id: str, payload: ApprovalRequest) -> APIResponse:
    try:
        data = diagnosis_orchestrator.approve(diagnosis_id, payload)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "不存在" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return APIResponse(data=data)


@app.get("/api/v1/probes")
def list_probe_definitions() -> APIResponse:
    return APIResponse(data=[probe.model_dump(mode="json") for probe in list_registered_probes()])


@app.post("/api/v1/diagnosis-evaluations/artifacts")
def evaluate_frozen_diagnosis_artifact(
    payload: EvaluationRequest,
    request: Request,
) -> APIResponse:
    """Evaluate an immutable artifact behind the evaluator-only trust boundary."""

    _require_trusted_role(request, "evaluator")
    try:
        result = _artifact_evaluator().evaluate(payload)
    except ArtifactEvaluationError as exc:
        status_code = 404 if exc.code == "ARTIFACT_NOT_FOUND" else 409
        if exc.code == "ORACLE_UNAVAILABLE":
            status_code = 503
        raise HTTPException(status_code=status_code, detail=exc.code) from None
    return APIResponse(data=result)


@app.get("/api/v1/diagnosis-evaluations/golden")
def evaluate_golden_diagnosis_suite() -> APIResponse:
    """离线执行版本化 Golden 场景，作为 AI 诊断发布前质量门禁。"""

    report = run_golden_evaluation()
    record_golden_evaluation(report)
    return APIResponse(data=report)


@app.post("/api/v1/diagnosis-evaluations/golden-runs")
def start_observable_golden_evaluation() -> APIResponse:
    """启动可观察 Golden 评测，供页面逐场景展示诊断与核验过程。"""

    return APIResponse(data=create_evaluation_run())


@app.get("/api/v1/diagnosis-evaluations/golden-runs/{run_id}")
def read_observable_golden_evaluation(run_id: str) -> APIResponse:
    run = get_evaluation_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Golden evaluation run not found")
    return APIResponse(data=run)


@app.get("/api/v1/diagnosis-evaluations/catalog")
def get_diagnosis_benchmark_catalog() -> APIResponse:
    """返回所有诊断策略共享的版本化测试集目录。"""

    return APIResponse(data=load_benchmark_catalog())


@app.get("/api/v1/diagnosis-evaluations/external")
def get_external_diagnosis_benchmark() -> APIResponse:
    """Expose the installed shared benchmark and its completed run summaries.

    Private Oracle data is not returned from this catalog endpoint.  It is
    revealed only in the per-case evaluator view after a historical diagnosis
    run is selected, preserving the public/private benchmark boundary.
    """

    try:
        return APIResponse(data=external_benchmark_summary())
    except ExternalBenchmarkUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/diagnosis-evaluations/external/cases/{case_id}")
def get_external_diagnosis_benchmark_case(case_id: str) -> APIResponse:
    """Return one completed public benchmark case without evaluator data."""

    try:
        return APIResponse(data=external_benchmark_case(case_id))
    except ExternalBenchmarkUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="external benchmark case not found") from exc


@app.get("/api/v1/diagnosis-campaigns/scenarios")
def list_diagnosis_campaign_scenarios() -> APIResponse:
    """Return real, allow-listed fault scenarios available to the Web UI."""

    return APIResponse(data={"items": get_campaign_manager(repo).scenarios()})


@app.get("/api/v1/real-world-benchmarks/catalog")
def get_real_world_benchmark_catalog() -> APIResponse:
    """Return PR-derived cases and explicit cloud execution readiness."""

    return APIResponse(data=real_world_catalog())


@app.post("/api/v1/real-world-benchmarks/runs")
def start_real_world_benchmark(payload: dict | None = None) -> APIResponse:
    case_id = str((payload or {}).get("case_id") or "")
    try:
        return APIResponse(data=get_real_world_run_manager().create(case_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/real-world-benchmarks/runs/{run_id}")
def get_real_world_benchmark_run(run_id: str) -> APIResponse:
    run = get_real_world_run_manager().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="真实业务测试运行不存在")
    return APIResponse(data=run)


@app.post("/api/v1/diagnosis-campaigns/runs")
def start_diagnosis_campaign(payload: dict | None = None) -> APIResponse:
    """Start a real fault campaign; execution continues in an observable worker."""

    scenario_id = (payload or {}).get("scenario_id", "LIVE-CPU-001")
    strategy = (payload or {}).get("strategy", "CONSTRAINED_HYBRID")
    try:
        run = get_campaign_manager(repo).create(str(scenario_id), str(strategy))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return APIResponse(data=run)


@app.get("/api/v1/diagnosis-campaigns/runs/{run_id}")
def get_diagnosis_campaign(run_id: str) -> APIResponse:
    """Read Campaign stages, snapshots, linked task, Oracle comparison and cleanup."""

    run = get_campaign_manager(repo).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Campaign 不存在")
    return APIResponse(data=run)


@app.get("/api/v1/diagnosis-evaluations/plan")
def get_diagnosis_benchmark_plan(
    repetitions: int | None = None,
) -> APIResponse:
    """生成三种 AI 路径共用同一测试集和 Oracle 的可复现实验计划。"""

    try:
        return APIResponse(data=build_run_plan(repetitions=repetitions))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/diagnostic-cases")
def list_diagnostic_cases(limit: int = 100, offset: int = 0) -> APIResponse:
    """统一读取 v1/v2 会话；旧接口和旧存储保持不变。"""

    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    cluster_items = diagnosis_orchestrator.list(limit=500, offset=0)
    insight_items = [item.to_dict() for item in list_drop_insight_diagnoses()]
    legacy_items = repo.list_diagnoses(limit=500, offset=0)
    return APIResponse(
        data=merge_diagnostic_cases(
            cluster_items,
            insight_items,
            legacy_items=legacy_items,
            limit=safe_limit,
            offset=safe_offset,
        )
    )


@app.get("/api/diagnostic-cases/{case_id}")
def get_diagnostic_case(case_id: str) -> APIResponse:
    """按来源读取统一案例详情，不触发旧会话迁移或状态推进。"""

    if case_id.startswith("insight_"):
        insight = get_drop_insight_diagnosis(case_id)
        if insight is not None:
            return APIResponse(
                data=adapt_drop_insight(insight.to_dict(), include_native=True)
            )
        legacy = repo.get_diagnosis(case_id)
        if legacy is None:
            raise HTTPException(status_code=404, detail="diagnostic case not found")
        return APIResponse(data=adapt_legacy_rca(legacy, include_native=True))

    cluster = diagnosis_orchestrator.store.get_detail(case_id)
    if cluster is None:
        # 兼容未来不再使用 insight_ 前缀的 v2 会话。
        insight = get_drop_insight_diagnosis(case_id)
        if insight is not None:
            return APIResponse(
                data=adapt_drop_insight(insight.to_dict(), include_native=True)
            )
        legacy = repo.get_diagnosis(case_id)
        if legacy is None:
            raise HTTPException(status_code=404, detail="diagnostic case not found")
        return APIResponse(data=adapt_legacy_rca(legacy, include_native=True))
    return APIResponse(
        data=adapt_cluster_diagnosis(cluster, include_native=True)
    )


def _extract_artifact_json(artifacts: list[dict], artifact_type: str) -> dict | None:
    """从 artifacts 列表中提取指定类型的 JSON 数据。"""
    for art in artifacts:
        if art.get("artifact_type") == artifact_type:
            local_path = art.get("local_path", "")
            try:
                path = _resolve_artifact_path_or_none(local_path)
                if path is not None:
                    return _json_mod.loads(path.read_text(encoding="utf-8"))
                if art.get("object_key"):
                    return _json_mod.loads(_read_artifact_object_text(art))
            except HTTPException as exc:
                log_event(
                    "warning",
                    "artifact_json_unavailable",
                    artifact_type=artifact_type,
                    local_path=local_path,
                    status_code=exc.status_code,
                )
                return None
            except Exception as exc:
                log_event(
                    "warning",
                    "artifact_json_parse_failed",
                    artifact_type=artifact_type,
                    local_path=local_path,
                    error=type(exc).__name__,
                )
                return None
    return None


def _artifact_root() -> _Path:
    return _Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).expanduser().resolve()


def _resolve_artifact_path(local_path: str | None) -> _Path:
    if not local_path:
        raise HTTPException(status_code=404, detail="本地产物不存在")

    root = _artifact_root()
    candidate = _Path(local_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()

    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=403, detail="产物路径不在允许目录内")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="本地产物不存在")
    return resolved


def _resolve_artifact_path_or_none(local_path: str | None) -> _Path | None:
    try:
        return _resolve_artifact_path(local_path)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _read_artifact_object_text(artifact: dict) -> str:
    bucket = artifact.get("bucket") or os.getenv("MINIO_BUCKET", "mini-drop")
    key = _validate_presign_request(bucket, artifact.get("object_key", ""))
    try:
        return store.read_object_bytes(bucket, key).decode("utf-8", errors="replace")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log_event("warning", "artifact_object_read_failed", bucket=bucket, object_key=key, error=type(exc).__name__)
        raise HTTPException(status_code=404, detail="对象存储产物不存在") from exc


def _validate_presign_request(bucket: str, key: str) -> str:
    allowed_bucket = os.getenv("MINIO_BUCKET", "mini-drop")
    if bucket != allowed_bucket:
        raise HTTPException(status_code=403, detail="bucket 不在允许范围内")
    if not key:
        raise HTTPException(status_code=400, detail="key 参数不能为空")
    normalized = key.replace("\\", "/")
    if normalized.startswith("/") or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise HTTPException(status_code=400, detail="key 路径不合法")
    if not normalized.startswith("tasks/"):
        raise HTTPException(status_code=403, detail="key 不在任务产物目录内")
    return normalized


def _safe_download_filename(value: str) -> str:
    filename = _Path(value.replace("\\", "/")).name
    filename = "".join(ch for ch in filename if ch >= " " and ch not in {'"', ';'})
    return filename[:255] or "artifact.bin"


# ── NLP 自然语言采集 ────────────────────────────────────────────


@app.post("/api/nlp/parse")
def nlp_parse_intent(body: dict) -> APIResponse:
    """将用户自然语言描述解析为结构化任务参数。"""
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    if len(query) > 500:
        raise HTTPException(status_code=400, detail="query 不能超过 500 字符")

    intent = parse_intent(query)
    candidates = [] if intent.target_pid else resolve_pid(intent.process_name)

    return APIResponse(data={
        "process_name": intent.process_name,
        "selected_pid": intent.target_pid,
        "collector_type": intent.collector_type,
        "duration_sec": intent.duration_sec,
        "sample_rate": intent.sample_rate,
        "reasoning": intent.reasoning,
        "candidate_pids": [c.to_dict() for c in candidates],
    })


@app.post("/api/nlp/summarize")
def nlp_summarize_task(body: dict) -> APIResponse:
    """对已完成任务的结果进行 AI 总结并生成追问建议。"""
    task_id = body.get("task_id", "")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")

    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    artifacts = repo.artifacts.get(task_id, [])
    top_functions = _extract_artifact_json(artifacts, "top_json") or []
    ebpf_metrics = _extract_artifact_json(artifacts, "ebpf_metrics")
    suggestions = []

    # 从 top_functions 中提取提示
    for func in top_functions[:5]:
        name = func.get("name", "").lower()
        if "fib" in name:
            suggestions.append("检测到递归 Fibonacci 热点，建议改用迭代 + 记忆化或查表法替代")
        elif "sort" in name:
            suggestions.append("排序开销较高，检查数据集大小，考虑原地排序或基数排序替代")
        elif "json" in name:
            suggestions.append("JSON 编解码占用 CPU 显著，检查是否存在不必要的重复序列化")
        elif "malloc" in name:
            suggestions.append("malloc 调用频繁，考虑使用内存池或 jemalloc 分配器")

    summary = summarize(top_functions, list(set(suggestions))[:3])
    collector = task.collector_type if hasattr(task, "collector_type") else "perf_cpu"
    questions = suggest_followup(top_functions, collector, ebpf_metrics)

    return APIResponse(data={
        "task_id": task_id,
        "summary": summary,
        "followup_questions": questions,
    })


# ── 启动入口 ──────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVER_PORT", "8191")),
    )
