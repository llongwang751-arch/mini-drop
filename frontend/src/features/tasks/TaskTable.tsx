import { ListMagnifyingGlass } from "@phosphor-icons/react";
import { collectorText, formatTime, reasonText } from "../../constants";
import { EmptyState } from "../../components/EmptyState";
import { StatusBadge } from "../../components/StatusBadge";
import type { Task } from "../../types";
import { useEffect, useMemo, useState } from "react";

interface TaskTableProps {
  tasks: Task[];
  selectedId?: string;
  onSelect: (id: string) => void;
}

export function TaskTable({ tasks, selectedId, onSelect }: TaskTableProps) {
  const pageSize = 6;
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(tasks.length / pageSize));
  const visibleTasks = useMemo(() => tasks.slice((page - 1) * pageSize, page * pageSize), [tasks, page]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

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
    <>
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
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {visibleTasks.map((task) => (
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
                <td>
                  <button className="table-action" type="button" onClick={(event) => {
                    event.stopPropagation();
                    onSelect(task.id);
                  }}>
                    查看分析
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pagination">
        <span>
          共 {tasks.length} 条，第 {page} / {totalPages} 页
        </span>
        <div>
          <button type="button" className="secondary" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>
            上一页
          </button>
          <button type="button" className="secondary" disabled={page >= totalPages} onClick={() => setPage((value) => Math.min(totalPages, value + 1))}>
            下一页
          </button>
        </div>
      </div>
    </>
  );
}
