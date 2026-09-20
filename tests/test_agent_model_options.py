from types import SimpleNamespace
import sys

import pytest

from server.app.agent_runtime.model_factory import create_chat_model


@pytest.mark.parametrize("provider,thinking,expected", [("siliconflow", "false", {"enable_thinking": False}), ("siliconflow", "true", {"enable_thinking": True}), ("other", "false", None)])
def test_provider_options_stay_scoped(monkeypatch, provider, thinking, expected):
    monkeypatch.setenv("MINI_DROP_SILICONFLOW_ENABLE_THINKING", thinking)
    monkeypatch.setenv("MINI_DROP_AGENT_MODEL_TIMEOUT_SEC", "999")
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=lambda **kw: kw))
    settings = SimpleNamespace(provider=provider, model="test", base_url="https://example.invalid/v1", api_key="test")
    options = create_chat_model(settings, temperature=0.1, max_tokens=1400, timeout=30)
    assert options["timeout"] == 120
    assert options["max_retries"] == 0
    assert options.get("extra_body") == expected
