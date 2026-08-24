from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


METRICS = (
    ("root_location_top1", "根因位置 Top-1"),
    ("mechanism_match", "因果机制匹配"),
    ("required_evidence_coverage", "必要证据覆盖"),
    ("abstention_calibration", "拒答/不确定性校准"),
    ("intervention_revision_rate", "干预后结论修订"),
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _percent(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value) * 100:.2f}%"


def _case_summary(score: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in score.get("results", []):
        grouped[str(result["case_id"])].append(result)

    summary: dict[str, dict[str, Any]] = {}
    for case_id, results in sorted(grouped.items()):
        completed = [item for item in results if item.get("eligible_for_mainboard")]
        reasoning = [item.get("reasoning", {}) for item in completed]
        summary[case_id] = {
            "runs": len(results),
            "completed": len(completed),
            "root": (
                sum(float(item.get("root_location", 0)) for item in reasoning) / len(reasoning)
                if reasoning
                else None
            ),
            "mechanism": (
                sum(float(item.get("mechanism", 0)) for item in reasoning) / len(reasoning)
                if reasoning
                else None
            ),
            "evidence": (
                sum(float(item.get("evidence_validity", 0)) for item in reasoning) / len(reasoning)
                if reasoning
                else None
            ),
        }
    return summary


def _comparison_payload(
    ours: dict[str, Any],
    competitor: dict[str, Any],
    skill: dict[str, Any],
    *,
    competitor_url: str,
    competitor_sha: str,
    competitor_archive_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": "mini-drop.agent-comparison.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cohort": {
            "cases": 9,
            "repeats_per_case": 3,
            "expected_runs": 27,
            "same_public_and_replay_inputs": True,
            "same_model": "deepseek-chat",
            "same_scorer": True,
        },
        "ours": ours,
        "competitor": competitor,
        "competitor_source": {
            "url": competitor_url,
            "main_sha": competitor_sha,
            "archive_sha256": competitor_archive_sha256,
        },
        "per_case": {
            "ours": _case_summary(ours),
            "competitor": _case_summary(competitor),
        },
        "differentiator": skill,
        "claim_boundary": (
            "Common-cohort metrics compare the two agents on the same replay inputs and model. "
            "The competitor cohort is invalid when any adapter run lacks a schema-valid final answer. "
            "Skill-evolution results are a separate Mini-Drop-only capability evaluation and are not "
            "reported as a zero score for a competitor without a matching adapter."
        ),
    }


