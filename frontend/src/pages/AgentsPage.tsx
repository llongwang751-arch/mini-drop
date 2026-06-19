import { UserFocus } from "@phosphor-icons/react";
import { StatusBadge } from "../components/StatusBadge";
import { formatTime } from "../constants";
import { useDashboardStore } from "../store/dashboardStore";

export function AgentsPage() {
  const agents = useDashboardStore((state) => state.agents);

  return (
    <>
      <div className="heading">
        <div>
          <p className="eyebrow">AGENT FLEET</p>
          <h1>采集节点</h1>
          <span>查看 Agent 心跳、环境与在线状态。</span>
        </div>
      </div>
      <section className="agent-list">
        {agents.map((agent) => (
          <article key={agent.id}>
            <UserFocus size={22} weight="duotone" />
            <div>
              <b>{agent.id}</b>
              <span>{agent.hostname}</span>
            </div>
            <code>{agent.version}</code>
            <span>最后心跳 {formatTime(agent.last_seen)}</span>
            <StatusBadge value={agent.online ? "DONE" : "FAILED"} />
          </article>
        ))}
      </section>
    </>
  );
}
