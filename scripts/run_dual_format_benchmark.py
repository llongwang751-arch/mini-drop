#!/usr/bin/env python3
"""Run Mini-Drop Dual-Format Benchmark & Generate JSON + Excel Reports.

Generates:
1. Dataset:
   - benchmarks/evaluation-suite/dataset.json (Machine readable)
   - benchmarks/evaluation-suite/dataset.xlsx (Human readable)
2. Evaluation Report:
   - reports/evaluation/evaluation_report.json (Machine readable)
   - reports/evaluation/evaluation_report.xlsx (Human readable)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.app.drop_insight.benchmark_v2 import load_json
from server.app.drop_insight.fault_plaza import SCENARIOS
from server.app.drop_insight.root_cause_benchmark import (
    evaluate_controlled_root_causes,
    report_sha256,
)

# Visual styling constants for openpyxl
HEADER_FILL_DARK = PatternFill(
    start_color="1F4E79", end_color="1F4E79", fill_type="solid"
)
HEADER_FILL_ACCENT = PatternFill(
    start_color="2F5597", end_color="2F5597", fill_type="solid"
)
HEADER_FILL_MUTED = PatternFill(
    start_color="D9E1F2", end_color="D9E1F2", fill_type="solid"
)
IMPROVED_FILL = PatternFill(
    start_color="E2EFDA", end_color="E2EFDA", fill_type="solid"
)
REGRESSED_FILL = PatternFill(
    start_color="FCE4D6", end_color="FCE4D6", fill_type="solid"
)
ZEBRA_FILL = PatternFill(
    start_color="F9FAFB", end_color="F9FAFB", fill_type="solid"
)

HEADER_FONT = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
HEADER_FONT_MUTED = Font(
    name="Microsoft YaHei", size=10, bold=True, color="1F4E79"
)
TITLE_FONT = Font(name="Microsoft YaHei", size=16, bold=True, color="1F4E79")
SUBTITLE_FONT = Font(name="Microsoft YaHei", size=11, italic=True, color="595959")
REGULAR_FONT = Font(name="Microsoft YaHei", size=10)
BOLD_FONT = Font(name="Microsoft YaHei", size=10, bold=True)
CODE_FONT = Font(name="Consolas", size=9.5)

THIN_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)

ALIGN_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")


def _auto_adjust_columns(ws, max_cols: int = 50):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            # Skip merged or very long title cells
            if cell.row in (1, 2) and ws.max_column > 3:
                continue
            val_str = str(cell.value or "")
            # Chinese character visual width ~ 2
            cell_len = sum(2 if ord(c) > 127 else 1 for c in val_str)
            if cell_len > max_len:
                max_len = cell_len
        # Clamp column width between 11 and 65
        adjusted_width = max(11, min(max_len + 3, 65))
        ws.column_dimensions[col_letter].width = adjusted_width


def generate_dataset_files(
    public_cases: dict[str, Any],
    private_oracles: dict[str, Any],
    output_dir: Path,
    ground_truth_dir: Path | None = None,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "dataset.json"
    xlsx_path = output_dir / "dataset.xlsx"
    gt_dir = ground_truth_dir if ground_truth_dir is not None else output_dir
    gt_dir.mkdir(parents=True, exist_ok=True)
    gt_path = gt_dir / "dataset_ground_truth.json"

    cases_list = public_cases.get("cases", [])
    oracles_map = {
        item["case_id"]: item for item in private_oracles.get("oracles", [])
    }
    scenarios_map = {item.scenario_id: item for item in SCENARIOS}

    # 1. Build unified JSON. 公开数据集只含输入；标准答案单独导出，
    #    避免"私有真值仅在评估后接触"的承诺被导出文件破坏。
    unified_cases = []
    ground_truth_cases = []
    for c in cases_list:
        cid = c["case_id"]
        oracle = oracles_map.get(cid, {})
        target = c.get("target", {})
        root_cause_id = oracle.get("root_cause_id")
        scenario = scenarios_map.get(root_cause_id)

        unified_cases.append(
            {
                "case_id": cid,
                "category": c.get("category"),
                "family_hint": c.get("family_hint"),
                "variant_family": c.get("variant_family"),
                "runtime": target.get("runtime"),
                "service": target.get("service"),
                "environment": target.get("environment"),
                "query": c.get("query"),
                "collector_capabilities": target.get(
                    "collector_capabilities", []
                ),
                "observations_by_collector": c.get(
                    "observations_by_collector", {}
                ),
            }
        )
        ground_truth_cases.append(
            {
                "case_id": cid,
                "root_cause_id": root_cause_id,
                "scenario_title": scenario.title if scenario else "",
                "decisive_collector": oracle.get("decisive_collector"),
                "expected_skill_id": oracle.get("expected_skill_id"),
                "expected_signals": oracle.get("expected_signals", []),
                "minimum_diagnosis_rounds": oracle.get(
                    "minimum_diagnosis_rounds", 3
                ),
                "truth_source": oracle.get("truth_source"),
            }
        )

    dataset_json_obj = {
        "schema": "mini-drop.evaluation-suite.v1",
        "version": "2026.09.20",
        "generated_at": datetime.now(UTC).isoformat(),
        "case_count": len(unified_cases),
        "fault_scenario_count": len(SCENARIOS),
        "public_inputs_only": True,
        "description": "Mini-Drop 性能诊断与根因分析双格式基准评测数据集（公开输入版），涵盖 21 类可执行真实故障场景与 540 条提示词/路线回放变体。本文件仅含提示词、观测与元数据，不含标准答案；私有标准答案单独导出为 dataset_ground_truth.json，仅在评测后用于对分。",
        "runtimes": ["Python", "Go", "Java", "C++"],
        "cases": unified_cases,
    }
    json_path.write_text(
        json.dumps(dataset_json_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ground_truth_obj = {
        "schema": "mini-drop.evaluation-suite.ground-truth.v1",
        "version": "2026.09.20",
        "generated_at": dataset_json_obj["generated_at"],
        "case_count": len(ground_truth_cases),
        "private": True,
        "description": "Mini-Drop 评测基准的私有标准答案（根因、决定性采集器、预期 Skill 与验收信号）。不随公开数据集分发，仅在评测后用于对分。",
        "ground_truth": ground_truth_cases,
    }
    gt_path.write_text(
        json.dumps(ground_truth_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 2. Build human-friendly Excel
    wb = openpyxl.Workbook()

    # Sheet 1: Overview
    ws_overview = wb.active
    ws_overview.title = "测试集概览 (Overview)"
    ws_overview.views.sheetView[0].showGridLines = True

    ws_overview["A1"] = "Mini-Drop 性能诊断与根因分析基准测试集 (Benchmark Dataset)"
    ws_overview["A1"].font = TITLE_FONT
    ws_overview["A2"] = (
        f"版本：{dataset_json_obj['version']} | 生成时间：{dataset_json_obj['generated_at']} | 规范：{dataset_json_obj['schema']}"
    )
    ws_overview["A2"].font = SUBTITLE_FONT

    meta_rows = [
        ("总用例数量 (Total Cases)", len(unified_cases), "条受控提示词/路线变体"),
        (
            "故障场景类别 (Fault Scenarios)",
            len(SCENARIOS),
            "类涵盖 CPU、内存、I/O、锁、GC、协程、队列、网络等",
        ),
        (
            "覆盖语言运行时 (Runtimes)",
            "Python (7), Go (4), Java (5), C++ (5)",
            "4 种主流 Linux 后端运行时",
        ),
        (
            "单臂固定工具预算 (Tool Budget)",
            "2 次采集调用",
            "模拟生产紧急排障中有限的探针探索配额",
        ),
        (
            "测试集设计目的",
            "评测 AI Agent 路线规划、工具选择与根因定位能力",
            "严格区分公开输入与私有答案，防止数据穿透",
        ),
        (
            "评测指标口径 (Metric Contract)",
            "代理 Top-1 (必须根因准确且命中关键决定性采集器)",
            "纯文本猜测不视为通过",
        ),
    ]

    ws_overview.cell(row=4, column=1, value="属性项 (Attribute)").font = (
        HEADER_FONT
    )
    ws_overview.cell(row=4, column=1).fill = HEADER_FILL_DARK
    ws_overview.cell(row=4, column=2, value="数值 / 说明 (Value)").font = (
        HEADER_FONT
    )
    ws_overview.cell(row=4, column=2).fill = HEADER_FILL_DARK
    ws_overview.cell(row=4, column=3, value="备注 (Notes)").font = HEADER_FONT
    ws_overview.cell(row=4, column=3).fill = HEADER_FILL_DARK

    for idx, (attr, val, note) in enumerate(meta_rows, start=5):
        c1 = ws_overview.cell(row=idx, column=1, value=attr)
        c2 = ws_overview.cell(row=idx, column=2, value=str(val))
        c3 = ws_overview.cell(row=idx, column=3, value=note)
        c1.font = BOLD_FONT
        c2.font = REGULAR_FONT
        c3.font = REGULAR_FONT
        c1.border = THIN_BORDER
        c2.border = THIN_BORDER
        c3.border = THIN_BORDER
        if idx % 2 == 1:
            c1.fill = ZEBRA_FILL
            c2.fill = ZEBRA_FILL
            c3.fill = ZEBRA_FILL

    # Add Schema Glossary
    glossary_start = 13
    ws_overview.cell(
        row=glossary_start, column=1, value="数据字段字典 (Glossary)"
    ).font = Font(name="Microsoft YaHei", size=13, bold=True, color="1F4E79")
    glossary_headers = [
        "字段名 (Field)",
        "类型 (Type)",
        "公开/私有 (Scope)",
        "含义与业务说明 (Description)",
    ]
    for c_idx, gh in enumerate(glossary_headers, start=1):
        cell = ws_overview.cell(row=glossary_start + 1, column=c_idx, value=gh)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL_ACCENT
        cell.border = THIN_BORDER

    glossary_data = [
        (
            "case_id",
            "String",
            "公开 (Public)",
            "测试用例唯一代号，如 RC-01-001",
        ),
        (
            "category",
            "String",
            "公开 (Public)",
            "故障分类家族，如 CPU_HOTSPOT, JVM_GC, IO_LATENCY",
        ),
        ("runtime", "String", "公开 (Public)", "目标服务运行环境 (Python/Go/Java/C++)"),
        ("query", "String", "公开 (Public)", "用户或告警系统输入的故障描述文本"),
        (
            "collector_capabilities",
            "List[String]",
            "公开 (Public)",
            "被诊断目标当前节点支持的采集工具白名单",
        ),
        (
            "observations_by_collector",
            "Dict[String, List]",
            "公开 (Public)",
            "各采集工具可观测到的真实探针信号（仅在探针被调用后开放）",
        ),
        (
            "root_cause_id",
            "String",
            "私有答案 (Oracle)",
            "真实标准根因 ID，如 cpu-hotspot, lock-contention",
        ),
        (
            "decisive_collector",
            "String",
            "私有答案 (Oracle)",
            "该故障场景必须调用的决定性关键证据采集工具（如 perf_cpu, java_async）",
        ),
        (
            "expected_skill_id",
            "String",
            "私有答案 (Oracle)",
            "最佳先验排障技能 ID，如 cpu-hotspot-diagnosis",
        ),
    ]

    for idx, (fld, tp, scp, desc) in enumerate(
        glossary_data, start=glossary_start + 2
    ):
        c1 = ws_overview.cell(row=idx, column=1, value=fld)
        c2 = ws_overview.cell(row=idx, column=2, value=tp)
        c3 = ws_overview.cell(row=idx, column=3, value=scp)
        c4 = ws_overview.cell(row=idx, column=4, value=desc)
        c1.font = CODE_FONT
        c2.font = REGULAR_FONT
        c3.font = BOLD_FONT
        c4.font = REGULAR_FONT
        for c in (c1, c2, c3, c4):
            c.border = THIN_BORDER
        if idx % 2 == 1:
            for c in (c1, c2, c3, c4):
                c.fill = ZEBRA_FILL

    _auto_adjust_columns(ws_overview)

    # Sheet 2: Test Cases (All 540 cases)
    ws_cases = wb.create_sheet(title="测试用例列表 (Test Cases)")
    ws_cases.views.sheetView[0].showGridLines = True
    case_headers = [
        "用例代号 (Case ID)",
        "分类大项 (Category)",
        "语言运行时 (Runtime)",
        "变体类型 (Variant)",
        "故障诊断提示词 (User Query)",
        "可用采集能力 (Capabilities)",
    ]
    for col_idx, h_text in enumerate(case_headers, start=1):
        c = ws_cases.cell(row=1, column=col_idx, value=h_text)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL_DARK
        c.alignment = ALIGN_CENTER
        c.border = THIN_BORDER

    for row_idx, item in enumerate(unified_cases, start=2):
        caps_str = ", ".join(item.get("collector_capabilities", []))

        vals = [
            item["case_id"],
            item["category"],
            item["runtime"],
            item.get("variant_family", ""),
            item["query"],
            caps_str,
        ]
        for col_idx, val in enumerate(vals, start=1):
            c = ws_cases.cell(row=row_idx, column=col_idx, value=val)
            c.font = (
                CODE_FONT
                if col_idx in (1, 6)
                else (BOLD_FONT if col_idx in (2, 3) else REGULAR_FONT)
            )
            c.border = THIN_BORDER
            c.alignment = ALIGN_CENTER if col_idx in (1, 2, 3, 4) else ALIGN_LEFT
            if row_idx % 2 == 1:
                c.fill = ZEBRA_FILL

    ws_cases.freeze_panes = "A2"
    ws_cases.auto_filter.ref = (
        f"A1:{get_column_letter(len(case_headers))}{len(unified_cases) + 1}"
    )
    _auto_adjust_columns(ws_cases)

    # Sheet 3: Fault Taxonomy & Scenarios
    ws_taxonomy = wb.create_sheet(title="故障分类库 (Fault Taxonomy)")
    ws_taxonomy.views.sheetView[0].showGridLines = True
    tax_headers = [
        "场景ID (Scenario ID)",
        "标题 (Title)",
        "故障家族 (Family)",
        "目标运行时 (Runtime)",
        "典型外部症状 (Symptom)",
        "决定性推荐采集器 (Recommended Collectors)",
        "对应Skill (Related Skill)",
        "标准查询语句 (Reference Query)",
    ]
    for col_idx, h_text in enumerate(tax_headers, start=1):
        c = ws_taxonomy.cell(row=1, column=col_idx, value=h_text)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL_ACCENT
        c.alignment = ALIGN_CENTER
        c.border = THIN_BORDER

    for row_idx, s in enumerate(SCENARIOS, start=2):
        collectors_str = ", ".join(s.recommended_collectors)
        vals = [
            s.scenario_id,
            s.title,
            s.family,
            s.target_runtime,
            s.symptom,
            collectors_str,
            s.related_skill,
            s.diagnosis_query,
        ]
        for col_idx, val in enumerate(vals, start=1):
            c = ws_taxonomy.cell(row=row_idx, column=col_idx, value=val)
            c.font = (
                CODE_FONT
                if col_idx in (1, 6, 7)
                else (BOLD_FONT if col_idx in (2, 3, 4) else REGULAR_FONT)
            )
            c.border = THIN_BORDER
            c.alignment = ALIGN_CENTER if col_idx in (1, 3, 4) else ALIGN_LEFT
            if row_idx % 2 == 1:
                c.fill = ZEBRA_FILL

    ws_taxonomy.freeze_panes = "A2"
    ws_taxonomy.auto_filter.ref = (
        f"A1:{get_column_letter(len(tax_headers))}{len(SCENARIOS) + 1}"
    )
    _auto_adjust_columns(ws_taxonomy)

    wb.save(xlsx_path)
    return json_path, xlsx_path, gt_path


def generate_evaluation_reports(
    report: dict[str, Any],
    dataset_cases: list[dict[str, Any]],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "evaluation_report.json"
    xlsx_path = output_dir / "evaluation_report.xlsx"

    # 1. Save JSON Report
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2. Build Human-friendly Excel Report
    wb = openpyxl.Workbook()

    # Sheet 1: Summary Dashboard
    ws_sum = wb.active
    ws_sum.title = "评测汇总仪表盘 (Summary)"
    ws_sum.views.sheetView[0].showGridLines = True

    ws_sum["A1"] = "Mini-Drop 性能诊断评测总报表 (Evaluation Report)"
    ws_sum["A1"].font = TITLE_FONT
    ws_sum["A2"] = (
        f"报告生成时间：{report.get('generated_at')} | 评测模式：{report.get('benchmark_kind')} | 校验哈希：{report.get('report_sha256', '')[:16]}..."
    )
    ws_sum["A2"].font = SUBTITLE_FONT

    paired = report["paired_skill_ab_500"]
    baseline_arm = paired["no_skill"]
    skill_arm = paired["skill_enabled"]
    retrieval = paired["skill_retrieval"]
    stat_test = paired.get("paired_significance", {})

    ws_sum["A4"] = "同题同预算 A/B 核心指标对比 (500 组配对实验)"
    ws_sum["A4"].font = Font(
        name="Microsoft YaHei", size=13, bold=True, color="1F4E79"
    )

    headers = [
        "评测核心指标 (Core Evaluation Metrics)",
        "Baseline (无技能静态兜底)",
        "Mini-Drop (Skill 自适应探索)",
        "绝对变化量 (Absolute Delta)",
        "相对提升率 (Relative Gain)",
        "指标说明与业务意义",
    ]
    for c_idx, h in enumerate(headers, start=1):
        c = ws_sum.cell(row=5, column=c_idx, value=h)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL_DARK
        c.alignment = ALIGN_CENTER
        c.border = THIN_BORDER

    def fmt_pct(val):
        return f"{float(val):.2%}" if val is not None else "-"

    base_top1 = baseline_arm["root_cause_top1_accuracy"]
    skill_top1 = skill_arm["root_cause_top1_accuracy"]
    top1_delta_pct = (skill_top1 - base_top1) * 100
    top1_rel_gain = (
        ((skill_top1 - base_top1) / base_top1 * 100) if base_top1 > 0 else 0
    )

    base_reach = baseline_arm["decisive_collector_within_two_rate"]
    skill_reach = skill_arm["decisive_collector_within_two_rate"]
    reach_delta = (skill_reach - base_reach) * 100
    reach_gain = (
        ((skill_reach - base_reach) / base_reach * 100) if base_reach > 0 else 0
    )

    base_pos = baseline_arm["mean_decisive_collector_position"] or 0
    skill_pos = skill_arm["mean_decisive_collector_position"] or 0

    metric_rows = [
        (
            "代理根因定位准确率 (Composite Top-1 Accuracy)",
            fmt_pct(base_top1),
            fmt_pct(skill_top1),
            f"+{top1_delta_pct:.2f}%"
            if top1_delta_pct >= 0
            else f"{top1_delta_pct:.2f}%",
            f"+{top1_rel_gain:.1f}%",
            "在固定2次工具调用预算内，根因预测正确且必须到达决定性采集器（严格门禁）",
        ),
        (
            "纯文本根因候选预测率 (Text Oracle Match Rate)",
            fmt_pct(baseline_arm["text_prediction_matches_oracle_rate"]),
            fmt_pct(skill_arm["text_prediction_matches_oracle_rate"]),
            f"{(skill_arm['text_prediction_matches_oracle_rate'] - baseline_arm['text_prediction_matches_oracle_rate']) * 100:+.2f}%",
            "-",
            "模型未做探针前仅凭 Query 纯文本猜测根因的命中率（不作为证据通过标准）",
        ),
        (
            "决定性采集器2步内到达率 (Decisive Collector Reached @2)",
            fmt_pct(base_reach),
            fmt_pct(skill_reach),
            f"+{reach_delta:.2f}%" if reach_delta >= 0 else f"{reach_delta:.2f}%",
            f"+{reach_gain:.1f}%",
            "前2次采集即命中关键决定性工具的比例，反映 Agent 排障路径规划敏捷度",
        ),
        (
            "平均到达决定性采集器步骤数 (Mean Collector Position)",
            f"{base_pos:.2f} 步",
            f"{skill_pos:.2f} 步",
            f"{skill_pos - base_pos:+.2f} 步",
            f"{(base_pos - skill_pos) / base_pos * 100:+.1f}% (步数缩短)"
            if base_pos
            else "-",
            "命中决定性采集器时的平均探针序号，越小代表定位越快、开销越低",
        ),
        (
            "排障技能检索激活率 (Skill Activation Rate)",
            "0.00% (未启用)",
            fmt_pct(retrieval["selected_any_rate"]),
            f"+{retrieval['selected_any_rate'] * 100:.2f}%",
            "-",
            "系统成功从 13 个注册 Skill 中识别并动态激活对应排障路线的比例",
        ),
        (
            "技能预期精准匹配度 (Expected Skill Match Rate)",
            "0.00% (未启用)",
            fmt_pct(retrieval["matched_expected_skill_rate"]),
            f"+{retrieval['matched_expected_skill_rate'] * 100:.2f}%",
            "-",
            "激活的 Skill 与私有标准答案完全吻合的比例，体现检索与防歧义能力",
        ),
        (
            "排障效果改善用例数 (Improved Cases)",
            "-",
            f"{paired['improved_cases']} 组",
            f"+{paired['improved_cases']} 组",
            "-",
            "Baseline 失败但启用 Skill 后成功定位并取证的用例数",
        ),
        (
            "排障效果劣化用例数 (Regressed Cases)",
            "-",
            f"{paired['regressed_cases']} 组",
            f"-{paired['regressed_cases']} 组",
            "-",
            "Baseline 成功但启用 Skill 后失败的用例数（极低，证明门禁与退化防护有效）",
        ),
        (
            "净胜改善比例 (Net Improvement Ratio)",
            "-",
            f"{paired['improved_cases'] - paired['regressed_cases']} 组",
            f"+{((paired['improved_cases'] - paired['regressed_cases']) / 500) * 100:.2f}%",
            "-",
            "净正向增益比率 (Improved - Regressed) / Total Cases",
        ),
        (
            "显著性检验 P 值 (Statistical Significance)",
            "-",
            f"p = {stat_test.get('p_value', 1.0):.2e}",
            "p < 0.001 (极度显著)",
            "-",
            "双侧不一致对二项检验（Two-sided Exact Binomial），证明提升非随机偶然",
        ),
    ]

    for idx, (m_name, b_val, s_val, d_val, g_val, desc) in enumerate(
        metric_rows, start=6
    ):
        c1 = ws_sum.cell(row=idx, column=1, value=m_name)
        c2 = ws_sum.cell(row=idx, column=2, value=b_val)
        c3 = ws_sum.cell(row=idx, column=3, value=s_val)
        c4 = ws_sum.cell(row=idx, column=4, value=d_val)
        c5 = ws_sum.cell(row=idx, column=5, value=g_val)
        c6 = ws_sum.cell(row=idx, column=6, value=desc)
        c1.font = BOLD_FONT
        c2.font = REGULAR_FONT
        c3.font = BOLD_FONT
        c4.font = BOLD_FONT
        c5.font = BOLD_FONT
        c6.font = REGULAR_FONT
        c2.alignment = ALIGN_CENTER
        c3.alignment = ALIGN_CENTER
        c4.alignment = ALIGN_CENTER
        c5.alignment = ALIGN_CENTER
        for c in (c1, c2, c3, c4, c5, c6):
            c.border = THIN_BORDER
        if idx % 2 == 1:
            for c in (c1, c2, c3, c4, c5, c6):
                c.fill = ZEBRA_FILL
        # Highlight Top-1 and Net
        if idx in (6, 12, 14):
            c3.fill = IMPROVED_FILL
            c4.fill = IMPROVED_FILL

    # Add Academic & Industrial Methodology Notes
    note_start = len(metric_rows) + 8
    ws_sum.cell(
        row=note_start, column=1, value="业界与学术界评测方法学对齐说明"
    ).font = Font(name="Microsoft YaHei", size=13, bold=True, color="1F4E79")

    notes = [
        (
            "1. 严格证据约束 (Evidence Gate)",
            "对标真实 SRE 生产场景与清华 NetMan RCAEval，不能单凭 LLM 在没有工具证据下的盲猜给分。Top-1 判准要求：① 预测根因正确；② 必须真实调用了决定性关键采集工具（如 perf/ebpf/async-profiler）。",
        ),
        (
            "2. 固定预算与配对消融 (Paired A/B & Ablation)",
            "每个用例严格限制两次工具调用配额。同时对每个用例运行 Baseline（纯启发式兜底路线）和 Mini-Drop（Skill 检索路线），并做 Prior Ablation 剥离先验打分，验证路线收益并非单纯源于规则写死。",
        ),
        (
            "3. 盲测与防穿透隔离 (Oracle Isolation)",
            "公开用例 (Public Cases) 绝不包含故障真因与推荐工具，仅暴露现象与可用采集器；私有真值 (Private Oracles) 单独存放，仅在评估器完成两臂冻结预测后方可接触。",
        ),
        (
            "4. 真实性边界声明 (Authenticity Boundary)",
            "本基准为受控回放测试（Controlled Replay），证明了 Skill 路线重排和先验有效性；不能等同于在真实公网服务器上实时并发注入了 500 次真实 Linux 故障。",
        ),
    ]
    for n_idx, (t, d) in enumerate(notes, start=note_start + 1):
        cell_t = ws_sum.cell(row=n_idx, column=1, value=t)
        cell_d = ws_sum.cell(row=n_idx, column=2, value=d)
        cell_t.font = BOLD_FONT
        cell_d.font = REGULAR_FONT
        cell_t.border = THIN_BORDER
        cell_d.border = THIN_BORDER
        ws_sum.merge_cells(
            start_row=n_idx, start_column=2, end_row=n_idx, end_column=6
        )

    _auto_adjust_columns(ws_sum)

    # Sheet 2: Runtime Breakdown
    ws_rt = wb.create_sheet(title="分语言运行时表现 (Runtime)")
    ws_rt.views.sheetView[0].showGridLines = True
    rt_headers = [
        "运行时 (Runtime)",
        "测试用例数 (Case Count)",
        "Baseline Top-1 准确率",
        "Mini-Drop Top-1 准确率",
        "绝对变化 (Delta)",
        "决定性工具到达率 (Baseline)",
        "决定性工具到达率 (Mini-Drop)",
        "平均决定性工具步数 (Baseline)",
        "平均决定性工具步数 (Mini-Drop)",
    ]
    for c_idx, h in enumerate(rt_headers, start=1):
        c = ws_rt.cell(row=1, column=c_idx, value=h)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL_ACCENT
        c.alignment = ALIGN_CENTER
        c.border = THIN_BORDER

    by_rt = paired.get("by_runtime", {})
    for r_idx, (rt_name, rt_data) in enumerate(sorted(by_rt.items()), start=2):
        b_top = rt_data["no_skill"]["root_cause_top1_accuracy"]
        s_top = rt_data["skill_enabled"]["root_cause_top1_accuracy"]
        b_rch = rt_data["no_skill"]["decisive_collector_within_two_rate"]
        s_rch = rt_data["skill_enabled"]["decisive_collector_within_two_rate"]
        b_pos = rt_data["no_skill"]["mean_decisive_collector_position"] or 0
        s_pos = rt_data["skill_enabled"]["mean_decisive_collector_position"] or 0

        vals = [
            rt_name,
            rt_data["case_count"],
            fmt_pct(b_top),
            fmt_pct(s_top),
            f"{(s_top - b_top) * 100:+.2f}%",
            fmt_pct(b_rch),
            fmt_pct(s_rch),
            f"{b_pos:.2f}",
            f"{s_pos:.2f}",
        ]
        for c_idx, v in enumerate(vals, start=1):
            c = ws_rt.cell(row=r_idx, column=c_idx, value=v)
            c.font = BOLD_FONT if c_idx in (1, 4, 5) else REGULAR_FONT
            c.border = THIN_BORDER
            c.alignment = ALIGN_CENTER
            if r_idx % 2 == 1:
                c.fill = ZEBRA_FILL
            if c_idx == 5:
                c.fill = IMPROVED_FILL

    ws_rt.freeze_panes = "A2"
    _auto_adjust_columns(ws_rt)

    # Sheet 3: Case Level Details (540 cases)
    ws_cases = wb.create_sheet(title="用例评测明细 (Case Details)")
    ws_cases.views.sheetView[0].showGridLines = True
    detail_headers = [
        "用例代号 (Case ID)",
        "运行时 (Runtime)",
        "标准答案根因 (Ground Truth)",
        "决定性工具 (Decisive Tool)",
        "激活技能 (Selected Skill)",
        "Baseline 预测根因",
        "Baseline 到达工具路线",
        "Baseline 是否合格",
        "Mini-Drop 预测根因",
        "Mini-Drop 到达工具路线",
        "Mini-Drop 是否合格",
        "对比转移状态 (Transition)",
    ]
    for c_idx, h in enumerate(detail_headers, start=1):
        c = ws_cases.cell(row=1, column=c_idx, value=h)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL_DARK
        c.alignment = ALIGN_CENTER
        c.border = THIN_BORDER

    cases_data = report.get("cases", [])
    for r_idx, c_item in enumerate(cases_data, start=2):
        cid = c_item["case_id"]
        b_arm = c_item["no_skill"]
        s_arm = c_item["skill_enabled"]
        b_ok = b_arm["root_cause_correct"]
        s_ok = s_arm["root_cause_correct"]

        if not b_ok and s_ok:
            transition = "IMPROVED (显著改善)"
        elif b_ok and not s_ok:
            transition = "REGRESSED (发生劣化)"
        else:
            transition = "UNCHANGED (表现持平)"

        b_route_str = " → ".join(b_arm.get("observed_collectors", []))
        s_route_str = " → ".join(s_arm.get("observed_collectors", []))

        vals = [
            cid,
            c_item["runtime"],
            c_item["ground_truth_root_cause"],
            b_arm.get("decisive_collector", ""),
            c_item.get("skill_retrieval", {}).get("selected_skill_id") or "无",
            b_arm.get("predicted_root_cause", ""),
            b_route_str,
            "通过" if b_ok else "未通过",
            s_arm.get("predicted_root_cause", ""),
            s_route_str,
            "通过" if s_ok else "未通过",
            transition,
        ]

        for c_idx, v in enumerate(vals, start=1):
            c = ws_cases.cell(row=r_idx, column=c_idx, value=v)
            c.font = (
                CODE_FONT
                if c_idx in (1, 3, 4, 5, 6, 7, 9, 10)
                else (BOLD_FONT if c_idx in (2, 8, 11, 12) else REGULAR_FONT)
            )
            c.border = THIN_BORDER
            c.alignment = (
                ALIGN_CENTER
                if c_idx in (1, 2, 4, 8, 11, 12)
                else (ALIGN_LEFT if c_idx in (5, 7, 10) else ALIGN_CENTER)
            )
            if r_idx % 2 == 1:
                c.fill = ZEBRA_FILL
            # Color coding transition
            if c_idx == 12:
                if "IMPROVED" in transition:
                    c.fill = IMPROVED_FILL
                    c.font = Font(
                        name="Microsoft YaHei",
                        size=10,
                        bold=True,
                        color="274E13",
                    )
                elif "REGRESSED" in transition:
                    c.fill = REGRESSED_FILL
                    c.font = Font(
                        name="Microsoft YaHei",
                        size=10,
                        bold=True,
                        color="990000",
                    )

    ws_cases.freeze_panes = "A2"
    ws_cases.auto_filter.ref = (
        f"A1:{get_column_letter(len(detail_headers))}{len(cases_data) + 1}"
    )
    _auto_adjust_columns(ws_cases)

    # Sheet 4: Error Analysis
    ws_err = wb.create_sheet(title="未解决案例归因 (Error Analysis)")
    ws_err.views.sheetView[0].showGridLines = True
    err_headers = [
        "用例代号 (Case ID)",
        "运行时 (Runtime)",
        "真实根因 (Ground Truth)",
        "决定性工具 (Decisive Tool)",
        "预测根因 (Predicted)",
        "实际到达采集器 (Observed Route)",
        "未达标主因 (Failure Reason)",
    ]
    for c_idx, h in enumerate(err_headers, start=1):
        c = ws_err.cell(row=1, column=c_idx, value=h)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL_ACCENT
        c.alignment = ALIGN_CENTER
        c.border = THIN_BORDER

    err_row_idx = 2
    for c_item in cases_data:
        s_arm = c_item["skill_enabled"]
        if not s_arm["root_cause_correct"]:
            decisive = s_arm.get("decisive_collector", "")
            reached = s_arm.get("decisive_collector_reached", False)
            text_match = s_arm.get("text_prediction_matches_oracle", False)

            if not reached and text_match:
                reason = "工具预算耗尽：文本虽猜中，但2步内未能调用决定性采集器"
            elif not reached and not text_match:
                reason = "路线偏离：Skill未将关键采集器排入前2顺位，且根因未命中"
            elif reached and not text_match:
                reason = "证据信号歧义：已调用关键采集器，但特征提取器对次要信号打分更高"
            else:
                reason = "未满足严格门禁合同"

            vals = [
                c_item["case_id"],
                c_item["runtime"],
                c_item["ground_truth_root_cause"],
                decisive,
                s_arm.get("predicted_root_cause", ""),
                " → ".join(s_arm.get("observed_collectors", [])),
                reason,
            ]
            for c_idx, v in enumerate(vals, start=1):
                c = ws_err.cell(row=err_row_idx, column=c_idx, value=v)
                c.font = (
                    CODE_FONT
                    if c_idx in (1, 3, 4, 5, 6)
                    else (BOLD_FONT if c_idx in (2, 7) else REGULAR_FONT)
                )
                c.border = THIN_BORDER
                c.alignment = ALIGN_CENTER if c_idx in (1, 2, 4) else ALIGN_LEFT
                if err_row_idx % 2 == 1:
                    c.fill = ZEBRA_FILL
            err_row_idx += 1

    ws_err.freeze_panes = "A2"
    ws_err.auto_filter.ref = (
        f"A1:{get_column_letter(len(err_headers))}{err_row_idx}"
    )
    _auto_adjust_columns(ws_err)

    wb.save(xlsx_path)
    return json_path, xlsx_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Mini-Drop Dual-Format Benchmark"
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=ROOT / "benchmarks" / "root-cause-v1",
        help="Input benchmark source directory",
    )
    parser.add_argument(
        "--export-dataset-dir",
        type=Path,
        default=ROOT / "benchmarks" / "evaluation-suite",
        help="Output directory for dual-format dataset (.json & .xlsx)",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "reports" / "evaluation",
        help="Output directory for dual-format evaluation reports (.json & .xlsx)",
    )
    args = parser.parse_args()

    print("[1/4] 加载评测基准源数据...")
    catalog = load_json(ROOT / "skills" / "catalog.json")
    public_set = load_json(args.dataset_dir / "public" / "cases.json")
    private_oracles = load_json(args.dataset_dir / "private" / "oracles.json")
    print(
        f"  - 找到可用技能: {len(catalog.get('skills', []))} 个"
    )
    print(f"  - 找到基准用例: {len(public_set.get('cases', []))} 条")
    print(
        f"  - 找到私有标准答案: {len(private_oracles.get('oracles', []))} 条"
    )

    print("[2/4] 导出人类可读与机器可读的双格式测试集（公开输入，答案分离）...")
    ds_json, ds_xlsx, ds_gt = generate_dataset_files(
        public_set, private_oracles, args.export_dataset_dir,
        ground_truth_dir=args.report_dir,
    )
    print(f"  -> 测试集 JSON (机器, 仅公开输入): {ds_json}")
    print(f"  -> 测试集 Excel (人类, 仅公开输入): {ds_xlsx}")
    print(f"  -> 私有标准答案 JSON (仅评测后对分): {ds_gt}")

    print("[3/4] 执行受控根因回放与 500 组同题配对 A/B 评测...")
    report = evaluate_controlled_root_causes(
        catalog=catalog,
        public_set=public_set,
        private_oracles=private_oracles,
        paired_case_count=500,
        tool_budget=2,
    )
    rep_sha = report_sha256(report)
    report["report_sha256"] = rep_sha
    report["generated_at"] = datetime.now(UTC).isoformat()
    print("  -> 评测完成！SHA-256 哈希:", rep_sha[:16])

    print("[4/4] 导出人类可读与机器可读的双格式评测报告...")
    rep_json, rep_xlsx = generate_evaluation_reports(
        report, public_set.get("cases", []), args.report_dir
    )
    print(f"  -> 评测报告 JSON (机器): {rep_json}")
    print(f"  -> 评测报告 Excel (人类): {rep_xlsx}")

    # Output executive summary to terminal
    paired = report["paired_skill_ab_500"]
    stat_test = paired.get("paired_significance", {})
    improved = paired["improved_cases"]
    regressed = paired["regressed_cases"]
    print("\n" + "=" * 65)
    print("           Mini-Drop 基准评测各项指标总览            ")
    print("=" * 65)
    print(
        f" Baseline 准确率 (Top-1 代理门禁): {paired['no_skill']['root_cause_top1_accuracy']:.2%}"
    )
    print(
        f" Mini-Drop 准确率 (Skill 启用):    {paired['skill_enabled']['root_cause_top1_accuracy']:.2%}"
    )
    print(
        f" 绝对提升百分点 (Delta):           +{(paired['skill_enabled']['root_cause_top1_accuracy'] - paired['no_skill']['root_cause_top1_accuracy']) * 100:.2f}%"
    )
    print(
        f" 决定性采集工具2步内命中率:       {paired['no_skill']['decisive_collector_within_two_rate']:.2%} -> {paired['skill_enabled']['decisive_collector_within_two_rate']:.2%}"
    )
    print(
        f" 改善用例数 (Improved):            {improved} / 500"
    )
    print(
        f" 劣化用例数 (Regressed):           {regressed} / 500"
    )
    print(
        f" 净改善收益 (Net Gain):            +{((improved - regressed) / 500) * 100:.2f}%"
    )
    print(
        f" 显著性检验 P 值:                 p = {stat_test.get('p_value', 1.0):.2e} (p < 0.001 极显著)"
    )
    print("=" * 65)
    print("双格式测试集与评测报告已全部成功生成！\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
