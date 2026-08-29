import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MentorComplexShowcase from "./MentorComplexShowcase";

function makeCase(id, shortTitle, rootTitle, childTitle) {
  return {
    case_id: id,
    short_title: shortTitle,
    title: `${shortTitle}诊断成功`,
    replay_label: "受控回放",
    source: "测试快照",
    root_cause: `${shortTitle}根因`,
    fix: "恢复后复测",
    first_run: {
      tool_calls: 5,
      duration_seconds: 100,
      confidence: 0.9,
      rounds: [
        { round: 1, duration_seconds: 20, direction: "基线", tool: "指标", decision: "异常", outcome: "继续" },
        { round: 2, duration_seconds: 20, direction: "反证", tool: "采样", decision: "不支持", outcome: "剪枝" },
        { round: 3, duration_seconds: 20, direction: "转向", tool: "新工具", decision: "发现线索", outcome: "继续" },
        { round: 4, duration_seconds: 20, direction: "命中", tool: "关联", decision: "支持", outcome: "验证" },
        { round: 5, duration_seconds: 20, direction: "恢复", tool: "复测", decision: "恢复", outcome: "结束" },
      ],
      switches: [{ from: "CPU", to: "I/O", reason: "CPU 证据不足" }],
      nodes: [
        { id: "root", parent_id: null, title: rootTitle, state: "visited", domain: "入口", evidence: "入口证据" },
        { id: "child", parent_id: "root", title: childTitle, state: "confirmed", domain: "根因", evidence: "恢复验证" },
      ],
    },
    generated_skill: {
      name: `${shortTitle} Skill`, version: "v1", status: "候选", action: "新建",
      source_report_id: `report-${id}`, candidate_id: `skill-${id}`, source_trace: "真实轨迹",
      probe_order: ["基线", "恢复"], negative_paths: ["错误方向"], switch_conditions: ["证据不足"],
      stop_conditions: ["恢复验证"], gate_results: [],
    },
    second_run: { tool_calls: 2, duration_seconds: 40, confidence: 0.88, incident: "相似故障", path: ["基线", "根因"] },
    counterexample: { tool_calls: 3, duration_seconds: 50, rejection_reason: "证据不匹配", root_cause: "另一根因" },
    comparison: { improvement: { tool_calls_percent: 60, duration_percent: 60, false_transfer_rate: 0 } },
  };
}

describe("MentorComplexShowcase", () => {
  it("switches the diagnosis rounds and exploration tree from the same selected case", () => {
    const first = makeCase("case-a", "案例甲", "甲入口", "甲根因路径");
    const second = makeCase("case-b", "案例乙", "乙入口", "乙根因路径");

    render(
      <MentorComplexShowcase
        open
        loading={false}
        data={{ ...first, default_case_id: first.case_id, cases: [first, second] }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("甲入口")).toBeInTheDocument();
    expect(screen.getByLabelText("可缩放拖动的诊断探索树")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("放大探索树"));
    expect(screen.getByText("84%")).toBeInTheDocument();
    fireEvent.click(screen.getByText("2. 案例乙"));
    expect(screen.getByText("乙入口")).toBeInTheDocument();
    expect(screen.getByText("乙根因路径")).toBeInTheDocument();
    expect(screen.queryByText("甲根因路径")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /大屏查看完整树/ }));
    expect(screen.getByText("案例乙诊断成功 · 完整探索树")).toBeInTheDocument();
    expect(screen.getAllByText("乙入口")).toHaveLength(2);
    expect(screen.getByText(/滚轮缩放、按住空白区域拖动/)).toBeInTheDocument();
  }, 15000);
});
