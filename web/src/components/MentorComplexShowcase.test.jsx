import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MentorComplexShowcase from "./MentorComplexShowcase";

const detail = {
  diagnosis_id: "diag-current-1",
  query: "当前订单服务 CPU 高",
  status: "COMPLETED",
  target: { agent_id: "worker-current", pid: 2048 },
};

const tree = {
  revision: 7,
  stats: { rounds: 3, nodes: 2, pruned: 1 },
  nodes: [
    { id: "current-root", parent_id: null, title: "当前案例入口", state: "visited", domain: "CPU", evidence: "当前基线" },
    { id: "current-cause", parent_id: "current-root", title: "当前案例根因", state: "confirmed", domain: "Python", evidence: "当前证据" },
  ],
  switches: [],
  last_event: { event_type: "EVIDENCE_PROMOTED" },
};

describe("MentorComplexShowcase", () => {
  it("replays only the diagnosis passed by the active workspace", () => {
    render(
      <MentorComplexShowcase
        open
        detail={detail}
        explorationTree={tree}
        hypotheses={[{ hypothesis_id: "hyp-current", statement: "当前假设" }]}
        toolCalls={[{ tool_call_id: "tool-current", tool_name: "start_pyspy_profile" }]}
        evidence={[{ evidence_id: "evidence-current" }]}
        reports={[]}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getAllByText("diag-current-1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("当前订单服务 CPU 高")).toBeInTheDocument();
    expect(screen.getByText("当前案例入口")).toBeInTheDocument();
    expect(screen.getByText("当前案例根因")).toBeInTheDocument();
    expect(screen.getByText("本案例暂不沉淀 Skill")).toBeInTheDocument();
    expect(screen.queryByText(/选择多轮复杂案例/)).not.toBeInTheDocument();
    expect(document.querySelector(".ant-drawer")).toBeNull();
  });

  it("shows the candidate Skill produced by the same diagnosis", () => {
    const onEvaluate = vi.fn();
    const skill = {
      skill_id: "skill-current",
      status: "CANDIDATE",
      version: 1,
      category: "CPU_HOTSPOT",
      source_diagnosis_ids: ["diag-current-1"],
      strategy: {
        probe_order: ["collect_sys_metrics", "start_perf_profile"],
        actual_exploration: { summary: { hypotheses_explored: 2, tool_calls: 2 } },
      },
      gate_metrics: { passed: 1, total: 3, eligible: false },
    };

    render(
      <MentorComplexShowcase
        open
        detail={detail}
        explorationTree={tree}
        sourceSkill={skill}
        onEvaluateSkill={onEvaluate}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("本次诊断自动沉淀的 Skill")).toBeInTheDocument();
    expect(screen.getAllByText("diag-current-1").length).toBeGreaterThanOrEqual(1);
    fireEvent.click(screen.getByRole("button", { name: /运行三类门禁评测/ }));
    expect(onEvaluate).toHaveBeenCalledWith(skill);
  });
});
