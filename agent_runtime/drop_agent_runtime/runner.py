"""Actual AgentScope 2.x ReAct loop with Go-owned tool capabilities."""

import asyncio
import contextvars
import logging
import re
import time
import uuid
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import httpx2 as httpx
import jsonschema
import openai
from agentscope.agent import (
    Agent,
    ContextConfig,
    InjectionConfig,
    ModelConfig,
    ReActConfig,
)
from agentscope.credential import DeepSeekCredential
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.middleware import MiddlewareBase
from agentscope.model import DeepSeekChatModel, FinishedReason
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolBase, ToolChoice, ToolChunk, Toolkit
from pydantic import ValidationError

from . import AGENTSCOPE_VERSION, PROTOCOL_VERSION
from .config import Config
from .model_transport import BoundedTransport
from .wire import (
    Gateway,
    ProtocolError,
    RunDiagnostics,
    current_diagnostics,
    json_bytes,
    set_stage,
    strict_json,
)


class SafeRuntimeFailure(ProtocolError):
    """Fixed private diagnostics, never exception text, class names or values."""

    def __init__(self, failure: Exception, diagnostics: RunDiagnostics) -> None:
        cause: BaseException | None = failure
        protocol = None
        for _ in range(6):
            if isinstance(cause, ProtocolError):
                protocol = cause
                break
            if cause is None:
                break
            cause = cause.__cause__ or cause.__context__
        if protocol is not None:
            code, kind = protocol.code, "protocol"
        elif isinstance(
            failure, (TimeoutError, httpx.TimeoutException, openai.APITimeoutError)
        ):
            code, kind = "AGENT_RUNTIME_FAILED", "timeout"
        elif isinstance(failure, (httpx.RequestError, openai.APIConnectionError)):
            code, kind = "AGENT_RUNTIME_FAILED", "transport"
        elif isinstance(
            failure,
            (ValidationError, jsonschema.ValidationError, jsonschema.SchemaError),
        ):
            code, kind = "AGENT_RUNTIME_FAILED", "validation"
        elif isinstance(failure, openai.OpenAIError):
            code, kind = "AGENT_RUNTIME_FAILED", "sdk"
        else:
            code, kind = "AGENT_RUNTIME_FAILED", "internal"
        super().__init__(code)
        self.diagnostics = {
            "stage": diagnostics.stage,
            "exception_kind": kind,
            "model_calls": diagnostics.model_calls,
            "tool_calls": diagnostics.tool_calls,
        }


# Some SDK exception paths include tool arguments. This service emits only
# fixed application error codes, never library request/content logging.
for _logger_name in ("as", "openai", "httpx", "httpx2", "httpcore", "httpcore2"):
    _logger = logging.getLogger(_logger_name)
    _logger.handlers.clear()
    _logger.addHandler(logging.NullHandler())
    _logger.propagate = False
    _logger.disabled = True


PHASES = {
    "planning": (6, 10, 150000, {"list_collectors", "propose_collection"}),
    "diagnosis": (
        5,
        7,
        150000,
        {
            "read_profile_summary",
            "inspect_hotspots",
            "check_collection_quality",
            "inspect_call_paths",
            "propose_verification",
            "compare_profiles",
        },
    ),
    "repair": (8, 12, 420000, {"profile_business_flow", "read_source", "test_patch"}),
}
CALL_ID = re.compile(r"^[^\x00-\x20\x7f]{1,128}$")
_current_call: contextvars.ContextVar[ToolCallBlock] = contextvars.ContextVar(
    "drop_tool_call"
)


class AcceptedResult(BaseException):
    """Stop the framework immediately after a terminal Go-owned plan acceptance."""

    def __init__(self, result: Any) -> None:
        self.result = result


