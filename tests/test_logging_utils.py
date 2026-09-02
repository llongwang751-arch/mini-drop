from server.app.logging_utils import log_event


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
