import json

from server.app.logging_utils import bind_traceparent, log_event, reset_traceparent


def test_log_event_redacts_nested_secrets(capsys):
    sentinel = "PRIVATE_LOG_SENTINEL"
    log_event(
        "warning",
        "request",
        api_key=sentinel,
        nested={"oracle-answer": sentinel, "url": "https://user:password@example.test/x"},
    )
    captured = capsys.readouterr()
    assert sentinel not in captured.err
    assert "user:password@" not in captured.err


def test_log_event_redacts_secrets_embedded_in_text(capsys):
    log_event(
        "error",
        "provider_failed",
        error=(
            "api_key=INLINE_KEY Authorization: Basic QkFTRTY0 "
            "dsn=postgres://user:pass@example.test/db "
            "-----BEGIN PRIVATE KEY-----secret-----END PRIVATE KEY-----"
        ),
    )
    captured = capsys.readouterr()
    assert "INLINE_KEY" not in captured.err
    assert "QkFTRTY0" not in captured.err
    assert "user:pass@" not in captured.err
    assert "BEGIN PRIVATE KEY" not in captured.err


def test_log_event_includes_bound_trace_id_and_resets_context(capsys):
    token = bind_traceparent(
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    try:
        log_event("info", "trace_test")
    finally:
        reset_traceparent(token)
    log_event("info", "after_trace")

    first, second = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert first["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert "trace_id" not in second
