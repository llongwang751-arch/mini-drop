import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ToolCallCard from "./ToolCallCard";

describe("ToolCallCard", () => {
  it("shows approve/reject for a pending approval tool", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <ToolCallCard
        tool={{
          tool_call_id: "tc-1",
          tool_name: "start_perf_profile",
          status: "PENDING_APPROVAL",
          policy_decision: "REQUIRE_APPROVAL",
          arguments_json: { agent_id: "a1", pid: 123, duration_seconds: 15 },
        }}
        onApprove={onApprove}
        onReject={onReject}
      />,
    );
    expect(screen.getByText("采集 CPU 火焰图")).toBeInTheDocument();
    expect(screen.getByText("等待人工审批")).toBeInTheDocument();
    expect(screen.getByText("需要人工审批")).toBeInTheDocument();
    expect(screen.getByText("查看技术详情").closest("details")).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("通过"));
    expect(onApprove).toHaveBeenCalledWith("tc-1");
    fireEvent.click(screen.getByText("拒绝"));
    expect(onReject).toHaveBeenCalledWith("tc-1");
  });

  it("keeps unknown protocol codes and raw errors inside collapsed technical details", () => {
    render(
      <ToolCallCard
        tool={{
          tool_call_id: "tc-future",
          tool_name: "future_internal_probe",
          status: "FUTURE_TOOL_STATE",
          policy_decision: "FUTURE_POLICY",
          error_message: "opaque driver failure 991",
        }}
        readOnly
      />,
    );

    expect(screen.getByText("未知诊断工具")).toBeInTheDocument();
    expect(screen.getByText("状态未知")).toBeInTheDocument();
    expect(screen.getByText("策略判定未知")).toBeInTheDocument();
    expect(screen.getByText(/执行失败，请展开技术详情/)).toBeInTheDocument();
    const details = screen.getByText("查看技术详情").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("future_internal_probe");
    expect(details).toHaveTextContent("FUTURE_TOOL_STATE");
    expect(details).toHaveTextContent("FUTURE_POLICY");
    expect(details).toHaveTextContent("opaque driver failure 991");
  });

  it("keeps the server-bound agent and PID immutable while editing sampling parameters", async () => {
    const onUpdateArgs = vi.fn().mockResolvedValue(undefined);
    render(
      <ToolCallCard
        tool={{
          tool_call_id: "tc-safe-target",
          tool_name: "start_perf_profile",
          status: "PENDING_APPROVAL",
          arguments_json: {
            agent_id: "worker-1",
            pid: 4321,
            duration_seconds: 15,
            sample_rate: 99,
          },
        }}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onUpdateArgs={onUpdateArgs}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /修改参数/ }));
    expect(screen.getByText("安全目标（不可修改）")).toBeInTheDocument();
    expect(screen.getAllByText(/worker-1.*PID 4321/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("spinbutton", { name: "目标 PID" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(onUpdateArgs).toHaveBeenCalledWith("tc-safe-target", {
      agent_id: "worker-1",
      pid: 4321,
      duration_seconds: 15,
      sample_rate: 99,
    }));
  });
});
