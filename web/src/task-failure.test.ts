/**
 * 导入任务元数据验证失败阶段、最新轮次制品和权限恢复入口，不执行真实任务。
 */
import { describe, expect, it } from "vitest";
import type { Artifact, Task, TaskEvent } from "./types";
import {
  currentSavedArtifacts,
  failureDetails,
  taskStages,
  timelineTone,
} from "./task-failure";

// 仅使用导入的元数据夹具；本单测不会启动浏览器、网络请求或采集器。
const task = (changes: Partial<Task> = {}): Task => ({
  id: "7",
  name: "imported failed task",
  kind: "PERF_CPU",
  params: {},
  creator_id: "1",
  target_agent_id: "fixture-agent",
  created_at: "2026-10-18T00:00:00Z",
  updated_at: "2026-10-18T00:02:00Z",
  collection_status: "COLLECTED",
  analysis_status: "FAILED",
  request_id: "request-7",
  error_code: "INVALID_INPUT",
  error_message: "Imported failure",
  attempts: [
    { id: "20", attempt: 2, status: "COLLECTED" },
    { id: "10", attempt: 1, status: "FAILED" },
  ],
  ...changes,
});
const artifact = (changes: Partial<Artifact> = {}): Artifact => ({
  id: "31",
  attempt_id: "20",
  kind: "RAW",
  name: "profile.raw",
  size: 12,
  sha256: "a".repeat(64),
  content_type: "application/octet-stream",
  created_at: "2026-10-18T00:01:00Z",
  status: "ready",
  download_allowed: true,
  ...changes,
});
const event = (changes: Partial<TaskEvent> = {}): TaskEvent => ({
  sequence: "9",
  attempt_id: "20",
  type: "ANALYSIS_FAILED",
  source: "analyzer",
  payload: { retryable: false, dead_letter: true },
  created_at: "2026-10-18T00:02:00Z",
  ...changes,
});
const now = Date.parse("2026-10-18T00:03:00Z");

