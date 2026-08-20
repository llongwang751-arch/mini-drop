"""AI provider configuration and OpenAI-compatible chat client.

The runtime is intentionally vendor-neutral. Any provider that exposes an
OpenAI-compatible `/v1/chat/completions` endpoint can be used by setting URL,
API key and model through environment variables.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Literal

from server.app.common_utils import env_bool

FeatureName = Literal["nlp", "rca", "summarize"]
_HTTP_LOCAL = threading.local()


@dataclass(frozen=True)
class AISettings:
    enabled: str
    provider: str
    base_url: str
    api_key: str
    model: str
    nlp_enabled: bool
    rca_enabled: bool
    summarize_enabled: bool


def get_ai_settings() -> AISettings:
    mode = os.getenv("MINI_DROP_AI_ENABLED", "full").strip().lower()
    provider = _first_non_empty("MINI_DROP_AI_PROVIDER", "DEEPSEEK_PROVIDER", default="deepseek")
    base_url = _first_non_empty("MINI_DROP_AI_BASE_URL", "DEEPSEEK_API_BASE", default="https://api.deepseek.com")
    api_key = _first_non_empty("MINI_DROP_AI_API_KEY", "DEEPSEEK_API_KEY", default="")
    model = _first_non_empty("MINI_DROP_AI_MODEL", "DEEPSEEK_MODEL", default="deepseek-v4-flash")

    defaults = _mode_defaults(mode)
    feature_flags = _apply_feature_overrides(defaults)
    return AISettings(
        enabled=mode,
        provider=provider,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        nlp_enabled=feature_flags["nlp"],
        rca_enabled=feature_flags["rca"],
        summarize_enabled=feature_flags["summarize"],
    )


def is_feature_enabled(feature: FeatureName) -> bool:
    settings = get_ai_settings()
    if not settings.api_key:
        return False
    return {
        "nlp": settings.nlp_enabled,
        "rca": settings.rca_enabled,
        "summarize": settings.summarize_enabled,
    }[feature]


def chat_completions(payload: dict[str, Any], timeout: int = 60):
    settings = get_ai_settings()
    return _post_json(
        _chat_url(settings.base_url),
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )


def _chat_url(base_url: str) -> str:
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


# ── Provider capability detection（assessment §4.6）─────────────────

@dataclass(frozen=True)
class ProviderCapabilities:
    """What the OpenAI-compatible endpoint supports, probed at startup."""

    supports_json_schema: bool = False
    supports_tools: bool = False
    provider_unavailable: bool = False

    @property
    def chat_only(self) -> bool:
        """Plain chat only: no structured output and no tools."""
        return (
            not self.supports_json_schema
            and not self.supports_tools
            and not self.provider_unavailable
        )


_capabilities_cache: ProviderCapabilities | None = None


def detect_provider_capabilities(
    settings: AISettings | None = None,
    *,
    chat_fn=chat_completions,
) -> ProviderCapabilities:
    """Probe the provider once for structured-output support.

    A connectivity ping decides availability first; then JSON Schema and tools
    are probed separately. Any probe failure is conservative (False), never an
    optimistic assumption. ``chat_fn`` is injectable for tests.
    """
    settings = settings or get_ai_settings()
    if not settings.api_key:
        return ProviderCapabilities(provider_unavailable=True)

    def _probe(payload: dict) -> bool:
        try:
            resp = chat_fn(payload, timeout=15)
        except Exception:
            return False
        return getattr(resp, "status_code", None) == 200

    base_payload = {
        "model": settings.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 4,
    }
    if not _probe(dict(base_payload)):
        return ProviderCapabilities(provider_unavailable=True)

    json_schema_ok = _probe({
        **base_payload,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ping",
                "strict": True,
                "schema": {"type": "object", "properties": {}},
            },
        },
    })
    tools_ok = _probe({
        **base_payload,
        "tools": [{
            "type": "function",
            "function": {
                "name": "ping",
                "description": "ping",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
    })
    return ProviderCapabilities(
        supports_json_schema=json_schema_ok,
        supports_tools=tools_ok,
    )


def preflight_provider(settings: AISettings | None = None) -> ProviderCapabilities:
    """Probe and cache provider capabilities (call once at server startup)."""
    global _capabilities_cache
    _capabilities_cache = detect_provider_capabilities(settings)
    return _capabilities_cache


def get_provider_capabilities() -> ProviderCapabilities:
    """Return cached capabilities; unprobed callers get a conservative default."""
    global _capabilities_cache
    if _capabilities_cache is None:
        _capabilities_cache = ProviderCapabilities()
    return _capabilities_cache


def reset_provider_capabilities() -> None:
    global _capabilities_cache
    _capabilities_cache = None


def _mode_defaults(mode: str) -> dict[str, bool]:
    if mode == "none":
        return {"nlp": False, "rca": False, "summarize": False}
    if mode == "nlp-only":
        return {"nlp": True, "rca": False, "summarize": False}
    if mode == "rca-only":
        return {"nlp": False, "rca": True, "summarize": False}
    return {"nlp": True, "rca": True, "summarize": True}


def _first_non_empty(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _apply_feature_overrides(mode_defaults: dict[str, bool]) -> dict[str, bool]:
    """Apply per-feature env flags without bypassing the global AI mode.

    MINI_DROP_AI_ENABLED is the upper bound. For example, `none` always disables
    every feature even if `.env` still contains MINI_DROP_NLP_ENABLED=true.
    """
    env_names = {
        "nlp": "MINI_DROP_NLP_ENABLED",
        "rca": "MINI_DROP_RCA_ENABLED",
        "summarize": "MINI_DROP_SUMMARIZE_ENABLED",
    }
    result: dict[str, bool] = {}
    for feature, default_enabled in mode_defaults.items():
        result[feature] = bool(default_enabled) and env_bool(env_names[feature], default_enabled)
    return result


def _post_json(url: str, headers: dict, json: dict, timeout: int):
    import requests
    from requests.adapters import HTTPAdapter

    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        pool_size = _bounded_int("MINI_DROP_AI_HTTP_POOL_SIZE", 8, 1, 64)
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=pool_size,
            pool_maxsize=pool_size,
            max_retries=0,
            pool_block=True,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _HTTP_LOCAL.session = session
    connect_timeout = _bounded_int("MINI_DROP_AI_CONNECT_TIMEOUT_SECONDS", 5, 1, 30)
    # POST is deliberately not retried here: an automatic retry may consume a
    # second model call after the provider has already accepted the first one.
    # Diagnosis-level retry and budget accounting remain the source of truth.
    return session.post(
        url,
        headers=headers,
        json=json,
        timeout=(connect_timeout, max(1, int(timeout))),
    )


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)
