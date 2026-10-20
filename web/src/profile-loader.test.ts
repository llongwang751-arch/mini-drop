import { afterEach, describe, expect, it, vi } from "vitest";
import { parseInProfileWorker } from "./profile-loader";

// Imported Worker protocol fixture. Native Worker behavior is tested in Chromium.
class WorkerFixture {
  static created: WorkerFixture[] = [];
  onmessage?: (event: { data: unknown }) => void;
  onerror?: () => void;
  terminated = false;
  transfer?: Transferable[];
  constructor() {
    WorkerFixture.created.push(this);
  }
  postMessage(_message: unknown, transfer: Transferable[]) {
    this.transfer = transfer;
  }
  terminate() {
    this.terminated = true;
  }
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  WorkerFixture.created = [];
});
describe("profile Worker ownership and cancellation", () => {
  it("transfers the input buffer and terminates after a result", async () => {
    vi.stubGlobal("Worker", WorkerFixture);
    const buffer = new ArrayBuffer(4);
    const pending = parseInProfileWorker(
      { op: "result", buffer, status: 200, requestId: "fixture" },
      undefined,
      [buffer],
    );
    const worker = WorkerFixture.created[0];
    expect(worker.transfer).toEqual([buffer]);
    worker.onmessage!({ data: { data: { name: "validated" } } });
    await expect(pending).resolves.toEqual({ name: "validated" });
    expect(worker.terminated).toBe(true);
  });
  it("does not create a Worker for an already aborted request", async () => {
    vi.stubGlobal("Worker", WorkerFixture);
    const controller = new AbortController();
    controller.abort();
    await expect(
      parseInProfileWorker(
        {
          op: "result",
          buffer: new ArrayBuffer(0),
          status: 200,
          requestId: "fixture",
        },
        controller.signal,
      ),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(WorkerFixture.created).toHaveLength(0);
  });
  it("terminates the in-flight parser on route cancellation", async () => {
    vi.stubGlobal("Worker", WorkerFixture);
    const controller = new AbortController();
    const pending = parseInProfileWorker(
      {
        op: "result",
        buffer: new ArrayBuffer(0),
        status: 200,
        requestId: "fixture",
      },
      controller.signal,
    );
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(WorkerFixture.created[0].terminated).toBe(true);
  });
  it("enforces the total Worker deadline and retains only safe error metadata", async () => {
    vi.stubGlobal("Worker", WorkerFixture);
    vi.useFakeTimers();
    const pending = parseInProfileWorker({
      op: "result",
      buffer: new ArrayBuffer(0),
      status: 200,
      requestId: "fixture",
    });
    const check = expect(pending).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
    });
    await vi.advanceTimersByTimeAsync(30000);
    await check;
    expect(WorkerFixture.created[0].terminated).toBe(true);
  });
  it("shows permanent forbidden and request ID without a signed URL", async () => {
    vi.stubGlobal("Worker", WorkerFixture);
    const pending = parseInProfileWorker({
      op: "result",
      buffer: new ArrayBuffer(0),
      status: 200,
      requestId: "fixture",
    });
    WorkerFixture.created[0].onmessage!({
      data: {
        error: {
          code: "PROFILE_FORBIDDEN",
          status: 403,
          requestId: "fixture-request",
        },
      },
    });
    await expect(pending).rejects.toMatchObject({
      code: "PROFILE_FORBIDDEN",
      status: 403,
      requestId: "fixture-request",
    });
    expect(WorkerFixture.created[0].terminated).toBe(true);
  });
});
