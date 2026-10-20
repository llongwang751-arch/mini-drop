import type { AnalysisResult, FlameNode, Task } from "./types";

export type ComparisonMode = "self" | "total";
type Weights = { self: number; total: number };
export interface ProfileWeights {
  functions: Map<string, Weights>;
  weight: number;
}
export interface ProfileComparison {
  baseline: ProfileWeights;
  candidate: ProfileWeights;
  unit: string;
}
export interface ComparisonRow {
  name: string;
  baseline: number;
  candidate: number;
  baselinePercent: number;
  candidatePercent: number;
  deltaPercentagePoints: number;
}

export function comparisonEligibility(tasks: Task[]): string {
  if (tasks.length !== 2) return "请选择恰好两个任务";
  if (tasks[0].id === tasks[1].id) return "请选择两个不同的任务";
  if (
    tasks.some(
      (task) =>
        task.analysis_status !== "SUCCESS" ||
        task.collection_status !== "COLLECTED",
    )
  )
    return "两个任务都需要完成分析";
  if (tasks[0].kind !== tasks[1].kind)
    return "请选择相同类型、相同权重口径的任务";
  if (taskMetric(tasks[0]) !== taskMetric(tasks[1]))
    return "两次任务的采样指标口径不同，请重新选择";
  return "";
}

function metricType(result: AnalysisResult): string {
  const summary = result.summary as AnalysisResult["summary"] & {
    profile_type?: string;
  };
  return (
    summary.profile_type || String(result.sections?.heap?.profile_type || "")
  );
}
function options(task: Task): Record<string, unknown> {
  const value = task.params.options;
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function taskMetric(task: Task): string {
  const value = options(task);
  if (task.kind === "PERF_CPU") return String(value.event || "cpu-clock");
  if (value.sample_type) return String(value.sample_type);
  if (["IO_TRACE", "MEMORY_TRACE", "SCRIPT"].includes(task.kind))
    return JSON.stringify([value.category || "", value.template_id || ""]);
  return "";
}

function profileWeights(result: AnalysisResult): ProfileWeights {
  const root = result.flamegraph;
  if (
    !root ||
    !Number.isSafeInteger(root.value) ||
    root.value <= 0 ||
    root.value !== result.total_samples
  )
    throw new Error("分析结果缺少可比较的非零调用树权重");
  const functions = new Map<string, Weights>();
  const seen = new Set<FlameNode>(),
    path = new Set<string>();
  const pending: {
    node: FlameNode;
    depth: number;
    exit?: boolean;
    first?: boolean;
  }[] = [{ node: root, depth: 0 }];
  while (pending.length) {
    const { node, depth, exit, first } = pending.pop()!;
    if (exit) {
      if (first) path.delete(node.name);
      continue;
    }
    if (seen.has(node) || seen.size >= 100000 || depth > 128)
      throw new Error("调用树包含循环或超过节点/深度限制");
    seen.add(node);
    if (
      typeof node.name !== "string" ||
      !Number.isSafeInteger(node.value) ||
      node.value < 0 ||
      !Number.isSafeInteger(node.self) ||
      node.self < 0
    )
      throw new Error("调用树权重格式无效");
    const children = node.children || [];
    if (
      !Array.isArray(children) ||
      node.self +
        children.reduce((sum, child) => sum + (child?.value || 0), 0) !==
        node.value
    )
      throw new Error("调用树总计与自身权重不一致");
    const firstOccurrence = depth > 0 && !path.has(node.name);
    if (depth > 0) {
      const item = functions.get(node.name) || { self: 0, total: 0 };
      item.self += node.self;
      if (firstOccurrence) item.total += node.value;
      functions.set(node.name, item);
    }
    if (depth > 0) path.add(node.name);
    pending.push({ node, depth, exit: true, first: firstOccurrence });
    for (let index = children.length - 1; index >= 0; index--)
      pending.push({ node: children[index], depth: depth + 1 });
  }
  return { functions, weight: root.value };
}

export function compareProfiles(
  baselineTask: Task,
  baselineResult: AnalysisResult,
  candidateTask: Task,
  candidateResult: AnalysisResult,
): ProfileComparison {
  const reason = comparisonEligibility([baselineTask, candidateTask]);
  if (reason) throw new Error(reason);
  if (
    baselineTask.kind !== baselineResult.kind ||
    candidateTask.kind !== candidateResult.kind
  )
    throw new Error("任务与结果类型不一致");
  if (
    !baselineResult.summary.unit ||
    baselineResult.summary.unit !== candidateResult.summary.unit
  )
    throw new Error("两份结果的权重单位不同，无法直接比较");
  if (
    metricType(baselineResult) !== metricType(candidateResult) ||
    taskMetric(baselineTask) !== taskMetric(candidateTask)
  )
    throw new Error("两份结果的采样或内存指标口径不同，无法直接比较");
  return {
    baseline: profileWeights(baselineResult),
    candidate: profileWeights(candidateResult),
    unit: baselineResult.summary.unit,
  };
}

export function comparisonRows(
  profile: ProfileComparison,
  mode: ComparisonMode,
): ComparisonRow[] {
  const names = new Set([
    ...profile.baseline.functions.keys(),
    ...profile.candidate.functions.keys(),
  ]);
  return Array.from(names, (name) => {
    const baseline = profile.baseline.functions.get(name)?.[mode] || 0;
    const candidate = profile.candidate.functions.get(name)?.[mode] || 0;
    const baselinePercent = (baseline / profile.baseline.weight) * 100;
    const candidatePercent = (candidate / profile.candidate.weight) * 100;
    return {
      name,
      baseline,
      candidate,
      baselinePercent,
      candidatePercent,
      deltaPercentagePoints: candidatePercent - baselinePercent,
    };
  })
    .filter((row) => row.baseline > 0 || row.candidate > 0)
    .sort(
      (a, b) =>
        Math.abs(b.deltaPercentagePoints) - Math.abs(a.deltaPercentagePoints) ||
        b.candidate - a.candidate ||
        a.name.localeCompare(b.name),
    );
}