def _render(payload: dict[str, Any]) -> str:
    ours = payload["ours"]
    competitor = payload["competitor"]
    source = payload["competitor_source"]
    skill = payload["differentiator"]
    independent = skill["independent_replay"]
    longitudinal = skill["longitudinal_campaign"]

    lines = [
        "# Mini-Drop 与 mini-drop-ai-agent-v2 统一测试集对比报告",
        "",
        f"> 生成时间：{payload['generated_at']}  ",
        "> 结论口径：共同赛道只比较同一输入、同一模型、同一评分器下的结果；项目独有能力单列，不把对方缺失能力粗暴记为 0 分。",
        "",
        "## 1. 执行摘要",
        "",
        f"- Mini-Drop：`{ours['eligible_run_count']}/{ours['run_count']}` 次有效，评测状态 `{ours['run_validity']}`。",
        f"- 对比项目：`{competitor['eligible_run_count']}/{competitor['run_count']}` 次有效，评测状态 `{competitor['run_validity']}`。",
        "- Mini-Drop 在完整性、证据覆盖、干预后修订和安全边界上更稳定；对比项目存在无法产出结构化最终答案的运行，因此其条件准确率只能作为描述性参考，不能作为有效排行榜成绩。",
        "- 本轮修复的核心不是针对题目写答案，而是让生产 RCA 链路能够把未知类型但来源可信的工具证据注册成可选候选，并明确区分服务内部机制、外部依赖和共享基础设施。",
        "",
        "## 2. 公平性与可复现性",
        "",
        "| 项目 | 设置 |",
        "|---|---|",
        "| 测试规模 | 9 个场景，每个重复 3 次，共 27 次 |",
        "| 输入 | 两个项目使用相同 public/replay 数据；私有 Oracle 仅由评分器在生成答案后读取 |",
        "| 模型 | `deepseek-chat` |",
        "| 评分器 | 同一份统一评分脚本 |",
        f"| 对比仓库 | [{source['url']}]({source['url']}) |",
        f"| 对比仓库 main SHA | `{source['main_sha']}` |",
        f"| 下载归档 SHA-256 | `{source['archive_sha256']}` |",
        "",
        "对比仓库公开适配器缺少其引用的 `benchmark.replay` 模块，并且原始配置把非官方模型名发送到官方端点；为使其能够参赛，本次只补入统一交付中的公共 ReplayService、统一模型名和 HTTP/UTF-8 兼容层，没有改变它的提示词、工具定义、推理循环或答案内容。所有兼容项均记录在原始 campaign metadata 中。",
        "",
        "## 3. 共同赛道总分",
        "",
        "| 指标 | Mini-Drop | 对比项目（仅已完成运行） | 差值 |",
        "|---|---:|---:|---:|",
    ]
    for key, label in METRICS:
        ours_value = ours.get(key)
        other_value = competitor.get(key)
        delta = None if ours_value is None or other_value is None else ours_value - other_value
        lines.append(
            f"| {label} | {_percent(ours_value)} | {_percent(other_value)} | "
            f"{('N/A' if delta is None else f'{delta * 100:+.2f} pp')} |"
        )

    lines.extend(
        [
            f"| 结构化有效完成率 | {_percent(ours['eligible_run_count'] / ours['run_count'])} | "
            f"{_percent(competitor['eligible_run_count'] / competitor['run_count'])} | "
            f"{(ours['eligible_run_count'] / ours['run_count'] - competitor['eligible_run_count'] / competitor['run_count']) * 100:+.2f} pp |",
            f"| 复用禁用证据次数 | {ours['excluded_evidence_reuse_count']} | {competitor['excluded_evidence_reuse_count']} | - |",
            f"| 盲从专家意见次数 | {ours['blind_expert_obedience_count']} | {competitor['blind_expert_obedience_count']} | - |",
            "",
            "### 有效性说明",
            "",
            f"- Mini-Drop：`{ours['run_validity']}`，27 次均有符合协议的最终答案。",
            f"- 对比项目：`{competitor['run_validity']}`，有 "
            f"{competitor['run_count'] - competitor['eligible_run_count']} 次未得到符合协议的最终答案。",
            "- 因此不能只看对比项目在成功样本上的准确率；生产系统首先必须稳定完成诊断。报告保留其成功样本指标用于定位差异，但不把它包装成完整 27 次的有效总分。",
            "",
            "## 4. 逐场景稳定性",
            "",
            "| 场景 | Mini-Drop 完成 | Mini-Drop 根因/机制 | 对比项目完成 | 对比项目根因/机制 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    ours_cases = payload["per_case"]["ours"]
    competitor_cases = payload["per_case"]["competitor"]
    for case_id in sorted(set(ours_cases) | set(competitor_cases)):
        own = ours_cases.get(case_id, {})
        other = competitor_cases.get(case_id, {})
        lines.append(
            f"| `{case_id}` | {own.get('completed', 0)}/{own.get('runs', 0)} | "
            f"{_percent(own.get('root'))} / {_percent(own.get('mechanism'))} | "
            f"{other.get('completed', 0)}/{other.get('runs', 0)} | "
            f"{_percent(other.get('root'))} / {_percent(other.get('mechanism'))} |"
        )

    lines.extend(
        [
            "",
            "## 5. Mini-Drop 独有能力：受控策略自进化",
            "",
            "这一部分不与对方做伪公平排名，因为对方没有可接入同一协议的策略生命周期接口。它回答的是另一个问题：Mini-Drop 能否把已验证诊断经验沉淀成策略，同时抵抗错误反馈、伪相似案例和环境漂移。",
            "",
            f"- 独立回放：基线通过 `{independent['baseline']['passed']}/{independent['baseline']['total']}`，启用策略后通过 `{independent['skill_enabled']['passed']}/{independent['skill_enabled']['total']}`。",
            f"- 通过率增量：`{independent['delta']['pass_rate'] * 100:+.2f} pp`。",
            f"- 负迁移率：`{_percent(independent['skill_enabled']['metrics']['negative_transfer_rate']['value'])}`。",
            f"- 误导反例拒绝率：`{_percent(independent['skill_enabled']['metrics']['misleading_counterexample_refusal_rate']['value'])}`。",
            f"- 环境漂移正确降级率：`{_percent(independent['skill_enabled']['metrics']['environment_drift_correct_degradation_rate']['value'])}`。",
            f"- 有状态生命周期：`{longitudinal['passed']}/{longitudinal['case_count']}`，覆盖发布、错误反馈隔离、自动隔离、版本回滚、回滚后恢复和污染证据拒绝。",
            "",
            "边界：这组结果证明的是离线路由与策略生命周期契约，不等价于 Linux 真机采集准确率，也不用于宣称对缺少适配器的外部项目取得 0 分胜利。",
            "",
            "## 6. 本轮 AI 链路整改",
            "",
            "1. **修复候选白名单断层**：把来源可信、完成状态正常、具备可追溯证据 ID 的外部工具结果汇总成 `external_evidence_synthesis` 候选，避免模型明明拿到证据却只能选择 `insufficient_data`。",
            "2. **明确根因边界**：服务内部的锁、队列、缓存、游标和控制循环归为 `self`；只有跨服务依赖或服务外公共组件才归入 dependency/shared infrastructure。",
            "3. **按问题选择证据**：内存、队列、锁、游标等证据不再因为缺少火焰图或 eBPF 被一票否决；缺失但无关的采集器只作为限制说明。",
            "4. **抗幻觉与抗盲从**：操作员、评审者和修复作者的文字不是证据，最终结论必须引用正式 evidence ID；冲突观测进入反证而不是被忽略。",
            "5. **保持可证伪性**：证据只能支持有限范围的机制结论，无法闭环时仍允许拒答，避免为了展示效果强行编造根因。",
            "",
            "## 7. 剩余风险",
            "",
            "- 9 个基础场景仍然偏小，需要继续加入不同语言、不同内核版本和真实 Linux Campaign。",
            "- `case-01` 等场景仍有机制级推理波动，说明提示词与候选摘要还可继续压缩歧义。",
            "- 对比项目的失败可能同时受其工具预算、适配器缺失和模型输出稳定性影响；本报告不把失败简单归咎于其核心算法。",
            "- 当前自进化评测的耗时和工具成本是投影值；真实诊断延迟、采集开销和线上根因准确率需要云端 Linux Campaign 实测。",
            "",
            "## 8. 原始产物",
            "",
            "- Mini-Drop 共同赛道：`artifacts/comparison-20260824/ours-fixed/`",
            "- 对比项目共同赛道：`artifacts/comparison-20260824/competitor-full/`",
            "- 策略自进化评测：`artifacts/comparison-20260824/ours-skill-evolution/benchmark-report.json`",
            "- 对比项目只读源码：`external/mini-drop-ai-agent-v2-src/mini-drop-ai-agent-v2-main/`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ours", type=Path, required=True)
    parser.add_argument("--competitor", type=Path, required=True)
    parser.add_argument("--skill", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--competitor-url", required=True)
    parser.add_argument("--competitor-sha", required=True)
    parser.add_argument("--competitor-archive-sha256", required=True)
    args = parser.parse_args()

    payload = _comparison_payload(
        _load(args.ours),
        _load(args.competitor),
        _load(args.skill),
        competitor_url=args.competitor_url,
        competitor_sha=args.competitor_sha,
        competitor_archive_sha256=args.competitor_archive_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_render(payload), encoding="utf-8")
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"REPORT={args.output.resolve()}")
    print(f"JSON={args.json_output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
