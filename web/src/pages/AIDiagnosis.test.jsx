import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import AIDiagnosis from "./AIDiagnosis";

vi.mock("../api/client", () => ({
  advanceDropInsightOrchestrator: vi.fn(),
  clarifyDropInsightDiagnosis: vi.fn(),
  createDropInsightDiagnosis: vi.fn(),
  createDiagnosticSkillCandidate: vi.fn(),
  decideDropInsightToolCall: vi.fn(),
  deleteDropInsightDiagnosis: vi.fn(),
  evaluateDiagnosticSkill: vi.fn(),
  getAgentRuntimeStatus: vi.fn(),
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
  listDropInsightInterventions: vi.fn(),
  listDropInsightReports: vi.fn(),
  listDropInsightRetrievals: vi.fn(),
  listDropInsightToolCalls: vi.fn(),
  runDropInsightPlanner: vi.fn(),
  submitDropInsightFeedback: vi.fn(),
  submitDropInsightIntervention: vi.fn(),
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
    api.listDropInsightRetrievals.mockResolvedValue([]);
    api.listDropInsightToolCalls.mockResolvedValue([]);
    api.listDropInsightFeedback.mockResolvedValue([]);
    api.listDropInsightInterventions.mockResolvedValue([]);
    api.listDiagnosticSkillActivations.mockResolvedValue([]);
    api.listDiagnosticSkills.mockResolvedValue([]);
    api.getDropInsightBudget.mockResolvedValue(null);
    api.getAgentRuntimeStatus.mockResolvedValue(null);
  });

  afterEach(cleanup);

  it("renders only Drop Insight V2 diagnoses", async () => {
    api.listDropInsightDiagnoses.mockResolvedValue([diagnosis]);
    api.getDropInsightDiagnosis.mockResolvedValue(diagnosis);
    render(<AIDiagnosis />);
    await waitFor(() => expect(api.listDropInsightDiagnoses).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开诊断案例列表" }));
    const drawer = await screen.findByRole("dialog", { name: "诊断案例" });
    expect(within(drawer).getByText("订单服务 CPU 高")).toBeInTheDocument();
    expect(within(drawer).getByText("Drop Insight V2")).toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole("button", { name: "打开诊断：订单服务 CPU 高" }));
    // Ant Design keeps the drawer mounted until its leave animation finishes.
    // Under the full Vitest suite the shared jsdom event loop can exceed the
    // default one-second wait even though selectCase closes it synchronously.
    await waitFor(
      () => expect(screen.queryByRole("dialog", { name: "诊断案例" })).not.toBeInTheDocument(),
      { timeout: 5000 },
    );
    expect(await screen.findByLabelText("补充诊断上下文")).toBeInTheDocument();
  });

  it("uses a full-width primary view and only splits when the user asks", async () => {
    window.localStorage.removeItem("mini-drop-diagnosis-content-view");
    window.history.replaceState({}, "", "/ai-diagnosis?case=drop_insight_v2%3Adiag-1");
    api.listDropInsightDiagnoses.mockResolvedValue([diagnosis]);
    api.getDropInsightDiagnosis.mockResolvedValue(diagnosis);
    api.getDropInsightExplorationTree.mockResolvedValue({
      revision: 1,
      stats: { rounds: 1, nodes: 1 },
      nodes: [{ id: "diagnosis:diag-1", parent_id: null, title: "CPU 高", state: "visited" }],
    });

    render(<AIDiagnosis />);
    expect(await screen.findByLabelText("Agent 可观测控制台")).toBeInTheDocument();
    expect(document.querySelector(".diagnosis-workbench-grid.view-conversation")).not.toBeNull();
    expect(screen.queryByLabelText("实时诊断探索树")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("探索树"));
    expect(document.querySelector(".diagnosis-workbench-grid.view-tree")).not.toBeNull();
    expect(screen.getByLabelText("实时诊断探索树")).toBeInTheDocument();
    expect(screen.queryByLabelText("多轮诊断对话")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("分屏"));
    expect(document.querySelector(".diagnosis-workbench-grid.view-split")).not.toBeNull();
    expect(screen.getByLabelText("多轮诊断对话")).toBeInTheDocument();
    expect(screen.getByLabelText("实时诊断探索树")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "全屏查看探索树" }));
    const fullscreenTree = screen.getByRole("dialog", { name: "实时诊断探索树 · 全屏阅读" });
    expect(within(fullscreenTree).getByText("实时探索树")).toBeInTheDocument();
  });

  it("lets the user review and edit a tree intervention before starting a new round", async () => {
    window.localStorage.removeItem("mini-drop-diagnosis-content-view");
    window.history.replaceState({}, "", "/ai-diagnosis?case=drop_insight_v2%3Adiag-1");
    api.listDropInsightDiagnoses.mockResolvedValue([{ ...diagnosis, diagnosis_version: 7 }]);
    api.getDropInsightDiagnosis.mockResolvedValue({ ...diagnosis, diagnosis_version: 7 });
    api.getDropInsightExplorationTree.mockResolvedValue({
      revision: 2,
      stats: { rounds: 1, nodes: 2 },
      nodes: [
        { id: "diagnosis:diag-1", parent_id: null, kind: "diagnosis", title: "CPU 高", state: "visited" },
        { id: "hypothesis:h-1", parent_id: "diagnosis:diag-1", kind: "hypothesis", title: "用户态热点", state: "visited", round_index: 1 },
      ],
      switches: [],
    });
    api.submitDropInsightIntervention.mockResolvedValue({
      intervention_id: "int-tree",
      diagnosis_id: "diag-1",
      action: "CHALLENGE_HYPOTHESIS",
      message: "先检查是否有能够推翻热点假设的调度证据",
      hypothesis_id: "h-1",
      round_index: 2,
      diagnosis_version: 8,
    });

    render(<AIDiagnosis />);
    await screen.findByLabelText("Agent 可观测控制台");
    fireEvent.click(screen.getByText("探索树"));
    const tree = screen.getByLabelText("实时诊断探索树");
    fireEvent.click(within(tree).getByRole("button", { name: "寻找反证：用户态热点" }));

    expect(api.submitDropInsightIntervention).not.toHaveBeenCalled();
    expect(await screen.findByRole("dialog", { name: "人工调整诊断路径" })).toBeInTheDocument();
    const instruction = screen.getByLabelText("给 Agent 的具体要求");
    fireEvent.change(instruction, { target: { value: "先检查是否有能够推翻热点假设的调度证据" } });
    fireEvent.click(screen.getByRole("button", { name: "确认并开启下一轮" }));

    await waitFor(() => expect(api.submitDropInsightIntervention).toHaveBeenCalledWith(
      "diag-1",
      expect.objectContaining({
        expected_version: 7,
        action: "CHALLENGE_HYPOTHESIS",
        hypothesis_id: "h-1",
        message: "先检查是否有能够推翻热点假设的调度证据",
      }),
    ));
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
      mode: "AUTONOMOUS",
      auto_scope: true,
    }));
  });

  it("merges the active diagnosis replay, exploration tree and Skill decision in a modal", async () => {
    window.history.replaceState({}, "", "/ai-diagnosis?case=drop_insight_v2%3Adiag-1");
    api.listDropInsightDiagnoses.mockResolvedValue([diagnosis]);
    api.getDropInsightDiagnosis.mockResolvedValue(diagnosis);
    api.getDropInsightExplorationTree.mockResolvedValue({
      revision: 3,
      stats: { rounds: 2, nodes: 0, pruned: 0 },
      nodes: [],
    });

    render(<AIDiagnosis />);

    const trigger = await screen.findByRole("button", { name: "打开当前复杂案例回放" });
    expect(screen.getByRole("heading", { name: /当前诊断 · 第/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("Mini-Drop 诊断架构分层")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(await screen.findByText("当前案例探索树")).toBeInTheDocument();
    expect(screen.getAllByText("diag-1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("本案例暂不沉淀 Skill")).toBeInTheDocument();
    expect(screen.getByText("最近一次树更新")).toBeInTheDocument();
    expect(document.querySelector(".ant-drawer")).toBeNull();
  });

  it("continues a completed diagnosis as a real intervention round", async () => {
    window.history.replaceState({}, "", "/ai-diagnosis?case=drop_insight_v2%3Adiag-1");
    api.listDropInsightDiagnoses.mockResolvedValue([{ ...diagnosis, diagnosis_version: 7 }]);
    api.getDropInsightDiagnosis.mockResolvedValue({ ...diagnosis, diagnosis_version: 7 });
    api.submitDropInsightIntervention.mockResolvedValue({
      intervention_id: "int-1",
      diagnosis_id: "diag-1",
      action: "ADD_CONTEXT",
      message: "补充：磁盘等待也在同一时间升高",
      round_index: 2,
      diagnosis_version: 8,
    });
    render(<AIDiagnosis />);

    const input = await screen.findByLabelText("补充诊断上下文");
    fireEvent.change(input, { target: { value: "补充：磁盘等待也在同一时间升高" } });
    fireEvent.click(screen.getByRole("button", { name: /基于结论继续一轮/ }));

    await waitFor(() => expect(api.submitDropInsightIntervention).toHaveBeenCalledWith(
      "diag-1",
      expect.objectContaining({
        expected_version: 7,
        action: "ADD_CONTEXT",
        message: "补充：磁盘等待也在同一时间升高",
        idempotency_key: expect.any(String),
      }),
    ));
  });

  it("lets the user challenge the current plan from the persistent composer", async () => {
    window.history.replaceState({}, "", "/ai-diagnosis?case=drop_insight_v2%3Adiag-1");
    api.listDropInsightDiagnoses.mockResolvedValue([{ ...diagnosis, diagnosis_version: 7 }]);
    api.getDropInsightDiagnosis.mockResolvedValue({ ...diagnosis, diagnosis_version: 7 });
    api.submitDropInsightIntervention.mockResolvedValue({
      intervention_id: "int-counter",
      diagnosis_id: "diag-1",
      action: "CHALLENGE_HYPOTHESIS",
      message: "先找能够推翻 CPU 热点假设的证据",
      round_index: 2,
      diagnosis_version: 8,
    });
    render(<AIDiagnosis />);

    const input = await screen.findByLabelText("补充诊断上下文");
    fireEvent.click(screen.getByText("寻找反证"));
    fireEvent.change(input, { target: { value: "先找能够推翻 CPU 热点假设的证据" } });
    fireEvent.click(screen.getByRole("button", { name: /基于结论继续一轮/ }));

    await waitFor(() => expect(api.submitDropInsightIntervention).toHaveBeenCalledWith(
      "diag-1",
      expect.objectContaining({
        expected_version: 7,
        action: "CHALLENGE_HYPOTHESIS",
        message: "先找能够推翻 CPU 热点假设的证据",
      }),
    ));
  });

  it("locks real collection, the ordinary planner and interventions for a frozen replay session", async () => {
    const replay = {
      diagnosis_id: "replay-diag-1",
      query: "Python 热点冻结回放",
      status: "PLANNING",
      target: {
        kind: "FROZEN_LATS_SHOWCASE",
        snapshot_id: "snap-1",
        snapshot_digest: "digest-1",
      },
      updated_at: "2026-09-06T08:00:00Z",
    };
    window.history.replaceState({}, "", "/ai-diagnosis?case=drop_insight_v2%3Areplay-diag-1");
    api.listDropInsightDiagnoses.mockResolvedValue([replay]);
    api.getDropInsightDiagnosis.mockResolvedValue(replay);
    api.getDropInsightExplorationTree.mockResolvedValue({
      revision: 0,
      nodes: [],
    });

    render(<AIDiagnosis />);

    expect(await screen.findByText("FULL_LATS · 冻结算法回放")).toBeInTheDocument();
    expect(screen.getByText("FULL_LATS · 冻结回放")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续推进" })).not.toBeInTheDocument();
    const input = screen.getByPlaceholderText("冻结回放不能追加真实采集、普通规划或人工干预");
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "回放只读" })).toBeDisabled();

    fireEvent.click(screen.getByText("探索树"));
    expect(screen.getByText("探索树将在规划后逐节点出现")).toBeInTheDocument();
    expect(api.runDropInsightPlanner).not.toHaveBeenCalled();
    expect(api.advanceDropInsightOrchestrator).not.toHaveBeenCalled();
    expect(api.submitDropInsightIntervention).not.toHaveBeenCalled();
  });

});
