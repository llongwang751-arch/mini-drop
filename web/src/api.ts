import type { Auth } from "./types";
export class APIError extends Error {
  code: string;
  requestId: string;
  status: number;
  constructor(
    message: string,
    code: string,
    requestId: string,
    status: number,
  ) {
    super(message);
    this.code = code;
    this.requestId = requestId;
    this.status = status;
  }
}
let csrf = "";
const kindLabels = new Map<string, string>();
// getRandomValues is available on plain IP HTTP origins, unlike randomUUID.
export function newIdempotencyKey(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 15) | 64;
  b[8] = (b[8] & 63) | 128;
  const h = Array.from(b, (v) => v.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
export function setAuth(auth: Auth) {
  csrf = auth.csrf_token;
}
export async function api<T>(
  path: string,
  options: RequestInit & {
    idempotent?: boolean;
    idempotencyKey?: string;
    timeoutMs?: number;
  } = {},
): Promise<T> {
  const {
    idempotent,
    idempotencyKey,
    timeoutMs = 20000,
    signal: callerSignal,
    ...requestOptions
  } = options;
  if (!Number.isFinite(timeoutMs) || timeoutMs < 1 || timeoutMs > 120000)
    throw new RangeError("请求超时须为 1–120000 毫秒");
  const headers = new Headers(options.headers);
  const requestId = headers.get("X-Request-ID") || newIdempotencyKey();
  headers.set("X-Request-ID", requestId);
  headers.set("Content-Type", "application/json");
  if (options.method && !["GET", "HEAD"].includes(options.method)) {
    headers.set("X-CSRF-Token", csrf);
    if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
    else if (idempotent) headers.set("Idempotency-Key", newIdempotencyKey());
  }
  const controller = new AbortController();
  const cancel = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) cancel();
  else callerSignal?.addEventListener("abort", cancel, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException("Request timed out", "TimeoutError"));
  }, timeoutMs);
  const cancellation = () => {
    // A user/navigation cancellation takes precedence over a later timer.
    if (callerSignal?.aborted) throw callerSignal.reason;
    if (timedOut)
      throw new APIError(
        "请求超时，请稍后重试",
        "REQUEST_TIMEOUT",
        requestId,
        0,
      );
  };
  try {
    let response: Response;
    try {
      response = await fetch("/api/v1" + path, {
        ...requestOptions,
        signal: controller.signal,
        headers,
        credentials: "same-origin",
      });
    } catch (error) {
      cancellation();
      if (error instanceof DOMException && error.name === "AbortError")
        throw error;
      throw new APIError("网络连接中断，请重试", "NETWORK_ERROR", requestId, 0);
    }
    let body;
    try {
      body = await response.json();
    } catch {
      cancellation();
      throw new APIError(
        "服务暂时无法响应，请稍后重试",
        "INVALID_RESPONSE",
        requestId,
        response.status,
      );
    }
    cancellation();
    if (!response.ok) {
      if (response.status === 401 && !path.startsWith("/auth/"))
        window.dispatchEvent(new Event("drop:unauthorized"));
      throw new APIError(
        body.error?.message || "请求失败",
        body.error?.code || "UNKNOWN",
        body.request_id || "",
        response.status,
      );
    }
    if (path === "/task-kinds" && Array.isArray(body.data)) {
      for (const kind of body.data)
        if (
          typeof kind.id === "string" &&
          typeof kind.display_name === "string"
        )
          kindLabels.set(kind.id, kind.display_name);
    }
    return body.data as T;
  } finally {
    clearTimeout(timer);
    callerSignal?.removeEventListener("abort", cancel);
  }
}
export const dateTime = (value?: string, timezone = "Asia/Shanghai") =>
  value && Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        timeZone: timezone,
      }).format(new Date(value))
    : "—";
export const number = (value: number | undefined) =>
  new Intl.NumberFormat("en-US").format(value ?? 0);
export function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1073741824) return `${(value / 1048576).toFixed(1)} MB`;
  return `${(value / 1073741824).toFixed(2)} GB`;
}
export const kindName = (kind: string) =>
  kindLabels.get(kind) ||
  {
    PERF_CPU: "CPU · perf",
    PROC_CPU: "线程 CPU · procfs",
    IMPORT_COLLAPSED: "折叠栈导入",
  }[kind] ||
  kind;
export function isTerminal(task: {
  collection_status: string;
  analysis_status: string;
}) {
  return (
    ["FAILED", "CANCELED"].includes(task.collection_status) ||
    ["SUCCESS", "FAILED", "CANCELED"].includes(task.analysis_status)
  );
}
