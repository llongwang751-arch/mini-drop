import AgentEvidence from "../components/AgentEvidence";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpRight,
  Broadcast,
  Cpu,
  HardDrives,
  Memory,
  ArrowClockwise,
  TerminalWindow,
} from "@phosphor-icons/react";
import { api, bytes, dateTime } from "../api";
import { pollingInterval } from "../query-policy";
import type { Agent, List } from "../types";
import {
  PageHeading,
  Badge,
  ErrorPanel,
  Skeleton,
  SearchField,
  Empty,
} from "../components/ui";
export default function Agents() {
  const [search, setSearch] = useState("");
  const q = useQuery({
    queryKey: ["agents", search],
    queryFn: ({ signal }) =>
      api<List<Agent>>(
        `/agents?search=${encodeURIComponent(search)}&page_size=100`,
        { signal },
      ),
    refetchInterval: (query) => pollingInterval(query, 10000),
  });
  return (
    <>
      <PageHeading
        eyebrow="INFRASTRUCTURE / AGENTS"
        title="连接每一个现场"
        description="实时掌握节点状态与采集能力，让诊断准确抵达。"
        action={
          <Link to="/guide" className="button secondary">
            <TerminalWindow size={17} />
            接入指南
          </Link>
        }
      />
      <div className="agents-summary">
        <span>
          <i className="status-dot green" />
          {q.data?.items.filter((a) => a.status === "online").length || 0} 在线
        </span>
        <span>{q.data?.total || 0} 个节点已接入</span>
        <span>实时心跳更新</span>
      </div>
      <div className="list-toolbar">
        <SearchField
          value={search}
          onChange={setSearch}
          placeholder="搜索主机名称、IP 或标签"
        />
        <button className="button ghost" onClick={() => q.refetch()}>
          <ArrowClockwise size={16} />
          刷新
        </button>
      </div>
      {q.error ? (
        <ErrorPanel error={q.error} />
      ) : q.isLoading ? (
        <Skeleton rows={6} />
      ) : !q.data?.items.length ? (
        <div className="panel">
          <Empty
            title="等待第一个节点接入"
            description="Agent 主动连接控制面，无需向目标节点开放入站端口。"
            icon={<Broadcast size={28} />}
            action={
              <Link className="button secondary" to="/guide">
                查看接入指南
                <ArrowUpRight size={16} />
              </Link>
            }
          />
        </div>
      ) : (
        <div className="agent-grid">
          {q.data.items.map((a) => (
            <Link to={`/agents/${a.id}`} className="agent-card" key={a.id}>
              <div className="agent-card-top">
                <span className="agent-card-icon">
                  <HardDrives size={24} />
                </span>
                <Badge status={a.status} />
              </div>
              <h2>{a.hostname}</h2>
              <span className="mono dim">{a.ip || a.id}</span>
              <div className="agent-labels">
                {Object.entries(a.labels || {})
                  .filter(([, v]) =>
                    ["string", "number", "boolean"].includes(typeof v),
                  )
                  .map(([k, v]) => (
                    <span key={k}>
                      {k}: {String(v)}
                    </span>
                  ))}
              </div>
              <div className="agent-resources">
                <ResourceMeter name="CPU" value={a.cpu_percent} />
                <ResourceMeter name="内存" value={a.memory_percent} />
              </div>
              <div className="agent-card-footer">
                <span className="mono">v{a.version}</span>
                <span>
                  {a.capabilities.length} 项采集能力
                  <ArrowUpRight size={15} />
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
function ResourceMeter({ name, value }: { name: string; value: number }) {
  return (
    <div className="resource-meter">
      <div>
        <span>{name}</span>
        <strong className="mono">
          {value.toFixed(1)}
          <small>%</small>
        </strong>
      </div>
      <div className="resource-track">
        <span style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
      </div>
    </div>
  );
}
export function AgentDetail() {
  const { id } = useParams();
  const q = useQuery({
    queryKey: ["agent", id],
    queryFn: ({ signal }) => api<Agent>(`/agents/${id}`, { signal }),
    refetchInterval: (query) => pollingInterval(query, 5000),
  });
  const a = q.data;
  if (q.error) return <ErrorPanel error={q.error} />;
  if (!a) return <Skeleton rows={7} />;
  return (
    <>
      <Link className="back-link" to="/agents">
        <ArrowLeft size={15} />
        返回接入节点
      </Link>
      <PageHeading
        eyebrow={`AGENT / ${a.id}`}
        title={a.hostname}
        description={`${a.platform} · ${a.ip}`}
        action={<Badge status={a.status} />}
      />
      <div className="agent-detail-grid">
        <div className="panel form-section">
          <div className="section-heading">
            <div>
              <h2>实时资源</h2>
              <span className="section-note">
                最后心跳 {dateTime(a.last_seen_at)}
              </span>
            </div>
          </div>
          <div className="large-meters">
            <ResourceMeter name="CPU 使用率" value={a.cpu_percent} />
            <ResourceMeter name="内存使用率" value={a.memory_percent} />
          </div>
          <div className="node-facts">
            <div>
              <Memory size={18} />
              <span>物理内存</span>
              <strong className="mono">
                {a.resources.memory_total_bytes
                  ? bytes(a.resources.memory_total_bytes)
                  : "—"}
              </strong>
            </div>
            <div>
              <Cpu size={18} />
              <span>系统负载</span>
              <strong className="mono">{a.load_average.toFixed(2)}</strong>
            </div>
          </div>
        </div>
      </div>
      <AgentEvidence agent={a} />
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>可诊断进程</h2>
            <span className="section-note">受控目标范围</span>
          </div>
        </div>
        {a.resources.processes?.length ? (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>进程名称</th>
                  <th>PID</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {a.resources.processes.map((p) => (
                  <tr key={p.pid}>
                    <td>
                      <span className="process-name">
                        <TerminalWindow size={18} />
                        {p.name}
                      </span>
                    </td>
                    <td className="mono">{p.pid}</td>
                    <td>
                      <Link className="text-link" to="/tasks/new">
                        创建诊断
                        <ArrowUpRight size={15} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="quiet-empty">暂无 Agent 上报的可诊断进程。</div>
        )}
      </section>
    </>
  );
}
