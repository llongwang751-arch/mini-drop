/**
 * 给批量执行器注入可控读写函数，检查权限刷新、并发上限和逐项结果，避免真实删除或重试。
 */
import { describe, expect, it } from "vitest";
import type { Task } from "./types";
import {
  bulkEligibility,
  runTaskBatch,
  toggleTaskSelection,
} from "./task-bulk";

const task = (id: string, changes: Partial<Task> = {}): Task => ({
  id,
  name: `Fixture ${id}`,
  kind: "PROC_CPU",
  target_agent_id: "fixture-agent",
  creator_id: "fixture-user",
  params: {},
  collection_status: "RUNNING",
  analysis_status: "PENDING",
  created_at: "",
  updated_at: "",
  permissions: {
    read: true,
    operate: true,
    delete: true,
    share: true,
    reanalyze: false,
  },
  ...changes,
});

describe("bounded task selection and per-item authorization", () => {
  it("keeps exact string IDs and caps selection at 50", () => {
    const rows = Array.from({ length: 50 }, (_, n) =>
      task(String(BigInt("9007199254740993") + BigInt(n))),
    );
    expect(toggleTaskSelection(rows, task("extra"))).toHaveLength(50);
    expect(toggleTaskSelection(rows, rows[0])).toHaveLength(49);
    expect(toggleTaskSelection([], rows[0])[0].id).toBe("9007199254740993");
  });
  it("requires current explicit permissions and valid lifecycle states", () => {
    expect(
      bulkEligibility(task("1", { permissions: undefined }), "cancel"),
    ).toContain("权限");
    expect(bulkEligibility(task("1"), "cancel")).toBe("");
    expect(bulkEligibility(task("1"), "delete")).toContain("结束");
    expect(
      bulkEligibility(task("1", { collection_status: "FAILED" }), "retry"),
    ).toBe("");
    expect(
      bulkEligibility(
        task("1", { collection_status: "FAILED", target_agent_id: undefined }),
        "retry",
      ),
    ).toContain("重新导入");
    expect(
      bulkEligibility(
        task("1", {
          collection_status: "COLLECTED",
          analysis_status: "SUCCESS",
        }),
        "cancel",
      ),
    ).toContain("终态");
  });
  it("refreshes each task, never mutates forbidden objects, and reports partial failure", async () => {
    const executed: string[] = [];
    const rows = [task("1"), task("2"), task("3")];
    const report = await runTaskBatch(rows, "cancel", {
      load: async (id) =>
        task(
          id,
          id === "2"
            ? {
                permissions: {
                  read: true,
                  operate: false,
                  delete: false,
                  share: false,
                  reanalyze: false,
                },
              }
            : {},
        ),
      execute: async (current) => {
        executed.push(current.id);
        if (current.id === "3") throw new Error("fixture gateway failure");
      },
    });
    expect(executed.sort()).toEqual(["1", "3"]);
    expect(report.map((row) => row.status)).toEqual([
      "success",
      "skipped",
      "failed",
    ]);
    expect(report[2].message).toBe("fixture gateway failure");
  });
  it("limits concurrent requests to two and rejects oversized batches before any read", async () => {
    let active = 0,
      peak = 0,
      reads = 0;
    const operations = {
      load: async (id: string) => {
        reads++;
        return task(id);
      },
      execute: async () => {
        active++;
        peak = Math.max(peak, active);
        await new Promise((resolve) => setTimeout(resolve, 2));
        active--;
      },
    };
    await runTaskBatch(
      Array.from({ length: 8 }, (_, n) => task(String(n))),
      "cancel",
      operations,
    );
    expect(peak).toBe(2);
    reads = 0;
    await expect(
      runTaskBatch(
        Array.from({ length: 51 }, (_, n) => task(String(n))),
        "cancel",
        operations,
      ),
    ).rejects.toThrow("50");
    expect(reads).toBe(0);
  });
});