@dataclass(frozen=True)
class RunRequest:
    run_id: str
    capability: str
    phase: str
    system_prompt: str
    messages: list[dict]
    tools: list[dict]
    budget: dict

    @classmethod
    def parse(cls, body: Any, model: str) -> "RunRequest":
        fields = {
            "version",
            "run_id",
            "capability",
            "phase",
            "model",
            "system_prompt",
            "messages",
            "tools",
            "budget",
        }
        if (
            not isinstance(body, dict)
            or set(body) != fields
            or body["version"] != 1
            or type(body["version"]) is not int
        ):
            raise ProtocolError()
        if body["model"] != model:
            raise ProtocolError("AI_CONFIGURATION_ERROR")
        run_id, capability, phase = body["run_id"], body["capability"], body["phase"]
        try:
            if not isinstance(run_id, str) or str(uuid.UUID(run_id)) != run_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ProtocolError() from None
        if (
            not isinstance(capability, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", capability)
            or capability == run_id
        ):
            raise ProtocolError()
        if phase not in PHASES:
            raise ProtocolError()
        if (
            not isinstance(body["system_prompt"], str)
            or not 1 <= len(body["system_prompt"].encode()) <= 32768
        ):
            raise ProtocolError()
        messages = body["messages"]
        if not isinstance(messages, list) or not 1 <= len(messages) <= 8:
            raise ProtocolError()
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message["role"] != "user"
                or not isinstance(message["content"], str)
                or len(message["content"].encode()) > 131072
            ):
                raise ProtocolError()
        max_models, max_tools, max_ms, allowed = PHASES[phase]
        budget = body["budget"]
        if not isinstance(budget, dict) or set(budget) != {
            "max_model_calls",
            "max_tool_calls",
            "max_tools_per_round",
            "timeout_ms",
        }:
            raise ProtocolError()
        for key, maximum in (
            ("max_model_calls", max_models),
            ("max_tool_calls", max_tools),
            ("max_tools_per_round", 2),
            ("timeout_ms", max_ms),
        ):
            if type(budget[key]) is not int or not 1 <= budget[key] <= maximum:
                raise ProtocolError()
        tools = body["tools"]
        if not isinstance(tools, list) or not 1 <= len(tools) <= len(allowed):
            raise ProtocolError()
        names = set()
        for definition in tools:
            if (
                not isinstance(definition, dict)
                or set(definition) != {"type", "function"}
                or definition["type"] != "function"
            ):
                raise ProtocolError()
            function = definition["function"]
            if not isinstance(function, dict) or set(function) != {
                "name",
                "description",
                "parameters",
            }:
                raise ProtocolError()
            name = function["name"]
            if (
                not isinstance(name, str)
                or name not in allowed
                or name in names
                or not isinstance(function["description"], str)
                or len(function["description"].encode()) > 4096
            ):
                raise ProtocolError("AI_TOOL_REJECTED")
            schema = function["parameters"]
            if (
                not isinstance(schema, dict)
                or schema.get("type") != "object"
                or schema.get("additionalProperties") is not False
            ):
                raise ProtocolError("AI_TOOL_REJECTED")

            # Schema references could trigger network retrieval; Go currently
            # sends entirely inline, bounded business schemas.
            def reject_refs(value: Any) -> None:
                if isinstance(value, dict):
                    if any(
                        key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")
                    ):
                        raise ProtocolError("AI_TOOL_REJECTED")
                    for item in value.values():
                        reject_refs(item)
                elif isinstance(value, list):
                    for item in value:
                        reject_refs(item)

            reject_refs(schema)
            try:
                jsonschema.Draft202012Validator.check_schema(schema)
            except jsonschema.SchemaError:
                raise ProtocolError("AI_TOOL_REJECTED") from None
            names.add(name)
        return cls(
            run_id, capability, phase, body["system_prompt"], messages, tools, budget
        )


