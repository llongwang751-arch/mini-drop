"""LangChain/LangGraph runtime for bounded Mini-Drop diagnosis planning.

The market framework owns the model/tool loop, checkpointing and thread memory.
Mini-Drop keeps authority over process binding, policy, Task execution, evidence
admission and Skill publication.  The model can only request a registered probe;
it never receives a shell or collector execution primitive.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelRequest,
    SummarizationMiddleware,
    dynamic_prompt,
)
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.app.ai_provider import AISettings
from server.app.logging_utils import log_event


AGENT_FRAMEWORK = "langchain-create-agent/langgraph"
AGENT_VERSION = "diagnosis-agent-v1"
_COLLECTION_FAILURE_MARKERS = (
    "采集失败",
    "工具失败",
    "权限不足",
    "无法附加",
    "连接失败",
    "agent 离线",
    "agent offline",
    "timeout",
    "timed out",
    "超时",
)


@dataclass(frozen=True)
class DiagnosisAgentContext:
    diagnosis_id: str
    query: str
    target: dict[str, Any]
    category: str
    rule_plan: dict[str, Any]
    allowed_tools: tuple[str, ...]
    prior_hypotheses: tuple[dict[str, Any], ...] = ()
    evidence_summary: tuple[dict[str, Any], ...] = ()
    user_correction: str = ""
    active_skill: dict[str, Any] | None = None
    route_priors: tuple[dict[str, Any], ...] = ()


class AgentHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=3, max_length=1000)
    expected_observations: list[str] = Field(min_length=1, max_length=8)
    falsification_criteria: list[str] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=3, max_length=1000)

    @field_validator("statement", "rationale")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("expected_observations", "falsification_criteria")
    @classmethod
    def _clean_items(cls, values: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if not cleaned:
            raise ValueError("at least one non-empty criterion is required")
        return cleaned


class DiagnosticProbeRequest(BaseModel):
    """Only side effect the model may request from the diagnosis Agent."""

    model_config = ConfigDict(extra="forbid")

    reasoning_summary: str = Field(min_length=3, max_length=1200)
    tool_name: str = Field(min_length=3, max_length=128)
    hypotheses: list[AgentHypothesis] = Field(min_length=1, max_length=3)


@tool(
    "request_diagnostic_probe",
    return_direct=True,
    description=(
        "Request exactly one registered Mini-Drop diagnostic probe. Supply one "
        "to three falsifiable hypotheses. This records a proposal only; policy "
        "and human approval happen before the C++ Agent executes anything."
    ),
)
def request_diagnostic_probe(
    reasoning_summary: str,
    tool_name: str,
    hypotheses: list[AgentHypothesis],
    runtime: ToolRuntime[DiagnosisAgentContext],
) -> str:
    context = runtime.context
    if tool_name not in context.allowed_tools:
        return json.dumps(
            {
                "accepted": False,
                "reason": "tool is outside the server supplied allowlist",
                "allowed_tools": list(context.allowed_tools),
            },
            ensure_ascii=False,
        )
    request = DiagnosticProbeRequest.model_validate(
        {
            "reasoning_summary": reasoning_summary,
            "tool_name": tool_name,
            "hypotheses": hypotheses,
        }
    )
    return json.dumps(
        {"accepted": True, "proposal": request.model_dump(mode="json")},
        ensure_ascii=False,
    )


@dynamic_prompt
def _diagnosis_system_prompt(request: ModelRequest) -> str:
    context: DiagnosisAgentContext = request.runtime.context
    trusted = {
        "diagnosis_id": context.diagnosis_id,
        "target": context.target,
        "category": context.category,
        "rule_baseline": context.rule_plan,
        "allowed_tools": list(context.allowed_tools),
        "prior_hypotheses": list(context.prior_hypotheses)[-10:],
        "evidence_summary": list(context.evidence_summary)[-20:],
        "user_correction": context.user_correction,
        "active_skill": context.active_skill,
        "historical_successful_routes": list(context.route_priors)[-5:],
    }
    return (
        "你是 Mini-Drop 性能诊断 Agent。你必须基于可信范围、已有证据、"
        "规则基线和已发布 Skill 选择下一步取证动作。\n"
        "必须调用 request_diagnostic_probe，且只能选择 allowed_tools 中的一个工具。\n"
        "每个假设必须同时包含支持条件和可推翻它的反证条件。\n"
        "Skill 只是路线先验，不能把旧根因当作本次结论；本次必须重新取证。\n"
        "禁止输出或拼接命令，禁止修改 PID、主机或时间窗，禁止绕过审批、"
        "风险预算、证据门禁，禁止把采集失败当作反证。\n"
        "采集失败、权限不足、Agent 离线或超时只能记为 UNKNOWN，绝不能写入"
        " falsification_criteria。\n"
        "只给简短可展示的 reasoning_summary，不输出隐藏思维过程。\n"
        "以下 JSON 是服务端提供的可信诊断上下文：\n"
        + json.dumps(trusted, ensure_ascii=False, separators=(",", ":"))
    )


_CHECKPOINTER_LOCK = threading.Lock()
_CHECKPOINTER: Any | None = None
_CHECKPOINTER_CONTEXT: Any | None = None
_AGENT_LOCK = threading.Lock()
_AGENTS: dict[tuple[str, str, str, int, int], Any] = {}


def _database_url_for_psycopg() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _close_postgres_checkpointer() -> None:
    global _CHECKPOINTER_CONTEXT
    context = _CHECKPOINTER_CONTEXT
    _CHECKPOINTER_CONTEXT = None
    if context is not None:
        try:
            context.__exit__(None, None, None)
        except Exception:
            pass


def _get_checkpointer() -> Any:
    global _CHECKPOINTER, _CHECKPOINTER_CONTEXT
    if _CHECKPOINTER is not None:
        return _CHECKPOINTER
    with _CHECKPOINTER_LOCK:
        if _CHECKPOINTER is not None:
            return _CHECKPOINTER
        backend = os.getenv("MINI_DROP_AGENT_CHECKPOINT_BACKEND", "memory").strip().lower()
        database_url = _database_url_for_psycopg()
        if backend == "postgres" and database_url.startswith("postgresql://"):
            try:
                from langgraph.checkpoint.postgres import PostgresSaver

                context = PostgresSaver.from_conn_string(database_url)
                saver = context.__enter__()
                saver.setup()
                _CHECKPOINTER_CONTEXT = context
                _CHECKPOINTER = saver
                atexit.register(_close_postgres_checkpointer)
                return saver
            except Exception as exc:
                log_event(
                    "error",
                    "diagnosis_agent_postgres_checkpoint_unavailable",
                    error=type(exc).__name__,
                    message=str(exc),
                )
        _CHECKPOINTER = InMemorySaver()
        return _CHECKPOINTER


def _agent_for(settings: AISettings) -> Any:
    checkpointer = _get_checkpointer()
    max_messages = min(
        max(int(os.getenv("MINI_DROP_AGENT_MEMORY_MAX_MESSAGES", "24")), 8),
        80,
    )
    key_fingerprint = hashlib.sha256(settings.api_key.encode("utf-8")).hexdigest()[:12]
    cache_key = (
        settings.model,
        settings.base_url,
        key_fingerprint,
        id(checkpointer),
        max_messages,
    )
    cached = _AGENTS.get(cache_key)
    if cached is not None:
        return cached
    with _AGENT_LOCK:
        cached = _AGENTS.get(cache_key)
        if cached is not None:
            return cached
        from langchain_deepseek import ChatDeepSeek

        model = ChatDeepSeek(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=0.1,
            max_tokens=1400,
            timeout=30,
            max_retries=0,
        )
        agent = create_agent(
            model=model,
            tools=[request_diagnostic_probe],
            middleware=[
                _diagnosis_system_prompt,
                SummarizationMiddleware(
                    model=model,
                    trigger=("messages", max_messages),
                    keep=("messages", max(6, max_messages // 2)),
                ),
            ],
            context_schema=DiagnosisAgentContext,
            checkpointer=checkpointer,
            name="mini_drop_diagnosis_agent",
        )
        _AGENTS[cache_key] = agent
        return agent


def _accepted_tool_payload(messages: list[Any]) -> dict[str, Any] | None:
    accepted = False
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "request_diagnostic_probe":
            try:
                payload = json.loads(str(message.content))
            except (TypeError, ValueError):
                return None
            accepted = payload.get("accepted") is True
            break
    if not accepted:
        return None
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in reversed(message.tool_calls or []):
            if call.get("name") != "request_diagnostic_probe":
                continue
            try:
                proposal = DiagnosticProbeRequest.model_validate(
                    call.get("args") or {}
                ).model_dump(mode="json")
                counters = (
                    criterion.casefold()
                    for hypothesis in proposal["hypotheses"]
                    for criterion in hypothesis["falsification_criteria"]
                )
                if any(
                    marker in criterion
                    for criterion in counters
                    for marker in _COLLECTION_FAILURE_MARKERS
                ):
                    return None
                return proposal
            except (TypeError, ValueError):
                return None
    return None


def plan_with_diagnosis_agent(
    context: DiagnosisAgentContext,
    settings: AISettings,
) -> dict[str, Any] | None:
    """Run one bounded Agent turn and return a validated probe proposal."""

    if not context.diagnosis_id or not context.allowed_tools:
        return None
    agent = _agent_for(settings)
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "<untrusted_user_problem>\n"
                        + context.query
                        + "\n</untrusted_user_problem>"
                    ),
                }
            ]
        },
        config={
            "configurable": {
                "thread_id": context.diagnosis_id,
                "checkpoint_ns": AGENT_VERSION,
            },
            "recursion_limit": 8,
            "tags": ["mini-drop", "diagnosis-agent", context.category],
        },
        context=context,
    )
    proposal = _accepted_tool_payload(list(result.get("messages") or []))
    if proposal is None or proposal["tool_name"] not in context.allowed_tools:
        return None
    return {
        **proposal,
        "agent_framework": AGENT_FRAMEWORK,
        "agent_version": AGENT_VERSION,
        "checkpoint_backend": os.getenv(
            "MINI_DROP_AGENT_CHECKPOINT_BACKEND", "memory"
        ).strip().lower(),
    }


def reset_agent_runtime_for_tests() -> None:
    global _CHECKPOINTER
    _close_postgres_checkpointer()
    with _CHECKPOINTER_LOCK:
        _CHECKPOINTER = None
    with _AGENT_LOCK:
        _AGENTS.clear()
