/**
 * SSE 协议与生命周期的导入字节夹具：覆盖分块、游标精度、上限、断线与可见性。
 * 网络和时钟可控，但被测解码器与订阅逻辑使用真实生产代码。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { EventStreamDecoder, subscribeTaskEvents } from "./event-stream";

const encoder = new TextEncoder();
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("bounded SSE decoding", () => {
  it("preserves split UTF-8, CRLF, comments, multiline data and exact string IDs", () => {
    const events: unknown[] = [],
      decoder = new EventStreamDecoder((event) => events.push(event));
    const bytes = encoder.encode(
      ": keepalive\r\nid: 9007199254740993\r\nevent: task_status\r\ndata: 中文\r\ndata: next\r\n\r\n",
    );
    for (const byte of bytes) decoder.push(Uint8Array.of(byte));
    decoder.finish();
    expect(events).toEqual([
      {
        type: "task_status",
        data: "中文\nnext",
        lastEventId: "9007199254740993",
      },
    ]);
  });
  it("never publishes incomplete frames or invents data from comments and IDs", () => {
    const events: unknown[] = [],
      decoder = new EventStreamDecoder((event) => events.push(event));
    decoder.push(encoder.encode(": ping\n\nid: 7\n\ndata: unfinished"));
    decoder.finish();
    expect(events).toEqual([]);
  });
  it("rejects malformed UTF-8 and oversized frames before dispatch", () => {
    const events: unknown[] = [],
      decoder = new EventStreamDecoder((event) => events.push(event));
    expect(() => decoder.push(Uint8Array.of(0xff))).toThrow();
    const oversized = new EventStreamDecoder((event) => events.push(event));
    expect(() =>
      oversized.push(encoder.encode("data: " + "中".repeat(22000) + "\n\n")),
    ).toThrow(/limit/i);
    const boundary = new EventStreamDecoder((event) => events.push(event));
    boundary.push(encoder.encode("data: " + "a".repeat(65528) + "\n\n"));
    expect(events).toHaveLength(1);
    const crlfBoundary = new EventStreamDecoder((event) => events.push(event));
    crlfBoundary.push(
      encoder.encode("data: " + "a".repeat(65526) + "\r\n\r\n"),
    );
    expect(events).toHaveLength(2);
    const crlfOversized = new EventStreamDecoder((event) => events.push(event));
    expect(() =>
      crlfOversized.push(
        encoder.encode("data: " + "a".repeat(65527) + "\r\n\r\n"),
      ),
    ).toThrow(/limit/i);
    expect(events).toHaveLength(2);
    const truncated = new EventStreamDecoder((event) => events.push(event));
    truncated.push(Uint8Array.of(0xe4, 0xb8));
    expect(() => truncated.finish()).toThrow(/UTF-8/);
    expect(events).toHaveLength(2);
  });
  it("handles CR-only frames and ignores NUL IDs and event-source retry fields", () => {
    const events: unknown[] = [],
      decoder = new EventStreamDecoder((event) => events.push(event));
    decoder.push(
      encoder.encode(
        "id: 4\rdata: first\r\rid: bad\0id\rretry: 0\rdata: second\r\r",
      ),
    );
    decoder.finish();
    expect(events).toEqual([
      { type: "message", data: "first", lastEventId: "4" },
      { type: "message", data: "second", lastEventId: "4" },
    ]);
  });
});

function environment() {
  const document = Object.assign(new EventTarget(), {
    visibilityState: "visible",
  });
  const window = new EventTarget();
  vi.stubGlobal("document", document);
  vi.stubGlobal("window", window);
  return {
    document,
    window,
    hidden(value: boolean) {
      document.visibilityState = value ? "hidden" : "visible";
      document.dispatchEvent(new Event("visibilitychange"));
    },
  };
}
function stream(text = "") {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        if (text) controller.enqueue(encoder.encode(text));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}
function waitForAbort(signal: AbortSignal): Promise<never> {
  return new Promise((_, reject) => {
    if (signal.aborted) reject(signal.reason);
    else
      signal.addEventListener("abort", () => reject(signal.reason), {
        once: true,
      });
  });
}

describe("SSE connection lifecycle", () => {
  it("backs off repeated EOF/errors, resumes the exact cursor and drops replayed events", async () => {
    vi.useFakeTimers();
    environment();
    const events: unknown[] = [],
      fetch = vi
        .fn()
        .mockResolvedValueOnce(
          stream("id: 9007199254740993\nevent: task_status\ndata: first\n\n"),
        )
        .mockResolvedValueOnce(new Response("unavailable", { status: 503 }))
        .mockResolvedValueOnce(
          stream(
            "id: 9007199254740993\nevent: task_status\ndata: duplicate\n\nid: 9007199254740994\nevent: task_status\ndata: next\n\n",
          ),
        )
        .mockImplementation((_: string, options: RequestInit) =>
          waitForAbort(options.signal!),
        );
    vi.stubGlobal("fetch", fetch);
    const close = subscribeTaskEvents("7", {
      onEvent: (event) => events.push(event),
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(999);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(fetch.mock.calls[1][1].headers.get("Last-Event-ID")).toBe(
      "9007199254740993",
    );
    expect(fetch.mock.calls[1][1].credentials).toBe("same-origin");
    expect(fetch.mock.calls[1][1].headers.has("X-CSRF-Token")).toBe(false);
    expect(events).toEqual([
      { type: "task_status", data: "first", lastEventId: "9007199254740993" },
      { type: "task_status", data: "next", lastEventId: "9007199254740994" },
    ]);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetch.mock.calls[3][1].headers.get("Last-Event-ID")).toBe(
      "9007199254740994",
    );
    close();
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("aborts on hidden/unmount, suspends reconnects and clears all timers/listeners", async () => {
    vi.useFakeTimers();
    const env = environment();
    const fetch = vi.fn((_: string, options: RequestInit) =>
      waitForAbort(options.signal!),
    );
    vi.stubGlobal("fetch", fetch);
    const close = subscribeTaskEvents("7", { onEvent: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    env.hidden(true);
    await vi.advanceTimersByTimeAsync(60000);
    expect(fetch.mock.calls[0][1].signal!.aborted).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    env.hidden(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(2);
    close();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch.mock.calls[1][1].signal!.aborted).toBe(true);
    env.hidden(true);
    env.hidden(false);
    await vi.advanceTimersByTimeAsync(60000);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("backs off repeated empty 200 responses and caps reconnect delays at 30 seconds", async () => {
    vi.useFakeTimers();
    environment();
    const fetch = vi.fn(async () => stream());
    vi.stubGlobal("fetch", fetch);
    const close = subscribeTaskEvents("7", { onEvent: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    for (const delay of [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]) {
      const before = fetch.mock.calls.length;
      await vi.advanceTimersByTimeAsync(delay - 1);
      expect(fetch).toHaveBeenCalledTimes(before);
      await vi.advanceTimersByTimeAsync(1);
      expect(fetch).toHaveBeenCalledTimes(before + 1);
    }
    close();
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each(["invalid-utf8", "oversized-frame", "wrong-content-type"])(
    "stops %s without publishing or repeatedly reconnecting",
    async (kind) => {
      vi.useFakeTimers();
      environment();
      const bytes =
        kind === "invalid-utf8"
          ? Uint8Array.of(0xff)
          : encoder.encode("data: " + "a".repeat(65536) + "\n\n");
      const fetch = vi.fn().mockResolvedValue(
        new Response(bytes, {
          headers: {
            "Content-Type":
              kind === "wrong-content-type"
                ? "text/event-stream-evil"
                : "text/event-stream",
          },
        }),
      );
      vi.stubGlobal("fetch", fetch);
      const events = vi.fn(),
        close = subscribeTaskEvents("7", { onEvent: events });
      await vi.advanceTimersByTimeAsync(120000);
      expect(fetch).toHaveBeenCalledTimes(1);
      expect(events).not.toHaveBeenCalled();
      close();
      expect(vi.getTimerCount()).toBe(0);
    },
  );
  it.each([401, 403, 404])(
    "stops permanent HTTP %i without spinning or announcing a live stream",
    async (status) => {
      vi.useFakeTimers();
      const env = environment(),
        unauthorized = vi.fn();
      env.window.addEventListener("drop:unauthorized", unauthorized);
      const states: string[] = [],
        fetch = vi.fn().mockResolvedValue(new Response("denied", { status }));
      vi.stubGlobal("fetch", fetch);
      const close = subscribeTaskEvents("7", {
        onEvent: vi.fn(),
        onState: (state) => states.push(state),
      });
      await vi.advanceTimersByTimeAsync(60000);
      env.hidden(true);
      env.hidden(false);
      await vi.advanceTimersByTimeAsync(60000);
      expect(fetch).toHaveBeenCalledTimes(1);
      expect(states).not.toContain("live");
      expect(unauthorized).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
      close();
      expect(vi.getTimerCount()).toBe(0);
    },
  );
  it("bounds header stalls at 20s and idle streams at 45s, then retries", async () => {
    vi.useFakeTimers();
    environment();
    const fetch = vi.fn((_: string, options: RequestInit) =>
      waitForAbort(options.signal!),
    );
    vi.stubGlobal("fetch", fetch);
    const close = subscribeTaskEvents("7", { onEvent: vi.fn() });
    await vi.advanceTimersByTimeAsync(20000);
    expect(fetch.mock.calls[0][1].signal!.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetch).toHaveBeenCalledTimes(2);
    close();
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(0);
    const idle = vi.fn((_: string, options: RequestInit) =>
      Promise.resolve(
        new Response(
          new ReadableStream({
            start(controller) {
              options.signal!.addEventListener(
                "abort",
                () => controller.error(options.signal!.reason),
                { once: true },
              );
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );
    vi.stubGlobal("fetch", idle);
    const closeIdle = subscribeTaskEvents("7", { onEvent: vi.fn() });
    await vi.advanceTimersByTimeAsync(45000);
    expect(idle.mock.calls[0][1].signal!.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(idle).toHaveBeenCalledTimes(2);
    closeIdle();
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("stops malformed protocol and invalid cursor frames without emitting partial success", async () => {
    vi.useFakeTimers();
    environment();
    const events = vi.fn(),
      fetch = vi
        .fn()
        .mockResolvedValue(
          stream("id: 9223372036854775808\ndata: invalid cursor\n\n"),
        );
    vi.stubGlobal("fetch", fetch);
    const close = subscribeTaskEvents("7", { onEvent: events });
    await vi.advanceTimersByTimeAsync(60000);
    expect(events).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
    close();
    expect(vi.getTimerCount()).toBe(0);
  });
});
