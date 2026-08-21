"""AI provider configuration tests."""

import threading

from server.app import ai_provider
from server.app.ai_provider import (
    ModelBoundaryError,
    _assert_model_boundary,
    _chat_url,
    _post_json,
    chat_completions,
    get_ai_settings,
    is_feature_enabled,
)


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


def test_model_boundary_rejects_nested_keys_strings_and_tool_enums():
    forbidden_payloads = [
        {"tool_choice": {"provider_extension": {"evaluation-oracle": "secret"}}},
        {"tools": [{"function": {"parameters": {"enum": ["ground_truth"]}}}]},
        {"messages": [{"content": '{"oracle_root_cause_id":"secret"}'}]},
        {"items": ({"Expected-Location": "self"},)},
    ]
    for payload in forbidden_payloads:
        try:
            _assert_model_boundary(payload)
        except ModelBoundaryError:
            pass
        else:
            raise AssertionError(f"payload was not rejected: {payload!r}")


def test_model_boundary_allows_expected_observation_and_rejects_excessive_depth():
    _assert_model_boundary({"expected_observation": "CPU should fall"})
    nested = value = {}
    for _ in range(34):
        child = {}
        value["next"] = child
        value = child
    try:
        _assert_model_boundary(nested)
    except ModelBoundaryError as exc:
        assert "nesting exceeds limit" in str(exc)
    else:
        raise AssertionError("deep payload was not rejected")


def test_model_boundary_allows_plain_text_discussion_but_rejects_structured_leaks():
    _assert_model_boundary(
        {"messages": [{"content": "Explain why ground_truth must stay evaluator-only."}]}
    )
    _assert_model_boundary(
        {"messages": [{"content": "The field evaluation_oracle is not part of this API."}]}
    )
    try:
        _assert_model_boundary(
            {"messages": [{"content": '{"oracle_root_cause_id":"secret"}'}]}
        )
    except ModelBoundaryError:
        pass
    else:
        raise AssertionError("structured evaluator data was not rejected")


def test_chat_completion_allows_plain_text_before_network_call(monkeypatch):
    called = False

    def fake_post(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(ai_provider, "_post_json", fake_post)
    chat_completions({"messages": [{"content": "Discuss evaluation_oracle isolation."}]})
    assert called is True


def test_chat_completion_rejects_before_network_call(monkeypatch):
    called = False

    def never_post(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    monkeypatch.setattr(ai_provider, "_post_json", never_post)
    try:
        chat_completions({"messages": [{"content": '{"evaluation_oracle":{"case":"secret"}}'}]})
    except ModelBoundaryError:
        pass
    else:
        raise AssertionError("forbidden payload was not rejected")
    assert called is False
