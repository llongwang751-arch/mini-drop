import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import RealWorldBenchmarkPanel from "./RealWorldBenchmarkPanel";

vi.mock("../api/client", () => ({
  getRealWorldBenchmarkCatalog: vi.fn(),
  getRealWorldComparisons: vi.fn(),
  getRealWorldBenchmarkRun: vi.fn(),
  getRealWorldComparisonInput: vi.fn(),
  startRealWorldBenchmark: vi.fn(),
  submitRealWorldComparison: vi.fn(),
}));

import * as api from "../api/client";

const catalog = {
  cases: [{
    case_id: "RW-TEST-1",
    title: "真实机制案例",
    project: "upstream/project",
    language: "Python",
    query: "内存持续增长",
    source_url: "https://example.com/pr/1",
    web_execution: "MECHANISM_REPRO_AVAILABLE",
    required_evidence: [],
  }],
  runnable_count: 1,
  replayed_count: 0,
  comparators: [],
  fair_comparison_rule: "使用相同输入。",
};

function completedRun(overrides = {}) {
  return {
    run_id: "run-1",
    case_id: "RW-TEST-1",
    status: "COMPLETED",
    stage: "COMPLETED",
    progress: 100,
    message: "执行完成",
    execution_fidelity: "MECHANISM_REPRO",
    scoring_status: "UNSCORED",
    events: [],
    snapshots: [],
    result: {
      passed: null,
      summary: "低资源机制复现结果",
      mechanism_verified: true,
      recovery_verified: false,
      admission_reason: "仅纳入机制验证",
      evidence_refs: ["snapshot:incident"],
      counter_evidence_refs: null,
      limitations: ["不是完整上游仓库回放"],
    },
    ...overrides,
  };
}

async function startAndReturn(run) {
  api.startRealWorldBenchmark.mockResolvedValue({
    run_id: "run-1",
    case_id: "RW-TEST-1",
    status: "RUNNING",
  });
  api.getRealWorldBenchmarkRun.mockResolvedValue(run);
  render(<RealWorldBenchmarkPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /在云端运行/ }));
  await waitFor(() => expect(api.getRealWorldBenchmarkRun).toHaveBeenCalledTimes(1));
}

describe("RealWorldBenchmarkPanel", () => {
  beforeEach(() => {
    api.getRealWorldBenchmarkCatalog.mockResolvedValue(catalog);
    api.getRealWorldComparisons.mockResolvedValue({
      items: [],
      latest_by_comparator: {},
      actual_submission_count: 0,
      scored_submission_count: 0,
      evaluator_ready: false,
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("renders an unscored mechanism completion without green Oracle-pass semantics", async () => {
    await startAndReturn(completedRun());

    expect(await screen.findByText("机制复现执行完成，未进入正式评分")).toBeInTheDocument();
    expect(screen.getByText("未评分/不适用")).toBeInTheDocument();
    expect(screen.getByText("机制验证结果（非 Oracle 正式评分）")).toBeInTheDocument();
    expect(screen.getByText("仅纳入机制验证")).toBeInTheDocument();
    expect(screen.getByText("snapshot:incident")).toBeInTheDocument();
    expect(screen.getByText("不是完整上游仓库回放")).toBeInTheDocument();
    expect(screen.getAllByText("是").length).toBeGreaterThan(0);
    expect(screen.getAllByText("否").length).toBeGreaterThan(0);
    expect(screen.queryByText(/根因机制命中|Oracle 门禁/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /下载同条件对照输入/ })).toBeInTheDocument();
  });

  it.each([
    ["FAILED", "执行失败"],
    ["INTERRUPTED", "执行中断"],
    ["PAUSED_BY_PLATFORM", "未知状态（PAUSED_BY_PLATFORM）"],
  ])("distinguishes terminal execution status %s and does not poll again", async (status, label) => {
    await startAndReturn(completedRun({ status, progress: 60, result: null, scoring_status: "UNSCORED" }));

    expect(screen.getByText(label)).toBeInTheDocument();
    await new Promise((resolve) => window.setTimeout(resolve, 450));
    expect(api.getRealWorldBenchmarkRun).toHaveBeenCalledTimes(1);
  });

  it("polls again only while execution status is RUNNING", async () => {
    api.startRealWorldBenchmark.mockResolvedValue({ run_id: "run-1", status: "RUNNING" });
    api.getRealWorldBenchmarkRun
      .mockResolvedValueOnce(completedRun({ status: "RUNNING", stage: "INCIDENT", progress: 40, result: null }))
      .mockResolvedValueOnce(completedRun({ status: "INTERRUPTED", progress: 40, result: null }));

    render(<RealWorldBenchmarkPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /在云端运行/ }));
    await waitFor(() => expect(api.getRealWorldBenchmarkRun).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.getRealWorldBenchmarkRun).toHaveBeenCalledTimes(2), { timeout: 1000 });
    await new Promise((resolve) => window.setTimeout(resolve, 450));
    expect(api.getRealWorldBenchmarkRun).toHaveBeenCalledTimes(2);
  });
});
