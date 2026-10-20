"""在官方 SDK 将响应读入内存前限制模型传输。

该层约束请求与响应字节、编码及基本结构，避免仅在反序列化后才发现超大响应。失败向上游返回固定错误码，不泄露供应商响应。
"""

import httpx2 as httpx

from .wire import ProtocolError, set_stage, strict_json


class BoundedTransport(httpx.AsyncBaseTransport):
    """包裹官方 HTTP transport，在 SDK 解码前核对字节和响应结构；内部 transport 可替换为离线测试夹具。"""
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        set_stage("provider_request")
        if len(request.content) > 262144:
            raise ProtocolError("AI_BUDGET_EXCEEDED")
        response = await self.inner.handle_async_request(request)
        set_stage("provider_response")
        try:
            if response.headers.get("content-encoding", "identity").lower() not in (
                "",
                "identity",
            ):
                raise ProtocolError("AI_INVALID_RESPONSE")
            if response.is_stream_consumed:
                body = response.content
                if len(body) > 65536:
                    raise ProtocolError("AI_INVALID_RESPONSE")
            else:
                parts = []
                size = 0
                async for part in response.aiter_raw():
                    size += len(part)
                    if size > 65536:
                        raise ProtocolError("AI_INVALID_RESPONSE")
                    parts.append(part)
                body = b"".join(parts)
            if response.status_code != 200:
                raise ProtocolError("AI_UPSTREAM_ERROR")
            parsed = strict_json(body)
            if (
                not isinstance(parsed, dict)
                or not isinstance(parsed.get("choices"), list)
                or len(parsed["choices"]) != 1
            ):
                raise ProtocolError("AI_INVALID_RESPONSE")
            choice = parsed["choices"][0]
            if not isinstance(choice, dict) or not isinstance(
                choice.get("message"), dict
            ):
                raise ProtocolError("AI_INVALID_RESPONSE")
            message = choice["message"]
            if message.get("role") != "assistant" or choice.get("finish_reason") != (
                "tool_calls" if message.get("tool_calls") else "stop"
            ):
                raise ProtocolError("AI_INVALID_RESPONSE")
            validated = httpx.Response(
                response.status_code,
                headers=response.headers,
                content=body,
                request=request,
            )
            set_stage("model_parse")
            return validated
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self.inner.aclose()
