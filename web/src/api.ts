/**
 * 同源 API 客户端：统一请求标识、CSRF、幂等键、超时与取消信号。
 * 认证依赖浏览器的同源会话 Cookie；CSRF 只保留在内存，不在浏览器保存后端长期密钥。
 */
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
// 会话 Cookie 由浏览器自动携带；此值仅是写请求的 CSRF 防护材料，不是长期访问密钥。
let csrf = "";
const kindLabels = new Map<string, string>();
// 兼容直接通过 IP/HTTP 访问的部署：getRandomValues 可用，randomUUID 则可能要求安全上下文。
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
  // 请求 ID 用于跨服务排查；幂等键用于识别同一次业务提交，二者不能互相替代。
  const headers = new Headers(options.headers);
  const requestId = headers.get("X-Request-ID") || newIdempotencyKey();
  headers.set("X-Request-ID", requestId);
  headers.set("Content-Type", "application/json");
  if (options.method && !["GET", "HEAD"].includes(options.method)) {
    headers.set("X-CSRF-Token", csrf);
    if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
    else if (idempotent) headers.set("Idempotency-Key", newIdempotencyKey());
  }
  // 合并调用者取消与本请求超时：同一个 fetch 只接收一个 signal，但保留原取消原因。
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
    // 用户取消或离开页面的意图优先于稍后触发的超时，避免把主动取消误报成服务故障。
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
    // 认证过期通知应用外壳清理缓存；其他业务错误仍保留服务端 code/request_id 便于定位。
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
    // 成功、失败和取消都走 finally，清掉监听器，避免已完成请求继续持有页面对象。
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
