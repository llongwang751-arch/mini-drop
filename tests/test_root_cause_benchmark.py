import json
from pathlib import Path

from scripts.generate_root_cause_benchmark import build
from scripts.run_root_cause_benchmark import _markdown
from server.app.drop_insight.benchmark_v2 import load_json
from server.app.drop_insight.root_cause_benchmark import (
    evaluate_controlled_root_causes,
    report_sha256,
)


ROOT = Path(__file__).resolve().parents[1]


def test_root_cause_dataset_has_540_ground_truth_cases_and_500_pair_capacity(tmp_path):
    manifest = build(tmp_path)
    public = load_json(tmp_path / "public" / "cases.json")
    private = load_json(tmp_path / "private" / "oracles.json")

    assert manifest["case_count"] == 540
    assert manifest["paired_ab_case_count"] == 500
    assert manifest["fault_contract_count"] == 21
    assert len(public["cases"]) == len(private["oracles"]) == 540
    assert all("root_cause_id" not in item for item in public["cases"])
    assert all("query" not in item for item in private["oracles"])
    assert public["source_policy"]["legacy_case_files_read"] is False


def test_root_cause_evaluator_runs_540_cases_and_exactly_500_paired_arms(tmp_path):
    build(tmp_path)
    report = evaluate_controlled_root_causes(
        json.loads((ROOT / "skills" / "catalog.json").read_text(encoding="utf-8")),
        load_json(tmp_path / "public" / "cases.json"),
        load_json(tmp_path / "private" / "oracles.json"),
        paired_case_count=500,
    )

    assert report["dataset"]["case_count"] == 540
    assert report["paired_skill_ab_500"]["case_count"] == 500
    assert report["paired_skill_ab_500"]["same_cases_and_tool_budget"] is True
    assert report["paired_skill_ab_500"]["paired_significance"]["p_value"] < 0.05
    interval = report["paired_skill_ab_500"]["delta_bootstrap_95"]
    assert interval["lower_percentage_points"] > 0
    assert interval["upper_percentage_points"] >= interval["lower_percentage_points"]
    assert set(report["paired_skill_ab_500"]["by_runtime"]) == {
        "C++", "Go", "Java", "Python"
    }
    assert report["measurement_boundary"]["live_linux_execution"].endswith("NOT_540_LIVE_INJECTIONS")
    assert len(report["cases"]) == 540
    assert report["paired_skill_ab_500"]["skill_enabled"]["root_cause_top1_accuracy"] >= report["paired_skill_ab_500"]["no_skill"]["root_cause_top1_accuracy"]
    assert report["root_cause_evaluation_540"]["no_skill"]["text_prediction_matches_oracle"] == 540
    assert report["root_cause_evaluation_540"]["skill_enabled"]["text_prediction_matches_oracle"] == 540
    assert report["root_cause_evaluation_540"]["skill_retrieval"]["matched_expected_skill"] == 404
    assert report["paired_skill_ab_500"]["skill_retrieval"]["matched_expected_skill"] == 364
    assert report["paired_skill_ab_500"]["regressed_cases"] == 0
    assert report["dataset"]["diversity_audit"]["normalized_observation_template_count"] == 21
    assert report["dataset"]["diversity_audit"]["cases_with_uniquely_identifying_public_metadata"] == 540
    assert report["paired_skill_ab_500"]["scenario_cluster_delta_bootstrap_95"]["cluster_count"] == 20


def test_root_cause_replay_is_deterministic(tmp_path):
    build(tmp_path)
    args = (
        json.loads((ROOT / "skills" / "catalog.json").read_text(encoding="utf-8")),
        load_json(tmp_path / "public" / "cases.json"),
        load_json(tmp_path / "private" / "oracles.json"),
    )
    first = evaluate_controlled_root_causes(*args)
    second = evaluate_controlled_root_causes(*args)
    assert first == second


def test_root_cause_markdown_reports_method_results_regressions_and_boundaries(tmp_path):
    build(tmp_path)
    report = evaluate_controlled_root_causes(
        json.loads((ROOT / "skills" / "catalog.json").read_text(encoding="utf-8")),
        load_json(tmp_path / "public" / "cases.json"),
        load_json(tmp_path / "private" / "oracles.json"),
        paired_case_count=500,
    )
    report["generated_at"] = "2026-09-08T00:00:00+00:00"
    report["report_sha256"] = report_sha256(report)

    markdown = _markdown(report)

    assert "# Mini-Drop 根因测试集与 Skill A/B 基准评测报告" in markdown
    assert "## 3. 测试集怎样构建" in markdown
    assert "## 5. 总体结果" in markdown
    assert "## 6. 退化样本审计" in markdown
    assert "500 组中未观察到组合代理指标退化" in markdown
    assert "已知故障类别硬边界" in markdown
    assert "评分与 Skill 尚未完全解耦" in markdown
    assert "不是 540 次真机实验" in markdown
    assert "证据路线达标率（代理 Top-1）" in markdown
    assert "文本根因与 Oracle 一致" in markdown
    assert "Skill 检索本身" in markdown
    assert "不能证明生产根因准确率达到 68%" in markdown
