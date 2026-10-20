/**
 * 无业务状态的共享界面部件：标题、表格、状态标签、载入占位和受控错误提示。
 * 需要写权限的入口读取会话上下文；真实变更仍由调用页面和服务端处理。
 */
import type { ReactNode } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CircleNotch,
  WarningCircle,
  Plus,
  MagnifyingGlass,
  Stack,
  CaretRight,
} from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { APIError, dateTime, kindName } from "../api";
import type { Task } from "../types";
import { useCanWrite } from "../auth-context";

const statusLabels: Record<string, string> = {
  CREATED: "已创建",
  QUEUED: "等待下发",
  DELIVERED: "已下发",
  RUNNING: "进行中",
  UPLOADING: "上传中",
  COLLECTED: "采集完成",
  SUCCESS: "已完成",
  FAILED: "失败",
  CANCELED: "已取消",
  PENDING: "等待分析",
  RETRY: "重试中",
  online: "在线",
  offline: "离线",
  degraded: "降级",
  disabled: "已禁用",
};
export function Badge({ status }: { status: string }) {
  const tone = ["SUCCESS", "COLLECTED", "online"].includes(status)
    ? "success"
    : ["FAILED", "offline"].includes(status)
      ? "danger"
      : ["RUNNING", "UPLOADING", "RETRY", "DELIVERED"].includes(status)
        ? "active"
        : "neutral";
  return (
    <span className={`badge ${tone}`}>
      <span className="status-dot" />
      {statusLabels[status] || status}
    </span>
  );
}
export function TaskStatus({ task }: { task: Task }) {
  return (
    <Badge
      status={
        task.collection_status === "COLLECTED"
          ? task.analysis_status
          : task.collection_status
      }
    />
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div className="page-heading-copy">
        <div className="eyebrow page-heading-eyebrow">{eyebrow}</div>
        <h1>
          {title}
          <span className="heading-dot">.</span>
        </h1>
        <p>{description}</p>
      </div>
      {action && <div className="heading-actions">{action}</div>}
    </div>
  );
}
export function NewTaskLink({
  children = "新建诊断",
}: {
  children?: ReactNode;
}) {
  const canWrite = useCanWrite();
  if (!canWrite) return null;
  return (
    <Link className="button primary" to="/tasks/new">
      <Plus size={17} weight="bold" />
      {children}
      <kbd>N</kbd>
    </Link>
  );
}
export function Empty({
  title = "这里还没有诊断任务",
  description = "连接目标节点，创建一次采集，让性能问题有据可循。",
  action = true,
  icon = <Stack size={28} />,
}: {
  title?: string;
  description?: string;
  action?: boolean | ReactNode;
  icon?: ReactNode;
}) {
  const canWrite = useCanWrite();
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action === true
        ? canWrite && (
            <Link className="button secondary" to="/tasks/new">
              创建诊断
              <ArrowRight size={16} />
            </Link>
          )
        : action || null}
    </div>
  );
}
export function ErrorPanel({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div className="error-panel" role="alert">
      <WarningCircle size={22} />
      <div>
        <strong>
          {error instanceof Error ? error.message : "无法加载数据"}
        </strong>
        {error instanceof APIError && (
          <small>
            {error.code}
            {error.requestId && ` · ${error.requestId}`}
          </small>
        )}
      </div>
      {retry && (
        <button className="button ghost" onClick={retry}>
          重试
        </button>
      )}
    </div>
  );
}
export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="skeleton-wrap" role="status" aria-label="正在加载">
      {Array.from({ length: rows }, (_, i) => (
        <div
          className="skeleton"
          style={{ width: i === 0 ? "45%" : "100%" }}
          key={i}
        />
      ))}
    </div>
  );
}
export function Busy({ children = "处理中" }: { children?: ReactNode }) {
  return (
    <>
      <CircleNotch className="spin" size={17} />
      {children}
    </>
  );
}
export function SectionHeading({
  title,
  note,
  link,
  to,
}: {
  title: string;
  note?: string;
  link?: string;
  to?: string;
}) {
  return (
    <div className="section-heading">
      <div>
        <h2>{title}</h2>
        {note && <span className="section-note">{note}</span>}
      </div>
      {to && (
        <Link className="text-link" to={to}>
          {link || "查看全部"}
          <ArrowUpRight size={15} />
        </Link>
      )}
    </div>
  );
}
export function TaskTable({
  tasks,
  compact = false,
  selection,
}: {
  tasks: Task[];
  compact?: boolean;
  selection?: {
    selectedIds: ReadonlySet<string>;
    disabled?: boolean;
    limitReached?: boolean;
    onToggle: (task: Task) => void;
    onTogglePage: () => void;
  };
}) {
  if (!tasks.length) return <Empty />;
  const selectedOnPage = selection
    ? tasks.filter((task) => selection.selectedIds.has(task.id)).length
    : 0;
  return (
    <div className="table-scroll task-table-wrap">
      <table
        className={`data-table task-table ${compact ? "compact" : "full"}${selection ? " selectable" : ""}`}
      >
        <thead>
          <tr>
            {selection && (
              <th scope="col" className="task-select-cell">
                <label className="task-select-control">
                  <input
                    type="checkbox"
                    aria-label="选择当前页任务"
                    checked={selectedOnPage === tasks.length}
                    ref={(input) => {
                      if (input)
                        input.indeterminate =
                          selectedOnPage > 0 && selectedOnPage < tasks.length;
                    }}
                    disabled={
                      selection.disabled ||
                      (selection.limitReached && selectedOnPage === 0)
                    }
                    onChange={selection.onTogglePage}
                  />
                </label>
              </th>
            )}
            <th scope="col" className="task-identity-cell">
              诊断任务
            </th>
            <th scope="col" className="task-type-cell">
              类型
            </th>
            {!compact && (
              <th scope="col" className="task-target-cell">
                目标节点
              </th>
            )}
            <th scope="col" className="task-status-cell">
              状态
            </th>
            <th scope="col" className="task-created-cell">
              创建时间 <span>↓</span>
            </th>
            <th scope="col" className="task-open-cell">
              <span className="sr-only">详情</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <tr
              key={task.id}
              data-selected={selection?.selectedIds.has(task.id) || undefined}
            >
              {selection && (
                <td className="task-select-cell">
                  <label className="task-select-control">
                    <input
                      type="checkbox"
                      aria-label={`选择${task.name}`}
                      checked={selection.selectedIds.has(task.id)}
                      disabled={
                        selection.disabled ||
                        (selection.limitReached &&
                          !selection.selectedIds.has(task.id))
                      }
                      onChange={() => selection.onToggle(task)}
                    />
                    <span className="task-select-label">选择任务</span>
                  </label>
                </td>
              )}
              <td className="task-identity-cell" data-label="诊断任务">
                <Link
                  to={`/tasks/${task.id}`}
                  className="task-name task-identity"
                  title={task.name}
                >
                  <span className="task-name-copy">
                    <strong className="task-title">{task.name}</strong>
                    <small className="task-meta mono">
                      <span className="task-id">#{task.id}</span>{" "}
                      <span aria-hidden="true">·</span>{" "}
                      {task.kind === "IMPORT_COLLAPSED"
                        ? "导入数据"
                        : `${task.params?.duration_seconds ?? "—"}s`}
                    </small>
                  </span>
                </Link>
              </td>
              <td className="task-type-cell" data-label="类型">
                <span className="kind-label">{kindName(task.kind)}</span>
              </td>
              {!compact && (
                <td className="task-target-cell" data-label="目标节点">
                  <span className="mono dim">
                    {task.agent_hostname || task.target_agent_id || "本地导入"}
                  </span>
                </td>
              )}
              <td className="task-status-cell" data-label="状态">
                <TaskStatus task={task} />
              </td>
              <td className="task-created-cell mono dim" data-label="创建时间">
                {dateTime(task.created_at)}
              </td>
              <td className="task-open-cell">
                <Link
                  className="icon-button"
                  aria-label={`查看${task.name}`}
                  to={`/tasks/${task.id}`}
                >
                  <CaretRight size={16} />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function SearchField({
  value,
  onChange,
  placeholder = "搜索任务名称或 ID",
  label = "搜索",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
}) {
  return (
    <label className="search-field">
      <MagnifyingGlass size={17} />
      <span className="sr-only">{label}</span>
      <input
        data-search-input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      {value && (
        <button
          type="button"
          aria-label="清除搜索"
          onClick={() => onChange("")}
        >
          ×
        </button>
      )}
    </label>
  );
}
export function CheckLabel({ children }: { children: ReactNode }) {
  return (
    <span className="check-label">
      <Check size={14} />
      {children}
    </span>
  );
}
