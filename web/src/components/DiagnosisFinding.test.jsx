import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import DiagnosisFinding from "./DiagnosisFinding";
import { selectBestReport } from "../utils/reportPresentation";

afterEach(cleanup);
describe("persisted diagnosis finding", () => {
  it("retains a supported report when a later branch is inconclusive, without hiding limitations", () => {
    const supported = { report_id: "supported", conclusion: "热点定位在业务循环", confidence: 0.7,
      evidence_refs: ["ev-1"], verification: { status: "PARTIAL_WITHOUT_COUNTER" },
      limitations: ["缺少修复前后对照"], next_actions: ["优化循环后重新采集"], created_at: "2026-09-08T10:00:00Z" };
    const deadEnd = { report_id: "dead-end", conclusion: "该分支证据不足", confidence: 0,
      verification: { status: "INSUFFICIENT_EVIDENCE" }, created_at: "2026-09-08T11:00:00Z" };
    const reports = [deadEnd, supported];
    expect(selectBestReport(reports)).toBe(supported);
    expect(reports[0]).toBe(deadEnd);
    render(<DiagnosisFinding reports={reports} status="COLLECTING" />);
    expect(screen.getByText("热点定位在业务循环")).toBeInTheDocument();
    expect(screen.getByText(/缺少修复前后对照/)).toBeInTheDocument();
    expect(screen.getByText("调查仍在进行")).toBeInTheDocument();
    expect(screen.queryByText("根因结论")).not.toBeInTheDocument();
  });
  it("does not invent a finding before a report is persisted", () => {
    render(<DiagnosisFinding reports={[]} status="COMPLETED" />);
    expect(screen.queryByLabelText("当前诊断结论摘要")).not.toBeInTheDocument();
  });
});