describe("current execution failure evidence", () => {
  it("never paints failure, cancellation or ordinary progress as success", () => {
    expect(timelineTone("TASK_FAILED")).toBe("failed");
    expect(timelineTone("ANALYSIS_FAILED")).toBe("failed");
    expect(timelineTone("TASK_CANCELED")).toBe("canceled");
    expect(timelineTone("ANALYSIS_PROGRESS")).toBe("neutral");
    expect(timelineTone("ANALYSIS_SUCCEEDED")).toBe("success");
  });
  it("lists all seven stages and marks analysis failure without a ready result", () => {
    const stages = taskStages(task());
    expect(stages).toHaveLength(7);
    expect(stages.map((s) => s.label)).toEqual([
      "已创建",
      "等待下发",
      "Agent 已接收",
      "采集中",
      "原始数据已保存",
      "分析中",
      "结果可用",
    ]);
    expect(stages[5].state).toBe("failed");
    expect(stages[6].state).toBe("pending");
    expect(
      taskStages(task({ analysis_status: "SUCCESS" })).every(
        (s) => s.state === "done",
      ),
    ).toBe(true);
  });
  it("retains explicit import provenance in seven-stage labels", () => {
    expect(
      taskStages(
        task({ kind: "IMPORT_COLLAPSED", params: { source: "import" } }),
      )[2].label,
    ).toBe("导入已接收");
  });
  it("does not use a prior attempt's runner event to locate current failure", () => {
    const value = task({
      collection_status: "FAILED",
      attempts: [{ id: "20", attempt: 2, status: "FAILED" }],
    });
    const failure = failureDetails(
      value,
      [event({ attempt_id: "10", type: "RUNNER_STARTED" })],
      { operate: true, reanalyze: false },
      now,
    )!;
    expect(failure.stage).toBe("采集（具体步骤未记录）");
    expect(failure.requestId).toBe("request-7");
    expect(failure.attemptId).toBe("20");
  });
  it("uses current attempt collection evidence without labelling it analysis", () => {
    const value = task({
      collection_status: "FAILED",
      attempts: [{ id: "20", attempt: 2, status: "FAILED" }],
    });
    expect(
      failureDetails(
        value,
        [event({ type: "RUNNER_STARTED" })],
        { operate: true, reanalyze: false },
        now,
      )!.stage,
    ).toBe("采集");
  });
  it("selects only current ready authorized unexpired saved collection files", () => {
    const value = task({
      artifacts: [
        artifact(),
        artifact({ id: "32", attempt_id: "10" }),
        artifact({ id: "33", kind: "RESULT" }),
        artifact({ id: "34", kind: "MANIFEST" }),
        artifact({ id: "35", status: "missing" }),
        artifact({ id: "36", download_allowed: false }),
        artifact({ id: "37", expires_at: "2026-10-17T00:00:00Z" }),
        artifact({ id: "38", expires_at: "invalid" }),
        artifact({ id: "39", kind: "LOG" }),
        artifact({ id: "40", kind: "BINARY" }),
      ],
    });
    expect(currentSavedArtifacts(value, now).map((a) => a.id)).toEqual([
      "31",
      "39",
      "40",
    ]);
  });
  it("rejects incomplete metadata and does not fall back to an old attempt", () => {
    expect(
      currentSavedArtifacts(
        task({
          artifacts: [
            artifact({ download_allowed: undefined }),
            artifact({ status: undefined }),
          ],
        }),
        now,
      ),
    ).toEqual([]);
    expect(
      currentSavedArtifacts(
        task({ attempts: [], artifacts: [artifact()] }),
        now,
      ),
    ).toEqual([]);
  });
  it("fails closed on an invalid newest attempt rather than using valid historical files", () => {
    expect(
      currentSavedArtifacts(
        task({
          attempts: [
            { id: "invalid-current", attempt: 2, status: "FAILED" },
            { id: "10", attempt: 1, status: "COLLECTED" },
          ],
          artifacts: [artifact({ attempt_id: "10" })],
        }),
        now,
      ),
    ).toEqual([]);
  });
  it("distinguishes automatic retry exhaustion from permitted manual reanalysis", () => {
    const value = task({ artifacts: [artifact()] });
    const failure = failureDetails(
      value,
      [event()],
      { operate: true, reanalyze: true },
      now,
    )!;
    expect(failure.retryLabel).toContain("自动重试已结束");
    expect(failure.action).toBe("reanalyze");
    expect(failure.recommendation).toContain("原始");
  });
  it("never offers manual mutation to a read-only caller or reanalysis without RAW", () => {
    expect(
      failureDetails(
        task({ artifacts: [artifact()] }),
        [],
        { operate: false, reanalyze: false },
        now,
      )!.action,
    ).toBeNull();
    expect(
      failureDetails(
        task({ artifacts: [] }),
        [],
        { operate: true, reanalyze: true },
        now,
      )!.action,
    ).toBe("retry");
  });
  it("offers reimport for imported failed input without claiming collection retry", () => {
    expect(
      failureDetails(
        task({ kind: "IMPORT_COLLAPSED", params: { source: "import" } }),
        [],
        { operate: true, reanalyze: false },
        now,
      )!.action,
    ).toBe("reimport");
  });
  it("ignores unknown or unsafe trace metadata and bounds diagnostic text", () => {
    const failure = failureDetails(
      task({ request_id: "bad\nrequest", error_message: "x".repeat(1000) }),
      [],
      { operate: false, reanalyze: false },
      now,
    )!;
    expect(failure.requestId).toBe("");
    expect(failure.message.length).toBeLessThanOrEqual(512);
    expect(failure.retryLabel).toContain("未提供");
  });
  it("does not retain a previous attempt failure panel after retry or success", () => {
    expect(
      failureDetails(
        task({
          collection_status: "RUNNING",
          analysis_status: "PENDING",
          error_code: undefined,
        }),
        [event({ attempt_id: "10" })],
        { operate: true, reanalyze: false },
        now,
      ),
    ).toBeUndefined();
    expect(
      failureDetails(
        task({ analysis_status: "SUCCESS", error_code: undefined }),
        [],
        { operate: true, reanalyze: true },
        now,
      ),
    ).toBeUndefined();
  });
});
