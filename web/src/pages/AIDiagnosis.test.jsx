import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import AIDiagnosis from "./AIDiagnosis";

vi.mock("../api/client", () => ({
  listDropInsightDiagnoses: vi.fn(),
  listDiagnosticCasesPage: vi.fn(),
  getDiagnosticCase: vi.fn(),
  getDropInsightDiagnosis: vi.fn(),
  getDropInsightTargetCandidates: vi.fn(),
  listDropInsightEvents: vi.fn(),
  listDropInsightHypotheses: vi.fn(),
  listDropInsightEvidence: vi.fn(),
  listDropInsightReports: vi.fn(),
  listDropInsightToolCalls: vi.fn(),
  getDropInsightBudget: vi.fn(),
  updateDropInsightToolCall: vi.fn(),
  clarifyDropInsightDiagnosis: vi.fn(),
  deleteDropInsightDiagnosis: vi.fn(),
  listDropInsightFeedback: vi.fn(),
  listDiagnosticSkillActivations: vi.fn(),
  submitDropInsightFeedback: vi.fn(),
  createDropInsightDiagnosis: vi.fn(),
  runDropInsightPlanner: vi.fn(),
  decideDropInsightToolCall: vi.fn(),
  advanceDropInsightOrchestrator: vi.fn(),
}));

import * as api from "../api/client";

function diagnosticCase(overrides = {}) {
  return {
    case_id: "diag-1",
    diagnosis_id: "diag-1",
    source: "drop_insight_v2",
    query: "订单服务 CPU 高",
    status: "COMPLETED",
    canonical_status: "COMPLETED",
    updated_at: "2026-08-21T08:00:00Z",
    ...overrides,
  };
}

function caseButton(label) {
  const button = screen
    .getAllByText(label)
    .map((node) => node.closest("button.diagnosis-case-select"))
    .find(Boolean);
  if (!button) throw new Error(`Case button not found: ${label}`);
  return button;
}

