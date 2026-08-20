"""显式诊断流水线、结构化 Action 与版本化 Golden 回归门禁。"""

import time

from server.app.diagnosis.eval_harness import (
    load_manifest,
    load_scenarios,
    render_html,
    run_evaluation,
)
from server.app.diagnosis.evaluation_runs import create_evaluation_run, get_evaluation_run


def test_golden_scenarios_cover_required_domains():
    scenarios = load_scenarios()
    ids = {item["scenario_id"] for item in scenarios}
    assert {
        "self_code_hotspot",
        "same_host_cpu_noise",
        "shared_io_contention",
        "downstream_cpu_hotspot",
        "memory_leak",
        "network_packet_loss",
        "mysql_lock_wait",
    }.issubset(ids)


def test_golden_scenarios_all_pass_with_safe_actions():
    report = run_evaluation()
    assert report["total"] >= 7
    assert report["failed"] == 0
    assert report["dataset_version"] == "2.0.0"
    assert len(report["dataset_fingerprint"]) == 64
    assert report["gate_status"] == "PASSED"
    assert report["metrics"]["classification_accuracy"] == 1.0
    assert report["metrics"]["evidence_reference_integrity"] == 1.0
    assert report["metrics"]["unsafe_auto_execute_count"] == 0
    assert report["metrics"]["falsification_plan_rate"] == 1.0
    assert report["metrics"]["diagnostic_analysis_coverage"] == 1.0
    assert all(item["checks"]["falsification_plan"] for item in report["results"])


def test_golden_html_report_exposes_process_and_oracle_comparison():
    html = render_html(run_evaluation())
    assert "Mini-Drop 统一诊断评测报告" in html
    assert "诊断分析覆盖率" in html
    assert "标准答案（Oracle）" in html
    assert "逐项判定" in html
    assert "self_code_hotspot" in html


def test_golden_manifest_requires_all_versioned_scenarios():
    manifest = load_manifest()
    scenarios = load_scenarios()

    assert manifest.dataset == "mini-drop-diagnosis-golden"
    assert manifest.scenario_schema_version == "1.0"
    assert set(manifest.required_scenarios) == {
        item["scenario_id"] for item in scenarios
    }
    assert all(item["schema_version"] == manifest.scenario_schema_version for item in scenarios)


def test_golden_dataset_fingerprint_is_deterministic():
    assert run_evaluation()["dataset_fingerprint"] == run_evaluation()["dataset_fingerprint"]


def test_observable_golden_run_exposes_scenario_and_verification_stages(monkeypatch):
    monkeypatch.setenv("MINI_DROP_EVAL_EVENT_DELAY_MS", "0")
    created = create_evaluation_run()
    deadline = time.time() + 5
    current = created
    while current["status"] == "RUNNING" and time.time() < deadline:
        time.sleep(0.01)
        current = get_evaluation_run(created["run_id"])

    assert current is not None
    assert current["status"] == "COMPLETED"
    assert current["report"]["gate_status"] == "PASSED"
    assert len(current["scenario_results"]) == current["total"]
    stages = {event["stage"] for event in current["events"]}
    assert {
        "SUITE_LOADED",
        "SCENARIO_STARTED",
        "DECISION_BRANCH_EVALUATED",
        "EVIDENCE_AND_FALSIFICATION_CHECKED",
        "SCENARIO_COMPLETED",
        "GATE_COMPLETED",
    }.issubset(stages)

