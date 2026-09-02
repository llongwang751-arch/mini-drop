import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import AIDiagnosis from "./AIDiagnosis";

vi.mock("../api/client", () => ({
  advanceDropInsightOrchestrator: vi.fn(),
  clarifyDropInsightDiagnosis: vi.fn(),
  createDropInsightDiagnosis: vi.fn(),
  createDiagnosticSkillCandidate: vi.fn(),
  decideDropInsightToolCall: vi.fn(),
  deleteDropInsightDiagnosis: vi.fn(),
  evaluateDiagnosticSkill: vi.fn(),
  getMentorComplexShowcase: vi.fn(),
  getDropInsightBudget: vi.fn(),
  getDropInsightDiagnosis: vi.fn(),
  getDropInsightExplorationTree: vi.fn(),
  listDiagnosticSkillActivations: vi.fn(),
  listDiagnosticSkills: vi.fn(),
  listDropInsightDiagnoses: vi.fn(),
  listDropInsightEvidence: vi.fn(),
  listDropInsightFeedback: vi.fn(),
  listDropInsightEvents: vi.fn(),
  listDropInsightHypotheses: vi.fn(),
  listDropInsightReports: vi.fn(),
  listDropInsightToolCalls: vi.fn(),
  runDropInsightPlanner: vi.fn(),
  submitDropInsightFeedback: vi.fn(),
  updateDropInsightToolCall: vi.fn(),
}));

vi.mock("../hooks/useSSE", () => ({
  default: vi.fn(() => ({ connected: true, reconnect: vi.fn() })),
}));

import * as api from "../api/client";

const diagnosis = {
  diagnosis_id: "diag-1",
  query: "订单服务 CPU 高",
  status: "COMPLETED",
  updated_at: "2026-09-01T08:00:00Z",
};

describe("AIDiagnosis V2 workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/ai-diagnosis");
    api.listDropInsightDiagnoses.mockResolvedValue([]);
    api.getDropInsightDiagnosis.mockResolvedValue(null);
    api.getDropInsightExplorationTree.mockResolvedValue(null);
    api.listDropInsightEvents.mockResolvedValue([]);
    api.listDropInsightHypotheses.mockResolvedValue([]);
    api.listDropInsightEvidence.mockResolvedValue([]);
    api.listDropInsightReports.mockResolvedValue([]);
    api.listDropInsightToolCalls.mockResolvedValue([]);
    api.listDropInsightFeedback.mockResolvedValue([]);
    api.listDiagnosticSkillActivations.mockResolvedValue([]);
    api.listDiagnosticSkills.mockResolvedValue([]);
    api.getDropInsightBudget.mockResolvedValue(null);
    api.getMentorComplexShowcase.mockResolvedValue(null);
  });

  afterEach(cleanup);

  it("renders only Drop Insight V2 diagnoses", async () => {
    api.listDropInsightDiagnoses.mockResolvedValue([diagnosis]);
    render(<AIDiagnosis />);
    expect(await screen.findByText("订单服务 CPU 高")).toBeInTheDocument();
    expect(screen.getByText("Drop Insight V2")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/描述问题/)).toBeInTheDocument();
  });

  it("creates a diagnosis through the retained V2 API", async () => {
    api.createDropInsightDiagnosis.mockResolvedValue({ diagnosis_id: "diag-new" });
    api.runDropInsightPlanner.mockResolvedValue({});
    render(<AIDiagnosis />);
    fireEvent.change(screen.getByPlaceholderText(/描述问题/), {
      target: { value: "定位订单服务 CPU" },
    });
    fireEvent.click(screen.getByText("开始诊断"));
    await waitFor(() => expect(api.createDropInsightDiagnosis).toHaveBeenCalledWith({
      query: "定位订单服务 CPU",
      mode: "ASSISTED",
      auto_scope: true,
    }));
  });
});
