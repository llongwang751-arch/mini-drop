"""Keep raw perf CLI failures reviewable without copying arbitrary CLI output."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from server.app import analyzer_runner


@pytest.fixture
def raw_perf(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    source = tmp_path / "perf.data"
    source.write_bytes(b"PERFILE2" + b"\0" * 7600)
    return [{"artifact_type": "raw", "filename": "perf.data", "local_path": str(source)}]


def _run_failure(monkeypatch, raw_perf, stdout, *, stderr=b"", returncode=1):
    monkeypatch.setattr(
        analyzer_runner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr,
        ),
    )
    return analyzer_runner.analyze_raw_perf_artifacts("failed-perf", raw_perf)


@pytest.mark.parametrize("as_bytes", [True, False])
def test_structured_cli_failure_preserves_reason(monkeypatch, raw_perf, as_bytes):
    reason = "perf script failed: requested srcline field is not supported"
    stdout = json.dumps({"status": "FAILED", "error": reason})
    if as_bytes:
        stdout = stdout.encode()
    with pytest.raises(analyzer_runner.AnalyzerCommandError) as failure:
        _run_failure(monkeypatch, raw_perf, stdout)
    payload = json.loads(str(failure.value))
    assert payload == {
        "error_code": "INTERNAL_ERROR",
        "failure_kind": "ANALYZER_COMMAND",
        "reason_code": "ANALYZER_COMMAND_FAILED",
        "analyzer": "hotmethod_analyzer",
        "returncode": 1,
        "message": reason,
    }


def test_structured_message_is_supported_and_extra_fields_are_not_persisted(
    monkeypatch, raw_perf, capsys,
):
    stdout = json.dumps({
        "status": "FAILED", "message": "perf parser failed",
        "env": {"PATH": "private-runtime-path", "API_KEY": "private-api-key"},
        "details": {"credentials": "private-credentials"},
    })
    with pytest.raises(analyzer_runner.AnalyzerCommandError) as failure:
        _run_failure(monkeypatch, raw_perf, stdout)
    persisted = str(failure.value)
    log = capsys.readouterr().err
    assert json.loads(persisted)["message"] == "perf parser failed"
    for value in ("env", "private-runtime-path", "private-api-key", "private-credentials"):
        assert value not in persisted
        assert value not in log


def test_structured_failure_redacts_credentials_before_persistence(
    monkeypatch, raw_perf, capsys,
):
    reason = "perf failed api_key=private-key https://user:private-pass@host/"
    with pytest.raises(analyzer_runner.AnalyzerCommandError) as failure:
        _run_failure(monkeypatch, raw_perf, json.dumps({"status": "FAILED", "error": reason}))
    assert "[REDACTED]" in str(failure.value)
    log = capsys.readouterr().err
    for value in ("private-key", "private-pass"):
        assert value not in str(failure.value)
        assert value not in log


def test_structured_failure_message_and_logs_are_bounded(monkeypatch, raw_perf, capsys):
    reason = "perf script failed: " + "x" * 12000
    with pytest.raises(analyzer_runner.AnalyzerCommandError) as failure:
        _run_failure(
            monkeypatch, raw_perf, json.dumps({"status": "FAILED", "error": reason}),
            stderr=b"y" * 12000,
        )
    message = json.loads(str(failure.value))["message"]
    assert message.startswith("perf script failed: ")
    assert len(message) == analyzer_runner.ANALYZER_FAILURE_MESSAGE_LIMIT
    log = json.loads(capsys.readouterr().err)
    assert len(log["stdout_tail"]) <= 500
    assert len(log["stderr"]) <= 500
    assert log["reason_code"] == "ANALYZER_COMMAND_FAILED"


@pytest.mark.parametrize("reason", [None, "", {"env": "private-value"}])
def test_failed_status_without_text_reason_still_raises(monkeypatch, raw_perf, reason):
    with pytest.raises(analyzer_runner.AnalyzerCommandError) as failure:
        _run_failure(monkeypatch, raw_perf, json.dumps({"status": "FAILED", "error": reason}))
    assert json.loads(str(failure.value))["message"] == "Analyzer command failed without a structured reason"
    assert "private-value" not in str(failure.value)


@pytest.mark.parametrize("stdout", [b"perf utility crashed", "{invalid JSON", b"", "[]"])
def test_unstructured_nonzero_exit_keeps_legacy_empty_result_and_stdout_tail(
    monkeypatch, raw_perf, capsys, stdout,
):
    assert _run_failure(monkeypatch, raw_perf, stdout, stderr="stderr diagnostic") == []
    log = json.loads(capsys.readouterr().err)
    expected = stdout.decode() if isinstance(stdout, bytes) else stdout
    assert log["stdout_tail"] == expected
    assert log["stderr"] == "stderr diagnostic"
    assert log["returncode"] == 1


def test_sample_quality_failure_keeps_existing_classification(monkeypatch, raw_perf):
    stdout = json.dumps({
        "status": "FAILED", "failure_kind": "SAMPLE_QUALITY",
        "reason_code": "NO_PERF_SAMPLES", "message": "no samples",
        "action_hint": "collect the target under load",
    })
    with pytest.raises(analyzer_runner.AnalyzerQualityError) as failure:
        _run_failure(monkeypatch, raw_perf, stdout, returncode=2)
    assert json.loads(str(failure.value))["reason_code"] == "NO_PERF_SAMPLES"


def test_subprocess_start_failure_keeps_legacy_empty_result(monkeypatch, raw_perf):
    def unavailable(*_args, **_kwargs):
        raise OSError("command unavailable")
    monkeypatch.setattr(analyzer_runner.subprocess, "run", unavailable)
    assert analyzer_runner.analyze_raw_perf_artifacts("failed-perf", raw_perf) == []


def test_successful_cli_is_not_reclassified_from_stdout(monkeypatch, raw_perf):
    monkeypatch.setattr(analyzer_runner, "_collect_analyzer_outputs", lambda _path: [{"id": 7}])
    assert _run_failure(
        monkeypatch, raw_perf, b'{"status":"FAILED","error":"not a process failure"}',
        returncode=0,
    ) == [{"id": 7}]


def test_oversized_stdout_is_not_parsed_and_logs_remain_bounded(
    monkeypatch, raw_perf, capsys,
):
    stdout = b"unstructured output " + b"x" * (analyzer_runner.ANALYZER_FAILURE_JSON_LIMIT + 1)
    assert _run_failure(monkeypatch, raw_perf, stdout) == []
    log = json.loads(capsys.readouterr().err)
    assert len(log["stdout_tail"]) == 500


def test_deep_json_does_not_escape_as_an_unrelated_decode_error(monkeypatch, raw_perf, capsys):
    stdout = "[" * 2000 + "0" + "]" * 2000
    assert _run_failure(monkeypatch, raw_perf, stdout) == []
    assert len(json.loads(capsys.readouterr().err)["stdout_tail"]) == 500


def test_structured_failure_is_persisted_by_actual_analysis_worker(
    monkeypatch, raw_perf,
):
    import hashlib

    from server.app.analysis_jobs import AnalysisWorker
    from server.app.database import _get_engine, init_db, reset_engine
    from server.app.models import Base
    from server.app.schemas import CreateTaskRequest
    from server.app.sql_repository import SqlRepository
    from server.app.state_machine import Actor, TaskStatus

    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_ANALYZER_RETRY_DELAY_SEC", "0")
    reset_engine()
    init_db()
    try:
        repo = SqlRepository()
        repo.register_agent("failed-perf-agent", "host", "10.0.0.9")
        task = repo.create_task(CreateTaskRequest(
            name="structured-perf-failure", agent_id="failed-perf-agent",
            target_pid=123, collector_type="perf_cpu", duration_sec=5,
        ))
        repo.transition_task(task.id, TaskStatus.RUNNING, "claimed", Actor.AGENT)
        repo.transition_task(task.id, TaskStatus.UPLOADING, "uploaded", Actor.AGENT)
        repo.transition_task(task.id, TaskStatus.ANALYZING, "queued", Actor.SERVER)
        attempt = repo.get_task_attempts(task.id)[-1]
        artifact = dict(raw_perf[0])
        artifact.update({
            "sha256": hashlib.sha256(b"PERFILE2" + b"\0" * 7600).hexdigest(),
            "size_bytes": 7608,
        })
        artifact_ids = repo.add_attempt_artifacts(task.id, attempt.id, [artifact])
        job = repo.enqueue_analysis_job(
            task.id, task_attempt_id=attempt.id, analyzer_type="artifact-set",
            analyzer_version="1.0.0", input_checksum="a" * 64,
            input_artifact_ids=artifact_ids, max_retries=0,
        )
        reason = "perf script failed: invalid field specification"
        monkeypatch.setattr(
            analyzer_runner.subprocess, "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1, stdout=json.dumps({"status": "FAILED", "error": reason}).encode(),
                stderr=b"",
            ),
        )
        result = AnalysisWorker(repo, worker_id="structured-error-worker").process_once()
        assert result is not None and result.status == "DEAD_LETTER"
        failed_job = repo.get_analysis_job(job.id)
        assert failed_job.error_code == "INTERNAL_ERROR"
        assert json.loads(failed_job.error_message)["message"] == reason
        assert json.loads(failed_job.error_message)["reason_code"] == "ANALYZER_COMMAND_FAILED"
        assert repo.get_task(task.id).analysis_status == "FAILED"
    finally:
        Base.metadata.drop_all(bind=_get_engine())
        reset_engine()
