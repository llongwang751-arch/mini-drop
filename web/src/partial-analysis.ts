import { APIError, newIdempotencyKey } from "./api";
import { readBounded, safeRequestId } from "./profile-data";
import type { Task } from "./types";
import schema from "../../contracts/partial-result.schema.json";

export const PARTIAL_LIMIT = 16 * 1024;
const HASH = /^[a-f0-9]{64}$/;
const encoder = new TextEncoder();
const sources = new Set(schema.properties.summary.properties.source.enum);
const kinds = new Set(schema.properties.kind.enum);
const unitPattern = new RegExp(
  schema.properties.summary.properties.unit.pattern,
);

export interface PartialAnalysisIdentity {
  analysis_job_id: string;
  analysis_run: number;
  input_artifact_id: string;
  input_digest: string;
}
export interface PartialAnalysis extends PartialAnalysisIdentity {
  schema_version: 1;
  completeness: "partial";
  task_id: string;
  attempt_id: string;
  kind: string;
  completed_stages: ["input_verified", "parsed"];
  missing_stages: ["diagnostic_rules", "result_artifacts", "final_commit"];
  summary: {
    source: string;
    unit: string;
    total_samples: number;
    unique_stacks: number;
    node_count: number;
    max_depth: number;
  };
  top_functions: {
    name: string;
    symbol_sha256: string;
    self: number;
    total: number;
    self_percent: number;
    total_percent: number;
  }[];
}

type RecordValue = Record<string, unknown>;
function object(value: unknown): value is RecordValue {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function keys(value: RecordValue, expected: string[]) {
  return (
    Object.keys(value).length === expected.length &&
    expected.every((key) => Object.hasOwn(value, key))
  );
}
function id(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[1-9]\d{0,18}$/.test(value) &&
    BigInt(value) <= 9223372036854775807n
  );
}
function numeric(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= Number.MAX_SAFE_INTEGER
  );
}
function integer(value: unknown): value is number {
  return numeric(value) && Number.isSafeInteger(value);
}
function sameArray(value: unknown, expected: string[]) {
  return (
    Array.isArray(value) &&
    value.length === expected.length &&
    value.every((item, index) => item === expected[index])
  );
}
export class PartialAnalysisError extends APIError {
  constructor(code = "PARTIAL_RESULT_INVALID", status = 0, requestId = "") {
    super(
      code === "PARTIAL_RESULT_INVALID"
        ? "部分分析结果与当前执行不符，已停止展示"
        : code === "REQUEST_TIMEOUT"
          ? "部分分析结果载入超时，请重新载入"
          : status === 403 || status === 404
            ? "当前无法读取此部分分析结果"
            : "部分分析结果载入失败，请稍后重新载入",
      code,
      requestId,
      status,
    );
  }
}
export function partialIdentity(task: Task | undefined) {
  if (
    !task ||
    !id(task.id) ||
    !integer(task.version) ||
    !kinds.has(task.kind) ||
    task.collection_status !== "COLLECTED" ||
    task.analysis_status !== "FAILED"
  )
    return;
  const attempts = task.attempts || [];
  if (
    !attempts.length ||
    attempts.some((attempt) => !integer(attempt.attempt) || attempt.attempt < 1)
  )
    return;
  const latest = [...attempts].sort((a, b) => b.attempt - a.attempt)[0];
  if (!id(latest.id) || latest.status !== "COLLECTED") return;
  const identity = task.analysis_identity;
  if (
    !identity ||
    !id(identity.analysis_job_id) ||
    !integer(identity.analysis_run) ||
    identity.analysis_run < 1 ||
    identity.analysis_run > 2147483647 ||
    !id(identity.input_artifact_id) ||
    !HASH.test(identity.input_digest)
  )
    return;
  const raw = (task.artifacts || []).filter(
    (artifact) =>
      artifact.kind === "RAW" &&
      artifact.status === "ready" &&
      artifact.attempt_id === latest.id &&
      (!artifact.task_id || artifact.task_id === task.id),
  );
  if (!raw.length || raw.some((artifact) => !id(artifact.id))) return;
  raw.sort((a, b) => (BigInt(a.id) > BigInt(b.id) ? -1 : 1));
  if (
    raw[0].id !== identity.input_artifact_id ||
    raw[0].sha256 !== identity.input_digest
  )
    return;
  return {
    ...identity,
    task_id: task.id,
    attempt_id: latest.id,
    kind: task.kind,
  };
}
export function partialQueryKey(task: Task | undefined) {
  const identity = partialIdentity(task);
  return [
    "partial-result",
    task?.id,
    task?.version,
    task?.updated_at,
    identity?.analysis_job_id,
    identity?.analysis_run,
    identity?.input_artifact_id,
    identity?.input_digest,
  ] as const;
}
export function validatePartialAnalysis(
  value: unknown,
  expected: NonNullable<ReturnType<typeof partialIdentity>>,
): PartialAnalysis {
  const reject = () => {
    throw new PartialAnalysisError();
  };
  if (
    !object(value) ||
    !keys(value, [
      "schema_version",
      "completeness",
      "task_id",
      "attempt_id",
      "analysis_job_id",
      "analysis_run",
      "input_artifact_id",
      "input_digest",
      "kind",
      "completed_stages",
      "missing_stages",
      "summary",
      "top_functions",
    ])
  )
    return reject();
  if (
    value.schema_version !== 1 ||
    value.completeness !== "partial" ||
    [
      "task_id",
      "attempt_id",
      "analysis_job_id",
      "analysis_run",
      "input_artifact_id",
      "input_digest",
      "kind",
    ].some((key) => value[key] !== expected[key as keyof typeof expected])
  )
    return reject();
  if (
    !sameArray(value.completed_stages, ["input_verified", "parsed"]) ||
    !sameArray(value.missing_stages, [
      "diagnostic_rules",
      "result_artifacts",
      "final_commit",
    ])
  )
    return reject();
  const summary = value.summary;
  if (
    !object(summary) ||
    !keys(summary, [
      "source",
      "unit",
      "total_samples",
      "unique_stacks",
      "node_count",
      "max_depth",
    ]) ||
    typeof summary.source !== "string" ||
    !sources.has(summary.source) ||
    typeof summary.unit !== "string" ||
    !unitPattern.test(summary.unit) ||
    !numeric(summary.total_samples) ||
    ![summary.unique_stacks, summary.node_count, summary.max_depth].every(
      integer,
    ) ||
    (summary.unique_stacks as number) > 100000 ||
    (summary.node_count as number) > 100000 ||
    (summary.max_depth as number) > 128
  )
    return reject();
  if (!Array.isArray(value.top_functions) || value.top_functions.length > 20)
    return reject();
  for (const row of value.top_functions) {
    if (
      !object(row) ||
      !keys(row, [
        "name",
        "symbol_sha256",
        "self",
        "total",
        "self_percent",
        "total_percent",
      ]) ||
      typeof row.name !== "string" ||
      !row.name ||
      encoder.encode(row.name).byteLength > 160 ||
      /\p{C}/u.test(row.name) ||
      typeof row.symbol_sha256 !== "string" ||
      !HASH.test(row.symbol_sha256) ||
      ![row.self, row.total, row.self_percent, row.total_percent].every(
        numeric,
      ) ||
      (row.self_percent as number) > 100 ||
      (row.total_percent as number) > 100
    )
      return reject();
  }
  try {
    if (encoder.encode(JSON.stringify(value)).byteLength > PARTIAL_LIMIT)
      return reject();
  } catch {
    return reject();
  }
  return value as unknown as PartialAnalysis;
}

