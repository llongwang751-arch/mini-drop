/**
 * 任务实时事件通道：将网络字节流拆成完整事件，再通知缓存失效。
 * 它不执行采集，也不直接改任务状态；状态最终来自授权的任务详情接口。
 */
import { newIdempotencyKey } from "./api";

export interface StreamEvent {
  type: string;
  data: string;
  lastEventId: string;
}
export type StreamState = "connecting" | "live" | "polling" | "paused";
class StreamProtocolError extends Error {}

/**
 * 按流解码 UTF-8 和 SSE 帧：网络分块不等于事件边界，只有遇到空行才提交完整事件。
 * 限额包含心跳注释、字段名和分隔符，防止小 JSON 之外的大量无用字节绕过内存限制。
 */
export class EventStreamDecoder {
  private decoder = new TextDecoder("utf-8", { fatal: true });
  private line = "";
  private bytes = 0;
  private afterCR = false;
  private data: string[] = [];
  private type = "message";
  private pendingId: string | undefined;
  private lastEventId: string;
  constructor(
    private emit: (event: StreamEvent) => void,
    cursor = "",
  ) {
    this.lastEventId = cursor;
  }
  push(chunk: Uint8Array) {
    this.consume(this.decode(chunk));
  }
  finish() {
    this.consume(this.decode());
    if (this.afterCR) {
      this.afterCR = false;
      this.consumeLine();
    }
    // 连接结束时丢弃未封口的事件；必须有空行结束标记，才能把数据交给页面。
  }
  private decode(chunk?: Uint8Array) {
    try {
      return this.decoder.decode(chunk, { stream: !!chunk });
    } catch {
      throw new StreamProtocolError("Invalid SSE UTF-8");
    }
  }
  // 分块可能恰好切在 CRLF 或多字节字符之间；TextDecoder 与 afterCR 共同保存跨块状态。
  private consume(text: string) {
    for (const character of text) {
      if (this.afterCR) {
        this.afterCR = false;
        if (character === "\n") {
          this.addBytes(1);
          this.consumeLine();
          continue;
        }
        this.consumeLine();
      }
      const point = character.codePointAt(0)!;
      this.addBytes(
        point < 0x80 ? 1 : point < 0x800 ? 2 : point < 0x10000 ? 3 : 4,
      );
      if (character === "\r") this.afterCR = true;
      else if (character === "\n") this.consumeLine();
      else this.line += character;
    }
  }
  private addBytes(count: number) {
    this.bytes += count;
    if (this.bytes > 65536)
      throw new StreamProtocolError("SSE frame exceeds byte limit");
  }
  private consumeLine() {
    const line = this.line;
    this.line = "";
    if (!line) {
      // 只有完整帧到达，才提交新游标；半个事件不能推进断点续读位置。
      if (this.pendingId !== undefined) this.lastEventId = this.pendingId;
      if (this.data.length)
        this.emit({
          type: this.type || "message",
          data: this.data.join("\n"),
          lastEventId: this.lastEventId,
        });
      this.bytes = 0;
      this.data = [];
      this.type = "message";
      this.pendingId = undefined;
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":"),
      field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.type = value;
    else if (field === "id" && !value.includes("\0")) {
      if (value.length > 128)
        throw new StreamProtocolError("SSE cursor exceeds length limit");
      this.pendingId = value;
    }
    // 不采纳服务端 retry 字段，重连仍由客户端有上限的退避策略统一控制。
  }
}

// int64 上限用 BigInt 比较，整个传输与缓存路径仍保存字符串，避免 number 精度损失。
function validCursor(value: string) {
  return /^[1-9]\d{0,18}$/.test(value) && BigInt(value) <= 9223372036854775807n;
}

/**
 * 为一个任务维护鉴权事件流，游标保留为字符串，避免 int64 ID 转成 JS number 后丢精度。
 * 使用 fetch 才能在断线后显式携带 Last-Event-ID；页面隐藏和路由退出都可立即取消连接。
 * SSE 仅补充实时体验，任务的 REST 轮询独立存在，不把流连通状态当成任务执行状态。
 */
