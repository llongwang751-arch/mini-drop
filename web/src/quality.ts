/** 质量记录只表示各套件自己的分母；不同实验的计数不相加为一个“总准确率”。 */
export type QualityStatus = "passed" | "failed" | "incomplete";
export type QualityRun = {
  schema_version: 1;
  id: string;
  title: string;
  category: string;
  suite: string;
  status: QualityStatus;
  started_at: string;
  finished_at: string;
  environment: string;
  source_commit: string | null;
  source_sha256: string;
  provenance: "actual_execution" | "imported_fixture";
  fixture_scope: string;
  counts: { passed: number; failed: number; skipped: number; total: number };
  metrics: { id: string; label: string; value: number | null; unit: string }[];
  checks: {
    id: string;
    label: string;
    status: "passed" | "failed" | "skipped";
    detail: string;
  }[];
  limits: string[];
};
export type QualityRecord = { run: QualityRun; record_sha256: string };
export const qualityLabels: Record<string, string> = {
  business: "业务接口",
  agent: "Agent 评测",
  security: "安全边界",
  protocol: "协议与状态",
  system: "整链与恢复",
  performance: "性能实验",
  release: "发布安全",
  client: "客户端",
};
export const qualityStatus = {
  passed: "通过",
  failed: "失败",
  incomplete: "未完成",
  skipped: "跳过",
};
export const environmentLabels: Record<string, string> = {
  windows_local: "Windows 本机",
  linux_isolated: "Linux 隔离环境",
  github_ci: "GitHub CI",
  production_bounded: "线上受控实验",
};
export function metricText(value: number | null, unit: string): string {
  if (value === null) return "未验收";
  if (!Number.isFinite(value)) return "无效数值";
  if (unit === "ratio") return `${(value * 100).toFixed(1)}%`;
  if (unit === "percent") return `${value.toFixed(1)}%`;
  if (unit === "multiple") return `${value.toFixed(3)}×`;
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value)}${({ ms: " ms", s: " s", bytes: " B", tokens: " tokens" } as Record<string, string>)[unit] ?? ""}`;
}
export function filterQuality(
  records: QualityRecord[],
  category: string,
  suite: string,
  status: string,
) {
  return records.filter(
    ({ run }) =>
      (!category || run.category === category) &&
      (!suite || run.suite === suite) &&
      (!status || run.status === status),
  );
}
