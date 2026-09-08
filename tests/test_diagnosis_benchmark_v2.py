import json
from pathlib import Path

from scripts.generate_diagnosis_benchmark_v2 import SCHEMA, build
from server.app.drop_insight.benchmark_v2 import _runtime_skill, evaluate_skill_reuse
from server.app.drop_insight.skill_evolution import (
    _rank_hybrid_skills,
    _select_ranked_skill,
)


ROOT = Path(__file__).resolve().parents[1]


def test_new_benchmark_is_large_blinded_and_does_not_read_legacy_cases(tmp_path):
    manifest = build(tmp_path)
    public = json.loads((tmp_path / "public" / "cases.json").read_text(encoding="utf-8"))
    private = json.loads((tmp_path / "private" / "oracles.json").read_text(encoding="utf-8"))

    assert manifest["schema"] == SCHEMA
    assert manifest["case_count"] == 540
    assert public["source_policy"]["legacy_case_files_read"] is False
    assert len(public["cases"]) == len(private["oracles"]) == 540
    assert all("expected_skill_id" not in row for row in public["cases"])
    assert all("query" not in row for row in private["oracles"])


def test_v2_evaluator_calls_production_skill_selector(tmp_path):
    build(tmp_path)
    catalog = json.loads((ROOT / "skills" / "catalog.json").read_text(encoding="utf-8"))
    public = json.loads((tmp_path / "public" / "cases.json").read_text(encoding="utf-8"))
    private = json.loads((tmp_path / "private" / "oracles.json").read_text(encoding="utf-8"))

    report = evaluate_skill_reuse(catalog, public, private)

    assert report["dataset"]["case_count"] == 540
    assert report["measurement_boundary"]["skill_selection_and_safety_gate"] == "MEASURED"
    assert report["measurement_boundary"]["evidence_verified_root_cause_accuracy"].startswith("REQUIRES")
    assert report["skill_enabled"]["negative_total"] == 180
    assert report["skill_enabled"]["false_activation_rate"] <= 0.05
    assert report["skill_enabled"]["positive_reuse_rate"] >= 0.90


def test_cpp_runtime_route_requires_an_explicit_runtime_anchor():
    catalog = json.loads((ROOT / "skills" / "catalog.json").read_text(encoding="utf-8"))
    skills = [_runtime_skill(item) for item in catalog["skills"]]
    target = {
        "service": "order-service",
        "environment": "production",
        "collector_capabilities": ["sys_metrics", "perf_cpu", "continuous_perf"],
        "_baseline_tool": "collect_sys_metrics",
    }

    def select(query):
        ranked = _rank_hybrid_skills(skills, "CPU_HOTSPOT", target, query)
        selected = _select_ranked_skill(ranked)
        return selected[2].id if selected else None

    assert select("系统自主选择下一步取证，但当前没有可复现指标") is None
    assert select("系统调用偶发变慢，运行时身份未知") is None
    assert (
        select("C++ 服务 CPU 持续升高，请用 perf 定位函数级热点")
        == "cpp-runtime-diagnosis"
    )
    assert (
        select("cpp 原生进程出现 mutex 与 futex 等待")
        == "cpp-runtime-diagnosis"
    )
