import { Pulse, X } from "@phosphor-icons/react";
import { StatusBadge } from "../../components/StatusBadge";
import { collectorText, formatPreciseTime, reasonText, statusText } from "../../constants";
import { useDashboardStore } from "../../store/dashboardStore";
import type { Task } from "../../types";
import { EmptyState } from "../../components/EmptyState";
import { FlameChart } from "./FlameChart";

export function TaskDetail({ task }: { task: Task | null }) {
  const clearSelectedTask = useDashboardStore((state) => state.clearSelectedTask);
  const stopContinuous = useDashboardStore((state) => state.stopContinuous);
  if (!task) return null;

  const result = task.result;
  const degraded = Boolean(result?.collector_meta.degraded);
  return (
    <section className="panel detail">
      <div className="panel-head">
        <div>
          <p className="eyebrow">PROFILE DETAIL</p>
          <h2>
            任务 <code>{task.id}</code>
          </h2>
        </div>
        <div className="detail-actions">
          {task.continuous && (
            <button className="secondary" type="button" onClick={() => void stopContinuous(task.id)}>
              停止持续采样
            </button>
          )}
          <button className="icon-btn" onClick={clearSelectedTask} aria-label="关闭详情">
            <X size={18} />
          </button>
        </div>
      </div>
      <div className="detail-meta">
        <span>{collectorText[task.collector]}</span>
        <span>PID {task.pid}</span>
        {result && <span>{result.collector_meta.backend}</span>}
        {result && <span className={degraded ? "meta-pill warn" : "meta-pill ok"}>{degraded ? "降级采集" : "真实采集"}</span>}
        <StatusBadge value={task.status} />
      </div>
      {result && (
        <div className="collector-meta">
          <b>采集状态：{degraded ? "降级采集" : "真实采集"}</b>
          <span>backend：{result.collector_meta.backend}</span>
          {degraded ? (
            <span>原因：{result.collector_meta.reason || "采集器返回降级标记，详情见 stderr。"}</span>
          ) : (
            <span>说明：采集器未标记 degraded，可作为录屏中的真实采集证据。</span>
          )}
        </div>
      )}
      {result ? (
        <div className="analysis-grid">
          <article className="analysis">
            <h3>{task.collector === "pyspy" ? "Python 语言调用栈" : "采样火焰图"}</h3>
            {task.collector === "pyspy" ? (
              <div className="language-stack">
                {result.top.map((frame, index) => (
                  <div key={frame.name}>
                    <i>{String(index + 1).padStart(2, "0")}</i>
                    <span>{frame.name}</span>
                    <b>{frame.samples}</b>
                  </div>
                ))}
              </div>
            ) : (
              <FlameChart tree={result.flamegraph} />
            )}
            {!!result.histogram?.length && (
              <>
                <h3>内核写入分布</h3>
                <div className="histogram">
                  {result.histogram.map((bucket) => (
                    <div key={bucket.bucket}>
                      <span>{bucket.bucket}</span>
                      <b style={{ width: `${Math.min(100, 12 + bucket.count / 8)}%` }}>{bucket.count}</b>
                    </div>
                  ))}
                </div>
              </>
            )}
          </article>
          <aside className="analysis">
            <h3>热点函数 TopN</h3>
            {result.top.map((frame) => (
              <div className="top-row" key={frame.name}>
                <span>{frame.name}</span>
                <b>{frame.samples}</b>
              </div>
            ))}
            <h3>可验证归因</h3>
            {result.attribution.map((item, index) => (
              <div className="attribution" key={`${item.rule}-${index}`}>
                <b>{item.conclusion}</b>
                <code>证据 {JSON.stringify(item.evidence)}</code>
                <code>规则 {item.rule}</code>
              </div>
            ))}
          </aside>
        </div>
      ) : (
        <EmptyState icon={Pulse} title="正在等待分析结果" description="Agent 完成采集后，这里会显示可视化结果。" />
      )}
      <div className="history">
        <h3>状态历史</h3>
        {(task.transitions ?? []).map((transition) => (
          <div key={transition.id}>
            <b>
              {transition.from_status ? statusText[transition.from_status] : "新建"} → {statusText[transition.to_status]}
            </b>
            <span>
              {reasonText[transition.reason] ?? transition.reason} · {formatPreciseTime(transition.created_at)}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
