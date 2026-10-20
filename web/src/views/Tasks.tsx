import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  FunnelSimple,
  ArrowLeft,
  ArrowRight,
  ArrowClockwise,
  ArrowsLeftRight,
  Stop,
  Trash,
} from "@phosphor-icons/react";
import { api, newIdempotencyKey } from "../api";
import { pollingInterval } from "../query-policy";
import type { List, Task } from "../types";
import {
  PageHeading,
  NewTaskLink,
  SearchField,
  TaskTable,
  ErrorPanel,
  Skeleton,
  Busy,
} from "../components/ui";
import ConfirmDialog from "../components/ConfirmDialog";
import { useCanWrite } from "../auth-context";
import { comparisonEligibility } from "../comparison";
import {
  MAX_SELECTED_TASKS,
  runTaskBatch,
  toggleTaskSelection,
} from "../task-bulk";
import type { BulkAction, BulkItemResult } from "../task-bulk";

const actionLabels: Record<BulkAction, string> = {
  cancel: "取消",
  retry: "重试",
  delete: "删除",
};

export default function Tasks() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("search") || "");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Task[]>([]);
  const [running, setRunning] = useState<BulkAction>();
  const [progress, setProgress] = useState(0);
  const [report, setReport] = useState<BulkItemResult[]>([]);
  const [batchError, setBatchError] = useState<unknown>();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const keys = useRef(new Map<string, string>());
  const canWrite = useCanWrite();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["tasks", search, status, page],
    queryFn: ({ signal }) =>
      api<List<Task>>(
        `/tasks?search=${encodeURIComponent(search)}&status=${encodeURIComponent(status)}&page=${page}&page_size=12`,
        { signal },
      ),
    refetchInterval: (query) => pollingInterval(query, 10000),
  });
  const selectedIds = new Set(selected.map((task) => task.id));
  const comparisonReason = comparisonEligibility(selected);
  const comparisonURL = comparisonReason
    ? ""
    : `/compare?baseline=${encodeURIComponent(selected[0].id)}&candidate=${encodeURIComponent(selected[1].id)}`;
  const failed = report.filter((item) => item.status !== "success");

  function togglePage() {
    const items = q.data?.items || [];
    const allSelected = items.every((task) => selectedIds.has(task.id));
    setSelected((current) =>
      allSelected
        ? current.filter((task) => !items.some((item) => item.id === task.id))
        : [
            ...current,
            ...items
              .filter((task) => !selectedIds.has(task.id))
              .slice(0, MAX_SELECTED_TASKS - current.length),
          ],
    );
  }
  async function run(action: BulkAction) {
    if (running || !selected.length) return;
    setRunning(action);
    setProgress(0);
    setReport([]);
    setBatchError(undefined);
    try {
      const result = await runTaskBatch(selected, action, {
        load: (id) => api<Task>(`/tasks/${encodeURIComponent(id)}`),
        execute: (task, operation) => {
          const keyName = `${operation}:${task.id}`;
          if (!keys.current.has(keyName))
            keys.current.set(keyName, newIdempotencyKey());
          return api(
            `/tasks/${encodeURIComponent(task.id)}${operation === "delete" ? "" : `/${operation}`}`,
            {
              method: operation === "delete" ? "DELETE" : "POST",
              ...(operation === "delete" ? {} : { body: "{}" }),
              idempotencyKey: keys.current.get(keyName),
            },
          );
        },
        onProgress: setProgress,
      });
      setReport(result);
      const completed = new Set(
        result
          .filter((item) => item.status === "success")
          .map((item) => item.id),
      );
      for (const id of completed) keys.current.delete(`${action}:${id}`);
      setSelected((current) =>
        current.filter((task) => !completed.has(task.id)),
      );
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["tasks"] }),
        qc.invalidateQueries({ queryKey: ["overview"] }),
      ]);
    } catch (error) {
      setBatchError(error);
    } finally {
      setRunning(undefined);
      setConfirmDelete(false);
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="WORKSPACE / DIAGNOSTICS"
        title="诊断任务"
        description="追踪采集过程，回到问题发生的现场。"
        action={<NewTaskLink />}
      />
      <ConfirmDialog
        open={confirmDelete}
        title={`删除选中的 ${selected.length} 个任务？`}
        description="仅删除有管理权限且已结束的任务。逐项检查最新权限和状态；制品仍按各自保留期管理，失败项会保留在选择中。"
        busy={running === "delete"}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => void run("delete")}
      />
      <div className="panel task-workspace">
        <div className="filter-bar">
          <SearchField
            value={search}
            onChange={(value) => {
              setSearch(value);
              setPage(1);
              setParams(value ? { search: value } : {}, { replace: true });
            }}
          />
          <div className="filter-actions">
            <label className="select-wrap">
              <FunnelSimple size={16} />
              <span className="sr-only">任务状态</span>
              <select
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部状态</option>
                <option value="RUNNING">进行中</option>
                <option value="SUCCESS">已完成</option>
                <option value="FAILED">失败</option>
                <option value="CANCELED">已取消</option>
                <option value="QUEUED">等待下发</option>
              </select>
            </label>
            <button
              className="icon-button"
              aria-label="刷新任务"
              onClick={() => q.refetch()}
            >
              <ArrowClockwise size={18} />
            </button>
          </div>
        </div>
        <div
          className="task-bulk-bar"
          aria-label="任务选择与批量操作"
          aria-busy={!!running}
        >
          <div className="task-selection-count">
            <strong className="mono">{selected.length}</strong>
            <span>已选择 · 最多 {MAX_SELECTED_TASKS} 项</span>
          </div>
          <div className="task-bulk-actions">
            {comparisonURL ? (
              <Link
                className="button secondary small"
                to={comparisonURL}
                aria-disabled={!!running}
                onClick={(event) => {
                  if (running) event.preventDefault();
                }}
              >
                <ArrowsLeftRight size={16} />
                对比结果
              </Link>
            ) : (
              <button
                className="button secondary small"
                disabled
                title={comparisonReason}
              >
                <ArrowsLeftRight size={16} />
                对比结果
              </button>
            )}
            {canWrite && (
              <>
                <button
                  className="button secondary small"
                  disabled={!selected.length || !!running}
                  onClick={() => void run("cancel")}
                >
                  <Stop size={15} />
                  批量取消
                </button>
                <button
                  className="button secondary small"
                  disabled={!selected.length || !!running}
                  onClick={() => void run("retry")}
                >
                  <ArrowClockwise size={15} />
                  批量重试
                </button>
                <button
                  className="button ghost small task-delete-button"
                  disabled={!selected.length || !!running}
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash size={15} />
                  删除
                </button>
              </>
            )}
            {!!selected.length && (
              <button
                className="button ghost small"
                disabled={!!running}
                onClick={() => setSelected([])}
              >
                清空选择
              </button>
            )}
          </div>
        </div>
        <p className="task-selection-help">
          可跨页选择任务。选择两个同类型且分析完成的任务，即可对比权重分布。
        </p>
        {running && (
          <div className="task-batch-progress" role="status" aria-live="polite">
            <Busy>
              正在{actionLabels[running]} · {progress} / {selected.length}
            </Busy>
          </div>
        )}
        {batchError != null && <ErrorPanel error={batchError} />}
        {!!report.length && (
          <div className="task-batch-report" role="status" aria-live="polite">
            <strong>
              本批次成功 {report.length - failed.length} 项，跳过{" "}
              {report.filter((item) => item.status === "skipped").length}{" "}
              项，失败{" "}
              {report.filter((item) => item.status === "failed").length} 项。
            </strong>
            {!!failed.length && (
              <details open>
                <summary>查看未完成项</summary>
                <ul>
                  {failed.map((item) => (
                    <li key={item.id}>
                      <Link to={`/tasks/${item.id}`}>{item.name}</Link>
                      <span>
                        {item.status === "skipped" ? "已跳过" : "失败"}：
                        {item.message}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
        {q.error ? (
          <ErrorPanel error={q.error} retry={() => q.refetch()} />
        ) : q.isLoading ? (
          <Skeleton />
        ) : (
          <TaskTable
            tasks={q.data?.items || []}
            selection={{
              selectedIds,
              disabled: !!running,
              limitReached: selected.length >= MAX_SELECTED_TASKS,
              onToggle: (task) =>
                setSelected((current) => toggleTaskSelection(current, task)),
              onTogglePage: togglePage,
            }}
          />
        )}
        <div className="pagination">
          <span>
            共 <strong className="mono">{q.data?.total || 0}</strong> 个任务
          </span>
          <div>
            <button
              className="icon-button"
              aria-label="上一页"
              disabled={page === 1}
              onClick={() => setPage((value) => value - 1)}
            >
              <ArrowLeft size={16} />
            </button>
            <span className="mono">{page}</span>
            <button
              className="icon-button"
              aria-label="下一页"
              disabled={page * 12 >= (q.data?.total || 0)}
              onClick={() => setPage((value) => value + 1)}
            >
              <ArrowRight size={16} />
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
