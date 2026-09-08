import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SkillABPanel, { buildLiveComparison } from "./SkillABPanel";

vi.mock("../api/client", () => ({
  createDropInsightDiagnosis: vi.fn(),
  getDropInsightDiagnosis: vi.fn(),
  getDropInsightExplorationTree: vi.fn(),
  listDiagnosticSkillActivations: vi.fn(),
  listDropInsightEvidence: vi.fn(),
  listDropInsightReports: vi.fn(),
  listDropInsightToolCalls: vi.fn(),
  runDropInsightPlanner: vi.fn(),
  listDiagnosticExperiments: vi.fn(),
  getDiagnosticExperiment: vi.fn(),
  createDiagnosticExperiment: vi.fn(),
  assignDiagnosticExperiment: vi.fn(),
  recordDiagnosticExperimentOutcome: vi.fn(),
  evaluateDiagnosticExperiment: vi.fn(),
  approveDiagnosticExperiment: vi.fn(),
}));

import * as api from "../api/client";

const BENCHMARK_FIXTURE = {
  root_cause_evaluation_540: {
    skill_enabled: { root_cause_top1_accuracy: 0.703704 },
    no_skill: { root_cause_top1_accuracy: 0.427778 },
  },
  paired_skill_ab_500: {
    skill_enabled: { root_cause_top1_accuracy: 0.68, decisive_collector_within_two_rate: 0.68 },
    no_skill: { root_cause_top1_accuracy: 0.412, decisive_collector_within_two_rate: 0.412 },
    delta_percentage_points: 26.8,
    improved_cases: 136,
    regressed_cases: 2,
    unchanged_cases: 362,
    decisive_route_improved_cases: 187,
  },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SkillABPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => BENCHMARK_FIXTURE,
    }));
    api.createDropInsightDiagnosis.mockImplementation(async (payload) => ({
      diagnosis_id: payload.skill_policy === "AUTO" ? "diag-auto" : "diag-disabled",
    }));
    api.listDiagnosticExperiments.mockResolvedValue([]);
    api.runDropInsightPlanner.mockResolvedValue({});
    api.getDropInsightDiagnosis.mockImplementation(async (id) => ({
      diagnosis_id: id,
      status: "COMPLETED",
      created_at: "2026-09-05T10:00:00Z",
      updated_at: "2026-09-05T10:01:10Z",
      target: { binding_id: "binding-1", pid: 123, service: "demo", environment: "demo" },
      time_range: { start: "2026-09-05T09:59:00Z", end: "2026-09-05T10:00:00Z" },
    }));
    api.listDiagnosticSkillActivations.mockImplementation(async (id) => id === "diag-auto"
      ? [{ activation_id: "a-1", skill_id: "cpu-skill", match_score: 0.91 }]
      : []);
    api.listDropInsightToolCalls.mockResolvedValue([{ tool_call_id: "t-1", tool_name: "start_perf_profile" }]);
    api.listDropInsightEvidence.mockResolvedValue([
      { evidence_id: "e-support", decision: "ACCEPT_SUPPORT", created_at: "2026-09-05T10:00:02Z" },
      { evidence_id: "e-counter", classification: { decision: "ACCEPT_COUNTER" }, created_at: "2026-09-05T10:00:03Z" },
      { evidence_id: "e-neutral", gate_decision: "ACCEPT_NEUTRAL", created_at: "2026-09-05T10:00:04Z" },
      { evidence_id: "e-limited", classification: "ACCEPT_LIMITED", created_at: "2026-09-05T10:00:05Z" },
      { evidence_id: "e-rejected", decision: "REJECT_LOW_QUALITY", created_at: "2026-09-05T10:00:06Z" },
    ]);
    api.listDropInsightReports.mockResolvedValue([{ confidence: 0.8, verification: { status: "VERIFIED" } }]);
    api.getDropInsightExplorationTree.mockResolvedValue({ stats: { rounds: 1 } });
  });

  it("creates real AUTO and DISABLED diagnoses and reports only observed metrics", async () => {
    const onCasesChanged = vi.fn();
    render(<SkillABPanel
      onCasesChanged={onCasesChanged}
      seedRequest={{
        query: "诊断 CPU 热点",
        budget: { min_diagnosis_rounds: 3, max_diagnosis_rounds: 4 },
      }}
    />);
    fireEvent.change(screen.getByLabelText("Skill A/B 诊断问题"), { target: { value: "诊断 CPU 热点" } });
    fireEvent.click(screen.getByRole("button", { name: /启动真实对比/ }));

    await waitFor(() => expect(api.createDropInsightDiagnosis).toHaveBeenCalledTimes(2));
    expect(api.createDropInsightDiagnosis).toHaveBeenCalledWith(expect.objectContaining({ skill_policy: "AUTO" }));
    expect(api.createDropInsightDiagnosis).toHaveBeenCalledWith(expect.objectContaining({ skill_policy: "DISABLED" }));
    expect(api.createDropInsightDiagnosis).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        skill_policy: "AUTO",
        budget: { min_diagnosis_rounds: 3, max_diagnosis_rounds: 4 },
      }),
    );
    expect(api.createDropInsightDiagnosis).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        skill_policy: "DISABLED",
        budget: { min_diagnosis_rounds: 3, max_diagnosis_rounds: 4 },
      }),
    );
    expect(await screen.findByText("两组已确认严格同一诊断范围")).toBeInTheDocument();
    expect(screen.getByText("cpu-skill")).toBeInTheDocument();
    expect(screen.getByText("策略已关闭")).toBeInTheDocument();
    expect(screen.getByText("自动复用 Skill")).toBeInTheDocument();
    expect(screen.getByText("不使用 Skill")).toBeInTheDocument();
    const autoArm = document.querySelector(".skill-ab-arm.is-auto");
    expect(within(autoArm).getByLabelText("AUTO 组真实诊断指标")).toHaveTextContent("1可支撑根因4门禁接受");
    expect(within(autoArm).getByLabelText("AUTO 组门禁接受分类")).toHaveTextContent("支持 1反证 1中性 1受限 1");
    expect(within(autoArm).getByText("第 1 轮")).toBeInTheDocument();
    expect(within(autoArm).getByText("首条支持证据")).toBeInTheDocument();
    expect(within(autoArm).getByText("总探索耗时")).toBeInTheDocument();
    expect(within(autoArm).getByText("70 秒")).toBeInTheDocument();
    expect(screen.getByText("500 组根因 Top-1")).toBeInTheDocument();
    expect(screen.getAllByText("68.0%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("+26.8 个百分点")).toBeInTheDocument();
    expect(onCasesChanged).toHaveBeenCalled();
  });

  it("quantifies when Skill reaches the decisive collector earlier", () => {
    const comparison = buildLiveComparison({
      auto: {
        detail: { created_at: "2026-09-05T10:00:00Z" },
        activations: [{ selected_tool: "collect_go_profile" }],
        tools: [{ tool_name: "collect_go_profile" }],
        evidence: [],
        reports: [],
      },
      disabled: {
        detail: { created_at: "2026-09-05T10:00:00Z" },
        activations: [],
        tools: [{ tool_name: "collect_sys_metrics" }, { tool_name: "collect_go_profile" }],
        evidence: [],
        reports: [],
      },
    });

    expect(comparison.routeTone).toBe("positive");
    expect(comparison.routeTitle).toBe("关键采集器提前 1 轮");
    expect(comparison.autoPosition).toBe(1);
    expect(comparison.disabledPosition).toBe(2);
    expect(comparison.rootTitle).toBe("当前不能判断哪组根因更准");
  });

  it("keeps an earlier supported hypothesis visible when a later branch is insufficient", async () => {
    api.listDropInsightReports.mockResolvedValue([
      {
        confidence: 0.75,
        verification: { status: "PARTIAL_WITHOUT_COUNTER" },
        evidence_refs: ["support-1"],
        created_at: "2026-09-05T10:00:20Z",
      },
      {
        confidence: 0,
        verification: { status: "INSUFFICIENT_EVIDENCE" },
        evidence_refs: [],
        created_at: "2026-09-05T10:01:00Z",
      },
    ]);
    render(<SkillABPanel />);
    fireEvent.change(screen.getByLabelText("Skill A/B 诊断问题"), { target: { value: "诊断 CPU 热点" } });
    fireEvent.click(screen.getByRole("button", { name: /启动真实对比/ }));

    await screen.findByText("Skill 启用组");
    const autoArm = document.querySelector(".skill-ab-arm.is-auto");
    await waitFor(() => expect(within(autoArm).getByText("75% · 阶段性结论")).toBeInTheDocument());
    expect(within(autoArm).queryByText("0% · 证据不足")).not.toBeInTheDocument();
  });

  it("restores a real A/B pair after leaving and returning to the validation center", async () => {
    const first = render(<SkillABPanel />);
    fireEvent.change(screen.getByLabelText("Skill A/B 诊断问题"), { target: { value: "诊断 Java GC 抖动" } });
    fireEvent.click(screen.getByRole("button", { name: /启动真实对比/ }));
    await waitFor(() => expect(api.createDropInsightDiagnosis).toHaveBeenCalledTimes(2));
    await screen.findByText("本浏览器的 A/B 对比历史");

    const saved = JSON.parse(window.localStorage.getItem("mini-drop:skill-ab-history:v1"));
    expect(saved[0]).toEqual({
      autoId: "diag-auto",
      disabledId: "diag-disabled",
    });
    expect(Object.keys(saved[0])).toEqual(["autoId", "disabledId"]);

    first.unmount();
    vi.clearAllMocks();
    api.getDropInsightDiagnosis.mockImplementation(async (id) => ({
      diagnosis_id: id,
      query: "诊断 Java GC 抖动",
      status: "COMPLETED",
    }));
    api.listDiagnosticSkillActivations.mockResolvedValue([]);
    api.listDropInsightToolCalls.mockResolvedValue([]);
    api.listDropInsightEvidence.mockResolvedValue([]);
    api.listDropInsightReports.mockResolvedValue([]);
    api.getDropInsightExplorationTree.mockResolvedValue({ stats: { rounds: 2 } });

    render(<SkillABPanel />);
    expect(screen.getByText(/启用 diag-auto \/ 关闭 diag-disabled/)).toBeInTheDocument();
    await waitFor(() => expect(api.getDropInsightDiagnosis).toHaveBeenCalledWith("diag-auto"));
    expect(api.getDropInsightDiagnosis).toHaveBeenCalledWith("diag-disabled");
    await waitFor(() => expect(screen.getByLabelText("Skill A/B 诊断问题")).toHaveValue("诊断 Java GC 抖动"));
  });
});
