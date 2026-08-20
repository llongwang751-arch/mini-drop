import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import AIDiagnosis from "./AIDiagnosis";

vi.mock("../api/client", () => ({
  listDropInsightDiagnoses: vi.fn(),
  listDiagnosticCases: vi.fn(),
  getDropInsightDiagnosis: vi.fn(),
  listDropInsightEvents: vi.fn(),
  listDropInsightHypotheses: vi.fn(),
  listDropInsightEvidence: vi.fn(),
  listDropInsightReports: vi.fn(),
  listDropInsightToolCalls: vi.fn(),
  getDropInsightBudget: vi.fn(),
  createDropInsightDiagnosis: vi.fn(),
  runDropInsightPlanner: vi.fn(),
  decideDropInsightToolCall: vi.fn(),
  advanceDropInsightOrchestrator: vi.fn(),
}));

import * as api from "../api/client";

describe("AIDiagnosis conversation page", () => {
  beforeEach(() => {
    api.listDropInsightDiagnoses.mockResolvedValue([]);
    api.listDiagnosticCases.mockResolvedValue([]);
    api.getDropInsightDiagnosis.mockResolvedValue(null);
    api.listDropInsightEvents.mockResolvedValue([]);
    api.listDropInsightHypotheses.mockResolvedValue([]);
    api.listDropInsightEvidence.mockResolvedValue([]);
    api.listDropInsightReports.mockResolvedValue([]);
    api.listDropInsightToolCalls.mockResolvedValue([]);
    api.getDropInsightBudget.mockResolvedValue(null);
  });

  it("renders the conversation page with a session list", async () => {
    api.listDropInsightDiagnoses.mockResolvedValue([
      { diagnosis_id: "diag-1", query: "订单服务 CPU 高", status: "COMPLETED" },
    ]);
    render(<AIDiagnosis />);
    expect(await screen.findByText("订单服务 CPU 高")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/描述问题/)).toBeInTheDocument();
  });

  it("starts a new conversation on send", async () => {
    api.createDropInsightDiagnosis.mockResolvedValue({ diagnosis_id: "diag-new" });
    api.runDropInsightPlanner.mockResolvedValue({});
    api.listDropInsightDiagnoses.mockResolvedValue([
      { diagnosis_id: "diag-new", query: "新问题", status: "RUNNING" },
    ]);
    render(<AIDiagnosis />);
    const input = screen.getByPlaceholderText(/描述问题/);
    fireEvent.change(input, { target: { value: "新问题" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() =>
      expect(api.createDropInsightDiagnosis).toHaveBeenCalledWith({
        query: "新问题",
        mode: "ASSISTED",
      }),
    );
  });
});
