"""通过官方 AgentScope 的单一工具生成有界 CRM 用例选择计划。

真实模型只接收场景 ID/分类，拿不到 CRM 源码、业务记录、固定 oracle 或身份。
密钥仅从显式文件参数读取，模型端点、轮数、工具数、超时和网络策略均不可由模型修改。
这不是性能采集规划，不复用 propose_collection 的语义或权限。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

from contract import (
    canonical,
    digest,
    load_plan,
    plan_schema,
    planner_context,
    reject_duplicate_keys,
    validate_plan,
)
from run import clean_environment, emit

SDK_VERSION = "2.0.10"
ENDPOINT = "https://api.deepseek.com"
MODELS = ("deepseek-flash", "deepseek-chat")


class PlanFailure(Exception):
    """只携带固定错误码与安全计数，不包含供应商响应、参数、凭据或异常文本。"""

    def __init__(self, code, receipt=None):
        super().__init__(code)
        self.code = code
        self.receipt = receipt


class AcceptedPlan(BaseException):
    def __init__(self, value, channel):
        self.value = value
        self.channel = channel


def strict_plan(raw: str):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16_384:
        raise PlanFailure("PLAN_OUTPUT_SIZE_INVALID")
    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
        validate_plan(value)
    except (ValueError, TypeError):
        raise PlanFailure("PLAN_OUTPUT_REJECTED") from None
    return value


async def generate_plan(
    key_file: Path, model_name="deepseek-flash", *, transport=None, timeout_seconds=60
):
    """transport 只用于离线回归注入，CLI 无自定义端点、TLS 或预算开关。"""
    if model_name not in MODELS:
        raise PlanFailure("PLAN_MODEL_REJECTED")
    installed = importlib.metadata.version("agentscope")
    if installed != SDK_VERSION:
        raise PlanFailure("PLAN_SDK_VERSION_MISMATCH")

    import httpx2 as httpx
    from agentscope.agent import (
        Agent,
        ContextConfig,
        InjectionConfig,
        ModelConfig,
        ReActConfig,
    )
    from agentscope.credential import DeepSeekCredential
    from agentscope.message import TextBlock, ToolCallBlock, UserMsg
    from agentscope.middleware import MiddlewareBase
    from agentscope.model import DeepSeekChatModel
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import ToolBase, Toolkit

    # 复用已审阅的有界传输和密钥读取；不复用性能诊断工具/业务阶段。
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "agent_runtime"))
    from drop_agent_runtime.config import Config
    from drop_agent_runtime.model_transport import BoundedTransport
    from drop_agent_runtime.provider_retry import RetryableProviderFailure

    stats = {
        "model_calls": 0,
        "provider_requests": 0,
        "tool_calls": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "usage_reports": 0,
    }
    started = time.monotonic()

    class CountingTransport(httpx.AsyncBaseTransport):
        def __init__(self, inner):
            self.inner = inner

        async def handle_async_request(self, request):
            url = request.url
            if (
                url.scheme != "https"
                or url.host != "api.deepseek.com"
                or url.port not in {None, 443}
                or url.path not in {"/chat/completions", "/v1/chat/completions"}
                or url.query
                or url.fragment
                or url.username
                or url.password
                or request.method != "POST"
            ):
                raise PlanFailure("PLAN_ENDPOINT_REJECTED")
            if stats["provider_requests"] >= 3:
                raise PlanFailure("PLAN_MODEL_BUDGET_EXCEEDED")
            stats["provider_requests"] += 1
            return await self.inner.handle_async_request(request)

        async def aclose(self):
            await self.inner.aclose()

    class SelectCases(ToolBase):
        name = "select_test_cases"
        description = "选择 CRM 联系人测试场景 ID。只能提交已给目录里的 ID；固定安全回归会自动补入。不能提供 URL、代码、断言或身份。"
        input_schema = plan_schema()
        is_read_only = True
        is_concurrency_safe = False

        async def check_permissions(self, tool_input, context):
            return PermissionDecision(
                PermissionBehavior.ALLOW, "Only bounded local case IDs are accepted."
            )

        async def call(self, **kwargs):
            validate_plan(kwargs)
            if stats["tool_calls"] >= 1:
                raise PlanFailure("PLAN_TOOL_BUDGET_EXCEEDED")
            stats["tool_calls"] += 1
            raise AcceptedPlan(kwargs, "native_select_test_cases_tool")

    class Guard(MiddlewareBase):
        async def on_compress_context(self, agent, input_kwargs, next_handler):
            return None

        async def on_model_call(self, agent, input_kwargs, next_handler):
            if stats["model_calls"] >= 3:
                raise PlanFailure("PLAN_MODEL_BUDGET_EXCEEDED")
            stats["model_calls"] += 1
            response = await next_handler(**input_kwargs)
            if not response.is_last:
                raise PlanFailure("PLAN_STREAM_RESPONSE_REJECTED")
            if response.usage is not None:
                for name in ("input_tokens", "output_tokens"):
                    count = getattr(response.usage, name)
                    if type(count) is not int or not 0 <= count <= 10_000_000:
                        raise PlanFailure("PLAN_USAGE_INVALID")
                    stats["usage"][name] += count
                stats["usage_reports"] += 1
            calls = [
                item for item in response.content if isinstance(item, ToolCallBlock)
            ]
            if len(calls) > 1 or any(
                call.name != "select_test_cases" for call in calls
            ):
                raise PlanFailure("PLAN_TOOL_REJECTED")
            for call in calls:
                strict_plan(call.input)
            # 不保存/转交隐藏推理，输出正文也有独立上限。
            response.content = [
                item
                for item in response.content
                if isinstance(item, (TextBlock, ToolCallBlock))
            ]
            if (
                sum(
                    len(item.text.encode("utf-8"))
                    for item in response.content
                    if isinstance(item, TextBlock)
                )
                > 16_384
            ):
                raise PlanFailure("PLAN_OUTPUT_SIZE_INVALID")
            return response

    def receipt(success, **extra):
        return {
            "schema_version": 1,
            "success": success,
            "framework": "agentscope",
            "framework_version": installed,
            "agent_class": "agentscope.agent.Agent",
            "model": model_name,
            "endpoint_origin": ENDPOINT,
            "model_calls": stats["model_calls"],
            "provider_requests": stats["provider_requests"],
            "tool_calls": stats["tool_calls"],
            "usage": stats["usage"]
            if stats["model_calls"] > 0
            and stats["usage_reports"] == stats["model_calls"]
            else None,
            "cost": None,
            "maximum_model_calls": 3,
            "maximum_tool_calls": 1,
            "timeout_seconds": 60,
            "sdk_and_http_retries": 0,
            "tls_verification": True,
            "proxy_environment_used": False,
            "source_or_oracle_sent_to_model": False,
            "context_sha256": digest(planner_context()),
            "schema_sha256": digest(plan_schema()),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "fixture_provider": transport is not None,
            **extra,
        }

    try:
        config = Config("", "", 0, ENDPOINT, model_name, str(key_file))
        async with httpx.AsyncClient(
            transport=BoundedTransport(
                CountingTransport(
                    transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False)
                )
            ),
            timeout=httpx.Timeout(60, connect=10),
            trust_env=False,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        ) as client:
            model = DeepSeekChatModel(
                credential=DeepSeekCredential(
                    api_key=config.read_api_key(), base_url=ENDPOINT
                ),
                model=model_name,
                parameters=DeepSeekChatModel.Parameters(
                    thinking_enable=False, temperature=0, max_tokens=1500
                ),
                stream=False,
                max_retries=0,
                client_kwargs={"max_retries": 0, "http_client": client},
            )
            agent = Agent(
                name="DropBusinessTestPlanner",
                system_prompt=(
                    "你是 CRM 接口测试计划选择器。只基于提供的场景 ID 和分类进行有界测试选择。"
                    "优先覆盖边界、异常、权限、租户和状态序列。调用唯一工具 select_test_cases 提交计划。"
                    "如果不用工具，只能输出符合 schema 的纯 JSON；不得输出 Markdown、任意代码、URL、断言或身份。"
                ),
                model=model,
                toolkit=Toolkit(tools=[SelectCases()]),
                middlewares=[Guard()],
                model_config=ModelConfig(max_retries=0),
                react_config=ReActConfig(
                    max_iters=3, interruption_raise_cancelled_error=True
                ),
                context_config=ContextConfig(
                    compression_tool_enabled=False, tool_result_limit=16_384
                ),
                injection_config=InjectionConfig(inject_runtime_state=False),
            )
            try:
                async with asyncio.timeout(timeout_seconds):
                    final = await agent.reply(
                        [
                            UserMsg(
                                name="user",
                                content=canonical(planner_context()).decode("utf-8"),
                            )
                        ]
                    )
                    if final.finished_reason != "completed":
                        raise PlanFailure("PLAN_MODEL_BUDGET_EXCEEDED")
                    value = strict_plan(final.get_text_content())
                    channel = "validated_pure_json"
            except AcceptedPlan as accepted:
                value, channel = accepted.value, accepted.channel
            finally:
                await model.client.close()
    except PlanFailure as failure:
        raise PlanFailure(
            failure.code, receipt(False, error_code=failure.code)
        ) from None
    except RetryableProviderFailure:
        # 公共 runtime 用 BaseException 发出重试信号；本工具明确不重试，只留下安全失败回执。
        raise PlanFailure(
            "PLAN_PROVIDER_FAILED_NO_RETRY",
            receipt(False, error_code="PLAN_PROVIDER_FAILED_NO_RETRY"),
        ) from None
    except Exception:  # noqa: BLE001 -- 固定失败码，不能打印供应商/密钥异常的原始文本。
        raise PlanFailure(
            "PLAN_PROVIDER_OR_RUNTIME_FAILED",
            receipt(False, error_code="PLAN_PROVIDER_OR_RUNTIME_FAILED"),
        ) from None
    selected = validate_plan(value)
    return value, receipt(
        True,
        acceptance_channel=channel,
        plan_sha256=digest(value),
        selected_case_count=len(selected),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--key-file",
        required=True,
        type=Path,
        help="只读密钥文件，最多 4096 字节；不会打印",
    )
    parser.add_argument("--model", choices=MODELS, default="deepseek-flash")
    parser.add_argument(
        "--output", required=True, type=Path, help="新的计划/模型回执目录"
    )
    args = parser.parse_args(argv)
    output = args.output.absolute()
    if output.exists() or any(path.is_symlink() for path in (output, *output.parents)):
        parser.error("输出必须是新的非符号链接目录")
    output.mkdir(parents=True)
    logging.disable(logging.CRITICAL)
    try:
        with (
            patch.dict(os.environ, clean_environment(), clear=True),
            open(os.devnull, "w", encoding="utf-8") as sink,
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            value, receipt = asyncio.run(generate_plan(args.key_file, args.model))
        emit(output / "plan.json", value)
        # 真实模型输出仍必须通过执行器同一个文件解析/校验入口，而不是另造宽松路径。
        selected, _provenance = load_plan(output / "plan.json")
        receipt["selected_case_count"] = len(selected)
        receipt["plan_file_sha256"] = hashlib.sha256(
            (output / "plan.json").read_bytes()
        ).hexdigest()
    except PlanFailure as failure:
        receipt = failure.receipt or {
            "schema_version": 1,
            "success": False,
            "error_code": failure.code,
        }
    except Exception:  # noqa: BLE001 -- CLI 最外层也只输出安全固定码。
        receipt = {
            "schema_version": 1,
            "success": False,
            "error_code": "PLAN_SETUP_FAILED",
        }
    emit(output / "model-receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