class Session:
    def __init__(self, request: RunRequest, gateway: Gateway) -> None:
        self.request = request
        self.gateway = gateway
        self.model_calls = 0
        self.tool_calls = 0
        self.round_tools = 0
        self.seen_ids: set[str] = set()
        self.format_only = False
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.tools: dict[str, GatewayTool] = {}
        self.cancelled = False
        self.failure: ProtocolError | None = None

    def check_active(self) -> None:
        if self.cancelled or self.gateway.cancelled:
            raise asyncio.CancelledError()
        if self.failure is not None:
            raise self.failure

    async def callback(self, kind: str, **fields: Any) -> dict:
        self.check_active()
        set_stage(
            {
                "model_start": "model_start",
                "model_end": "model_end",
                "tool": "tool_callback",
                "finish": "finish_callback",
            }[kind]
        )
        payload = {
            "type": kind,
            "round": self.model_calls,
            "call_id": str(uuid.uuid4()),
            **fields,
        }
        try:
            response = await self.gateway.call(payload)
        except ProtocolError as failure:
            self.failure = failure
            raise
        self.check_active()
        if response.get("ok") is not True and (
            response.get("fatal", True) or kind in ("model_start", "model_end")
        ):
            code = response.get("error_code", "AGENT_RUNTIME_GATEWAY_REJECTED")
            if not isinstance(code, str) or not re.fullmatch(
                r"[A-Z][A-Z0-9_]{1,79}", code
            ):
                code = "AGENT_RUNTIME_GATEWAY_REJECTED"
            self.failure = ProtocolError(code)
            raise self.failure
        return response


class GatewayTool(ToolBase):
    is_concurrency_safe = False
    is_read_only = True

    def __init__(self, function: dict, session: Session) -> None:
        super().__init__()
        self.name = function["name"]
        self.description = function["description"]
        self.input_schema = function["parameters"]
        self.session = session

    async def check_permissions(
        self, tool_input: dict, context: Any
    ) -> PermissionDecision:
        return PermissionDecision(
            PermissionBehavior.ALLOW,
            "Execution is delegated to Go's capability gateway.",
        )

    async def call(self, **kwargs: Any) -> ToolChunk:
        session = self.session
        session.check_active()
        call = _current_call.get()
        if (
            call.name != self.name
            or call.id in session.seen_ids
            or not CALL_ID.fullmatch(call.id)
            or session.format_only
        ):
            raise ProtocolError("AI_TOOL_REJECTED")
        if (
            session.tool_calls >= session.request.budget["max_tool_calls"]
            or session.round_tools >= session.request.budget["max_tools_per_round"]
        ):
            raise ProtocolError("AI_BUDGET_EXCEEDED")
        session.seen_ids.add(call.id)
        session.tool_calls += 1
        diagnostics = current_diagnostics.get()
        if diagnostics is not None:
            diagnostics.tool_calls = session.tool_calls
        session.round_tools += 1
        response = await session.callback(
            "tool", call_id=call.id, name=self.name, arguments=kwargs
        )
        if response.get("terminal") is True:
            if (
                session.request.phase != "planning"
                or self.name != "propose_collection"
                or response.get("ok") is not True
                or "result" not in response
            ):
                raise ProtocolError()
            raise AcceptedResult(response["result"])
        observation = response.get("observation")
        if observation is None:
            observation = {"error_code": response.get("error_code", "AI_TOOL_REJECTED")}
        set_stage("agent_reply")
        return ToolChunk(
            content=[TextBlock(text=json_bytes(observation).decode())],
            state=ToolResultState.SUCCESS
            if response.get("ok") is True
            else ToolResultState.ERROR,
        )


