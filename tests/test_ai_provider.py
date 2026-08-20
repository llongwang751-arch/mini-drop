"""AI provider configuration tests."""

import threading

from server.app import ai_provider
from server.app.ai_provider import _chat_url, _post_json, get_ai_settings, is_feature_enabled


def test_ai_defaults_use_current_deepseek_flash(monkeypatch):
    for name in (
        "MINI_DROP_AI_PROVIDER",
        "DEEPSEEK_PROVIDER",
        "MINI_DROP_AI_BASE_URL",
        "DEEPSEEK_API_BASE",
        "MINI_DROP_AI_API_KEY",
        "DEEPSEEK_API_KEY",
        "MINI_DROP_AI_MODEL",
        "DEEPSEEK_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = get_ai_settings()
    assert settings.provider == "deepseek"
    assert settings.base_url == "https://api.deepseek.com"
    assert settings.model == "deepseek-v4-flash"
    assert _chat_url(settings.base_url) == "https://api.deepseek.com/v1/chat/completions"


def test_ai_mode_none_disables_all(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "key")
    settings = get_ai_settings()
    assert settings.nlp_enabled is False
    assert settings.rca_enabled is False
    assert settings.summarize_enabled is False
    assert is_feature_enabled("nlp") is False


def test_ai_mode_nlp_only(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "nlp-only")
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "key")
    assert is_feature_enabled("nlp") is True
    assert is_feature_enabled("rca") is False
    assert is_feature_enabled("summarize") is False


def test_ai_custom_provider_env(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AI_PROVIDER", "openai-compatible")
    monkeypatch.setenv("MINI_DROP_AI_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "key")
    monkeypatch.setenv("MINI_DROP_AI_MODEL", "custom-model")
    settings = get_ai_settings()
    assert settings.provider == "openai-compatible"
    assert settings.base_url == "https://llm.example.com/v1"
    assert settings.model == "custom-model"


def test_ai_http_client_reuses_thread_local_connection_pool(monkeypatch):
    created = []

    class FakeSession:
        def __init__(self):
            self.mounts = []
            self.calls = []
            created.append(self)

        def mount(self, prefix, adapter):
            self.mounts.append((prefix, adapter))

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return {"ok": True}

    monkeypatch.setattr("requests.Session", FakeSession)
    monkeypatch.setattr(ai_provider, "_HTTP_LOCAL", threading.local())
    monkeypatch.setenv("MINI_DROP_AI_CONNECT_TIMEOUT_SECONDS", "3")

    _post_json("https://provider.test/v1/chat/completions", {}, {"x": 1}, 20)
    _post_json("https://provider.test/v1/chat/completions", {}, {"x": 2}, 30)

    assert len(created) == 1
    assert [item[0] for item in created[0].mounts] == ["https://", "http://"]
    assert created[0].calls[0][1]["timeout"] == (3, 20)
    assert created[0].calls[1][1]["timeout"] == (3, 30)
