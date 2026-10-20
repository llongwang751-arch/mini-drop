import { number } from "../api";
import { parseFlameThreads } from "./flame-analysis";
import { ResultTable } from "./AnalysisSections";

export default function PerfEvidence({
  section,
}: {
  section?: Record<string, unknown>;
}) {
  if (!section) return null;
  const threads = parseFlameThreads(section);
  const range =
    section.time_range_ns && typeof section.time_range_ns === "object"
      ? (section.time_range_ns as Record<string, unknown>)
      : {};
  const measured = (value: unknown) =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0
      ? number(value)
      : "未知";
  const timestamp = (value: unknown) =>
    typeof value === "string" && /^\d{1,24}$/.test(value) ? value : "未知";
  return (
    <details className="perf-evidence">
      <summary>采样证据 · 线程、时钟与丢样</summary>
      <dl className="perf-evidence-metrics">
        <div>
          <dt>独立线程记录</dt>
          <dd>{threads.length ? number(threads.length) : "未知"}</dd>
        </div>
        <div>
          <dt>已证实丢样</dt>
          <dd>{measured(section.lost_samples)}</dd>
        </div>
        <div>
          <dt>脚本报告丢样</dt>
          <dd>{measured(section.reported_lost_samples)}</dd>
        </div>
        <div>
          <dt>丢样记录数</dt>
          <dd>{measured(section.lost_records)}</dd>
        </div>
        <div>
          <dt>采样起点 · ns</dt>
          <dd>{timestamp(range.start)}</dd>
        </div>
        <div>
          <dt>采样终点 · ns</dt>
          <dd>{timestamp(range.end)}</dd>
        </div>
        <div>
          <dt>观测跨度 · ns</dt>
          <dd>{timestamp(range.duration)}</dd>
        </div>
      </dl>
      <p className="flame-view-note">
        时钟值保留原始纳秒精度，观测跨度来自首尾样本。缺少完整丢样证据时显示未知；脚本报告值不等于已证明没有其他丢样。
      </p>
      <ResultTable
        title="原始线程记录"
        data={threads.map((thread) => ({ ...thread }))}
        columns={[
          ["pid", "进程 PID"],
          ["tid", "线程 TID"],
          ["comm", "进程名称"],
          ["sample_count", "样本数"],
        ]}
      />
      {section.filters && typeof section.filters === "object" ? (
        <details className="perf-source-filters">
          <summary>采集过滤条件</summary>
          <pre tabIndex={0} role="region" aria-label="采集过滤条件内容">
            {JSON.stringify(section.filters, null, 2)}
          </pre>
        </details>
      ) : null}
    </details>
  );
}
