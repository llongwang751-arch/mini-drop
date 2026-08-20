from __future__ import annotations

import types
from dataclasses import replace

import pytest

from server.app import ai_provider
from server.app.ai_provider import (
    ProviderCapabilities,
    detect_provider_capabilities,
    get_ai_settings,
    reset_provider_capabilities,
)


def _settings():
    base = get_ai_settings()
    return replace(
        base,
        api_key="test-key",
        model="test-model",
        base_url="https://example.com",
    )


def _resp(status=200):
    return types.SimpleNamespace(status_code=status, text="ok")


def test_unavailable_without_api_key():
    settings = replace(_settings(), api_key="")
    caps = detect_provider_capabilities(settings, chat_fn=lambda *a, **k: _resp())
    assert caps.provider_unavailable


def test_unavailable_on_connectivity_failure():
    def down(*args, **kwargs):
        raise RuntimeError("provider down")

    caps = detect_provider_capabilities(_settings(), chat_fn=down)
    assert caps.provider_unavailable


def test_detects_json_schema_and_tools():
    caps = detect_provider_capabilities(
        _settings(), chat_fn=lambda *a, **k: _resp()
    )
    assert caps.supports_json_schema and caps.supports_tools
    assert not caps.chat_only


def test_chat_only_when_feature_probes_rejected():
    def selective(payload, timeout=15):
        if "response_format" in payload or "tools" in payload:
            return _resp(400)
        return _resp(200)

    caps = detect_provider_capabilities(_settings(), chat_fn=selective)
    assert caps.chat_only
    assert not caps.provider_unavailable


def test_llm_client_uses_json_schema_when_supported(monkeypatch):
    reset_provider_capabilities()
    monkeypatch.setattr(
        ai_provider,
        "_capabilities_cache",
        ProviderCapabilities(supports_json_schema=True),
    )
    captured: dict = {}
    from server.app.rca import llm_client

    def fake_chat(payload, timeout=60):
        captured["payload"] = payload
        return types.SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {"choices": [{"message": {"content": "{}"}}]},
        )

    monkeypatch.setattr(llm_client, "chat_completions", fake_chat)
    llm_client._call_deepseek([{"role": "user", "content": "hi"}], "model-x")
    assert captured["payload"]["response_format"]["type"] == "json_schema"
    reset_provider_capabilities()
