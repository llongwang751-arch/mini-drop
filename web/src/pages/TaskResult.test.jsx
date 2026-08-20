import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../api/client", () => ({
  getTask: vi.fn(),
  getTaskEvents: vi.fn(),
  getTaskArtifacts: vi.fn(),
  listTaskDiagnoses: vi.fn(),
  getDiagnosis: vi.fn(),
  getTaskArtifactContent: vi.fn(),
  downloadTaskArtifact: vi.fn(),
  createTask: vi.fn(),
  submitDiagnosisFeedback: vi.fn(),
  triggerDiagnose: vi.fn(),
}));

import * as api from "../api/client";
import TaskResult from "./TaskResult";

function renderTask() {
  return render(
    <MemoryRouter initialEntries={["/task/task-1"]}>
      <Routes>
        <Route path="/task/:taskId" element={<TaskResult />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("TaskResult", () => {
  beforeEach(() => {
    api.getTask.mockResolvedValue({
      id: "task-1",
      name: "测试任务",
      agent_id: "agent-a",
      target_pid: 123,
      collector_type: "perf_cpu",
      sample_rate: 99,
      duration_sec: 15,
      status: "DONE",
      collection_status: "SUCCEEDED",
      analysis_status: "DONE",
      request_params: {},
      created_at: "2026-08-05T10:00:00Z",
    });
    api.getTaskEvents.mockResolvedValue([]);
    api.getTaskArtifacts.mockResolvedValue([]);
    api.listTaskDiagnoses.mockResolvedValue([]);
    api.getDiagnosis.mockResolvedValue(null);
  });

  it("loads and renders the task name and agent", async () => {
    renderTask();
    expect(await screen.findByText("测试任务")).toBeInTheDocument();
    expect(screen.getByText(/agent-a/)).toBeInTheDocument();
  });

  it("renders a done task without crashing", async () => {
    renderTask();
    expect(await screen.findByText("测试任务")).toBeInTheDocument();
  });
});

describe("TaskResult trust tags", () => {
  it("renders generation mode and validation layers instead of a bare boolean", async () => {
    api.listTaskDiagnoses.mockResolvedValue([{ id: "diag-1" }]);
    api.getDiagnosis.mockResolvedValue({
      run: { status: "DONE" },
      report: {
        report: {
          summary: "降级诊断",
          generation_mode: "RULE_FALLBACK",
          semantic_validated: false,
          model_invoked: false,
          fallback_reason: "无可用模型",
          verification: { status: "INSUFFICIENT_EVIDENCE" },
        },
        ranked_causes: [],
      },
      tool_results: [],
      repair_plan: null,
    });
    renderTask();
    expect(await screen.findByText("RULE_FALLBACK")).toBeInTheDocument();
    expect(screen.getByText("模型未调用")).toBeInTheDocument();
    expect(screen.getByText(/语义未验证/)).toBeInTheDocument();
    expect(screen.getByText(/反证门禁/)).toBeInTheDocument();
    expect(screen.getByText("无可用模型")).toBeInTheDocument();
  });
});

describe("TaskResult dual status", () => {
  it("shows collection succeeded but analysis failed distinctly", async () => {
    api.getTask.mockResolvedValue({
      id: "task-1",
      name: "双状态任务",
      agent_id: "agent-a",
      target_pid: 123,
      collector_type: "perf_cpu",
      sample_rate: 99,
      duration_sec: 15,
      status: "FAILED",
      collection_status: "SUCCEEDED",
      analysis_status: "FAILED",
      request_params: {},
      created_at: "2026-08-05T10:00:00Z",
    });
    renderTask();
    expect(await screen.findByText("双状态任务")).toBeInTheDocument();
    // Both the collection tag and the analysis tag are visible (采集成功/分析失败).
    expect(screen.getByText("SUCCEEDED")).toBeInTheDocument();
    expect(screen.getAllByText("FAILED").length).toBeGreaterThan(0);
  });
});
