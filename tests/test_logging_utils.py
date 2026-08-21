import json

from agent.mini_drop_agent.logging_utils import log_event
from server.app.logging_utils import log_event as server_log_event


def test_log_event_writes_json(capsys):
    log_event("info", "task_completed", task_id="task_1", artifact_count=2)

    captured = capsys.readouterr()
    record = json.loads(captured.out)

    assert record["level"] == "info"
    assert record["event"] == "task_completed"
    assert record["task_id"] == "task_1"
    assert record["artifact_count"] == 2
    assert "ts" in record


def test_error_log_uses_stderr(capsys):
    log_event("error", "heartbeat_failed", code="UNAVAILABLE")

    captured = capsys.readouterr()
    record = json.loads(captured.err)

    assert record["level"] == "error"
    assert record["event"] == "heartbeat_failed"


def test_server_log_event_redacts_nested_secrets(capsys):
    server_log_event("warning", "request", api_key="SERVER_SECRET", nested={"oracle-answer": "ORACLE_SECRET"})
    captured = capsys.readouterr()
    assert "SERVER_SECRET" not in captured.err
    assert "ORACLE_SECRET" not in captured.err


    sentinel = "PRIVATE_LOG_SENTINEL"
    log_event(
        "info",
        "request",
        authorization=sentinel,
        nested={"api-key": sentinel, "url": "https://user:password@example.test/x"},
        items=[{"ground_truth": sentinel}],
    )

    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert "user:password@" not in captured.out
    record = json.loads(captured.out)
    assert record["authorization"] == "[REDACTED]"
    assert record["nested"]["api-key"] == "[REDACTED]"
    assert record["items"][0]["ground_truth"] == "[REDACTED]"


def test_server_log_event_redacts_secrets_embedded_in_text(capsys):
    server_log_event(
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