class RuntimeMiddleware(MiddlewareBase):
    def __init__(self, session: Session) -> None:
        self.session = session

    async def on_compress_context(
        self, agent: Agent, input_kwargs: dict, next_handler: Any
    ) -> None:
        # The SDK invokes this hook every iteration, even below its threshold.
        # Deliberately skip its autonomous model-driven compression; transport
        # and phase limits reject oversized input instead.
        return None

    async def on_model_call(
        self, agent: Agent, input_kwargs: dict, next_handler: Any
    ) -> Any:
        session = self.session
        session.check_active()
        if session.model_calls >= session.request.budget["max_model_calls"]:
            raise ProtocolError("AI_BUDGET_EXCEEDED")
        if (
            session.request.phase == "diagnosis"
            and session.model_calls >= 4
            and not session.format_only
        ):
            raise ProtocolError("AI_BUDGET_EXCEEDED")
        session.model_calls += 1
        diagnostics = current_diagnostics.get()
        if diagnostics is not None:
            diagnostics.model_calls = session.model_calls
        session.round_tools = 0
        await session.callback("model_start")
        if session.format_only:
            input_kwargs = {
                **input_kwargs,
                "tools": [],
                "tool_choice": ToolChoice(mode="none"),
            }
        set_stage("provider_request")
        response = await next_handler(**input_kwargs)
        set_stage("model_parse")
        session.check_active()
        if response.finished_reason == FinishedReason.INTERRUPTED:
            raise asyncio.CancelledError()
        if not response.is_last:
            raise ProtocolError("AI_INVALID_RESPONSE")
        calls = [item for item in response.content if isinstance(item, ToolCallBlock)]
        if (session.format_only and calls) or len(calls) > session.request.budget[
            "max_tools_per_round"
        ]:
            raise ProtocolError("AI_BUDGET_EXCEEDED")
        batch_ids = set()
        for call in calls:
            if (
                not CALL_ID.fullmatch(call.id)
                or call.id in session.seen_ids
                or call.id in batch_ids
                or call.name not in session.tools
                or len(call.input.encode()) > 49152
            ):
                raise ProtocolError("AI_TOOL_REJECTED")
            arguments = strict_json(call.input)
            try:
                if not isinstance(arguments, dict):
                    raise ProtocolError("AI_TOOL_REJECTED")
                jsonschema.Draft202012Validator(
                    session.tools[call.name].input_schema
                ).validate(arguments)
            except jsonschema.ValidationError:
                raise ProtocolError("AI_TOOL_REJECTED") from None
            batch_ids.add(call.id)
        # Retain only visible text and tool calls; hidden thought is never
        # forwarded to Go, logs, persisted state, or a subsequent request.
        response.content = [
            item
            for item in response.content
            if isinstance(item, (TextBlock, ToolCallBlock))
        ]
        if (
            sum(
                len(item.text.encode())
                for item in response.content
                if isinstance(item, TextBlock)
            )
            > 49152
        ):
            raise ProtocolError("AI_INVALID_RESPONSE")
        usage = None
        if response.usage is not None:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            for key, count in usage.items():
                if type(count) is not int or not 0 <= count <= 10000000:
                    raise ProtocolError("AI_INVALID_RESPONSE")
                session.usage[key] += count
        await session.callback("model_end", **({"usage": usage} if usage else {}))
        set_stage("agent_reply")
        return response

    async def on_acting(
        self, agent: Agent, input_kwargs: dict, next_handler: Any
    ) -> Any:
        token = _current_call.set(input_kwargs["tool_call"])
        chunks = []
        try:
            async for chunk in next_handler():
                # Tools are non-streaming and responses are bounded. Drain
                # the SDK's final result before yielding: its interrupted
                # result otherwise makes Agent break with generators open.
                chunks.append(chunk)
        finally:
            _current_call.reset(token)
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            self.session.cancelled = True
            self.session.gateway.cancelled = True
            raise asyncio.CancelledError()
        self.session.check_active()
        for chunk in chunks:
            yield chunk


