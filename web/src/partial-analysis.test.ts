import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../../contracts/fixtures/partial-result-v1.json";
import type { Task } from "./types";
import {
  loadPartialAnalysis,
  partialIdentity,
  partialQueryKey,
  validatePartialAnalysis,
} from "./partial-analysis";

function task(): Task {
  return {
    id: fixture.task_id,
    version: 10,
    name: "Imported synthetic partial fixture",
    kind: fixture.kind,
    params: { source: "import" },
    collection_status: "COLLECTED",
    analysis_status: "FAILED",
    creator_id: "fixture-user",
    created_at: "2026-10-18T00:00:00Z",
    updated_at: "2026-10-18T00:01:00Z",
    attempts: [{ id: fixture.attempt_id, attempt: 2, status: "COLLECTED" }],
    analysis_identity: {
      analysis_job_id: fixture.analysis_job_id,
      analysis_run: fixture.analysis_run,
      input_artifact_id: fixture.input_artifact_id,
      input_digest: fixture.input_digest,
    },
    artifacts: [
      {
        id: fixture.input_artifact_id,
        task_id: fixture.task_id,
        attempt_id: fixture.attempt_id,
        kind: "RAW",
        name: "synthetic.folded",
        status: "ready",
        sha256: fixture.input_digest,
        size: 60,
        content_type: "text/plain",
        created_at: "2026-10-18T00:00:00Z",
      },
    ],
  };
}
const payload = () => structuredClone(fixture);
const anchor = () => partialIdentity(task())!;
const response = (
  data: unknown = fixture,
  status = 200,
  code = "PARTIAL_RESULT_NOT_READY",
) =>
  new Response(
    JSON.stringify({
      request_id: "imported-fixture",
      data: status === 200 ? data : null,
      error:
        status === 200 ? null : { code, message: "Imported fixture error" },
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("current failed-run partial analysis boundaries", () => {
  it("accepts the shared explicitly imported synthetic contract fixture", () => {
    expect(validatePartialAnalysis(payload(), anchor())).toEqual(fixture);
    expect(partialIdentity(task())?.analysis_run).toBe(3);
  });
  it("never enables for running, canceled, successful, or unanchored tasks", () => {
    for (const update of [
      { analysis_status: "RUNNING" },
      { collection_status: "CANCELED" },
      { analysis_status: "SUCCESS" },
      { collection_status: "FAILED" },
      { analysis_identity: undefined },
      { version: undefined },
      { kind: "COMPOSITE" },
    ])
      expect(partialIdentity({ ...task(), ...update })).toBeUndefined();
  });
  it("does not fall back to an older valid attempt when the newest identity is invalid", () => {
    const current = task();
    current.attempts!.push({ id: "invalid", attempt: 3, status: "COLLECTED" });
    expect(partialIdentity(current)).toBeUndefined();
  });
  it("binds the latest ready RAW rather than an older input with a matching digest", () => {
    const current = task();
    current.artifacts!.push({
      ...current.artifacts![0],
      id: "41",
      sha256: "b".repeat(64),
    });
    expect(partialIdentity(current)).toBeUndefined();
  });
  it("separates query keys by task version, job/run and input", () => {
    const first = task();
    const newer = task();
    newer.version = 11;
    newer.analysis_identity!.analysis_run = 4;
    expect(partialQueryKey(first)).not.toEqual(partialQueryKey(newer));
  });
  it("rejects old task, attempt, job, run, input and kind bindings", () => {
    for (const [key, value] of Object.entries({
      task_id: "8",
      attempt_id: "10",
      analysis_job_id: "29",
      analysis_run: 2,
      input_artifact_id: "39",
      input_digest: "b".repeat(64),
      kind: "PERF_CPU",
      completeness: "complete",
      schema_version: 2,
    }))
      expect(() =>
        validatePartialAnalysis({ ...payload(), [key]: value }, anchor()),
      ).toThrow("当前执行不符");
  });
  it("rejects unknown top-level/nested keys and missing required fields", () => {
    const extra = payload() as Record<string, unknown>;
    extra.flamegraph = { name: "RAW_MUST_NOT_LEAK" };
    expect(() => validatePartialAnalysis(extra, anchor())).toThrow();
    const nested = payload();
    Object.assign(nested.summary, { tool_report: "private-path" });
    expect(() => validatePartialAnalysis(nested, anchor())).toThrow();
    const missing = payload() as Record<string, unknown>;
    delete missing.input_digest;
    expect(() => validatePartialAnalysis(missing, anchor())).toThrow();
  });
  it("rejects nonfinite, unsafe, negative and invalid count/depth values", () => {
    for (const value of [
      NaN,
      Infinity,
      -1,
      Number.MAX_SAFE_INTEGER + 1,
      true,
    ]) {
      const data = payload();
      Object.assign(data.summary, { total_samples: value });
      expect(() => validatePartialAnalysis(data, anchor())).toThrow();
    }
    for (const [key, value] of Object.entries({
      unique_stacks: 100001,
      node_count: 100001,
      max_depth: 129,
    })) {
      const data = payload();
      Object.assign(data.summary, { [key]: value });
      expect(() => validatePartialAnalysis(data, anchor())).toThrow();
    }
  });
  it("bounds Top K and percentages while preserving authorized escaped symbol text", () => {
    const data = payload();
    data.top_functions[0].name = '<img src=x onerror="alert(1)">';
    expect(
      validatePartialAnalysis(data, anchor()).top_functions[0].name,
    ).toContain("<img");
    data.top_functions = Array.from(
      { length: 21 },
      () => data.top_functions[0],
    );
    expect(() => validatePartialAnalysis(data, anchor())).toThrow();
    const over = payload();
    over.top_functions[0].total_percent = 100.01;
    expect(() => validatePartialAnalysis(over, anchor())).toThrow();
  });
  it("enforces UTF-8 byte name limits and rejects Unicode control/format characters", () => {
    const data = payload();
    data.top_functions[0].name = "函".repeat(53);
    expect(() => validatePartialAnalysis(data, anchor())).not.toThrow();
    for (const name of [
      "函".repeat(54),
      "a\u0000b",
      "a\u202eb",
      "\ud800",
      "",
    ]) {
      data.top_functions[0].name = name;
      expect(() => validatePartialAnalysis(data, anchor())).toThrow();
    }
  });
  it("accepts bounded ASCII units and unknown source without exposing arbitrary unit/path strings", () => {
    const data = payload();
    data.summary.unit = "cpu.cycles-1";
    data.summary.source = "unknown";
    expect(() => validatePartialAnalysis(data, anchor())).not.toThrow();
    for (const unit of ["/tmp/private", "a".repeat(33), "字节"]) {
      data.summary.unit = unit;
      expect(() => validatePartialAnalysis(data, anchor())).toThrow();
    }
  });
  it("loads twenty maximum-byte symbols containing HTML metacharacters and Unicode as inert data", async () => {
    const data = payload();
    const name = '&<>"函'.repeat(22) + "abcdef";
    expect(new TextEncoder().encode(name).byteLength).toBe(160);
    data.top_functions = Array.from({ length: 20 }, () => ({
      ...data.top_functions[0],
      name,
    }));
    const encoded = JSON.stringify(data);
    expect(new TextEncoder().encode(encoded).byteLength).toBeLessThanOrEqual(
      16384,
    );
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(data)));
    expect((await loadPartialAnalysis(task()))?.top_functions[19].name).toBe(
      name,
    );
  });
  it("requires exact checkpoint stages instead of implying later execution never happened", () => {
    const data = payload();
    data.completed_stages.push("rules");
    expect(() => validatePartialAnalysis(data, anchor())).toThrow();
  });
  it("loads only the dedicated endpoint with same-origin cookies and no redirects/cache", async () => {
    const fetch = vi.fn().mockResolvedValue(response());
    vi.stubGlobal("fetch", fetch);
    expect(await loadPartialAnalysis(task())).toEqual(fixture);
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toBe("/api/v1/tasks/7/partial-result");
    expect(fetch.mock.calls[0][1]).toMatchObject({
      credentials: "same-origin",
      redirect: "error",
      cache: "no-store",
    });
  });
  it("treats only the exact no-checkpoint 409 as empty, never falls back to /result", async () => {
    const fetch = vi.fn().mockResolvedValue(response(null, 409));
    vi.stubGlobal("fetch", fetch);
    expect(await loadPartialAnalysis(task())).toBeNull();
    expect(fetch).toHaveBeenCalledOnce();
  });
  it("does not retry forbidden, hidden, or mismatched failures", async () => {
    for (const status of [403, 404, 409]) {
      const fetch = vi
        .fn()
        .mockResolvedValue(response(null, status, "OTHER_ERROR"));
      vi.stubGlobal("fetch", fetch);
      await expect(loadPartialAnalysis(task())).rejects.toMatchObject({
        status,
      });
      expect(fetch).toHaveBeenCalledOnce();
    }
  });
  it("rejects invalid or canceled identities before starting a request", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const canceled = new AbortController();
    canceled.abort(new DOMException("Navigation", "AbortError"));
    await expect(
      loadPartialAnalysis({ ...task(), analysis_status: "SUCCESS" }),
    ).rejects.toThrow();
    await expect(
      loadPartialAnalysis(task(), canceled.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("caps actual streamed bytes regardless of misleading content length and drops raw error text", async () => {
    const raw = "Cookie=private X-Amz-Signature=private";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(raw.repeat(1000), {
          headers: { "Content-Length": "1" },
        }),
      ),
    );
    await expect(loadPartialAnalysis(task())).rejects.toMatchObject({
      code: "PARTIAL_RESULT_UNAVAILABLE",
    });
  });
  it("rejects invalid UTF-8/JSON without reflecting its contents", async () => {
    for (const data of [
      new Uint8Array([0xff]),
      new TextEncoder().encode("Cookie=private"),
    ]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(data)));
      await expect(loadPartialAnalysis(task())).rejects.toMatchObject({
        code: "PARTIAL_RESULT_INVALID",
      });
    }
  });
  it("propagates navigation cancellation through the active request", async () => {
    const caller = new AbortController();
    let aborted = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url, options: RequestInit) =>
          new Promise((_resolve, reject) =>
            options.signal!.addEventListener("abort", () => {
              aborted = true;
              reject(new DOMException("Aborted", "AbortError"));
            }),
          ),
      ),
    );
    const result = loadPartialAnalysis(task(), caller.signal);
    caller.abort(new DOMException("Navigation", "AbortError"));
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
    expect(aborted).toBe(true);
  });
  it("enforces the load deadline with a fixed safe error code", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url, options: RequestInit) =>
          new Promise((_resolve, reject) =>
            options.signal!.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            ),
          ),
      ),
    );
    const result = loadPartialAnalysis(task());
    const checked = expect(result).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
    });
    await vi.advanceTimersByTimeAsync(20000);
    await checked;
  });
});
