from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import run_memory_campaign_window as campaign


class FakeSession:
    def __init__(self) -> None:
        self.stopped = False
        self.poll_result: int | None = None

    def poll(self) -> int | None:
        return self.poll_result

    def stop(self) -> dict[str, Any]:
        self.stopped = True
        return {"requests": 20, "errors": 0, "workers": 1}


def _final(strategy: str, diagnosis_id: str) -> dict[str, Any]:
    return {
        "data": {
            "diagnosis_id": diagnosis_id,
            "status": "COMPLETED",
            "normalized_intent": {"analysis_strategy": strategy},
            "latest_conclusion": {"summary": "real terminal conclusion"},
            "evidence": [],
            "evidence_snapshots": [],
        }
    }


def _prepare_success(
    monkeypatch: pytest.MonkeyPatch,
    *,
    baseline_memory: str = "10MiB / 100MiB",
    fault_memory: str = "12MiB / 100MiB",
) -> tuple[list[FakeSession], list[dict[str, Any]]]:
    sessions = [FakeSession(), FakeSession()]
    started: list[dict[str, Any]] = []
    stats_calls = 0

    def fake_start(workload, target: str, workers: int) -> FakeSession:
        session = sessions[len(started)]
        started.append({"workload": workload, "target": target, "workers": workers})
        return session

    def fake_stats(_container: str) -> dict[str, Any]:
        nonlocal stats_calls
        stats_calls += 1
        usage = baseline_memory if stats_calls == 1 else fault_memory
        return {"cpu_percent": 1.0, "memory_usage": usage}

    monkeypatch.setattr(campaign, "wait_container_healthy", lambda _container: None)
    monkeypatch.setattr(campaign, "host_pid", lambda _container: 123)
    monkeypatch.setattr(campaign, "resolve_agent_host", lambda *args: "worker-1")
    monkeypatch.setattr(campaign, "docker_host_port", lambda *args: 6060)
    monkeypatch.setattr(campaign, "docker_stats", fake_stats)
    monkeypatch.setattr(
        campaign,
        "request_factory",
        lambda *args: object(),
    )
    monkeypatch.setattr(campaign, "start_workload", fake_start)
    monkeypatch.setattr(campaign.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        campaign,
        "set_otel_feature_flag",
        lambda *args, **kwargs: {"enabled": kwargs["enabled"]},
    )
    monkeypatch.setattr(
        campaign,
        "restart_and_wait_healthy",
        lambda _container: {
            "action": "container_restart",
            "before_pid": 123,
            "after_pid": 456,
            "status": "healthy",
        },
    )
    monkeypatch.setattr(
        campaign,
        "wait_task_terminal",
        lambda *args, **kwargs: {"status": "DONE", "finished_at": "now"},
    )
    diagnosis_counter = iter(range(1, 4))

    def fake_api(_base: str, method: str, path: str, payload=None, **kwargs):
        if path == "/api/tasks":
            assert payload["collector_type"] == "sys_metrics"
            assert payload["target_pid"] == 123
            return {"data": {"task_id": "baseline-1"}}
        assert method == "POST"
        assert path == "/api/v1/diagnoses"
        assert payload["diagnosis_mode"] == "LIVE"
        assert payload["context"]["service_id"] == "otel-email"
        assert payload["context"]["instances"][0]["runtime"] == "ruby"
        assert payload["baseline_task_ids"] == ["baseline-1"]
        assert "oracle" not in json.dumps(payload).lower()
        return {"data": {"diagnosis_id": f"diag-{next(diagnosis_counter)}"}}

    monkeypatch.setattr(campaign, "api_json", fake_api)
    monkeypatch.setattr(
        campaign,
        "wait_for_terminal",
        lambda _base, diagnosis_id, **kwargs: _final(
            campaign.STRATEGIES[int(diagnosis_id.rsplit("-", 1)[1]) - 1], diagnosis_id
        ),
    )
    return sessions, started


