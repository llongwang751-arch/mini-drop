from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import run_live_subset as subset


def _detail(status: str = "INSUFFICIENT_EVIDENCE") -> dict[str, Any]:
    return {
        "status": status,
        "normalized_intent": {},
        "latest_conclusion": {
            "root_location": {"type": "unknown", "target_ref": None},
            "domain_cause": {"type": "unknown"},
            "cluster_assessment": {
                "classification": "unknown",
                "evidence_refs": [],
            },
        },
        "evidence": [],
        "evidence_snapshots": [],
    }


def _submission(
    case_id: str,
    strategy: str,
    repetition: int,
    *,
    manifest: Path | None = None,
    status: str = "INSUFFICIENT_EVIDENCE",
) -> dict[str, Any]:
    detail = _detail(status)
    if manifest is not None:
        detail["campaign_window"] = {
            "window_id": f"{case_id}-r{repetition}",
            "manifest": str(manifest.resolve()),
        }
    return {
        "case_id": case_id,
        "strategy": strategy,
        "repetition": repetition,
        "diagnosis_detail": detail,
    }


def _all_submissions(tmp_path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for case_id in subset.LIVE_CASES:
        for repetition in subset.REPETITIONS:
            manifest_path = tmp_path / f"{case_id}-r{repetition}-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "window_id": f"{case_id}-r{repetition}",
                        "case_id": case_id,
                        "repetition": repetition,
                        "publication": {"published": True},
                        "cleanup": {"errors": []},
                    }
                ),
                encoding="utf-8",
            )
            for strategy in subset.STRATEGIES:
                values.append(
                    _submission(
                        case_id,
                        strategy,
                        repetition,
                        manifest=manifest_path,
                    )
                )
    return values


def test_plan_is_exactly_two_cases_three_strategies_three_repetitions() -> None:
    plan = subset.build_live_subset_plan()

    assert plan["execution_count"] == 18
    assert plan["case_ids"] == ["T1-CPU-001", "T1-MEM-001"]
    assert plan["oracle_in_runner_input"] is False
    ids = [item["execution_id"] for item in plan["executions"]]
    assert len(ids) == len(set(ids)) == 18
    assert not hasattr(subset, "CASE_RUNTIME")
    assert not hasattr(subset, "SUPPORTED_TAGS")
    source = inspect.getsource(subset)
    assert "run_official_campaign" not in source
    assert "evaluation_oracle" not in source


def test_subset_completeness_rejects_missing_duplicate_and_unexpected(
    tmp_path: Path,
) -> None:
    complete = _all_submissions(tmp_path)
    assert subset.validate_subset_completeness(complete)["observed"] == 18

    with pytest.raises(ValueError, match="missing=1"):
        subset.validate_subset_completeness(complete[:-1])

    with pytest.raises(ValueError, match="duplicates=1"):
        subset.validate_subset_completeness(complete + [complete[0]])

    unexpected = list(complete)
    unexpected[-1] = _submission("T1-CODE-001", "EXPLORATORY", 3)
    with pytest.raises(ValueError, match="unexpected=1"):
        subset.validate_subset_completeness(unexpected)


