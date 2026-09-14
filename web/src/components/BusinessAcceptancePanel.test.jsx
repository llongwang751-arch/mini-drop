import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import BusinessAcceptancePanel from "./BusinessAcceptancePanel";
import { getBusinessAcceptance } from "../api/client";
vi.mock("../api/client", () => ({ getBusinessAcceptance: vi.fn() }));
beforeEach(() => vi.clearAllMocks());

it("does not turn missing evidence into a passing result", async () => {
  getBusinessAcceptance.mockResolvedValue({ status: "NOT_RUN", cases: [] });
  render(<BusinessAcceptancePanel />);
  expect(await screen.findByText("尚无业务验收记录，不能推断通过")).toBeInTheDocument();
  expect(screen.queryByText("业务指标改善已验证")).not.toBeInTheDocument();
});

it("shows fallback separately from full recovery and AI diagnosis", async () => {
  getBusinessAcceptance.mockResolvedValue({ status: "AVAILABLE", revision: "r1", finished_at: "today", cases: [{
    scenario_id: "RAG-03", title: "慢依赖", comparison: {
      outcome: "DEGRADED_AVAILABLE", summaries: {}, reasons: [], change_summary: "返回摘录", policy: {},
    },
  }] });
  render(<BusinessAcceptancePanel />);
  expect(await screen.findByText("仅降级可用 · 未恢复完整能力")).toBeInTheDocument();
  expect(screen.getByText("AI 根因验收：未执行")).toBeInTheDocument();
  expect(screen.queryByText("业务指标改善已验证")).not.toBeInTheDocument();
});

it("shows retrieval errors instead of zero-valued scores", async () => {
  getBusinessAcceptance.mockRejectedValue(new Error("offline"));
  render(<BusinessAcceptancePanel />);
  await waitFor(() => expect(screen.getByText("无法确认验收状态")).toBeInTheDocument());
  expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
});

it("links an executed actual RAG diagnosis without claiming root verification", async () => {
  getBusinessAcceptance.mockResolvedValue({ status: "AVAILABLE", cases: [{
    scenario_id: "RAG-ACTUAL-01", title: "实际检索", source_kind: "ACTUAL_RAG_ENGINE", environment: "isolated-linux",
    diagnosis: { diagnosis_id: "insight_actual", task_count: 2, evidence_count: 2, report_count: 1 },
    comparison: { outcome: "IMPROVEMENT_VERIFIED", summaries: { fault: { stage_p95_ms: { local_search: 42, answer_composition: 0.048 } } } },
  }] });
  render(<BusinessAcceptancePanel />);
  expect(await screen.findByText("实际 RAG 引擎 · isolated-linux")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看这次 AI 诊断与采集证据" })).toHaveAttribute("href", "/ai-diagnosis?case=insight_actual");
  expect(screen.getByText("AI 诊断已执行 · 根因另看报告")).toBeInTheDocument();
  expect(screen.getByText("本地词法检索")).toBeInTheDocument();
  expect(screen.getByText("< 0.1 ms")).toBeInTheDocument();
  expect(screen.queryByText("全文检索")).not.toBeInTheDocument();
});
