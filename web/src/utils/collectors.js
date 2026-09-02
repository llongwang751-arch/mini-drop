import { TASK_KINDS } from "../generated/taskKinds";

export const COLLECTOR_META = Object.fromEntries(TASK_KINDS.map((item) => [item.name, {
  label: item.display_name,
  resultLabel: item.result_label,
  description: item.description,
  color: item.color,
  defaultDuration: item.default_duration_seconds,
  maxDuration: item.max_duration_seconds,
  defaultSampleRate: item.default_sample_rate,
  maxSampleRate: item.max_sample_rate,
  flamegraph: item.flamegraph,
  analysisPipeline: item.analysis_pipeline,
  requiresCapabilities: item.requires_capabilities,
}]));

export const COLLECTOR_OPTIONS = Object.entries(COLLECTOR_META).map(([value, meta]) => ({
  value,
  label: `${meta.label} · ${meta.resultLabel}`,
}));

export function collectorMeta(collectorType) {
  return COLLECTOR_META[collectorType] || {
    label: collectorType || "未知采集器",
    resultLabel: "任务产物",
    description: "任务完成后可在结果页查看采集产物。",
    color: "default",
    defaultDuration: 15,
    maxDuration: 300,
    defaultSampleRate: 99,
    maxSampleRate: 999,
    flamegraph: false,
  };
}
