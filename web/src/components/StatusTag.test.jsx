import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusTag from "./StatusTag";

describe("StatusTag", () => {
  it("shows task and diagnosis states as Chinese labels", () => {
    const { rerender } = render(<StatusTag status="DELIVERED" />);
    expect(screen.getByText("采集节点已接收")).toBeInTheDocument();

    rerender(<StatusTag status="COLLECTING_EVIDENCE" />);
    expect(screen.getByText("正在取证")).toBeInTheDocument();
  });

  it("does not expose an unknown raw protocol code as the primary label", () => {
    render(<StatusTag status="FUTURE_INTERNAL_STATE" />);
    expect(screen.getByText("状态未知")).toBeInTheDocument();
    expect(screen.queryByText("FUTURE_INTERNAL_STATE")).not.toBeInTheDocument();
  });
});
