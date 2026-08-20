import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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
    expect(screen.getByText("perf CPU 采样")).toBeInTheDocument();
    fireEvent.click(screen.getByText("通过"));
    expect(onApprove).toHaveBeenCalledWith("tc-1");
    fireEvent.click(screen.getByText("拒绝"));
    expect(onReject).toHaveBeenCalledWith("tc-1");
  });
});
