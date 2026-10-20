/**
 * 用可控 fetch 与虚拟时钟检查超时、主动取消、幂等和终态判断；不会请求真实 API。
 * 断言同时检查定时器和监听器释放，避免请求已结束却留下后台工作。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, APIError, isTerminal, setAuth, newIdempotencyKey } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function waitForAbort(signal: AbortSignal): Promise<never> {
  return new Promise((_, reject) => {
    if (signal.aborted) reject(signal.reason);
    else
      signal.addEventListener("abort", () => reject(signal.reason), {
        once: true,
      });
  });
}

describe("bounded API requests", () => {
  it("aborts a stalled network request and reports a retryable timeout", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn((_: string, options: RequestInit) =>
      waitForAbort(options.signal!),
    );
    vi.stubGlobal("fetch", fetch);
    const pending = expect(
      api("/tasks", { timeoutMs: 25 }),
    ).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
      status: 0,
    });
    await vi.advanceTimersByTimeAsync(25);
    await pending;
    expect(fetch.mock.calls[0][1].signal!.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("preserves caller cancellation and removes the timeout", async () => {
    vi.useFakeTimers();
    const caller = new AbortController();
    const reason = new DOMException("Navigation canceled", "AbortError");
    vi.stubGlobal("fetch", (_: string, options: RequestInit) =>
      waitForAbort(options.signal!),
    );
    const pending = expect(
      api("/tasks", { signal: caller.signal }),
    ).rejects.toBe(reason);
    caller.abort(reason);
    await pending;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("also bounds a stalled response body after headers arrive", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", async (_: string, options: RequestInit) => ({
      ok: true,
      status: 200,
      json: () => waitForAbort(options.signal!),
    }));
    const pending = expect(
      api("/tasks", { timeoutMs: 30 }),
    ).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
    });
    await vi.advanceTimersByTimeAsync(30);
    await pending;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cleans up a completed request and keeps its real envelope data", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      async () => new Response(JSON.stringify({ data: { id: "42" } })),
    );
    expect(await api("/tasks/42")).toEqual({ id: "42" });
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("HTTP security and failure contracts", () => {
  it("generates bounded UUID keys without requiring secure-context randomUUID", () => {
    const a = newIdempotencyKey();
    expect(a).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(a).not.toBe(newIdempotencyKey());
  });
  it("preserves an explicit idempotency key across a lost response retry", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Network interrupted"))
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            data: { id: "9007199254740993" },
            error: null,
            request_id: "req-test",
          }),
          { status: 201 },
        ),
      );
    vi.stubGlobal("fetch", fetch);
    setAuth({
      user: {
        id: "u1",
        username: "operator",
        display_name: "Op",
        role: "operator",
      },
      csrf_token: "session-bound-csrf",
    });
    const options = {
      method: "POST",
      body: "{}",
      idempotencyKey: "same-logical-submission",
    };
    await expect(api("/tasks", options)).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      status: 0,
    });
    expect(await api("/tasks", options)).toEqual({ id: "9007199254740993" });
    for (const [, request] of fetch.mock.calls) {
      expect(request.credentials).toBe("same-origin");
      expect(request.headers.get("Idempotency-Key")).toBe(
        "same-logical-submission",
      );
      expect(request.headers.get("X-CSRF-Token")).toBe("session-bound-csrf");
    }
  });
  it("retains a traceable structured error without displaying response HTML", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            data: null,
            error: { code: "AGENT_OFFLINE", message: "目标节点离线" },
            request_id: "req-123",
          }),
          { status: 409 },
        ),
      ),
    );
    try {
      await api("/tasks");
      expect.fail("must reject");
    } catch (error) {
      expect(error).toBeInstanceOf(APIError);
      expect(error).toMatchObject({
        code: "AGENT_OFFLINE",
        requestId: "req-123",
        status: 409,
      });
    }
  });
  it("handles an invalid gateway response as a bounded generic error", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html>gateway</html>", { status: 502 }),
        ),
    );
    await expect(api("/overview")).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 502,
    });
  });
});
describe("independent collection and analysis lifecycle", () => {
  it.each([
    ["COLLECTED", "PENDING", false],
    ["COLLECTED", "RUNNING", false],
    ["COLLECTED", "RETRY", false],
    ["COLLECTED", "SUCCESS", true],
    ["FAILED", "PENDING", true],
    ["CANCELED", "RUNNING", true],
    ["COLLECTED", "FAILED", true],
    ["RUNNING", "PENDING", false],
  ])(
    "%s / %s => terminal=%s",
    (collection_status, analysis_status, terminal) => {
      expect(isTerminal({ collection_status, analysis_status })).toBe(terminal);
    },
  );
});
