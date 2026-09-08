import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatThread from "./ChatThread";

vi.mock("./FixVerificationPanel", () => ({ default: () => <div>fix verification</div> }));
vi.mock("./DiagnosisFeedbackCard", () => ({ default: () => <div>feedback card</div> }));

const report = {
  conclusion: "热点函数 CpuFault._run 导致 CPU 升高",
  confidence: 0.73,
  evidence_refs: ["evidence-1"],
  next_actions: ["优化热点循环后复测"],
  limitations: ["缺少独立对照证据"],
  verification: { status: "PARTIAL_WITHOUT_COUNTER" },
};

const baseProps = {
  detail: { diagnosis_id: "diag-1", query: "CPU 持续升高", status: "COMPLETED" },
  hypotheses: [{ hypothesis_id: "hyp-1", statement: "用户态热点", status: "SUPPORTED" }],
  toolCalls: [],
  evidence: [],
  reports: [report],
  events: [],
};

afterEach(cleanup);

describe("ChatThread conclusion-first mode", () => {
  it("puts the conclusion before the investigation details in simple mode", () => {
    render(<ChatThread {...baseProps} mode="simple" />);

    const conclusion = screen.getByText("结论先行");
    const investigation = screen.getByText("完整调查过程");
    expect(conclusion.compareDocumentPosition(investigation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByText("阶段性根因")).toHaveLength(1);
    expect(screen.getByText("优化热点循环后复测")).toBeInTheDocument();
    expect(screen.queryByText("形成可验证假设")).not.toBeInTheDocument();

    fireEvent.click(investigation);
    expect(screen.getByText("形成可验证假设")).toBeInTheDocument();
  });

  it("keeps the evidence-first narrative in expert mode", () => {
    render(<ChatThread {...baseProps} mode="expert" />);

    expect(screen.queryByText("结论先行")).not.toBeInTheDocument();
    expect(screen.getAllByText("阶段性根因")).toHaveLength(1);
  });

  it("keeps autonomous scope selection inside the AI runtime", () => {
    render(
      <ChatThread
        {...baseProps}
        detail={{
          diagnosis_id: "diag-scope",
          query: "检查两台 Worker",
          status: "NEEDS_CLARIFICATION",
          mode: "AUTONOMOUS",
        }}
        hypotheses={[]}
        reports={[]}
        mode="simple"
      />,
    );

    expect(screen.getByText("AI 正在自主确定诊断范围")).toBeInTheDocument();
    expect(screen.queryByText("AI 需要确认诊断范围")).not.toBeInTheDocument();
  });

  it("renders human interventions as additional conversation rounds", () => {
    render(
      <ChatThread
        {...baseProps}
        interventions={[{
          intervention_id: "int-1",
          action: "CHALLENGE_HYPOTHESIS",
          message: "先找用户态热点的反证",
          round_index: 2,
          revision_hypothesis_id: "hyp-2",
        }]}
      />,
    );

    expect(screen.getByText("先找用户态热点的反证")).toBeInTheDocument();
    expect(screen.getByText("已进入第 2 轮诊断")).toBeInTheDocument();
    expect(screen.getByText(/旧报告与旧证据保持可回放/)).toBeInTheDocument();
  });

  it("explains that insufficient evidence is not proof of no fault", () => {
    render(<ChatThread {...baseProps} detail={{ ...baseProps.detail, status: "INSUFFICIENT_EVIDENCE" }} reports={[]} />);
    expect(screen.getByText("本轮证据不足，不等于没有故障")).toBeInTheDocument();
  });

  it("semantically merges a repeated cause without dropping later-round activity", () => {
    const { container } = render(
      <ChatThread
        {...baseProps}
        hypotheses={[
          {
            hypothesis_id: "hyp-r1",
            round_index: 1,
            statement: "目标 Python 进程可能存在用户态 CPU 热点函数或 GIL 竞争",
            status: "OPEN",
          },
          {
            hypothesis_id: "hyp-r2",
            round_index: 2,
            statement: "Python 用户态 CPU 热点函数与 GIL contention 可能导致 CPU 持续升高",
            status: "SUPPORTED",
          },
        ]}
        toolCalls={[
          {
            tool_call_id: "tool-r2",
            hypothesis_id: "hyp-r2",
            tool_name: "start_pyspy_profile",
            status: "COMPLETED",
            created_at: "2026-09-05T10:01:00Z",
          },
        ]}
        reports={[]}
        interventions={[
          {
            intervention_id: "int-r2",
            action: "CONTINUE_INVESTIGATION",
            message: "继续验证同一热点原因",
            round_index: 2,
            revision_hypothesis_id: "hyp-r2",
          },
        ]}
      />,
    );

    expect(screen.getByLabelText("诊断过程摘要")).toHaveTextContent("1 个去重后假设");
    expect(screen.getByText("合并 1 个跨轮重述")).toBeInTheDocument();
    const history = container.querySelector(".diagnosis-round-history");
    expect(within(history).getAllByText(/第 [12] 轮/).length).toBeGreaterThanOrEqual(2);
    expect(within(history).getByText("Python 调用栈采集")).toBeInTheDocument();
    expect(within(history).getByText(/本轮工具、证据和评分更新仍单独保留/)).toBeInTheDocument();
  });
});
