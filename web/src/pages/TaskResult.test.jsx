import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../api/client", () => ({
  createTask: vi.fn(),
  downloadTaskArtifact: vi.fn(),
  getTask: vi.fn(),
  getTaskArtifactContent: vi.fn(),
  getTaskArtifacts: vi.fn(),
  getTaskEvents: vi.fn(),
}));

import * as api from "../api/client";
import TaskResult from "./TaskResult";

function renderTask() {
  return render(
    <MemoryRouter initialEntries={["/task/task-1"]}>
      <Routes><Route path="/task/:taskId" element={<TaskResult />} /></Routes>
    </MemoryRouter>,
  );
}

describe("TaskResult collection boundary", () => {
  beforeEach(() => {
    api.getTask.mockResolvedValue({
      id: "task-1", name: "测试任务", agent_id: "agent-a", target_pid: 123,
      collector_type: "perf_cpu", sample_rate: 99, duration_sec: 15,
      status: "DONE", collection_status: "COLLECTED", analysis_status: "SUCCESS",
      request_params: {}, created_at: "2026-09-01T10:00:00Z",
    });
    api.getTaskEvents.mockResolvedValue([]);
    api.getTaskArtifacts.mockResolvedValue([]);
  });

  it("renders collection results and points diagnosis to the V2 workspace", async () => {
    renderTask();
    expect(await screen.findByText("测试任务")).toBeInTheDocument();
    expect(screen.getByText(/agent-a/)).toBeInTheDocument();
    expect(screen.getByText("AI 诊断已统一到 Drop Insight 工作台")).toBeInTheDocument();
  });
});