def test_raw_manifest_gate_requires_published_clean_complete_windows(
    tmp_path: Path,
) -> None:
    submissions = _all_submissions(tmp_path)

    result = subset.validate_raw_manifests(submissions)

    assert result["validated"] == 18
    assert result["unique_manifests"] == 6

    manifest = Path(
        submissions[0]["diagnosis_detail"]["campaign_window"]["manifest"]
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cleanup"]["errors"] = ["flag_reset failed"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="cleanup contains errors"):
        subset.validate_raw_manifests(submissions)


def test_raw_manifest_gate_rejects_manifest_reused_across_windows(
    tmp_path: Path,
) -> None:
    submissions = _all_submissions(tmp_path)
    shared_manifest = submissions[0]["diagnosis_detail"]["campaign_window"]["manifest"]
    for item in submissions[3:6]:
        item["diagnosis_detail"]["campaign_window"]["manifest"] = shared_manifest
        item["diagnosis_detail"]["campaign_window"]["window_id"] = "T1-CPU-001-r1"

    with pytest.raises(ValueError, match="expected 6 unique manifests, got 5"):
        subset.validate_raw_manifests(submissions)


def test_finalize_preserves_insufficient_evidence_and_writes_subset_report(
    tmp_path: Path,
) -> None:
    submissions = _all_submissions(tmp_path)
    submissions_path = tmp_path / "submissions.json"
    submissions_path.write_text(json.dumps(submissions), encoding="utf-8")

    result = subset.finalize_live_subset(
        submissions_path=submissions_path,
        output_dir=tmp_path / "report",
    )

    report = json.loads(
        (tmp_path / "report" / "evaluation-report.json").read_text(encoding="utf-8")
    )
    html = (tmp_path / "report" / "evaluation-report.html").read_text(
        encoding="utf-8"
    )
    persisted = json.loads(submissions_path.read_text(encoding="utf-8"))
    assert result["complete"] is True
    assert report["result_count"] == 18
    assert report["oracle_isolated"] is True
    assert report["historical_official_90_modified"] is False
    assert all(
        item["diagnosis_detail"]["status"] == "INSUFFICIENT_EVIDENCE"
        for item in persisted
    )
    assert "2 case / 18 run / orchestrator-backed live subset" in html
    assert "90 次" not in html


def test_execute_resumes_complete_windows_without_overwriting_failures(
    tmp_path: Path,
) -> None:
    submissions_path = tmp_path / "submissions.json"
    existing = [
        _submission("T1-CPU-001", strategy, 1)
        for strategy in subset.STRATEGIES
    ]
    submissions_path.write_text(json.dumps(existing), encoding="utf-8")
    calls: list[tuple[str, int, bool]] = []

    def executor(**kwargs):
        case_id = "T1-CPU-001" if kwargs["container"] == "ad" else "T1-MEM-001"
        calls.append((case_id, kwargs["repetition"], kwargs["overwrite"]))
        return {
            "case_id": case_id,
            "repetition": kwargs["repetition"],
            "window_id": kwargs["window_id"],
            "publication": {"published": False},
            "fixture_failure": {"reason": "fixture failed"},
        }

    result = subset.execute_live_subset(
        otel_root=tmp_path,
        base_url="http://control",
        agent_id="agent-1",
        submissions_path=submissions_path,
        output_dir=tmp_path / "output",
        approve_r2=True,
        cpu_executor=executor,
        memory_executor=executor,
    )

    assert result["complete"] is False
    assert calls == [("T1-CPU-001", 2, False)]
    assert json.loads(submissions_path.read_text(encoding="utf-8")) == existing


def test_execute_resolves_scoped_compose_service_before_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    submissions_path = tmp_path / "submissions.json"
    common_file = tmp_path / "common.yaml"
    cpu_file = tmp_path / "cpu.yaml"
    environment_file = tmp_path / "fixture.env"
    resolutions: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def resolve(**kwargs):
        resolutions.append(kwargs)
        return "cpu-container-id"

    def executor(**kwargs):
        calls.append(kwargs)
        return {
            "case_id": "T1-CPU-001",
            "repetition": kwargs["repetition"],
            "window_id": kwargs["window_id"],
            "publication": {"published": False},
            "fixture_failure": {"reason": "fixture failed"},
        }

    monkeypatch.setattr(subset, "resolve_compose_service_container", resolve)

    result = subset.execute_live_subset(
        otel_root=tmp_path,
        base_url="http://control",
        agent_id="agent-1",
        submissions_path=submissions_path,
        output_dir=tmp_path / "output",
        approve_r2=True,
        cpu_project_name="mini-drop-cpu-r1",
        cpu_compose_files=[common_file, cpu_file],
        environment_file=environment_file,
        cpu_executor=executor,
    )

    assert result["complete"] is False
    assert resolutions == [{
        "project_name": "mini-drop-cpu-r1",
        "compose_files": [common_file, cpu_file],
        "service": "ad",
        "environment_file": environment_file,
    }]
    assert calls[0]["container"] == "cpu-container-id"
    assert calls[0]["project_name"] == "mini-drop-cpu-r1"
    assert calls[0]["compose_files"] == [common_file, cpu_file]
    assert calls[0]["environment_file"] == environment_file


def test_execute_rejects_incomplete_compose_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires project_name and compose_files together"):
        subset.execute_live_subset(
            otel_root=tmp_path,
            base_url="http://control",
            agent_id="agent-1",
            submissions_path=tmp_path / "submissions.json",
            output_dir=tmp_path / "output",
            approve_r2=True,
            cpu_project_name="mini-drop-cpu-r1",
        )


def test_execute_rejects_partial_window_instead_of_silent_rerun(tmp_path: Path) -> None:
    submissions_path = tmp_path / "submissions.json"
    submissions_path.write_text(
        json.dumps([_submission("T1-CPU-001", "CONSTRAINED_HYBRID", 1)]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="partial campaign windows"):
        subset.execute_live_subset(
            otel_root=tmp_path,
            base_url="http://control",
            agent_id="agent-1",
            submissions_path=submissions_path,
            output_dir=tmp_path / "output",
            approve_r2=True,
        )


def test_execute_rejects_duplicate_strategy_in_resumed_window(tmp_path: Path) -> None:
    submissions_path = tmp_path / "submissions.json"
    submissions = [
        _submission("T1-CPU-001", strategy, 1)
        for strategy in subset.STRATEGIES
    ]
    submissions.append(_submission("T1-CPU-001", subset.STRATEGIES[0], 1))
    submissions_path.write_text(json.dumps(submissions), encoding="utf-8")

    with pytest.raises(ValueError, match="partial campaign windows"):
        subset.execute_live_subset(
            otel_root=tmp_path,
            base_url="http://control",
            agent_id="agent-1",
            submissions_path=submissions_path,
            output_dir=tmp_path / "output",
            approve_r2=True,
        )
