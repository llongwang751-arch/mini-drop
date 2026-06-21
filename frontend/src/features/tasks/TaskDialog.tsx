import { FormEvent, useEffect, useMemo, useState } from "react";
import { X } from "@phosphor-icons/react";
import { useDashboardStore } from "../../store/dashboardStore";
import type { Agent, Collector, TaskRequest } from "../../types";

interface TaskDialogProps {
  agents: Agent[];
  onClose: () => void;
}

export function TaskDialog({ agents, onClose }: TaskDialogProps) {
  const createTask = useDashboardStore((state) => state.createTask);
  const onlineAgents = useMemo(() => agents.filter((agent) => agent.online), [agents]);
  const defaultAgent = useMemo(() => onlineAgents[0]?.id ?? "", [onlineAgents]);
  const [form, setForm] = useState<TaskRequest>({
    agent_id: defaultAgent,
    pid: 1,
    duration: 5,
    rate: 49,
    collector: "perf",
    continuous: false,
  });
  const [error, setError] = useState("");

  useEffect(() => {
    if (!form.agent_id || !onlineAgents.some((agent) => agent.id === form.agent_id)) {
      setForm((current) => (current.agent_id === defaultAgent ? current : { ...current, agent_id: defaultAgent }));
    }
  }, [defaultAgent, form.agent_id, onlineAgents]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await createTask(form);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div className="modal-backdrop">
      <form className="modal" onSubmit={submit}>
        <div className="panel-head">
          <div>
            <p className="eyebrow">NEW PROFILE</p>
            <h2>创建采集任务</h2>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭弹窗">
            <X size={18} />
          </button>
        </div>
        <label>
          目标 Agent
          <select value={form.agent_id} onChange={(event) => setForm({ ...form, agent_id: event.target.value })}>
            {!onlineAgents.length && <option value="">暂无在线 Agent</option>}
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id} disabled={!agent.online}>
                {agent.id} {agent.online ? "（在线）" : "（离线）"}
              </option>
            ))}
          </select>
        </label>
        <label>
          目标进程 PID
          <input type="number" min="1" value={form.pid} onChange={(event) => setForm({ ...form, pid: Number(event.target.value) })} />
        </label>
        <div className="form-grid">
          <label>
            采样时长（秒）
            <input
              type="number"
              min="1"
              max="300"
              value={form.duration}
              onChange={(event) => setForm({ ...form, duration: Number(event.target.value) })}
            />
          </label>
          <label>
            采样频率（Hz）
            <input
              type="number"
              min="1"
              max="999"
              value={form.rate}
              onChange={(event) => setForm({ ...form, rate: Number(event.target.value) })}
            />
          </label>
        </div>
        <label>
          采集器
          <select value={form.collector} onChange={(event) => setForm({ ...form, collector: event.target.value as Collector })}>
            <option value="perf">CPU / perf</option>
            <option value="ebpf">eBPF 内核探针</option>
            <option value="pyspy">Python 调用栈</option>
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={form.continuous}
            onChange={(event) => setForm({ ...form, continuous: event.target.checked })}
          />
          <span>
            <b>持续性能采集</b>
            <small>完成后自动创建下一个时间切片</small>
          </span>
        </label>
        {error && <div className="form-error">{error}</div>}
        <button className="primary" type="submit" disabled={!form.agent_id}>
          下发采集任务
        </button>
      </form>
    </div>
  );
}
