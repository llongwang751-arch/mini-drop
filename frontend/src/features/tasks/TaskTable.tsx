import { ListMagnifyingGlass } from "@phosphor-icons/react";
import { collectorText, formatTime, reasonText } from "../../constants";
import { EmptyState } from "../../components/EmptyState";
import { StatusBadge } from "../../components/StatusBadge";
import type { Task } from "../../types";

interface TaskTableProps {
  tasks: Task[];
  selectedId?: string;
  onSelect: (id: string) => void;
}

export function TaskTable({ tasks, selectedId, onSelect }: TaskTableProps) {
  if (!tasks.length) {
    return (
      <EmptyState
        icon={ListMagnifyingGlass}
        title="暂无性能任务"
        description="创建第一个任务，Agent 会自动领取并分析。"
      />
    );
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>任务 ID</th>
            <th>采集器</th>
            <th>目标 PID</th>
            <th>状态</th>
            <th>最近进展</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={task.id} className={selectedId === task.id ? "selected" : ""} onClick={() => onSelect(task.id)}>
              <td>
                <code>{task.id}</code>
              </td>
              <td>{collectorText[task.collector]}</td>
              <td>{task.pid}</td>
              <td>
                <StatusBadge value={task.status} />
              </td>
              <td>{reasonText[task.reason] ?? task.reason}</td>
              <td>{formatTime(task.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
