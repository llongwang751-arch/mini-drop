import { newIdempotencyKey } from "./api";

export interface StreamEvent {
  type: string;
  data: string;
  lastEventId: string;
}
export type StreamState = "connecting" | "live" | "polling" | "paused";
class StreamProtocolError extends Error {}

/** Streaming UTF-8/SSE framing. An unterminated frame never becomes an event.
 * Bounds include comment, field and delimiter bytes, not just JSON payloads.
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
    // EOF discards an incomplete event; dispatch requires a blank line.
  }
  private decode(chunk?: Uint8Array) {
    try {
      return this.decoder.decode(chunk, { stream: !!chunk });
    } catch {
      throw new StreamProtocolError("Invalid SSE UTF-8");
    }
  }
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
    // retry fields do not override the client's bounded outage policy.
  }
}

function validCursor(value: string) {
  return /^[1-9]\d{0,18}$/.test(value) && BigInt(value) <= 9223372036854775807n;
}

/** One authenticated stream, with bounded retries and an exact int64 cursor.
 * Fetch permits Last-Event-ID on reconnect after a visibility/navigation abort.
 * Fallback REST polling stays independent of this optional live connection.
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
        const delay = Math.min(30000, 1000 * 2 ** Math.min(failures, 5));
        failures = Math.min(failures + 1, 16);
        reconnect = setTimeout(start, delay);
      }
    }
  };
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
  return () => {
    disposed = true;
    document.removeEventListener("visibilitychange", visibility);
    clearTimeouts();
    const connection = active;
    active = undefined;
    connection?.abort(new DOMException("Navigation canceled", "AbortError"));
  };
}