def _execute(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    values = {
        "repetition": 1,
        "otel_root": tmp_path,
        "base_url": "http://control",
        "agent_id": "agent-1",
        "load_duration": 120,
        "load_workers": 1,
        "diagnosis_timeout": 35,
        "approve_r2": True,
        "overwrite": False,
        "submissions": tmp_path / "submissions.json",
        "output_dir": tmp_path / "windows",
        "window_id": "memory-window-1",
    }
    values.update(overrides)
    return campaign.execute_campaign_window(**values)


def test_memory_window_requires_single_worker_and_bounded_duration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _execute(tmp_path, load_workers=2)
    with pytest.raises(ValueError, match="between 1 and 120"):
        _execute(tmp_path, load_duration=121)
    with pytest.raises(ValueError, match="requires at least 109 seconds"):
        _execute(tmp_path, load_duration=108)


def test_memory_window_deadline_uses_requested_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(monkeypatch)
    monkeypatch.setattr(campaign.time, "monotonic", lambda: 100.0)
    timeouts: list[float] = []

    def wait_for_terminal(_base: str, diagnosis_id: str, **kwargs):
        timeouts.append(kwargs["timeout_seconds"])
        return _final(
            campaign.STRATEGIES[int(diagnosis_id.rsplit("-", 1)[1]) - 1],
            diagnosis_id,
        )

    monkeypatch.setattr(campaign, "wait_for_terminal", wait_for_terminal)

    result = _execute(tmp_path, load_duration=109, diagnosis_timeout=35)

    assert result["fault_deadline_seconds"] == 109
    assert timeouts == [35.0, 35.0, 35.0]


def test_memory_window_publishes_after_growth_and_mandatory_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    sessions, started = _prepare_success(monkeypatch)

    result = _execute(tmp_path)

    submissions = json.loads((tmp_path / "submissions.json").read_text(encoding="utf-8"))
    assert result["publication"]["published"] is True
    assert result["fault_observation"]["growth_bytes"] == 2 * 1024 * 1024
    assert result["cleanup"]["memory_restart"]["after_pid"] == 456
    assert len(submissions) == 3
    assert len(started) == 2
    assert all(session.stopped for session in sessions)
    assert result["baseline_load"]["status"] == "STOPPED"
    assert result["incident_load"]["status"] == "STOPPED"
    assert not (tmp_path / "submissions.json.window.tmp").exists()
    assert not (tmp_path / "submissions.json.window.tmp.tmp").exists()


def test_baseline_memory_cannot_satisfy_incident_growth_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(
        monkeypatch,
        baseline_memory="20MiB / 100MiB",
        fault_memory="19MiB / 100MiB",
    )

    result = _execute(tmp_path)

    assert result["publication"]["published"] is False
    assert result["fault_observation"]["observed"] is False
    assert result["fault_observation"]["max_memory_bytes"] == 19 * 1024 * 1024
    assert "baseline_window_ready" not in result["fault_observation"]["phases"]
    assert result["cleanup"]["memory_restart"]["after_pid"] == 456
    assert not (tmp_path / "submissions.json").exists()


def test_memory_abort_threshold_blocks_publication_and_still_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    sessions, _started = _prepare_success(
        monkeypatch,
        fault_memory="80MiB / 100MiB",
    )

    result = _execute(tmp_path)

    assert result["publication"]["published"] is False
    assert result["fixture_failure"]["reason"] == "campaign_window_exception"
    assert "memory abort threshold reached" in result["fixture_failure"]["error"]
    assert result["cleanup"]["memory_restart"]["after_pid"] == 456
    assert all(session.stopped for session in sessions)
    assert not (tmp_path / "submissions.json").exists()


def test_baseline_failure_never_starts_incident_or_restarts_email(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    sessions, started = _prepare_success(monkeypatch)
    restart_calls = 0

    def restart(_container: str):
        nonlocal restart_calls
        restart_calls += 1
        return {}

    monkeypatch.setattr(campaign, "restart_and_wait_healthy", restart)
    monkeypatch.setattr(
        campaign,
        "wait_task_terminal",
        lambda *args, **kwargs: {"status": "FAILED", "finished_at": "now"},
    )

    result = _execute(tmp_path)

    assert result["publication"]["published"] is False
    assert len(started) == 1
    assert sessions[0].stopped is True
    assert restart_calls == 0
    assert "incident_load" not in result
    assert not (tmp_path / "submissions.json").exists()


def test_restart_failure_preserves_results_but_blocks_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(monkeypatch)
    monkeypatch.setattr(
        campaign,
        "restart_and_wait_healthy",
        lambda _container: (_ for _ in ()).throw(RuntimeError("restart failed")),
    )

    result = _execute(tmp_path)

    assert len(result["strategies"]) == 3
    assert result["publication"]["published"] is False
    assert result["fixture_failure"]["reason"] == "cleanup_or_verification_failed"
    assert "memory_restart: RuntimeError: restart failed" in result["cleanup"]["errors"]
    assert not (tmp_path / "submissions.json").exists()


def test_memory_window_records_initial_and_recovery_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(monkeypatch)
    compose = {"project": "mini-drop-memory", "sha256": "compose-sha"}
    containers = iter(
        (
            {"host_pid": 123, "container_id": "initial"},
            {"host_pid": 456, "container_id": "recovered"},
        )
    )
    monkeypatch.setattr(campaign, "compose_config_provenance", lambda **kwargs: compose)
    monkeypatch.setattr(
        campaign, "docker_container_provenance", lambda _container: next(containers)
    )

    result = _execute(
        tmp_path,
        project_name="mini-drop-memory",
        compose_files=[tmp_path / "compose.yaml"],
    )

    assert result["provenance"] == {
        "compose": compose,
        "containers": {
            "initial": {"host_pid": 123, "container_id": "initial"},
            "recovery": {"host_pid": 456, "container_id": "recovered"},
        },
    }


def test_pid_change_during_diagnosis_blocks_publication_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(monkeypatch)
    state = {"pid": 123}
    monkeypatch.setattr(campaign, "host_pid", lambda _container: state["pid"])

    def fail_during_first_diagnosis(_base: str, _diagnosis_id: str, **kwargs):
        state["pid"] = 456
        kwargs["fixture_check"]()
        raise AssertionError("fixture check must fail")

    monkeypatch.setattr(campaign, "wait_for_terminal", fail_during_first_diagnosis)
    monkeypatch.setattr(
        campaign,
        "restart_and_wait_healthy",
        lambda _container: {
            "action": "container_restart",
            "before_pid": 456,
            "after_pid": 789,
            "status": "healthy",
        },
    )

    result = _execute(tmp_path)

    assert result["publication"]["published"] is False
    assert result["fixture_failure"]["reason"] == "campaign_window_exception"
    assert "PID changed" in result["fixture_failure"]["error"]
    assert result["cleanup"]["memory_restart"]["after_pid"] == 789
    assert result["strategies"][0]["status"] == "RUNNING"
    assert not (tmp_path / "submissions.json").exists()


def test_staged_publication_failure_preserves_existing_submissions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(monkeypatch)
    submissions_path = tmp_path / "submissions.json"
    original = "[]\n"
    submissions_path.write_text(original, encoding="utf-8")
    real_upsert = campaign.upsert_submission
    calls = 0

    def fail_second_upsert(path: Path, submission: dict[str, Any], *, overwrite: bool):
        nonlocal calls
        calls += 1
        if calls == 2:
            nested = path.with_suffix(path.suffix + ".tmp")
            nested.write_text("partial", encoding="utf-8")
            raise RuntimeError("staging interrupted")
        return real_upsert(path, submission, overwrite=overwrite)

    monkeypatch.setattr(campaign, "upsert_submission", fail_second_upsert)

    result = _execute(tmp_path)

    assert result["publication"]["published"] is False
    assert result["fixture_failure"]["reason"] == "submission_publication_failed"
    assert submissions_path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "submissions.json.window.tmp").exists()
    assert not (tmp_path / "submissions.json.window.tmp.tmp").exists()
