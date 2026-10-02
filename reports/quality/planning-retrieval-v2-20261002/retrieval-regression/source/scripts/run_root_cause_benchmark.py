#!/usr/bin/env python3
"""Run the 540-case root-cause replay and 500-pair Skill A/B evaluation."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from server.app.drop_insight.benchmark_v2 import load_json
from server.app.drop_insight.root_cause_benchmark import (
    evaluate_controlled_root_causes,
    report_sha256,
)


ROOT = Path(__file__).resolve().parents[1]


def _markdown(report: dict) -> str:
    root = report["root_cause_evaluation_540"]
    paired = report["paired_skill_ab_500"]
    dataset = report["dataset"]
    diversity = dataset["diversity_audit"]
    root_retrieval = root["skill_retrieval"]
    paired_retrieval = paired["skill_retrieval"]
    rows = list(report.get("cases") or [])
    paired_rows = rows[: int(paired["case_count"])]

    def percentage(value: float) -> str:
        return f"{float(value):.2%}"

    def interval(metric: dict) -> str:
        bounds = metric["accuracy_wilson_95"]
        return f"[{percentage(bounds['lower'])}, {percentage(bounds['upper'])}]"

    def route(arm: dict) -> str:
        return " → ".join(str(item) for item in arm.get("observed_collectors") or []) or "无"

    variant_counts: dict[str, int] = {}
    for row in rows:
        family = str(row.get("variant_family") or "UNKNOWN")
        variant_counts[family] = variant_counts.get(family, 0) + 1

    regressions = [
        row
        for row in paired_rows
        if row["no_skill"]["root_cause_correct"]
        and not row["skill_enabled"]["root_cause_correct"]
    ]
    improvements = [
        row
        for row in paired_rows
        if not row["no_skill"]["root_cause_correct"]
        and row["skill_enabled"]["root_cause_correct"]
    ]
    representative_improvements: list[dict] = []
    represented_causes: set[str] = set()
    for row in improvements:
        root_cause = str(row["ground_truth_root_cause"])
        if root_cause in represented_causes:
            continue
        represented_causes.add(root_cause)
        representative_improvements.append(row)
        if len(representative_improvements) >= 8:
            break

    lines = [
        "# Mini-Drop 根因测试集与 Skill A/B 基准评测报告",
        "",
        "> **结论先行：这不是“540 个真实故障的生产根因准确率报告”。** "
        "它是 21 个已知故障合同生成的 540 条提示词/路线变体。在固定两次工具调用预算下，启用 Skill 的 500 组同题"
        "“证据路线达标率（代理 Top-1）”"
        f"由 {percentage(paired['no_skill']['root_cause_top1_accuracy'])} 提升到 "
        f"{percentage(paired['skill_enabled']['root_cause_top1_accuracy'])}。当前结果证明 Skill 改善了已知合同下的采集路线排序；"
        "它不能证明生产根因准确率达到 68%，也不能证明完整 Agent 能力提升。",
        "",
        "## 1. 报告身份",
        "",
        f"- 生成时间（UTC）：`{report['generated_at']}`",
        f"- 数据集版本：`{dataset['version']}`",
        f"- 数据集模式：`{report['benchmark_kind']}`",
        f"- 提示词/路线回放变体：`{dataset['case_count']}` 条",
        f"- 同题同预算 A/B：`{paired['case_count']}` 组",
        f"- 可执行故障合同：`{dataset['executable_fault_contract_count']}` 个",
        f"- 每组每臂工具预算：`{paired['tool_budget_per_arm']}` 次",
        f"- 生成器随机种子：`{dataset['generator_seed']}`",
        f"- 报告内容 SHA-256：`{report['report_sha256']}`",
        "",
        "## 2. Benchmark 到底是什么",
        "",
        "Benchmark 不是单独一份题库，而是“固定题目 + 私有标准答案 + 固定执行规则 + 指标 + 可复现报告”的组合。"
        "本次评测把公开输入和私有 Oracle 分开：诊断侧只能读取问题、目标环境和按采集器划分的观察；"
        "评分侧再使用 Oracle 中的根因、决定性采集器和预期 Skill 判分。",
        "",
        "本评测沿用字段名 `root_cause_top1_accuracy` 以兼容页面，但它实际是一个**组合代理指标**。一次代理 Top-1 判为正确必须同时满足：",
        "",
        "1. 预测的根因 ID 与私有 Oracle 一致；",
        "2. 在同一固定工具预算内，实际路线已经到达该故障合同指定的决定性采集器。",
        "",
        "因此，只靠问题文本猜对根因、但没有走到关键证据采集器，不计为正确。反过来也要注意："
        "这里的“到达采集器”只是路线代理，并没有真正创建生产 Task、Artifact、Evidence Envelope、Analyzer 和 Report，"
        "也没有执行线上 Evidence Gate。",
        "",
        "## 3. 测试集怎样构建",
        "",
        "540 条 Case 由故障广场中的 21 个可执行白名单故障合同生成，没有读取旧测试 Case。"
        "生成器围绕同一个合同改变中英文表述、噪声信息、服务名和干扰线索，同时保持私有根因、观察模板和决定性采集器不变。"
        "所以它们应称为 540 条**提示词/路由变体**，而不是 540 个彼此独立的遥测故障。",
        "",
        "### 3.1 语言分布",
        "",
        "| 运行时 | Case 数 | 占比 |",
        "|---|---:|---:|",
    ]
    for runtime in ("Python", "Java", "C++", "Go"):
        count = int(dataset["runtime_counts"].get(runtime, 0))
        lines.append(f"| {runtime} | {count} | {count / dataset['case_count']:.2%} |")

    lines.extend(
        [
            "",
            "### 3.2 变体难度",
            "",
            "| 变体类型 | Case 数 | 含义 |",
            "|---|---:|---|",
            f"| PARAPHRASE | {variant_counts.get('PARAPHRASE', 0)} | 同一故障的不同自然语言表达 |",
            f"| NOISY | {variant_counts.get('NOISY', 0)} | 加入刷新、发布、背景描述等非决定性噪声 |",
            f"| HARD_CONFOUNDING | {variant_counts.get('HARD_CONFOUNDING', 0)} | 加入容易误导类别修正或 Skill 检索的强干扰线索 |",
            "",
            "### 3.3 21 个根因场景",
            "",
            "| 根因合同 | Case 数 |",
            "|---|---:|",
        ]
    )
    for scenario, count in sorted(dataset["scenario_counts"].items()):
        lines.append(f"| `{scenario}` | {count} |")

    lines.extend(
        [
            "",
            "### 3.4 数据多样性与泄漏审计",
            "",
            "| 审计项 | 结果 | 怎么理解 |",
            "|---|---:|---|",
            f"| 原始问题文本去重 | {diversity['exact_unique_query_count']}/{dataset['case_count']} | 表面措辞较丰富，但不代表独立故障 |",
            f"| 去掉采集窗口编号后的观察模板 | {diversity['normalized_observation_template_count']} | 与 21 个合同一一对应，Case 共享模板化信号 |",
            f"| 公开元数据签名 | {diversity['public_metadata_signature_count']} | `runtime/category/family_hint/capabilities` 的组合数 |",
            f"| 一对一指向单一根因的元数据签名 | {diversity['one_to_one_metadata_signature_count']} | 公开元数据本身已能区分全部合同 |",
            f"| 可由公开元数据唯一定位标签的 Case | {diversity['cases_with_uniquely_identifying_public_metadata']}/{dataset['case_count']} | 存在明显闭集标签泄漏风险 |",
            "",
            "生成器、Oracle、观察文本和确定性预测画像都来自同一份 `SCENARIOS`。"
            "因此该集合适合验证路由代码是否发生回归，不适合作为对外宣称模型泛化能力的独立测试集。",
            "",
            "## 4. A/B 实验规则",
            "",
            "| 项目 | 不使用 Skill | 启用 Skill |",
            "|---|---|---|",
            "| 输入 | 同一条 Case | 同一条 Case |",
            f"| 工具预算 | {paired['tool_budget_per_arm']} 次 | {paired['tool_budget_per_arm']} 次 |",
            "| 初始路线 | 按问题类别选择通用基线路线 | 混合检索 Skill，优先执行 Skill 探针，再接基线路线 |",
            "| 可见观察 | 仅暴露预算内已到达采集器的观察 | 仅暴露预算内已到达采集器的观察 |",
            "| 组合代理判分 | 文本根因命中且到达决定性采集器 | 文本根因命中且到达决定性采集器 |",
            "",
            "500 组来自固定排序后的前 500 条 Case，而不是线上随机分流。两臂输入和工具预算一致，"
            "适合做版本回归；它不是生产流量实验。",
            "",
            "## 5. 总体结果",
            "",
            "### 5.1 全部 540 条：把三个指标拆开看",
            "",
            "| 组别 | 文本根因与 Oracle 一致 | 两轮内到达决定性采集器 | 组合代理指标 | 组合指标 Wilson 95% 区间 |",
            "|---|---:|---:|---:|---:|",
            f"| 不使用 Skill | {root['no_skill']['text_prediction_matches_oracle']}/{root['no_skill']['total']} "
            f"（{percentage(root['no_skill']['text_prediction_matches_oracle_rate'])}） | "
            f"{root['no_skill']['decisive_collector_within_two']}/{root['no_skill']['total']} "
            f"（{percentage(root['no_skill']['decisive_collector_within_two_rate'])}） | "
            f"{root['no_skill']['correct']}/{root['no_skill']['total']} "
            f"（{percentage(root['no_skill']['root_cause_top1_accuracy'])}） | {interval(root['no_skill'])} |",
            f"| 启用 Skill | {root['skill_enabled']['text_prediction_matches_oracle']}/{root['skill_enabled']['total']} "
            f"（{percentage(root['skill_enabled']['text_prediction_matches_oracle_rate'])}） | "
            f"{root['skill_enabled']['decisive_collector_within_two']}/{root['skill_enabled']['total']} "
            f"（{percentage(root['skill_enabled']['decisive_collector_within_two_rate'])}） | "
            f"{root['skill_enabled']['correct']}/{root['skill_enabled']['total']} "
            f"（{percentage(root['skill_enabled']['root_cause_top1_accuracy'])}） | {interval(root['skill_enabled'])} |",
            "",
            f"组合代理指标的绝对差值为 **{root['delta_percentage_points']:+.2f} 个百分点**。"
            "但两组的组合正确数与“两轮内到达决定性采集器”数量完全相等，而文本预测在不使用 Skill 时已经接近饱和。"
            "所以这个差值实际主要测到的是**路线排序变化**，不能解释为根因识别准确率提升。",
            "",
            "### 5.2 500 组同题同预算 Skill A/B",
            "",
            "| 指标 | 不使用 Skill | 启用 Skill | 差值 |",
            "|---|---:|---:|---:|",
            f"| 文本根因与 Oracle 一致 | {paired['no_skill']['text_prediction_matches_oracle']}/{paired['no_skill']['total']} "
            f"（{percentage(paired['no_skill']['text_prediction_matches_oracle_rate'])}） | "
            f"{paired['skill_enabled']['text_prediction_matches_oracle']}/{paired['skill_enabled']['total']} "
            f"（{percentage(paired['skill_enabled']['text_prediction_matches_oracle_rate'])}） | "
            f"{(paired['skill_enabled']['text_prediction_matches_oracle_rate'] - paired['no_skill']['text_prediction_matches_oracle_rate']) * 100:+.2f} 个百分点 |",
            f"| 两轮内到达决定性采集器 | {paired['no_skill']['decisive_collector_within_two']}/{paired['no_skill']['total']} "
            f"（{percentage(paired['no_skill']['decisive_collector_within_two_rate'])}） | "
            f"{paired['skill_enabled']['decisive_collector_within_two']}/{paired['skill_enabled']['total']} "
            f"（{percentage(paired['skill_enabled']['decisive_collector_within_two_rate'])}） | "
            f"{paired['delta_percentage_points']:+.2f} 个百分点 |",
            f"| 组合代理指标 | {paired['no_skill']['correct']}/{paired['no_skill']['total']} "
            f"（{percentage(paired['no_skill']['root_cause_top1_accuracy'])}） | "
            f"{paired['skill_enabled']['correct']}/{paired['skill_enabled']['total']} "
            f"（{percentage(paired['skill_enabled']['root_cause_top1_accuracy'])}） | "
            f"{paired['delta_percentage_points']:+.2f} 个百分点 |",
            f"| 决定性采集器平均位置 | {paired['no_skill']['mean_decisive_collector_position']:.4f} | "
            f"{paired['skill_enabled']['mean_decisive_collector_position']:.4f} | "
            f"{paired['skill_enabled']['mean_decisive_collector_position'] - paired['no_skill']['mean_decisive_collector_position']:+.4f} |",
            "",
            f"- 改善：**{paired['improved_cases']}** 组",
            f"- 退化：**{paired['regressed_cases']}** 组",
            f"- 两组都对或都错：**{paired['unchanged_cases']}** 组",
            f"- Skill 让决定性采集器位置提前：**{paired['decisive_route_improved_cases']}** 组",
            f"- 逐 Case Bootstrap 95% 区间（把同源变体当独立样本的朴素统计）："
            f"**[{paired['delta_bootstrap_95']['lower_percentage_points']:+.2f}, "
            f"{paired['delta_bootstrap_95']['upper_percentage_points']:+.2f}] 个百分点**",
            f"- 按根因合同聚类、合同等权 Bootstrap 95% 区间："
            f"**[{paired['scenario_cluster_delta_bootstrap_95']['lower_percentage_points']:+.2f}, "
            f"{paired['scenario_cluster_delta_bootstrap_95']['upper_percentage_points']:+.2f}] 个百分点**"
            f"（{paired['scenario_cluster_delta_bootstrap_95']['cluster_count']} 个合同，"
            f"{paired['scenario_cluster_delta_bootstrap_95']['samples']} 次重采样）",
            f"- 不一致样本的双侧精确符号检验：`p={paired['paired_significance']['p_value']:.6e}`（同样是逐 Case 的描述统计）",
            "",
            "这些区间和 p 值只能描述当前固定受控变体。尤其是逐 Case 统计会高估 25/26 个同源变体的独立性，"
            "不能外推成生产流量的因果收益。聚类区间更保守，但合同数量仍然很少。",
            "",
            "### 5.3 Skill 检索本身",
            "",
            "| 范围 | 选择出任意 Skill | 精确命中 Oracle 预期 Skill |",
            "|---|---:|---:|",
            f"| 全部 540 条 | {root_retrieval['selected_any']}/{root_retrieval['total']} "
            f"（{percentage(root_retrieval['selected_any_rate'])}） | "
            f"{root_retrieval['matched_expected_skill']}/{root_retrieval['total']} "
            f"（{percentage(root_retrieval['matched_expected_skill_rate'])}） |",
            f"| A/B 前 500 条 | {paired_retrieval['selected_any']}/{paired_retrieval['total']} "
            f"（{percentage(paired_retrieval['selected_any_rate'])}） | "
            f"{paired_retrieval['matched_expected_skill']}/{paired_retrieval['total']} "
            f"（{percentage(paired_retrieval['matched_expected_skill_rate'])}） |",
            "",
            f"当前目录共有 **{root_retrieval['catalog_skill_count']}** 个 Skill。"
            f"Oracle 期望但目录缺失的 Skill 为 `{', '.join(root_retrieval['expected_skill_ids_missing_from_catalog']) or '无'}`，"
            f"影响 {root_retrieval['cases_whose_expected_skill_is_missing']} 条 Case。"
            "注意：精确命中 Skill 的比率与组合代理指标不是同一个指标；错误 Skill 也可能因共享采集器而走到正确路线。",
            "",
            "### 5.4 按运行时拆分的 500 组组合代理结果",
            "",
            "| 运行时 | Case 数 | 不使用 Skill | 启用 Skill | 差值 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for runtime in ("Python", "Java", "C++", "Go"):
        metrics = paired["by_runtime"].get(runtime)
        if not metrics:
            continue
        baseline_rate = metrics["no_skill"]["root_cause_top1_accuracy"]
        skill_rate = metrics["skill_enabled"]["root_cause_top1_accuracy"]
        lines.append(
            f"| {runtime} | {metrics['case_count']} | {percentage(baseline_rate)} | "
            f"{percentage(skill_rate)} | {(skill_rate - baseline_rate) * 100:+.2f} 个百分点 |"
        )

    lines.extend(["", "## 6. 退化样本审计", ""])
    if regressions:
        lines.extend(
            [
                f"500 组中共有 {len(regressions)} 组退化，不能只展示平均提升而隐藏失败项：",
                "",
                "| Case | 运行时 | 标准根因 | Skill 实际选择 | 基线路线（预算内） | Skill 路线（预算内） |",
                "|---|---|---|---|---|---|",
            ]
        )
        for row in regressions:
            lines.append(
                f"| `{row['case_id']}` | {row['runtime']} | `{row['ground_truth_root_cause']}` | "
                f"`{row['skill_retrieval'].get('selected_skill_id') or '未命中'}` | "
                f"{route(row['no_skill'])} | {route(row['skill_enabled'])} |"
            )
        lines.extend(
            [
                "",
                "这些退化项必须逐条保留并复盘；平均提升不能覆盖错误 Skill 把决定性采集器挤出预算的风险。",
            ]
        )
    else:
        lines.extend(
            [
                "500 组中未观察到组合代理指标退化。此前的跨类别误激活已通过已知故障类别硬边界修复："
                "只有类别未知时才允许 Skill 跨类别补充候选。",
                "",
                "这里的 0 退化只描述当前同源闭集和两次工具预算，不能证明生产环境不存在 Skill 误用。"
                "开放集、类别识别错误和环境漂移仍需独立测试。",
            ]
        )
    lines.extend(
        [
            "",
            "## 7. 代表性改善样本",
            "",
            "以下只列每类根因的首个代表样本，完整 540 条逐项结果保存在机器报告中：",
            "",
            "| Case | 运行时 | 标准根因 | 命中 Skill | 基线路线（预算内） | Skill 路线（预算内） |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in representative_improvements:
        lines.append(
            f"| `{row['case_id']}` | {row['runtime']} | `{row['ground_truth_root_cause']}` | "
            f"`{row['skill_retrieval'].get('selected_skill_id') or '未命中'}` | "
            f"{route(row['no_skill'])} | {route(row['skill_enabled'])} |"
        )

    lines.extend(
        [
            "",
            "## 8. 方法学审计与限制",
            "",
            "1. **不是 540 次真机实验。** Case 使用预先定义的采集器观察进行确定性回放，不包含真实 Linux 调度抖动、权限失败、采样丢失和依赖波动。",
            "2. **540 条不是 540 个独立故障。** 每个合同派生 25 或 26 个语言变体；归一化采集窗口编号后只有 21 组观察模板。",
            "3. **属于同源闭集评测。** 生成器、Oracle、观察文本与预测画像都来自 `SCENARIOS`；公开元数据签名也与根因一一对应，"
            "存在标签泄漏，不能证明系统能识别合同外的新根因。",
            "4. **没有跑生产 Evidence 链。** `_available_observations()` 只是按路线返回预生成字符串；本评测没有创建并校验真实 Artifact、"
            "Evidence Envelope、Analyzer 输出或 Report。所谓“证据合格”仅是决定性采集器到达代理。",
            "5. **评分与 Skill 尚未完全解耦。** 当前确定性闭集预测器会给匹配 Skill 直接加 `0.16` 分，"
            "所以分数不能解释为校准后的模型置信度。反事实消融中，移除这项加分后，"
            f"全部 540 条有 {root['skill_score_prior_ablation']['prediction_changed_cases']} 条文本预测改变、"
            f"{root['skill_score_prior_ablation']['composite_outcome_changed_cases']} 条组合结果改变；"
            f"500 子集中分别为 {paired['skill_score_prior_ablation']['prediction_changed_cases']} 和 "
            f"{paired['skill_score_prior_ablation']['composite_outcome_changed_cases']} 条。",
            "6. **Case 之间并非独立。** 普通逐 Case Bootstrap 与符号检验低估同源变体相关性；"
            "报告同时给出按根因合同聚类的启发式区间，但 20 个合同仍不足以支撑生产外推。",
            "7. **500 组是固定前缀，不是分层随机抽样。** 它完整遗漏 `cpp-downstream-latency`，"
            "并且 `cpp-file-io` 只进入 10/25 条；因此 500 组结果还带有排序选择偏差。",
            "8. **没有调用真实大模型。** 根因文本判断由确定性闭集打分器完成，本报告不是模型推理能力或 Agentic RAG 质量评测。",
            "9. **指标没有覆盖完整 Agent。** 没有测多轮重规划、工具失败恢复、人工干预、开放集未知根因、无故障、多故障、"
            "会话记忆、结论文字质量、Token 成本、墙钟耗时与修复后恢复。",
            "10. **外部有效性仍需 live E2E。** 当前已有 Python、Go、C++、Java 代表场景及 continuous perf/eBPF 的真机报告，"
            "但其余故障合同尚未逐场景完成独立 Linux 根因验收。",
            "",
            "## 9. 下一版测试集建议",
            "",
            "- 从公开事故报告、真实脱敏 Trace/Profile 和人工构造的新合同建立独立外部测试集；",
            "- 生成数据和判分数据按故障合同或事故来源分组切分，避免同源语言变体同时进入开发集和测试集；",
            "- 从根因判分器中移除 `selected_skill_id` 直接加分，只允许 Skill 通过实际工具路线和新观察影响结果；",
            "- 补齐 `load-saturation-diagnosis` 或把对应合同显式定义成应弃权，随后重新跑 Skill 检索指标；",
            "- 对 500 组按运行时、根因族和难度分层抽样，并把按合同聚类统计作为主区间；",
            "- 为开放集加入 `OTHER/UNKNOWN`、无故障和多故障叠加 Case，测量误报率与弃权质量；",
            "- 对代表性子集运行真实故障注入，保存 Task、Artifact、Evidence、Report、清理证明和 SHA-256；",
            "- 增加报告根因字段、Evidence 引用、修复验证、成本和总耗时指标。",
            "",
            "## 10. 数据完整性与复现",
            "",
            f"- 公开 Case 规范化 SHA-256：`{dataset['public_sha256']}`",
            f"- 私有 Oracle 规范化 SHA-256：`{dataset['private_oracle_sha256']}`",
            f"- 合并数据规范化 SHA-256：`{dataset['combined_sha256']}`",
            f"- 机器报告 SHA-256：`{report['report_sha256']}`",
            "",
            "```powershell",
            "python scripts/generate_root_cause_benchmark.py",
            "python scripts/run_root_cause_benchmark.py",
            "python -m pytest tests/test_root_cause_benchmark.py -q",
            "```",
            "",
            "关键文件：",
            "",
            "- `benchmarks/root-cause-v1/public/cases.json`：公开输入；",
            "- `benchmarks/root-cause-v1/private/oracles.json`：私有根因与决定性采集器；",
            "- `benchmarks/root-cause-v1/manifest.json`：版本、数量和文件摘要；",
            "- `scripts/generate_root_cause_benchmark.py`：测试集生成器；",
            "- `server/app/drop_insight/root_cause_benchmark.py`：评测和统计实现；",
            "- `web/public/report-assets/evaluation/root-cause-skill-ab.json`：页面使用的完整机器报告；",
            "- `reports/ai-diagnosis/受控故障根因与Skill对比评测-20260907.md`：本报告。",
            "",
            "## 11. 最终结论",
            "",
            "当前测试集、私有 Oracle、评测执行器、页面机器报告和 Markdown 报告均已落盘。"
            "当前结果只支持下面这句可复现结论：**Skill 在 21 个已知故障合同的确定性回放中，"
            "更频繁地把决定性采集器排进前两步。** "
            "它不支持“生产根因准确率为 68%”“已经评测 540 个独立真实故障”“Skill 在所有故障上都更好”"
            "或“完整 Agent 能力已经得到验证”这四种说法。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmarks" / "root-cause-v1")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "web" / "public" / "report-assets" / "evaluation" / "root-cause-skill-ab.json",
    )
    args = parser.parse_args()
    report = evaluate_controlled_root_causes(
        load_json(ROOT / "skills" / "catalog.json"),
        load_json(args.dataset / "public" / "cases.json"),
        load_json(args.dataset / "private" / "oracles.json"),
        paired_case_count=500,
    )
    report["generated_at"] = datetime.now(UTC).isoformat()
    report["report_sha256"] = report_sha256(report)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown = ROOT / "reports" / "ai-diagnosis" / "受控故障根因与Skill对比评测-20260907.md"
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "markdown": str(markdown),
                "report_sha256": report["report_sha256"],
                "root_cause_cases": report["dataset"]["case_count"],
                "paired_ab_cases": report["paired_skill_ab_500"]["case_count"],
                "no_skill_accuracy": report["paired_skill_ab_500"]["no_skill"]["root_cause_top1_accuracy"],
                "skill_accuracy": report["paired_skill_ab_500"]["skill_enabled"]["root_cause_top1_accuracy"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
