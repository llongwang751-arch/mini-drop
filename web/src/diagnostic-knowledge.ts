/**
 * 诊断知识的公开数据形状与固定标签。历史案例只提供先验，不能充当本轮工具证据。
 * 案例只保存服务端允许的纠正类别和受控来源，不接收可被下一轮模型当作指令的自由文本。
 */
export type KnowledgeMode = "off" | "skills" | "experience";
export const knowledgeModes: { value: KnowledgeMode; label: string }[] = [
  { value: "off", label: "不使用知识" },
  { value: "skills", label: "仅使用 Skill" },
  { value: "experience", label: "Skill 与历史经验" },
];

export interface DiagnosticSkill {
  id: string;
  version: string;
  title: string;
  task_kinds: string[];
  required_tools: string[];
  steps: string[];
  counter_evidence: string[];
  stop_conditions: string[];
  verification: string[];
  match_reasons?: string[];
}
export type CorrectionCode =
  | "insufficient_evidence"
  | "wrong_hotspot"
  | "causality_not_established"
  | "incomparable_measurements"
  | "regression_detected";
export const correctionOptions: { value: CorrectionCode; label: string }[] = [
  { value: "insufficient_evidence", label: "证据不足" },
  { value: "wrong_hotspot", label: "热点解释需纠正" },
  { value: "causality_not_established", label: "因果结论未经验证" },
  { value: "incomparable_measurements", label: "采样条件不可比" },
  { value: "regression_detected", label: "回归需复核" },
];
export type CaseOutcome = "reviewed_correction" | "verified_repair";
export interface DiagnosticCase {
  id: string;
  task_id: string;
  diagnosis_id: string;
  attempt_id: string;
  analysis_job_id: string;
  analysis_run: number;
  code_repair_id?: string;
  task_kind: string;
  project_id?: string;
  source_revision?: string;
  outcome: CaseOutcome;
  correction_code?: CorrectionCode;
  status: string;
  created_at: string;
  expires_at: string;
  revoked_at?: string;
}
export interface DiagnosticCasePrior {
  id: string;
  source_diagnosis_id: string;
  outcome: CaseOutcome;
  assurance: string;
  conclusion: string;
  skill_refs: { id: string; version: string }[];
  match_reasons: string[];
  prior_only: true;
}
export interface DiagnosisKnowledge {
  skills: DiagnosticSkill[];
  cases: DiagnosticCasePrior[];
  prior_only: true;
  history_enabled: boolean;
}
export interface DiagnosticCaseInput {
  outcome: CaseOutcome;
  human_confirmed: boolean;
  correction_code?: CorrectionCode;
  code_repair_id?: string;
}
export function correctionLabel(code?: string) {
  return (
    correctionOptions.find((option) => option.value === code)?.label ||
    code ||
    "—"
  );
}
export function caseOutcomeLabel(outcome: CaseOutcome) {
  return outcome === "verified_repair" ? "受控修复通过" : "人工复核纠正";
}
const reasonLabels: Record<string, string> = {
  quality_review_required: "需要核查采样质量",
  no_weight: "没有正采样权重",
  low_samples: "观测样本较少",
  short_window: "采集窗口较短",
  imported: "来自离线导入",
  counters_only: "仅有计数，没有函数调用栈",
  unknown_unit: "计量单位未知",
  cpu_kind_with_hotspots: "CPU 类采集中存在热点",
  memory_collector: "属于内存采集类型",
  allocation: "存在分配符号线索",
  garbage_collection: "存在垃圾回收符号线索",
  io_collector: "属于 I/O 采集类型",
  lock_or_wait: "存在锁或等待符号线索",
  io_or_network: "存在 I/O 或网络符号线索",
  hotspot_fingerprint_overlap: "脱敏热点指纹重合",
  symbol_hint_overlap: "符号线索类别重合",
  same_kind_agent_project_version: "采集类型、节点与登记项目版本一致",
  same_quality_conditions: "采样质量条件一致",
};
export function knowledgeReasonLabel(reason: string) {
  return reasonLabels[reason] || reason;
}
export function caseAssuranceLabel(assurance: string) {
  return (
    (
      {
        human_reviewed: "人工复核",
        controlled_benchmark_verified: "隔离基准已验证",
      } as Record<string, string>
    )[assurance] || "历史复核记录"
  );
}

/** 构造闭合字段的请求；浏览器确认仅表示人工审阅，真实修复资格仍由服务端核验。 */
export function caseInput(
  outcome: CaseOutcome,
  confirmed: boolean,
  correction: string,
  repairID?: string,
): DiagnosticCaseInput {
  if (!confirmed) throw new Error("请先确认已复核当前记录");
  if (outcome === "verified_repair") {
    if (!repairID) throw new Error("缺少关联的受控修复记录");
    return { outcome, human_confirmed: true, code_repair_id: repairID };
  }
  const option = correctionOptions.find((item) => item.value === correction);
  if (!option) throw new Error("请选择本次复核的纠正类别");
  return { outcome, human_confirmed: true, correction_code: option.value };
}
