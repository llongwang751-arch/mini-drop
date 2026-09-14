"""Render the strict campaign's preserved observations, never edit its verdicts."""
import argparse
import json
from pathlib import Path


def render(source: Path, output: Path, base_url: str | None = None):
    campaign = json.loads(source.read_text(encoding="utf-8"))
    rows = campaign["results"]
    lines = [f"# {campaign['selected_count']} 场景云端严格复验", "",
             f"运行状态：**{campaign['run_status']}**；已执行 **{len(rows)}/{campaign['selected_count']}**；严格通过 **{campaign['passed_count']}**，未通过 **{campaign['failed_count']}**。",
             "", "本报告逐项区分采集链路、根因报告和撤销故障后的恢复。没有独立对照或只有阶段性结论的场景不算严格通过。所有原始失败保留，未改写数据库中的历史诊断。",
             "", "**本轮没有完成同负载下的代码修复实验。** 恢复列只表示撤销注入后，被注入活动的指标回落；不能据此声称业务 SLO 恢复或代码故障已修复。",
             "", f"机器汇总：[完整 JSON]({source.name})。协议：[严格验收口径](../../docs/FAULT_PLAZA_ACCEPTANCE.md)。",
             "", "| 场景 | 任务完成/总数 | 根因门禁 | 具体结论匹配 | 撤销后指标回落 | 清理 | 严格结果 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    details = []
    for row in rows:
        case_path = source.parent / (source.stem + "-cases") / (row["scenario_id"] + ".json")
        case = json.loads(case_path.read_text(encoding="utf-8"))
        records = case.get("records", {})
        tasks = records.get("tasks", [])
        reports = records.get("reports", [])
        yes = lambda value: "通过" if value else "未通过"
        ev = row.get("report_evaluation", {})
        completed = sum(task.get("status") == "DONE" for task in tasks)
        concrete_match = any(r.get("concrete_finding") and r.get("oracle_vocabulary_match")
                             for r in ev.get("reports", []))
        lines.append(f"| {row['title']} | {completed}/{len(tasks)} | {yes(ev.get('root_gate_verified'))} | {yes(concrete_match)} | {yes(row.get('intervention', {}).get('recovery_observed'))} | {yes(row.get('cleanup_verified'))} | **{yes(row['passed'])}** |")
        details += ["", f"## {row['title']}（{row['scenario_id']}）", "",
                    f"诊断：`{row.get('diagnosis_id')}`；会话状态：`{records.get('diagnosis', {}).get('status', '未创建')}`。",
                    f"原始证据：[逐场 JSON]({source.stem}-cases/{row['scenario_id']}.json)。"]
        if base_url and row.get("diagnosis_id"):
            details += [f"在线查看：[打开这次诊断]({base_url.rstrip('/')}/ai-diagnosis?case={row['diagnosis_id']})。"]
        if row.get("error"):
            details += ["", f"链路校验异常：{row['error']}"]
        failed_tasks = [t for t in tasks if t.get("status") != "DONE"]
        if failed_tasks:
            details += ["", "未完成或失败任务："]
            for task in failed_tasks:
                details += [f"- `{task['task_id']}` / `{task.get('collector_type')}` / `{task.get('status')}`：{task.get('error_code') or task.get('status_reason') or task.get('lineage_error') or '见原始状态事件'}"]
        best = max(reports, key=lambda x: (x.get("verification", {}).get("status") == "VERIFIED", x.get("confidence") or 0), default=None)
        if best:
            gate = best.get("verification", {})
            details += ["", f"本轮证据最强的报告：`{best['report_id']}`，门禁 `{gate.get('status')}`，覆盖率 `{gate.get('coverage_ratio')}`。", "",
                        best.get("conclusion") or "没有结论文本。"]
            limits = best.get("limitations") or []
            if limits:
                details += ["", "仍缺少：" + "；".join(limits)]
            hyp = next((h for h in records.get("hypotheses", []) if h.get("hypothesis_id") == best.get("hypothesis_id")), {})
            verification = gate.get("verification") or gate
            missing = []
            for field, covered_key in (("expected_observations", "covered_expected"), ("falsification_criteria", "covered_falsification")):
                covered = verification.get(covered_key) or []
                missing += [text for index, text in enumerate(hyp.get(field) or []) if index not in covered]
            if missing:
                details += ["", "该报告未覆盖的判据："] + ["- " + str(x) for x in missing]
        intervention = row.get("intervention") or {}
        details += ["", f"独立测量字段：`{intervention.get('field')}`；模式：`{intervention.get('mode', '缺失')}`。"]
        if intervention.get("values"):
            values = intervention["values"]
            formatted = " → ".join(f"{stage}={values.get(stage, '缺失'):.3f}" if isinstance(values.get(stage), (int, float)) else f"{stage}=缺失" for stage in ("baseline", "fault", "recovery"))
            details += [formatted + "。累计计数展示每秒增量，gauge 展示原单位的窗口末值。"]
        if intervention.get("error"):
            details += ["测量失败：" + intervention["error"]]
    output.write_text("\n".join(lines + details) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-url")
    args = parser.parse_args()
    render(args.source, args.output, args.base_url)
