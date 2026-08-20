from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import run_cpu_campaign_window as campaign


class FakeProcess:
    def __init__(self, *, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.communicate_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, timeout: float) -> tuple[str, None]:
        assert timeout == 10
        self.communicate_calls += 1
        if self.returncode is None:
            self.returncode = 0
        return ("load complete", None)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15


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
    baseline_cpu: float = 8.0,
    fault_cpu: float = 8.0,
) -> tuple[list[FakeProcess], list[list[str]]]:
    processes = [FakeProcess(), FakeProcess()]
    popen_commands: list[list[str]] = []
    stats_calls = 0

    def fake_popen(command: list[str], *args, **kwargs) -> FakeProcess:
        if popen_commands:
            assert processes[0].communicate_calls == 1
        popen_commands.append(command)
        return processes[len(popen_commands) - 1]

    def fake_stats(_container: str) -> dict[str, Any]:
        nonlocal stats_calls
        stats_calls += 1
        cpu = baseline_cpu if stats_calls == 1 else fault_cpu
        return {"cpu_percent": cpu, "memory_usage": "10MiB / 300MiB"}

    monkeypatch.setattr(campaign, "wait_container_healthy", lambda _container: None)
    monkeypatch.setattr(campaign, "host_pid", lambda _container: 123)
    monkeypatch.setattr(campaign, "resolve_agent_host", lambda *args: "worker-1")
    monkeypatch.setattr(campaign, "docker_host_port", lambda *args: 9555)
    monkeypatch.setattr(campaign, "docker_stats", fake_stats)
    monkeypatch.setattr(
        campaign,
        "set_otel_feature_flag",
        lambda *args, **kwargs: {"enabled": kwargs["enabled"]},
    )
    monkeypatch.setattr(campaign.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(campaign.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        campaign,
        "wait_task_terminal",
        lambda *args, **kwargs: {"status": "DONE", "finished_at": "now"},
    )
    diagnosis_counter = iter(range(1, 4))

    def fake_api(_base: str, method: str, path: str, payload=None, **kwargs):
        if path == "/api/tasks":
            return {"data": {"task_id": "baseline-1"}}
        assert method == "POST"
        assert path == "/api/v1/diagnoses"
        return {"data": {"diagnosis_id": f"diag-{next(diagnosis_counter)}"}}

    monkeypatch.setattr(campaign, "api_json", fake_api)
    monkeypatch.setattr(
        campaign,
        "wait_for_terminal",
        lambda _base, diagnosis_id, **kwargs: _final(
            campaign.STRATEGIES[int(diagnosis_id.rsplit("-", 1)[1]) - 1], diagnosis_id
        ),
    )
    return processes, popen_commands


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
        "window_id": "cpu-window-1",
    }
    values.update(overrides)
    return campaign.execute_campaign_window(**values)


def test_campaign_window_requires_single_worker_and_bounded_duration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _execute(tmp_path, load_workers=2)
    with pytest.raises(ValueError, match="between 1 and 120"):
        _execute(tmp_path, load_duration=121)
    with pytest.raises(ValueError, match="requires at least 109 seconds"):
        _execute(tmp_path, load_duration=108)


def test_campaign_window_deadline_uses_requested_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(monkeypatch)
    monkeypatch.setattr(campaign.time, "monotonic", lambda: 100.0)
    timeouts: list[float] = []
    diagnosis_counter = iter(range(1, 4))

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


