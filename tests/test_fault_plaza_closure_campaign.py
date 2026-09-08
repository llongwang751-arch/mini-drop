from __future__ import annotations

import json

from scripts import run_fault_plaza_closure_campaign as campaign


def _scenario() -> dict:
    return {
        "scenario_id": "cpp-memory-growth",
        "title": "C++ 有界内存保留",
        "target_runtime": "C++",
        "lab_key": "cpp",
        "recommended_collectors": ["sys_metrics", "memory_smaps"],
        "minimum_diagnosis_rounds": 3,
        "active": False,
        "available": True,
    }


def test_decisive_collector_is_scenario_evidence_contract_not_list_position():
    assert campaign._decisive_collector(
        {
            "scenario_id": "noisy-neighbor",
            "recommended_collectors": ["sys_metrics", "perf_cpu"],
        }
    ) == "sys_metrics"
    assert campaign._decisive_collector(
        {
            "scenario_id": "go-network-latency",
            "recommended_collectors": ["sys_metrics", "go_pprof"],
        }
    ) == "sys_metrics"


def test_scenario_pass_requires_diagnosis_chain_and_cleanup(monkeypatch):
    stops = []
    start_durations = []
    monkeypatch.setattr(
        campaign,
        "_start_fault",
        lambda _client, _scenario_id, duration: start_durations.append(duration) or {
            "diagnosis_request": {"query": "diagnose memory growth"}
        },
    )
    monkeypatch.setattr(
        campaign,
        "_find_demo_process",
        lambda *_args, **_kwargs: {"pid": 42, "comm": "cpp-hotspot"},
    )
    monkeypatch.setattr(
        campaign,
        "_run_diagnosis",
        lambda *_args, **kwargs: {
            "diagnosis_id": "diag-1",
            "status": "COMPLETED",
            "decisive_collector": kwargs["expected_collector"],
        },
    )
    monkeypatch.setattr(
        campaign,
        "_stop_fault",
        lambda _client, scenario_id: stops.append(scenario_id),
    )

    result = campaign.run_scenario(
        object(),
        _scenario(),
        agent_id="agent-1",
        fault_duration_seconds=60,
        timeout_seconds=60,
        warmup_seconds=0,
        poll_seconds=0,
    )

    assert result["passed"] is True
    assert result["diagnosis_chain_verified"] is True
    assert result["cleanup_verified"] is True
    assert result["decisive_collector"] == "memory_smaps"
    assert result["diagnosis_timeout_seconds"] == {"requested": 60, "effective": 60}
    assert result["fault_duration_seconds"] == {"requested": 60, "effective": 90}
    assert start_durations == [90]
    assert stops == ["cpp-memory-growth"]


def test_scenario_keeps_diagnosis_failure_and_still_cleans_up(monkeypatch):
    monkeypatch.setattr(
        campaign,
        "_start_fault",
        lambda *_args, **_kwargs: {
            "diagnosis_request": {"query": "diagnose memory growth"}
        },
    )
    monkeypatch.setattr(
        campaign,
        "_find_demo_process",
        lambda *_args, **_kwargs: {"pid": 42, "comm": "cpp-hotspot"},
    )
    monkeypatch.setattr(
        campaign,
        "_run_diagnosis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no Evidence")),
    )
    cleaned = []
    monkeypatch.setattr(
        campaign,
        "_stop_fault",
        lambda _client, scenario_id: cleaned.append(scenario_id),
    )

    result = campaign.run_scenario(
        object(),
        _scenario(),
        agent_id="agent-1",
        fault_duration_seconds=60,
        timeout_seconds=60,
        warmup_seconds=0,
        poll_seconds=0,
    )

    assert result["passed"] is False
    assert result["error"] == "no Evidence"
    assert result["cleanup_verified"] is True
    assert cleaned == ["cpp-memory-growth"]


def test_scenario_caps_diagnosis_before_the_fault_lab_dead_man_switch(monkeypatch):
    observed = {}
    monkeypatch.setattr(
        campaign,
        "_start_fault",
        lambda _client, _scenario_id, duration: observed.update(
            fault_duration=duration
        )
        or {"diagnosis_request": {"query": "diagnose memory growth"}},
    )
    monkeypatch.setattr(
        campaign,
        "_find_demo_process",
        lambda *_args, **_kwargs: {"pid": 42, "comm": "cpp-hotspot"},
    )
    monkeypatch.setattr(
        campaign,
        "_run_diagnosis",
        lambda *_args, **kwargs: observed.update(
            diagnosis_timeout=kwargs["timeout_seconds"]
        )
        or {"diagnosis_id": "diag-capped", "status": "COMPLETED"},
    )
    monkeypatch.setattr(campaign, "_stop_fault", lambda *_args: None)

    result = campaign.run_scenario(
        object(),
        _scenario(),
        agent_id="agent-1",
        fault_duration_seconds=100,
        timeout_seconds=900,
        warmup_seconds=0,
        poll_seconds=0,
    )

    assert result["passed"] is True
    assert observed == {"fault_duration": 300, "diagnosis_timeout": 270}
    assert result["diagnosis_timeout_seconds"] == {
        "requested": 900,
        "effective": 270,
    }


def test_campaign_persists_an_atomic_running_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(campaign, "_get_plaza", lambda _client: {"scenarios": [_scenario()]})
    monkeypatch.setattr(
        campaign,
        "run_scenario",
        lambda *_args, **_kwargs: {"scenario_id": "cpp-memory-growth", "passed": True},
    )

    class FakeClient:
        def request(self, *_args, **_kwargs):
            return {"dependencies": {"database": "healthy"}}

    output = tmp_path / "campaign.json"
    report = campaign.run_campaign(
        FakeClient(),
        scenario_ids=None,
        agent_id="agent-1",
        fault_duration_seconds=100,
        timeout_seconds=300,
        warmup_seconds=0,
        poll_seconds=0,
        checkpoint_path=output,
    )

    checkpoint = json.loads(output.read_text(encoding="utf-8"))
    assert checkpoint["run_status"] == "RUNNING"
    assert checkpoint["completed_count"] == 1
    assert checkpoint["passed_count"] == 1
    assert report["run_status"] == "COMPLETED"
