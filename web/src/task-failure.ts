import type { Artifact, Attempt, Task, TaskEvent } from "./types";

export type TimelineTone = "failed" | "canceled" | "success" | "neutral";
export type FailureAction = "retry" | "reanalyze" | "reimport" | null;
type StageState = "done" | "current" | "failed" | "canceled" | "pending";
export interface TaskStage {
  label: string;
  state: StageState;
}
export interface FailureDetails {
  stage: string;
  code: string;
  message: string;
  requestId: string;
  attemptId: string;
  retryLabel: string;
  recommendation: string;
  action: FailureAction;
  saved: Artifact[];
}

function identifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[1-9]\d{0,18}$/.test(value) &&
    BigInt(value) <= 9223372036854775807n
  );
}
function latestAttempt(task: Task): Attempt | undefined {
  const attempts = task.attempts || [];
  if (attempts.some((a) => !Number.isSafeInteger(a.attempt) || a.attempt < 1))
    return;
  const latest = [...attempts].sort((a, b) => b.attempt - a.attempt)[0];
  // Validate only after locating the newest attempt. Filtering invalid IDs
  // first would silently relabel an older attempt's files as current.
  return latest && identifier(latest.id) ? latest : undefined;
}
function currentEvents(task: Task, events: TaskEvent[]) {
  const attempt = latestAttempt(task);
  if (!attempt) return [];
  return events
    .filter(
      (e) =>
        e.attempt_id === attempt.id &&
        (!e.task_id || e.task_id === task.id) &&
        identifier(e.sequence),
    )
    .sort((a, b) => (BigInt(a.sequence) < BigInt(b.sequence) ? -1 : 1));
}
export function timelineTone(type: string): TimelineTone {
  if (type.endsWith("_FAILED") || type === "TASK_TIMED_OUT") return "failed";
  if (type.endsWith("_CANCELED")) return "canceled";
  if (type.endsWith("_SUCCEEDED")) return "success";
  return "neutral";
}
function imported(task: Task) {
  return task.kind === "IMPORT_COLLAPSED" || task.params.source === "import";
}
function labels(task: Task) {
  return imported(task)
    ? [
        "已创建",
        "等待处理",
        "导入已接收",
        "数据导入中",
        "原始数据已保存",
        "分析中",
        "结果可用",
      ]
    : [
        "已创建",
        "等待下发",
        "Agent 已接收",
        "采集中",
        "原始数据已保存",
        "分析中",
        "结果可用",
      ];
}
function collectionFailureIndex(task: Task, events: TaskEvent[]) {
  if (task.error_code?.startsWith("UPLOAD_")) return 4;
  if (/^(DISPATCH_|QUEUE_|AGENT_OFFLINE)/.test(task.error_code || "")) return 1;
  if (imported(task) && task.error_code?.startsWith("IMPORT_")) return 3;
  const reached = currentEvents(task, events).reduce<number | undefined>(
    (index, e) => {
      if (e.type === "RAW_ARTIFACT_UPLOADED") return Math.max(index ?? 1, 4);
      if (e.type === "RUNNER_STARTED" || e.type === "TASK_DELIVERED")
        return Math.max(index ?? 1, 3);
      if (e.type === "DISPATCH_QUEUED") return Math.max(index ?? 1, 1);
      return index;
    },
    undefined,
  );
  return reached;
}
export function taskStages(
  task: Task,
  events: TaskEvent[] = task.events || [],
): TaskStage[] {
  const failed =
    task.collection_status === "FAILED" || task.analysis_status === "FAILED";
  const canceled = task.collection_status === "CANCELED";
  const success =
    task.collection_status === "COLLECTED" &&
    task.analysis_status === "SUCCESS";
  const failureIndex = collectionFailureIndex(task, events);
  const unknownFailure =
    (failed || canceled) &&
    task.collection_status !== "COLLECTED" &&
    failureIndex === undefined;
  const position =
    task.collection_status === "COLLECTED"
      ? 5
      : failed || canceled
        ? (failureIndex ?? 1)
        : task.collection_status === "UPLOADING"
          ? 4
          : task.collection_status === "RUNNING"
            ? 3
            : task.collection_status === "DELIVERED"
              ? 2
              : 1;
  return labels(task).map((label, index) => ({
    label,
    state: unknownFailure
      ? index === 0
        ? "done"
        : "pending"
      : success || index < position
        ? "done"
        : index > position
          ? "pending"
          : canceled
            ? "canceled"
            : failed
              ? "failed"
              : "current",
  }));
}
export function currentSavedArtifacts(
  task: Task,
  now = Date.now(),
): Artifact[] {
  const latest = latestAttempt(task);
  if (!latest) return [];
  return (task.artifacts || []).filter(
    (a) =>
      identifier(a.id) &&
      a.attempt_id === latest.id &&
      (!a.task_id || a.task_id === task.id) &&
      ["RAW", "LOG", "BINARY"].includes(a.kind) &&
      a.status === "ready" &&
      a.download_allowed === true &&
      Number.isSafeInteger(a.size) &&
      a.size >= 0 &&
      /^[a-f0-9]{64}$/.test(a.sha256) &&
      (!a.expires_at || Date.parse(a.expires_at) > now),
  );
}
export function failureDetails(
  task: Task,
  events: TaskEvent[],
  permission: { operate: boolean; reanalyze: boolean },
  now = Date.now(),
): FailureDetails | undefined {
  if (
    !["FAILED", "CANCELED"].includes(task.collection_status) &&
    task.analysis_status !== "FAILED"
  )
    return;
  const attempt = latestAttempt(task);
  const saved = currentSavedArtifacts(task, now);
  const analysisFailure = task.collection_status === "COLLECTED";
  const failureEvent = currentEvents(task, events)
    .filter((e) =>
      ["TASK_FAILED", "ANALYSIS_FAILED", "TASK_CANCELED"].includes(e.type),
    )
    .at(-1);
  const hasRaw = saved.some((a) => a.kind === "RAW");
  const action: FailureAction = permission.operate
    ? analysisFailure && permission.reanalyze && hasRaw
      ? "reanalyze"
      : imported(task)
        ? "reimport"
        : task.target_agent_id
          ? "retry"
          : null
    : null;
  const code =
    typeof task.error_code === "string" &&
    /^[A-Z][A-Z0-9_]{0,95}$/.test(task.error_code)
      ? task.error_code
      : task.collection_status === "CANCELED"
        ? "TASK_CANCELED"
        : "EXECUTION_FAILED";
  let recommendation =
    action === "reanalyze"
      ? "本次原始制品仍可用。修正分析配置或输入问题后，可重新分析原始数据。"
      : action === "reimport"
        ? "检查原始文件格式与内容，修正后重新导入。"
        : action === "retry"
          ? "查看本次时间线与已保存制品，确认目标、权限和工具状态后，再重新采集。"
          : "请联系任务负责人，并提供任务、执行尝试和请求追踪 ID。";
  if (/PERMISSION|TARGET_|TOOL_|CAPABILITY|NOT_SUPPORTED/.test(code))
    recommendation = "请检查目标进程、节点能力和采集权限；修正后再重试。";
  else if (/STORAGE|UPLOAD|DISPATCH|UNAVAILABLE/.test(code))
    recommendation =
      "请检查节点和依赖服务是否可用；保留本次已保存制品，恢复后再重试。";
  const retryLabel =
    failureEvent?.payload.retryable === false
      ? "自动重试已结束；手动操作仍须满足权限与输入条件"
      : "服务端未提供自动重试判定；请先按建议检查";
  return {
    stage: analysisFailure
      ? "离线分析"
      : collectionFailureIndex(task, events) === undefined
        ? "采集（具体步骤未记录）"
        : labels(task)[collectionFailureIndex(task, events)!].replace(
            /中$/,
            "",
          ),
    code,
    message: (
      task.error_message ||
      (task.collection_status === "CANCELED"
        ? "本次任务已取消。"
        : "本次执行未能完成，请查看事件与已保存制品。")
    ).slice(0, 512),
    requestId:
      typeof task.request_id === "string" &&
      /^[A-Za-z0-9._:-]{1,128}$/.test(task.request_id)
        ? task.request_id
        : "",
    attemptId: attempt?.id || "",
    retryLabel,
    recommendation,
    action,
    saved,
  };
}