async def _run_agent(
    request: RunRequest,
    config: Config,
    gateway: Gateway,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    started = time.monotonic()
    installed = version("agentscope")
    if installed != AGENTSCOPE_VERSION:
        raise ProtocolError("AGENT_RUNTIME_VERSION_MISMATCH")
    session = Session(request, gateway)
    for definition in request.tools:
        tool = GatewayTool(definition["function"], session)
        session.tools[tool.name] = tool
    async with httpx.AsyncClient(
        transport=BoundedTransport(transport),
        timeout=httpx.Timeout(60, connect=10),
        headers={"Accept-Encoding": "identity"},
        trust_env=False,
        follow_redirects=False,
    ) as client:
        model = DeepSeekChatModel(
            credential=DeepSeekCredential(
                api_key=config.read_api_key(), base_url=config.endpoint
            ),
            model=config.model,
            parameters=DeepSeekChatModel.Parameters(
                thinking_enable=False,
                temperature=0,
                max_tokens=3000 if request.phase != "repair" else 5000,
            ),
            stream=False,
            max_retries=0,
            client_kwargs={"max_retries": 0, "http_client": client},
        )
        agent = Agent(
            name="Drop",
            system_prompt=request.system_prompt,
            model=model,
            toolkit=Toolkit(tools=list(session.tools.values())),
            middlewares=[RuntimeMiddleware(session)],
            model_config=ModelConfig(max_retries=0),
            react_config=ReActConfig(
                max_iters=request.budget["max_model_calls"],
                interruption_raise_cancelled_error=True,
            ),
            context_config=ContextConfig(
                compression_tool_enabled=False, tool_result_limit=65536
            ),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        # Resolve the actual imported class through its public module; fail
        # closed if SDK layout stops exporting the class we instantiated.
        import agentscope.agent as agent_module

        if type(agent) is not agent_module.Agent:
            raise ProtocolError("AGENT_RUNTIME_VERSION_MISMATCH")
        agent_class = f"{agent_module.__name__}.{type(agent).__name__}"
        inputs = [
            UserMsg(name="user", content=message["content"])
            for message in request.messages
        ]
        try:
            async with asyncio.timeout(request.budget["timeout_ms"] / 1000):
                while True:
                    set_stage("agent_reply")
                    final = await agent.reply(inputs)
                    session.check_active()
                    if final.finished_reason != "completed":
                        raise ProtocolError("AI_BUDGET_EXCEEDED")
                    content = final.get_text_content()
                    if not isinstance(content, str) or len(content.encode()) > 49152:
                        raise ProtocolError("AI_INVALID_RESPONSE")
                    response = await session.callback("finish", content=content)
                    if (
                        response.get("ok") is True
                        and response.get("accepted") is True
                        and "result" in response
                    ):
                        result = response["result"]
                        break
                    if session.model_calls >= request.budget["max_model_calls"]:
                        raise ProtocolError("AI_BUDGET_EXCEEDED")
                    if response.get("format_only") is True:
                        if request.phase != "diagnosis" or session.model_calls != 4:
                            raise ProtocolError("AGENT_RUNTIME_PROTOCOL_ERROR")
                        session.format_only = True
                    feedback = (
                        response.get("feedback")
                        or "The result was rejected by Go. Correct the JSON format and evidence references using only observed evidence."
                    )
                    if not isinstance(feedback, str) or len(feedback.encode()) > 4096:
                        raise ProtocolError()
                    inputs = [UserMsg(name="gateway", content=feedback)]
        except AcceptedResult as accepted:
            result = accepted.result
        except (asyncio.CancelledError, TimeoutError):
            session.cancelled = True
            gateway.cancelled = True
            raise
        finally:
            await model.client.close()
    set_stage("result_encode")
    runtime = {
        "framework": "agentscope",
        "framework_version": installed,
        "agent_class": agent_class,
        "run_id": request.run_id,
        "model_calls": session.model_calls,
        "tool_calls": session.tool_calls,
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        "usage": session.usage,
    }
    response = {
        "version": PROTOCOL_VERSION,
        "run_id": request.run_id,
        "result": result,
        "runtime": runtime,
    }
    if len(json_bytes(response)) > 131072:
        raise ProtocolError("AGENT_RUNTIME_BODY_TOO_LARGE")
    return response


async def run_agent(
    request: RunRequest,
    config: Config,
    gateway: Gateway,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    diagnostics = RunDiagnostics()
    token = current_diagnostics.set(diagnostics)
    try:
        return await _run_agent(request, config, gateway, transport=transport)
    except Exception as failure:  # noqa: BLE001 -- sanitized at the private control boundary.
        raise SafeRuntimeFailure(failure, diagnostics) from None
    finally:
        current_diagnostics.reset(token)
