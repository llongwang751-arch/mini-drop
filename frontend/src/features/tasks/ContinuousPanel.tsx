import { useEffect, useState } from "react";
import { ClockCounterClockwise } from "@phosphor-icons/react";
import { EmptyState } from "../../components/EmptyState";
import { collectorText, formatTime } from "../../constants";
import { useDashboardStore } from "../../store/dashboardStore";
import type { Agent, Task } from "../../types";

export function ContinuousPanel({ agents }: { agents: Agent[] }) {
  const selectTask = useDashboardStore((state) => state.selectTask);
  const loadContinuous = useDashboardStore((state) => state.loadContinuous);
  const [agentId, setAgentId] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [slices, setSlices] = useState<Task[]>([]);

  useEffect(() => {
    if (!agentId && agents.length) setAgentId(agents[0].id);
  }, [agents, agentId]);

  const load = async () => {
    if (!agentId) return;
    setSlices(await loadContinuous(agentId, start, end));
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <p className="eyebrow">CONTINUOUS PROFILING</p>
          <h2>持续采样时间轴</h2>
        </div>
        <div className="range">
          <select value={agentId} onChange={(event) => setAgentId(event.target.value)}>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.id}
              </option>
            ))}
          </select>
          <input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} />
          <input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} />
          <button className="secondary" onClick={load}>
            查询窗口
          </button>
        </div>
      </div>
      <div className="slices">
        {slices.length ? (
          slices.map((slice) => (
            <button key={slice.id} onClick={() => void selectTask(slice.id)}>
              <code>{slice.id}</code>
              <span>
                {formatTime(slice.updated_at)} · {collectorText[slice.collector]}
              </span>
              <b>{slice.result?.fingerprint}</b>
            </button>
          ))
        ) : (
          <EmptyState
            compact
            icon={ClockCounterClockwise}
            title="暂无时间轴数据"
            description="创建持续采集任务后，可在这里回看切片。"
          />
        )}
      </div>
    </section>
  );
}
