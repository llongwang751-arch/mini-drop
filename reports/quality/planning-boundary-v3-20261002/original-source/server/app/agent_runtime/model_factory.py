"""Keep chat provider choice separate from embedding and reranking."""
import os
from server.app.ai_provider import AISettings


def create_chat_model(settings: AISettings, *, temperature: float, max_tokens: int, timeout: int):
    # Explicit deployment override; keep each request bounded and retries off.
    timeout = min(120, max(5, int(os.getenv("MINI_DROP_AGENT_MODEL_TIMEOUT_SEC", str(timeout)))))
    options = dict(model=settings.model, api_key=settings.api_key, base_url=settings.base_url,
                   temperature=temperature, max_tokens=max_tokens, timeout=timeout, max_retries=0)
    if settings.provider.lower() == "deepseek":
        from langchain_deepseek import ChatDeepSeek
        return ChatDeepSeek(**options)
    from langchain_openai import ChatOpenAI
    thinking = os.getenv("MINI_DROP_SILICONFLOW_ENABLE_THINKING", "").lower()
    if settings.provider.lower() == "siliconflow" and thinking in {"true", "false"}:
        options["extra_body"] = {"enable_thinking": thinking == "true"}
    # Provider compatibility is tested independently; no hidden provider retries.
    return ChatOpenAI(**options)
