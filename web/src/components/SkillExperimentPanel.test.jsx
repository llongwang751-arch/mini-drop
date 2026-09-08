import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SkillExperimentPanel from "./SkillExperimentPanel";

vi.mock("../api/client", () => ({
  approveDiagnosticExperiment: vi.fn(),
  assignDiagnosticExperiment: vi.fn(),
  createDiagnosticExperiment: vi.fn(),
  evaluateDiagnosticExperiment: vi.fn(),
  getDiagnosticExperiment: vi.fn(),
  listDiagnosticExperiments: vi.fn(),
  recordDiagnosticExperimentOutcome: vi.fn(),
  runDropInsightPlanner: vi.fn(),
}));

import * as api from "../api/client";

const summary = {
  experiment: {
    experiment_id: "exp-1",
    name: "Skill 随机实验",
    status: "RUNNING",
    minimum_labeled_per_arm: 30,
  },
  arms: {
    AUTO: { root_cause_accuracy: { rate: null, total: 0 } },
    DISABLED: { root_cause_accuracy: { rate: null, total: 0 } },
  },
  significance: {
    p_value: null,
    delta_percentage_points: null,
    confidence_interval_95_percentage_points: [null, null],
  },
  assignment_count: 0,
  labeled_count: 0,
  assignments: [],
  metric_history: [],
};

describe("SkillExperimentPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listDiagnosticExperiments.mockResolvedValue([
      { experiment_id: "exp-1", name: "Skill 随机实验", status: "RUNNING" },
    ]);
    api.getDiagnosticExperiment.mockResolvedValue(summary);
    api.assignDiagnosticExperiment.mockResolvedValue({
      assignment: { assignment_id: "a-1", arm: "AUTO" },
      diagnosis: { diagnosis_id: "diag-1" },
    });
    api.runDropInsightPlanner.mockResolvedValue({});
  });

  afterEach(cleanup);

  it("uses the server-assigned arm and never asks the browser to choose one", async () => {
    render(<SkillExperimentPanel seedRequest={{ query: "诊断 Go CPU 异常" }} />);
    await screen.findByText("服务端随机实验");
    fireEvent.click(screen.getByRole("button", { name: /由服务端随机分流并诊断/ }));

    await waitFor(() => expect(api.assignDiagnosticExperiment).toHaveBeenCalledTimes(1));
    const payload = api.assignDiagnosticExperiment.mock.calls[0][1];
    expect(payload.diagnosis).not.toHaveProperty("skill_policy");
    expect(payload.unit_key).toMatch(/^manual-traffic-/);
    expect(api.runDropInsightPlanner).toHaveBeenCalledWith("diag-1");
  });
});
