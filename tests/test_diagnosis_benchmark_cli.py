from __future__ import annotations

import json
import sys

from scripts import diagnosis_benchmark


def test_campaign_reports_layered_adapter_readiness(
    monkeypatch, tmp_path, capsys
) -> None:
    output_dir = tmp_path / "campaign"
    run_plan = {"execution_count": 90, "case_count": 10, "executions": []}
    readiness = {
        "case_count": 10,
        "supported_count": 10,
        "fixture_ready_count": 5,
        "diagnosis_ready_count": 2,
        "ready_count": 5,
        "checks": [],
    }
    monkeypatch.setattr(diagnosis_benchmark, "build_run_plan", lambda: run_plan)
    monkeypatch.setattr(
        diagnosis_benchmark,
        "preflight_all",
        lambda *, otel_root=None: readiness,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnosis_benchmark.py",
            "campaign",
            "--output-dir",
            str(output_dir),
        ],
    )

    diagnosis_benchmark.main()

    result = json.loads(capsys.readouterr().out)
    assert result["ready_adapter_count"] == 5
    assert result["fixture_ready_adapter_count"] == 5
    assert result["diagnosis_ready_adapter_count"] == 2
    assert result["supported_adapter_count"] == 10
    assert json.loads((output_dir / "run-plan.json").read_text(encoding="utf-8")) == run_plan
    assert json.loads(
        (output_dir / "adapter-readiness.json").read_text(encoding="utf-8")
    ) == readiness
    assert json.loads(
        (output_dir / "submissions.json").read_text(encoding="utf-8")
    ) == []