export async function loadPartialAnalysis(
  task: Task,
  signal?: AbortSignal,
): Promise<PartialAnalysis | null> {
  if (signal?.aborted) throw signal.reason;
  const expected = partialIdentity(task);
  if (!expected) throw new PartialAnalysisError();
  const requestId = newIdempotencyKey();
  const controller = new AbortController();
  const cancel = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 20000);
  try {
    const response = await fetch(`/api/v1/tasks/${task.id}/partial-result`, {
      signal: controller.signal,
      credentials: "same-origin",
      redirect: "error",
      cache: "no-store",
      headers: { "X-Request-ID": requestId },
    });
    const buffer = await readBounded(response.body, PARTIAL_LIMIT + 1024);
    if (signal?.aborted) throw signal.reason;
    let body: unknown;
    try {
      body = JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(buffer),
      );
    } catch {
      throw new PartialAnalysisError();
    }
    const responseId = object(body)
      ? safeRequestId(body.request_id) || requestId
      : requestId;
    if (
      response.status === 409 &&
      object(body) &&
      object(body.error) &&
      body.error.code === "PARTIAL_RESULT_NOT_READY"
    )
      return null;
    if (!response.ok) {
      if (response.status === 401)
        window.dispatchEvent(new Event("drop:unauthorized"));
      throw new PartialAnalysisError(
        "PARTIAL_RESULT_UNAVAILABLE",
        response.status,
        responseId,
      );
    }
    if (!object(body) || body.error !== null || !Object.hasOwn(body, "data"))
      throw new PartialAnalysisError();
    return validatePartialAnalysis(body.data, expected);
  } catch (error) {
    if (signal?.aborted) throw signal.reason;
    if (timedOut)
      throw new PartialAnalysisError("REQUEST_TIMEOUT", 0, requestId);
    if (error instanceof PartialAnalysisError) throw error;
    throw new PartialAnalysisError("PARTIAL_RESULT_UNAVAILABLE", 0, requestId);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}
