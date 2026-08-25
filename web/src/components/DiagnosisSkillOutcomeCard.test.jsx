import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import DiagnosisSkillOutcomeCard from "./DiagnosisSkillOutcomeCard";

function candidate(overrides = {}) {
  return {
    skill_id: "skill-cpu-1",
    category: "CPU_HOTSPOT",
    version: 1,
    status: "CANDIDATE",
    source_diagnosis_ids: ["diag-verified-1"],
    strategy: {
      probe_order: ["collect_sys_metrics", "start_perf_profile"],
      minimum_evidence: 2,
      confidence_floor: 0.8,
    },
    gate_metrics: { eligible: false, passed: 1, total: 3 },
    ...overrides,
  };
}

describe("DiagnosisSkillOutcomeCard", () => {
  it("shows the Skill produced by a verified diagnosis and exposes its gates", () => {
    const onEvaluate = vi.fn();
    const onOpenPlaza = vi.fn();
    const skill = candidate();

    render(
      <DiagnosisSkillOutcomeCard
        skill={skill}
        onEvaluate={onEvaluate}
        onOpenPlaza={onOpenPlaza}
      />,
    );

    expect(screen.getByText("本次诊断沉淀的 Skill")).toBeInTheDocument();
    expect(screen.getByText("已生成首个候选版本 v1")).toBeInTheDocument();
    expect(screen.getByText("结论已确认")).toBeInTheDocument();
    expect(screen.getByText("Skill 已生成")).toBeInTheDocument();
    expect(screen.getAllByText("1/3 通过")).toHaveLength(2);
    expect(screen.getByText("等待确认")).toBeInTheDocument();
    expect(screen.getByText("采集系统指标 → 采集 CPU 火焰图")).toBeInTheDocument();
    expect(screen.getByText("diag-verified-1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /运行三类门禁评测/ }));
    fireEvent.click(screen.getByRole("button", { name: "打开 Skill 广场" }));
    expect(onEvaluate).toHaveBeenCalledWith(skill);
    expect(onOpenPlaza).toHaveBeenCalledOnce();
  });

  it("makes optimization and passed gates visible", () => {
    render(
      <DiagnosisSkillOutcomeCard
        skill={candidate({
          version: 3,
          status: "ACTIVE",
          parent_skill_id: "skill-cpu-2",
          gate_metrics: { eligible: true, passed: 3, total: 3 },
        })}
      />,
    );

    expect(screen.getByText("已基于上一版优化为 v3")).toBeInTheDocument();
    expect(screen.getByText("Skill 已升级")).toBeInTheDocument();
    expect(screen.getByText("已投入复用")).toBeInTheDocument();
    expect(screen.getByText("正例、反例与环境迁移门禁已通过")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /运行三类门禁评测/ })).not.toBeInTheDocument();
  });
});
