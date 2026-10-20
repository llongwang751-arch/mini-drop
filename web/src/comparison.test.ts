/**
 * 使用明确构造的调用树检查比较口径、递归聚合和非法权重拒绝，不把夹具数值当实测性能。
 */
import { describe, expect, it } from "vitest";
import type { AnalysisResult, Task, FlameNode } from "./types";
import {
  compareProfiles,
  comparisonEligibility,
  comparisonRows,
} from "./comparison";

const task = (id: string, kind = "PERF_CPU"): Task => ({
  id,
  kind,
  name: "Imported comparison fixture",
  creator_id: "fixture",
  params: {},
  collection_status: "COLLECTED",
  analysis_status: "SUCCESS",
  created_at: "",
  updated_at: "",
});
const result = (
  tree: FlameNode,
  extra: Partial<AnalysisResult> = {},
): AnalysisResult => ({
  schema_version: 1,
  kind: "PERF_CPU",
  total_samples: tree.value,
  unique_stacks: 2,
  node_count: 3,
  max_depth: 2,
  flamegraph: tree,
  top_functions: [],
  suggestions: [],
  summary: {
    duration_seconds: 2,
    frequency_hz: 99,
    unit: "samples",
    source: "import",
    limitations: [],
  },
  ...extra,
});
const tree = (a: number, b: number): FlameNode => ({
  name: "root",
  value: a + b,
  self: 0,
  children: [
    { name: "worker", value: a, self: a, children: [] },
    { name: "io", value: b, self: b, children: [] },
  ],
});

describe("comparison of actual profiles", () => {
  it("normalizes shares independently instead of treating larger captures as a regression", () => {
    const profile = compareProfiles(
      task("1"),
      result(tree(80, 20)),
      task("2"),
      result(tree(160, 40)),
    );
    const row = comparisonRows(profile, "self").find(
      (item) => item.name === "worker",
    )!;
    expect(row.baseline).toBe(80);
    expect(row.candidate).toBe(160);
    expect(row.deltaPercentagePoints).toBe(0);
  });
  it("shows added and removed functions with signed percentage-point changes", () => {
    const profile = compareProfiles(
      task("1"),
      result(tree(100, 0)),
      task("2"),
      result(tree(0, 200)),
    );
    const rows = comparisonRows(profile, "self");
    expect(rows.find((row) => row.name === "io")?.deltaPercentagePoints).toBe(
      100,
    );
    expect(
      rows.find((row) => row.name === "worker")?.deltaPercentagePoints,
    ).toBe(-100);
  });
  it("counts recursive function inclusive weight once per stack, and sums self weight", () => {
    const recursive = {
      name: "root",
      value: 10,
      self: 0,
      children: [
        {
          name: "recur",
          value: 10,
          self: 2,
          children: [{ name: "recur", value: 8, self: 8, children: [] }],
        },
      ],
    };
    const profile = compareProfiles(
      task("1"),
      result(recursive),
      task("2"),
      result(recursive),
    );
    expect(profile.baseline.functions.get("recur")).toEqual({
      self: 10,
      total: 10,
    });
    expect(profile.baseline.functions.has("root")).toBe(false);
  });
  it("rejects unfinished, same-ID, different-kind, different-unit and different metric profiles", () => {
    expect(comparisonEligibility([task("1"), task("1")])).toContain("不同");
    expect(
      comparisonEligibility([
        task("1"),
        { ...task("2"), analysis_status: "RUNNING" },
      ]),
    ).toContain("完成");
    expect(comparisonEligibility([task("1"), task("2", "GO_HEAP")])).toContain(
      "类型",
    );
    expect(() =>
      compareProfiles(
        task("1"),
        result(tree(1, 1)),
        task("2"),
        result(tree(1, 1), {
          summary: {
            unit: "bytes",
            source: "import",
            limitations: [],
            duration_seconds: null,
            frequency_hz: null,
          },
        }),
      ),
    ).toThrow("单位");
    const heapA = result(tree(1, 1), {
      kind: "GO_HEAP",
      summary: {
        ...result(tree(1, 1)).summary,
        ...{ profile_type: "alloc_space" },
      },
    });
    const heapB = result(tree(1, 1), {
      kind: "GO_HEAP",
      summary: {
        ...result(tree(1, 1)).summary,
        ...{ profile_type: "inuse_space" },
      },
    });
    expect(() =>
      compareProfiles(task("1", "GO_HEAP"), heapA, task("2", "GO_HEAP"), heapB),
    ).toThrow("口径");
  });
  it("rejects invalid or cyclic trees instead of rendering misleading comparisons", () => {
    const invalid = tree(1, 1);
    invalid.value = 4;
    expect(() =>
      compareProfiles(
        task("1"),
        result(invalid),
        task("2"),
        result(tree(1, 1)),
      ),
    ).toThrow("权重");
    const cyclic = tree(1, 1);
    cyclic.children!.push(cyclic);
    expect(() =>
      compareProfiles(task("1"), result(cyclic), task("2"), result(tree(1, 1))),
    ).toThrow();
  });
  it("does not confuse a real function name with the synthetic root", () => {
    const namedRoot = {
      name: "worker",
      value: 5,
      self: 0,
      children: [{ name: "worker", value: 5, self: 5, children: [] }],
    };
    const profile = compareProfiles(
      task("1"),
      result(namedRoot),
      task("2"),
      result(namedRoot),
    );
    expect(profile.baseline.functions.get("worker")).toEqual({
      self: 5,
      total: 5,
    });
  });
  it("blocks known different perf events in the list before navigation", () => {
    expect(
      comparisonEligibility([
        { ...task("1"), params: { options: { event: "cycles" } } },
        task("2"),
      ]),
    ).toContain("口径");
  });
});