export function subscribeTaskEvents(
  taskId: string,
  options: {
    onEvent: (event: StreamEvent) => void;
    onState?: (state: StreamState) => void;
  },
): () => void {
  if (!validCursor(taskId)) throw new RangeError("Invalid task ID");
  let disposed = false,
    blocked = false,
    cursor = "",
    failures = 0;
  let active: AbortController | undefined;
  let reconnect: ReturnType<typeof setTimeout> | undefined;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const clearTimeouts = () => {
    clearTimeout(reconnect);
    clearTimeout(timeout);
    reconnect = timeout = undefined;
  };
  // 连接身份围栏：旧连接的异步回调不得覆盖替换后的连接或路由退出后的状态。
  const current = (controller: AbortController) =>
    !disposed && active === controller && !controller.signal.aborted;
  const deadline = (controller: AbortController, ms: number) => {
    clearTimeout(timeout);
    timeout = setTimeout(
      () =>
        controller.abort(new DOMException("Stream timed out", "TimeoutError")),
      ms,
    );
  };
  const start = () => {
    if (disposed || blocked || active || document.visibilityState === "hidden")
      return;
    clearTimeout(reconnect);
    reconnect = undefined;
    const controller = new AbortController();
    active = controller;
    options.onState?.("connecting");
    deadline(controller, 20000);
    void connect(controller);
  };
  const connect = async (controller: AbortController) => {
    let permanent = false;
    try {
      const headers = new Headers({
        Accept: "text/event-stream",
        "X-Request-ID": newIdempotencyKey(),
      });
      if (cursor) headers.set("Last-Event-ID", cursor);
      const response = await fetch(`/api/v1/tasks/${taskId}/events/stream`, {
        credentials: "same-origin",
        cache: "no-store",
        headers,
        signal: controller.signal,
      });
      if (!current(controller)) return;
      if (!response.ok || response.status === 204) {
        // 无内容或不可恢复的客户端错误停止重连；408/429 仍按暂时性故障处理。
        permanent =
          response.status === 204 ||
          (response.status >= 400 &&
            response.status < 500 &&
            ![408, 429].includes(response.status));
        if (response.status === 401)
          window.dispatchEvent(new Event("drop:unauthorized"));
        if (permanent) blocked = true;
        await response.body?.cancel();
        return;
      }
      if (
        response.headers
          .get("Content-Type")
          ?.split(";")[0]
          .trim()
          .toLowerCase() !== "text/event-stream" ||
        !response.body
      )
        throw new StreamProtocolError("Invalid SSE response");
      options.onState?.("live");
      deadline(controller, 45000);
      const parser = new EventStreamDecoder((event) => {
        if (!current(controller)) return;
        if (!validCursor(event.lastEventId))
          throw new StreamProtocolError("Invalid SSE cursor");
        if (cursor && BigInt(event.lastEventId) <= BigInt(cursor)) return;
        cursor = event.lastEventId;
        failures = 0;
        options.onEvent(event);
      }, cursor);
      const reader = response.body.getReader();
      try {
        while (current(controller)) {
          const chunk = await reader.read();
          if (!current(controller)) return;
          if (chunk.done) {
            parser.finish();
            break;
          }
          deadline(controller, 45000);
          parser.push(chunk.value);
        }
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    } catch (error) {
      permanent = error instanceof StreamProtocolError;
    } finally {
      if (active !== controller) return;
      active = undefined;
      clearTimeout(timeout);
      timeout = undefined;
      if (disposed || document.visibilityState === "hidden") return;
      blocked = permanent;
      options.onState?.("polling");
      if (!blocked) {
        // 指数退避限制故障时的连接风暴；收到有效新事件后 failures 会归零。
        const delay = Math.min(30000, 1000 * 2 ** Math.min(failures, 5));
        failures = Math.min(failures + 1, 16);
        reconnect = setTimeout(start, delay);
      }
    }
  };
  // 页面隐藏主动释放流与重连定时器；恢复可见时重新从已提交游标连接。
  const visibility = () => {
    if (document.visibilityState === "hidden") {
      clearTimeouts();
      const connection = active;
      active = undefined;
      connection?.abort(new DOMException("Page hidden", "AbortError"));
      options.onState?.("paused");
    } else start();
  };
  document.addEventListener("visibilitychange", visibility);
  start();
  // React effect 的清理函数负责路由退出；仅停止浏览器订阅，不代表取消后台采集任务。
  return () => {
    disposed = true;
    document.removeEventListener("visibilitychange", visibility);
    clearTimeouts();
    const connection = active;
    active = undefined;
    connection?.abort(new DOMException("Navigation canceled", "AbortError"));
  };
}
