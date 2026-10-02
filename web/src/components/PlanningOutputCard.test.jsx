import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ChatThread from "./ChatThread";
import PlanningOutputCard from "./PlanningOutputCard";

afterEach(cleanup);

const record = (disposition) => ({
  round_index: 1,
  claim_scope: "PLANNING_ONLY_NOT_HEALTH_OR_CAUSATION",
  is_evidence: false, health_check_performed: false, new_tool_requested: false,
  planning_output: {
    schema_version: "mini-drop.planning-output.v2", disposition,
    tool_name: null, hypotheses: [], causal_root_cause_verified: false,
    reasoning_summary: "只检查提供的描述", missing_evidence: ["当前时间窗"], limitations: ["没有实时采样"],
  },
});

describe("bounded persisted planning output", () => {
  it.each([
    ["NORMAL", "描述范围内未提出异常"],
    ["INSUFFICIENT_EVIDENCE", "缺少必要观测"],
    ["REFUSED", "拒绝此请求"],
  ])("shows %s with the health and causation boundary", (disposition, label) => {
    render(<PlanningOutputCard payload={record(disposition)} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText(/不代表业务健康，也不证明故障根因/)).toBeInTheDocument();
    expect(screen.getByText(/待补充观测：当前时间窗/)).toBeInTheDocument();
    expect(screen.queryByText("业务一切正常")).not.toBeInTheDocument();
  });

  it.each([
    { health_check_performed: true }, { is_evidence: true }, { new_tool_requested: true },
    { claim_scope: "CAUSAL_ROOT_CAUSE" },
    { planning_output: { ...record("NORMAL").planning_output, hypotheses: [{ statement: "异常" }] } },
  ])("does not render a conflicting record as a safe planning result", (override) => {
    const { container } = render(<PlanningOutputCard payload={{ ...record("NORMAL"), ...override }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it.each(["simple", "expert"])("projects the actual recorded event in %s mode without a false running state", (mode) => {
    render(<ChatThread
      mode={mode} detail={{ diagnosis_id: "bounded", query: "描述没有异常", status: "INSUFFICIENT_EVIDENCE" }}
      hypotheses={[]} toolCalls={[]} evidence={[]} reports={[]}
      events={[{ event_type: "planner.output_recorded", sequence: 3, payload_json: record("NORMAL") }]}
    />);
    expect(screen.getByText("描述范围内未提出异常")).toBeInTheDocument();
    expect(screen.getByText("已完成本轮规划判断")).toBeInTheDocument();
    expect(screen.queryByText("正在规划")).not.toBeInTheDocument();
    expect(screen.queryByText(/Agent 正在解析意图/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("fix-gate")).not.toBeInTheDocument();
  });
});