function clickCase(label) {
  fireEvent.click(caseButton(label));
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("AIDiagnosis conversation page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.history.replaceState({}, "", "/ai-diagnosis");
    api.listDropInsightDiagnoses.mockResolvedValue([]);
    api.listDiagnosticCasesPage.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    api.getDiagnosticCase.mockResolvedValue({ native_payload: {} });
    api.getDropInsightDiagnosis.mockResolvedValue(null);
    api.getDropInsightTargetCandidates.mockResolvedValue({
      diagnosis_id: "diag-1",
      diagnosis_version: 1,
      discovery_id: "discovery-1",
      status: "EMPTY",
      reason: null,
      candidates: [],
    });
    api.listDropInsightEvents.mockResolvedValue([]);
    api.listDropInsightHypotheses.mockResolvedValue([]);
    api.listDropInsightEvidence.mockResolvedValue([]);
    api.listDropInsightReports.mockResolvedValue([]);
    api.listDropInsightToolCalls.mockResolvedValue([]);
    api.listDropInsightFeedback.mockResolvedValue([]);
    api.listDiagnosticSkillActivations.mockResolvedValue([]);
    api.getDropInsightBudget.mockResolvedValue(null);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("renders the unified case list and composer", async () => {
    api.listDropInsightDiagnoses.mockResolvedValue([
      diagnosticCase({ source: undefined, canonical_status: undefined }),
    ]);

    render(<AIDiagnosis />);

    expect(await screen.findByText("订单服务 CPU 高")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/描述问题/)).toBeInTheDocument();
    expect(screen.getByText("诊断工作台")).toBeInTheDocument();
  });

  it("shows when a verified diagnostic strategy changes the probe route", async () => {
    const item = diagnosticCase();
    api.listDropInsightDiagnoses.mockResolvedValue([item]);
    api.getDropInsightDiagnosis.mockResolvedValue({
      diagnosis_id: "diag-1",
      query: "订单服务 CPU 高",
      status: "COMPLETED",
    });
    api.listDiagnosticSkillActivations.mockResolvedValue([{
      activation_id: "activation-1",
      skill_id: "skill-1",
      match_score: 0.91,
      baseline_tool: "collect_sys_metrics",
      selected_tool: "start_perf_profile",
      match_reason: {
        skill_version: 2,
        route: ["start_perf_profile", "collect_sys_metrics"],
      },
      updated_at: "2026-08-24T09:00:00Z",
    }]);

    render(<AIDiagnosis />);
    await screen.findByText("订单服务 CPU 高");
    clickCase("订单服务 CPU 高");

    expect(await screen.findByText("已复用经过验证的诊断经验")).toBeInTheDocument();
    expect(screen.getByText("技能 v2")).toBeInTheDocument();
    expect(screen.getByText("匹配度 91%")).toBeInTheDocument();
    expect(screen.getAllByText("start_perf_profile").length).toBeGreaterThan(0);
  });

  it("starts a new conversation on send", async () => {
    api.createDropInsightDiagnosis.mockResolvedValue({ diagnosis_id: "diag-new" });
    api.runDropInsightPlanner.mockResolvedValue({});
    api.listDropInsightDiagnoses.mockResolvedValue([
      diagnosticCase({ diagnosis_id: "diag-new", case_id: "diag-new", query: "新问题", status: "RUNNING" }),
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
    await waitFor(() => expect(window.location.search).toContain("case=drop_insight_v2%3Adiag-new"));
  });

  it("passes diagnosis identity and version into secure target discovery and clarify", async () => {
    const item = diagnosticCase({ status: "NEEDS_CLARIFICATION", canonical_status: "NEEDS_CLARIFICATION" });
    api.listDropInsightDiagnoses.mockResolvedValue([item]);
    api.getDropInsightDiagnosis.mockResolvedValue({
      diagnosis_id: "diag-1",
      query: "订单服务 CPU 高",
      status: "NEEDS_CLARIFICATION",
      version: 12,
      clarification_questions: [],
    });
    api.getDropInsightTargetCandidates.mockResolvedValue({
      diagnosis_id: "diag-1",
      diagnosis_version: 12,
      discovery_id: "discovery-12",
      status: "READY",
      reason: null,
      candidates: [{
        binding_id: "binding-12",
        service: "order-service",
        instance: "order-service-7c9d",
        process: "order-service",
        collector_capabilities: ["perf_cpu"],
        eligible: true,
      }],
    });
    api.clarifyDropInsightDiagnosis.mockResolvedValue({});
    api.runDropInsightPlanner.mockResolvedValue({});

    render(<AIDiagnosis />);
    await screen.findByText("订单服务 CPU 高");
    clickCase("订单服务 CPU 高");
    expect(await screen.findByText("已选安全目标：binding-12")).toBeInTheDocument();
    expect(api.getDropInsightTargetCandidates).toHaveBeenCalledWith("diag-1");

    fireEvent.change(screen.getByLabelText("服务"), { target: { value: "order-service" } });
    fireEvent.mouseDown(screen.getByLabelText("环境"));
    fireEvent.click(await screen.findByText("production"));
    fireEvent.click(screen.getByRole("button", { name: "确认范围并开始取证" }));

    await waitFor(() => expect(api.clarifyDropInsightDiagnosis).toHaveBeenCalledTimes(1));
    expect(api.clarifyDropInsightDiagnosis).toHaveBeenCalledWith("diag-1", expect.objectContaining({
      expected_version: 12,
      target: {
        service: "order-service",
        environment: "production",
        binding_id: "binding-12",
        discovery_id: "discovery-12",
      },
    }));
  });

  it("de-duplicates the same v2 diagnosis from active and archive APIs", async () => {
    api.listDropInsightDiagnoses.mockResolvedValue([
      diagnosticCase({ source: undefined, canonical_status: undefined }),
    ]);
    api.listDiagnosticCasesPage.mockResolvedValue({
      items: [diagnosticCase()],
      total: 1,
      limit: 100,
      offset: 0,
    });

    render(<AIDiagnosis />);

    await screen.findByText("订单服务 CPU 高");
    expect(screen.getAllByText("订单服务 CPU 高")).toHaveLength(1);
  });

  it("filters the unified list by canonical status", async () => {
    api.listDiagnosticCasesPage.mockResolvedValue({
      items: [
        diagnosticCase({ case_id: "active", diagnosis_id: "active", query: "正在采集", status: "RUNNING", canonical_status: "COLLECTING" }),
        diagnosticCase({ case_id: "done", diagnosis_id: "done", query: "已经完成" }),
      ],
      total: 2,
      limit: 100,
      offset: 0,
    });

    render(<AIDiagnosis />);
    await screen.findByText("正在采集");
    fireEvent.click(screen.getAllByText("已完成")[0]);

    expect(screen.queryByText("正在采集")).not.toBeInTheDocument();
    expect(screen.getByText("已经完成")).toBeInTheDocument();
  });

  it("hydrates and adapts a v1 historical diagnosis as read-only", async () => {
    const item = diagnosticCase({
      case_id: "cluster-1",
      diagnosis_id: undefined,
      source: "cluster_diagnosis_v1",
      query: "历史集群问题",
    });
    api.listDiagnosticCasesPage.mockResolvedValue({ items: [item], total: 1, limit: 100, offset: 0 });
    api.getDiagnosticCase.mockResolvedValue({
      native_payload: {
        id: "cluster-1",
        raw_query: "节点内存异常",
        status: "COMPLETED",
        hypothesis_graph: {
          hypotheses: [{ hypothesis_id: "h-1", statement: "存在内存泄漏", status: "SUPPORTED" }],
        },
        evidence: [],
        conclusion_versions: [{ conclusion: "缓存对象未释放", confidence: 0.82 }],
      },
    });

    render(<AIDiagnosis />);
    await screen.findByText("历史集群问题");
    clickCase("历史集群问题");

    expect((await screen.findAllByText("节点内存异常")).length).toBeGreaterThan(0);
    expect(screen.getByText("存在内存泄漏")).toBeInTheDocument();
    expect(screen.getByText("缓存对象未释放")).toBeInTheDocument();
    expect(screen.getByText("只读记录")).toBeInTheDocument();
    expect(screen.getByText(/该版本未记录此类数据/)).toBeInTheDocument();
    expect(api.getDiagnosticCase).toHaveBeenCalledWith("cluster-1");
    expect(screen.queryByRole("button", { name: "继续推进" })).not.toBeInTheDocument();
  });

  it("adapts a legacy RCA report without exposing write actions", async () => {
    const item = diagnosticCase({
      case_id: "rca-1",
      diagnosis_id: undefined,
      source: "legacy_rca",
      query: "旧版任务 RCA",
    });
    api.listDiagnosticCasesPage.mockResolvedValue({ items: [item], total: 1, limit: 100, offset: 0 });
    api.getDiagnosticCase.mockResolvedValue({
      native_payload: {
        run: { id: "rca-1", task_id: "task-1", status: "SUCCEEDED", summary: "CPU hotspot" },
        report: { summary: "热点位于序列化函数", confidence: 0.7 },
      },
    });

    render(<AIDiagnosis />);
    await screen.findByText("旧版任务 RCA");
    clickCase("旧版任务 RCA");

    expect((await screen.findAllByText("CPU hotspot")).length).toBeGreaterThan(0);
    expect(screen.getByText("热点位于序列化函数")).toBeInTheDocument();
    expect(screen.getByText("只读记录")).toBeInTheDocument();
  });

  it("keeps successful v2 resources visible when an optional resource fails", async () => {
    const item = diagnosticCase({ status: "RUNNING", canonical_status: "COLLECTING" });
    api.listDropInsightDiagnoses.mockResolvedValue([item]);
    api.getDropInsightDiagnosis.mockResolvedValue({ diagnosis_id: "diag-1", query: "订单服务 CPU 高", status: "RUNNING" });
    api.listDropInsightHypotheses.mockResolvedValue([
      { hypothesis_id: "h-1", statement: "线程竞争", status: "OPEN" },
    ]);
    api.listDropInsightEvidence.mockRejectedValue(new Error("evidence unavailable"));

    render(<AIDiagnosis />);
    await screen.findByText("订单服务 CPU 高");
    clickCase("订单服务 CPU 高");

    expect(await screen.findByText("线程竞争")).toBeInTheDocument();
    expect(screen.getByText(/部分数据加载失败：证据/)).toBeInTheDocument();
    expect((await screen.findAllByText("订单服务 CPU 高")).length).toBeGreaterThan(1);
  });

  it("suppresses pending tool approval controls for archived v2 records", async () => {
    const item = diagnosticCase();
    api.listDiagnosticCasesPage.mockResolvedValue({ items: [item], total: 1, limit: 100, offset: 0 });
    api.getDiagnosticCase.mockResolvedValue({
      native_payload: { diagnosis_id: "diag-1", query: "订单服务 CPU 高", status: "COMPLETED" },
    });
    api.getDropInsightDiagnosis.mockRejectedValue(new Error("archived native endpoint unavailable"));
    api.listDropInsightToolCalls.mockResolvedValue([
      { tool_call_id: "tool-1", tool_name: "collect_sys_metrics", status: "PENDING_APPROVAL", arguments_json: {} },
    ]);

    render(<AIDiagnosis />);
    await screen.findByText("订单服务 CPU 高");
    clickCase("订单服务 CPU 高");

    expect(await screen.findByText("查询 Agent 状态").catch(() => screen.findByText("采集系统指标"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "通过" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拒绝" })).not.toBeInTheDocument();
  });

  it("marks the selected case and archive clicks do not select it", async () => {
    const first = diagnosticCase({
      case_id: "diag-1",
      diagnosis_id: "diag-1",
      query: "第一个案例",
      status: "RUNNING",
      canonical_status: "COLLECTING",
    });
    const second = diagnosticCase({
      case_id: "diag-2",
      diagnosis_id: "diag-2",
      query: "第二个案例",
      status: "RUNNING",
      canonical_status: "COLLECTING",
    });
    api.listDropInsightDiagnoses.mockResolvedValue([first, second]);
    api.getDropInsightDiagnosis.mockImplementation((id) =>
      Promise.resolve({
        diagnosis_id: id,
        query: id === "diag-1" ? "第一个详情" : "第二个详情",
        status: "RUNNING",
      }),
    );

    render(<AIDiagnosis />);
    await screen.findByText("第一个案例");
    clickCase("第一个案例");

    const firstButton = caseButton("第一个案例");
    const secondButton = caseButton("第二个案例");
    expect(firstButton).toHaveAttribute("aria-current", "true");
    expect(secondButton).not.toHaveAttribute("aria-current");

    fireEvent.click(screen.getByRole("button", { name: "归档诊断：第二个案例" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("归档诊断「第二个案例」？");
    expect(firstButton).toHaveAttribute("aria-current", "true");
    expect(secondButton).not.toHaveAttribute("aria-current");
  });

  it("ignores stale detail responses after the user switches cases", async () => {
    const first = diagnosticCase({ case_id: "diag-1", diagnosis_id: "diag-1", query: "第一个问题" });
    const second = diagnosticCase({ case_id: "diag-2", diagnosis_id: "diag-2", query: "第二个问题" });
    const firstRequest = deferred();
    api.listDropInsightDiagnoses.mockResolvedValue([first, second]);
    api.getDropInsightDiagnosis.mockImplementation((id) => {
      if (id === "diag-1") return firstRequest.promise;
      return Promise.resolve({ diagnosis_id: "diag-2", query: "第二个详情", status: "COMPLETED" });
    });

    render(<AIDiagnosis />);
    await screen.findByText("第一个问题");
    clickCase("第一个问题");
    clickCase("第二个问题");
    expect((await screen.findAllByText("第二个详情")).length).toBeGreaterThan(0);

    await act(async () => {
      firstRequest.resolve({ diagnosis_id: "diag-1", query: "过期的第一个详情", status: "COMPLETED" });
      await firstRequest.promise;
    });
    await waitFor(() => expect(screen.queryByText("过期的第一个详情")).not.toBeInTheDocument());
    expect(screen.getAllByText("第二个详情").length).toBeGreaterThan(0);
  });

  it("polls active diagnoses with GET requests only", async () => {
    vi.useFakeTimers();
    const item = diagnosticCase({ status: "RUNNING", canonical_status: "COLLECTING" });
    api.listDropInsightDiagnoses.mockResolvedValue([item]);
    api.getDropInsightDiagnosis.mockResolvedValue({
      diagnosis_id: "diag-1",
      query: "订单服务 CPU 高",
      status: "RUNNING",
    });

    render(<AIDiagnosis />);
    await act(async () => { await Promise.resolve(); });
    clickCase("订单服务 CPU 高");
    await act(async () => { await Promise.resolve(); });
    const diagnosisReadsBeforePoll = api.getDropInsightDiagnosis.mock.calls.length;

    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });

    expect(api.getDropInsightDiagnosis.mock.calls.length).toBeGreaterThan(diagnosisReadsBeforePoll);
    expect(api.advanceDropInsightOrchestrator).not.toHaveBeenCalled();
  });

  it("does not poll terminal diagnoses", async () => {
    vi.useFakeTimers();
    const item = diagnosticCase();
    api.listDropInsightDiagnoses.mockResolvedValue([item]);
    api.getDropInsightDiagnosis.mockResolvedValue({
      diagnosis_id: "diag-1",
      query: "订单服务 CPU 高",
      status: "COMPLETED",
    });

    render(<AIDiagnosis />);
    await act(async () => { await Promise.resolve(); });
    clickCase("订单服务 CPU 高");
    await act(async () => { await Promise.resolve(); });
    const diagnosisReadsBeforeWait = api.getDropInsightDiagnosis.mock.calls.length;

    await act(async () => { await vi.advanceTimersByTimeAsync(7500); });

    expect(api.getDropInsightDiagnosis).toHaveBeenCalledTimes(diagnosisReadsBeforeWait);
    expect(api.advanceDropInsightOrchestrator).not.toHaveBeenCalled();
  });

  it("advances only after an explicit user click", async () => {
    const item = diagnosticCase({ status: "RUNNING", canonical_status: "COLLECTING" });
    api.listDropInsightDiagnoses.mockResolvedValue([item]);
    api.getDropInsightDiagnosis.mockResolvedValue({
      diagnosis_id: "diag-1",
      query: "订单服务 CPU 高",
      status: "RUNNING",
    });
    api.advanceDropInsightOrchestrator.mockResolvedValue({});

    render(<AIDiagnosis />);
    await screen.findByText("订单服务 CPU 高");
    clickCase("订单服务 CPU 高");
    const button = await screen.findByRole("button", { name: /继续推进/ });

    expect(api.advanceDropInsightOrchestrator).not.toHaveBeenCalled();
    fireEvent.click(button);

    await waitFor(() => expect(api.advanceDropInsightOrchestrator).toHaveBeenCalledWith("diag-1"));
  });

  it("selects a case from the query parameter", async () => {
    const item = diagnosticCase();
    window.history.replaceState({}, "", "/ai-diagnosis?case=drop_insight_v2%3Adiag-1");
    api.listDropInsightDiagnoses.mockResolvedValue([item]);
    api.getDropInsightDiagnosis.mockResolvedValue({ diagnosis_id: "diag-1", query: "深链接详情", status: "COMPLETED" });

    render(<AIDiagnosis />);
    expect((await screen.findAllByText("深链接详情")).length).toBeGreaterThan(0);
    expect(api.getDropInsightDiagnosis).toHaveBeenCalledWith("diag-1");
  });

  it("recovers from an unknown query-parameter case", async () => {
    window.history.replaceState({}, "", "/ai-diagnosis?case=drop_insight_v2%3Amissing");

    render(<AIDiagnosis />);

    expect(await screen.findByText("链接中的诊断案例不存在或已从列表隐藏。")).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });
});
