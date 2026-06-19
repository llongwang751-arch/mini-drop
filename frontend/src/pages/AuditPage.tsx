import { Database } from "@phosphor-icons/react";
import { formatTime, reasonText } from "../constants";
import { useDashboardStore } from "../store/dashboardStore";

export function AuditPage() {
  const audit = useDashboardStore((state) => state.audit);

  return (
    <>
      <div className="heading">
        <div>
          <p className="eyebrow">CONNECTIVITY HISTORY</p>
          <h1>审计日志</h1>
          <span>记录 Agent 上线、离线与恢复事件。</span>
        </div>
      </div>
      <section className="panel audit">
        {audit.map((event) => (
          <article key={event.id}>
            <Database size={18} />
            <div>
              <b>
                {event.kind} · {event.subject}
              </b>
              <span>{reasonText[event.message] ?? event.message}</span>
            </div>
            <time>{formatTime(event.created_at)}</time>
          </article>
        ))}
      </section>
    </>
  );
}
