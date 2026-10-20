/**
 * 调用树纯数据单测：拒绝环、超限和不守恒权重，并确认过滤没有篡改证据口径。
 */
import { describe, expect, it } from "vitest";
import {
  functionRelations,
  parseFlameThreads,
  prepareFlameTree,
} from "./flame-analysis";
import type { FlameNode } from "../types";
const leaf = (name: string, value: number): FlameNode => ({
  name,
  value,
  self: value,
  children: [],
});

describe("evidence based flame transformations", () => {
  it("promotes user frames and retains hidden system self weight at the root", () => {
    const input = {
      name: "all",
      value: 10,
      self: 0,
      children: [
        {
          name: "__libc_start_main",
          self: 2,
          value: 10,
          children: [leaf("business", 8)],
        },
      ],
    };
    const view = prepareFlameTree(input, { hideSystem: true });
    expect(view.hiddenSelf).toBe(2);
    expect(view.hiddenFrames).toBe(1);
    expect(view.root).toMatchObject({
      value: 10,
      self: 2,
      children: [{ name: "business", value: 8 }],
    });
    expect(input.children[0].name).toBe("__libc_start_main");
  });
  it("aggregates self cost without double counting recursive inclusive weights", () => {
    const data = {
      name: "all",
      value: 10,
      self: 0,
      children: [
        {
          name: "recursive",
          value: 10,
          self: 3,
          children: [leaf("recursive", 7)],
        },
      ],
    };
    const view = prepareFlameTree(data, { metric: "self" });
    expect(view.root.children).toEqual([leaf("recursive", 10)]);
    expect(view.root.value).toBe(10);
  });
  it("selects only an explicitly recorded thread root", () => {
    const data = {
      name: "all",
      value: 10,
      self: 0,
      children: [
        leaf("worker [pid=7 tid=8]", 4),
        leaf("worker [pid=7 tid=9]", 6),
      ],
    };
    expect(
      prepareFlameTree(data, { threadRoot: "worker [pid=7 tid=8]" }).root.value,
    ).toBe(4);
    expect(() =>
      prepareFlameTree(data, { threadRoot: "guessed-thread" }),
    ).toThrow("线程");
    expect(parseFlameThreads()).toEqual([]);
    expect(
      parseFlameThreads({
        threads: [
          {
            pid: 7,
            tid: 8,
            comm: "worker",
            root_name: "worker [pid=7 tid=8]",
            sample_count: 4,
          },
          { pid: 7, tid: "9", root_name: "bad" },
        ],
      }),
    ).toHaveLength(1);
  });
  it("derives callers and callees from actual adjacent edges", () => {
    const data = {
      name: "all",
      value: 10,
      self: 0,
      children: [
        {
          name: "a",
          value: 10,
          self: 1,
          children: [
            { name: "b", value: 9, self: 3, children: [leaf("c", 6)] },
          ],
        },
      ],
    };
    const relation = functionRelations(data, "b");
    expect(relation.callers).toEqual([{ name: "a", weight: 9 }]);
    expect(relation.callees).toEqual([{ name: "c", weight: 6 }]);
    expect(relation.strongest?.self).toBe(3);
  });
  it("validates hidden branches before applying a filter", () => {
    const data = {
      name: "all",
      value: 2,
      self: 0,
      children: [{ name: "__libc_bad", value: 2, self: 1, children: [] }],
    };
    expect(() => prepareFlameTree(data, { hideSystem: true })).toThrow(
      "权重不一致",
    );
  });
});