def test_campaign_window_publishes_only_after_complete_valid_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    processes, commands = _prepare_success(monkeypatch)

    result = _execute(tmp_path)

    persisted = json.loads(
        (tmp_path / "windows" / "cpu-window-1-manifest.json").read_text(encoding="utf-8")
    )
    submissions = json.loads((tmp_path / "submissions.json").read_text(encoding="utf-8"))
    assert result["publication"]["published"] is True
    assert persisted["cleanup"]["errors"] == []
    assert len(submissions) == 3
    assert {item["diagnosis_detail"]["diagnosis_id"] for item in submissions} == {
        "diag-1", "diag-2", "diag-3"
    }
    assert len(commands) == 2
    assert commands[0][commands[0].index("--duration") + 1] == "30"
    assert commands[1][commands[1].index("--duration") + 1] == "120"
    assert commands[0][commands[0].index("--output") + 1].endswith(
        "cpu-window-1-baseline-load.json"
    )
    assert commands[1][commands[1].index("--output") + 1].endswith(
        "cpu-window-1-load.json"
    )
    assert processes[0] is not processes[1]
    assert [process.communicate_calls for process in processes] == [1, 1]
    assert persisted["baseline_load"]["status"] == "STOPPED"
    assert persisted["incident_load"]["status"] == "STOPPED"
    assert not (tmp_path / "submissions.json.window.tmp").exists()
    assert not (tmp_path / "submissions.json.window.tmp.tmp").exists()
    assert not (tmp_path / "windows" / "cpu-window-1-manifest.json.tmp").exists()


def test_baseline_cpu_cannot_satisfy_incident_fault_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(monkeypatch, baseline_cpu=8.0, fault_cpu=1.0)

    result = _execute(tmp_path)

    assert result["publication"]["published"] is False
    assert result["fixture_failure"]["reason"] == "cleanup_or_verification_failed"
    assert result["fault_observation"]["observed"] is False
    assert result["fault_observation"]["max_cpu_percent"] == 1.0
    assert "baseline_window_ready" not in result["fault_observation"]["phases"]
    assert not (tmp_path / "submissions.json").exists()


def test_baseline_failure_stops_process_without_starting_incident(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    processes, commands = _prepare_success(monkeypatch)
    monkeypatch.setattr(
        campaign,
        "wait_task_terminal",
        lambda *args, **kwargs: {"status": "FAILED", "finished_at": "now"},
    )

    result = _execute(tmp_path)

    assert result["publication"]["published"] is False
    assert len(commands) == 1
    assert processes[0].communicate_calls == 1
    assert processes[1].communicate_calls == 0
    assert result["baseline_load"]["status"] == "STOPPED"
    assert "incident_load" not in result
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
    assert not (tmp_path / "windows" / "cpu-window-1-manifest.json.tmp").exists()


def test_pid_change_invalidates_window_without_publishing_submissions(
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

    result = _execute(tmp_path)

    assert result["publication"]["published"] is False
    assert result["fixture_failure"]["reason"] == "cleanup_or_verification_failed"
    assert result["fixture_failure"]["prior_error"]["reason"] == "campaign_window_exception"
    assert not (tmp_path / "submissions.json").exists()
    persisted = json.loads(
        (tmp_path / "windows" / "cpu-window-1-manifest.json").read_text(encoding="utf-8")
    )
    assert persisted["strategies"][0]["status"] == "RUNNING"


def test_flag_reset_failure_preserves_results_but_blocks_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(monkeypatch)
    calls = 0

    def toggle(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("flagd unavailable")
        return {"enabled": kwargs["enabled"]}

    monkeypatch.setattr(campaign, "set_otel_feature_flag", toggle)

    result = _execute(tmp_path)

    assert len(result["strategies"]) == 3
    assert result["publication"]["published"] is False
    assert result["fixture_failure"]["reason"] == "cleanup_or_verification_failed"
    assert "flag_reset: RuntimeError: flagd unavailable" in result["cleanup"]["errors"]
    assert not (tmp_path / "submissions.json").exists()


def test_campaign_window_records_explicit_compose_and_container_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_success(monkeypatch)
    compose = {"project": "mini-drop-cpu", "sha256": "compose-sha"}
    containers = iter(
        (
            {"host_pid": 123, "container_id": "initial"},
            {"host_pid": 123, "container_id": "final"},
        )
    )
    monkeypatch.setattr(campaign, "compose_config_provenance", lambda **kwargs: compose)
    monkeypatch.setattr(
        campaign, "docker_container_provenance", lambda _container: next(containers)
    )

    result = _execute(
        tmp_path,
        project_name="mini-drop-cpu",
        compose_files=[tmp_path / "compose.yaml"],
    )

    assert result["provenance"] == {
        "compose": compose,
        "containers": {
            "initial": {"host_pid": 123, "container_id": "initial"},
            "final": {"host_pid": 123, "container_id": "final"},
        },
    }
