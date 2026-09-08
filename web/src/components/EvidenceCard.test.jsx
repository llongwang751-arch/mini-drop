import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import EvidenceCard from "./EvidenceCard";

describe("EvidenceCard", () => {
  it("uses Chinese evidence role, gate decision, tool and observation labels", () => {
    render(
      <EvidenceCard evidence={{
        evidence_id: "e-1",
        role: "SUPPORTS",
        classification: { decision: "ACCEPT_SUPPORT" },
        envelope: {
          source: { tool_name: "start_pyspy_profile" },
          observation: {
            summary: "Continuous profile shows no synchronous write activity and instead reveals a CPU-bound hot path",
          },
        },
      }} />,
    );

    expect(screen.getByText("支持证据")).toBeInTheDocument();
    expect(screen.getByText("采信为支持证据")).toBeInTheDocument();
    expect(screen.getByText("采集 Python 调用栈")).toBeInTheDocument();
    expect(screen.getByText(/连续性能采集没有发现同步写活动/)).toBeInTheDocument();
  });

  it("keeps unknown evidence protocol codes in collapsed technical details", () => {
    render(
      <EvidenceCard evidence={{
        evidence_id: "e-future",
        role: "FUTURE_ROLE",
        classification: { decision: "FUTURE_GATE" },
        envelope: { source: { tool_name: "future_probe" } },
      }} />,
    );

    expect(screen.getByText("证据角色未知")).toBeInTheDocument();
    expect(screen.getByText("门禁判定未知")).toBeInTheDocument();
    expect(screen.getByText("未知诊断工具")).toBeInTheDocument();
    const details = screen.getByText("查看技术详情").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("FUTURE_ROLE");
    expect(details).toHaveTextContent("FUTURE_GATE");
    expect(details).toHaveTextContent("future_probe");
  });
});
