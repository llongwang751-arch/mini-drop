import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import CausalReplayPanel from "./CausalReplayPanel";

const waitingExperiment = {
  experiment_id: "cr-exp-1",
  status: "WAITING_APPROVAL",
  plan: {
    design_mode: "DUAL_NODE_CONTROL",
    hypothesis: "服务变慢由 noisy neighbor 引起",
    treatment: "只限制处理节点上的干扰进程 CPU",
    control: "对照节点保持不变",
    primary_metric: "p99_latency_ms",
    cleanup: "恢复处理节点 CPU 配额",
  },
};

describe("CausalReplayPanel human-in-the-loop boundaries", () => {
  afterEach(() => cleanup());

  it("keeps full context and requires an explicit human approval", async () => {
    const onDecision = vi.fn().mockResolvedValue({});
    render(
      <CausalReplayPanel
        diagnosis={{ diagnosis_id: "diag-1", target: {} }}
        experiments={[waitingExperiment]}
        onDecision={onDecision}
      />,
    );

    expect(screen.getByText("这不是删除上下文，也不是让 AI 自己改生产环境")).toBeInTheDocument();
    expect(screen.getByText("等待人工审批")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /核对无误并批准/ }));
    await waitFor(() => expect(onDecision).toHaveBeenCalledWith("cr-exp-1", {
      approved: true,
      reason: "用户已核对处理组、对照组、单一干预和自动清理范围",
    }));
  });

  it("records rejection instead of running an unapproved intervention", async () => {
    const onDecision = vi.fn().mockResolvedValue({});
    render(
      <CausalReplayPanel
        diagnosis={{ diagnosis_id: "diag-1", target: {} }}
        experiments={[waitingExperiment]}
        onDecision={onDecision}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /拒绝实验/ }));
    await waitFor(() => expect(onDecision).toHaveBeenCalledWith("cr-exp-1", {
      approved: false,
      reason: "用户拒绝当前实验范围或风险",
    }));
  });
});
