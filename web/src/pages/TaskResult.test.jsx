import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
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

  it("rejects an explicit zero-sample artifact instead of rendering an empty flamegraph", async () => {
    api.getTaskArtifacts.mockResolvedValue([{
      artifact_type: "flamegraph_json",
      filename: "flamegraph.json",
      size_bytes: 36,
      metadata: {
        sample_count: 0,
        profile_quality: { status: "UNUSABLE", reason_code: "NO_PROFILE_SAMPLES" },
      },
    }]);
    renderTask();

    expect(await screen.findByText(/无法生成可信火焰图 · NO_PROFILE_SAMPLES/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重新采集有效样本/ })).toBeInTheDocument();
    expect(api.getTaskArtifactContent).not.toHaveBeenCalledWith("task-1", "flamegraph_json");
  });

  it("renders continuous window outcomes in Chinese", async () => {
    api.getTaskArtifacts.mockResolvedValue([{
      artifact_type: "continuous_summary",
      filename: "continuous-summary.json",
      size_bytes: 256,
      metadata: {
        windows: [
          { window_index: 1, start_ts: 1, end_ts: 2, ok: true, reason: "采样完成" },
          { window_index: 2, start_ts: 3, end_ts: 4, ok: false, reason: "样本不足" },
        ],
      },
    }]);

    renderTask();

    expect(await screen.findByText("连续采样窗口")).toBeInTheDocument();
    expect(within(screen.getByText("采样完成").closest("tr")).getByText("成功")).toBeInTheDocument();
    expect(within(screen.getByText("样本不足").closest("tr")).getByText("失败")).toBeInTheDocument();
    expect(screen.queryByText("OK")).not.toBeInTheDocument();
    expect(screen.queryByText("FAILED")).not.toBeInTheDocument();
  });
});
