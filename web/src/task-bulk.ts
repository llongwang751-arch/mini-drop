/**
 * 批量操作采用有上限的选择数和小并发池，逐项返回成功、跳过或失败。
 * 点击后会重新读取每个任务，避免拿列表中已过期的权限或状态直接执行变更。
 */
import { isTerminal } from "./api";
import type { Task } from "./types";

export const MAX_SELECTED_TASKS = 50;
export type BulkAction = "cancel" | "retry" | "delete";
export interface BulkItemResult {
  id: string;
  name: string;
  status: "success" | "skipped" | "failed";
  message: string;
}

export function toggleTaskSelection(selected: Task[], task: Task): Task[] {
  if (selected.some((item) => item.id === task.id))
    return selected.filter((item) => item.id !== task.id);
  return selected.length < MAX_SELECTED_TASKS ? [...selected, task] : selected;
}

export function bulkEligibility(task: Task, action: BulkAction): string {
  if (
    !(action === "delete"
      ? task.permissions?.delete
      : task.permissions?.operate)
  )
    return "当前账号没有此项操作权限";
  if (action === "cancel")
    return isTerminal(task) ? "任务已进入终态，无需取消" : "";
  if (!isTerminal(task)) return "请先取消或等待任务结束";
  if (action === "retry" && !task.target_agent_id)
    return "导入任务请重新导入文件";
  return "";
}

/** 每次批量变更前重新读取权限和状态；前端检查改善体验，最终授权仍以 API 为准。 */
export async function runTaskBatch(
  selected: Task[],
  action: BulkAction,
  operations: {
    load: (id: string) => Promise<Task>;
    execute: (task: Task, action: BulkAction) => Promise<unknown>;
    onProgress?: (completed: number) => void;
  },
): Promise<BulkItemResult[]> {
  if (selected.length > MAX_SELECTED_TASKS)
    throw new Error("每批最多选择 50 个任务");
  if (new Set(selected.map((task) => task.id)).size !== selected.length)
    throw new Error("批量任务不能重复");
  const result: BulkItemResult[] = new Array(selected.length);
  let next = 0,
    completed = 0;
  // 两个消费者共享递增索引，保持小并发；结果按原选择顺序落位，而不是按完成时间乱序。
  const worker = async () => {
    while (next < selected.length) {
      const index = next++,
        selectedTask = selected[index];
      try {
        const current = await operations.load(selectedTask.id);
        if (current.id !== selectedTask.id) throw new Error("任务标识不一致");
        const reason = bulkEligibility(current, action);
        if (reason)
          result[index] = {
            id: current.id,
            name: current.name,
            status: "skipped",
            message: reason,
          };
        else {
          await operations.execute(current, action);
          result[index] = {
            id: current.id,
            name: current.name,
            status: "success",
            message: "操作完成",
          };
        }
      } catch (error) {
        result[index] = {
          id: selectedTask.id,
          name: selectedTask.name,
          status: "failed",
          message:
            error instanceof Error ? error.message : "操作失败，请刷新后重试",
        };
      }
      operations.onProgress?.(++completed);
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(2, selected.length) }, worker),
  );
  return result;
}
