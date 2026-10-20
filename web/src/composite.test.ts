/**
 * 复合任务依赖图的纯函数单测，重点覆盖缺失依赖与环，不启动任何采集步骤。
 */
import { describe, expect, it } from "vitest";
import { validateDAG } from "./composite";
describe("composite graph validation", () => {
  it("allows a diamond with shared dependencies", () =>
    expect(
      validateDAG([
        { key: "root", depends_on: [] },
        { key: "a", depends_on: ["root"] },
        { key: "b", depends_on: ["root"] },
        { key: "end", depends_on: ["a", "b"] },
      ]),
    ).toBe(""));
  it.each([
    [
      { key: "a", depends_on: ["b"] },
      { key: "b", depends_on: ["a"] },
    ],
    [{ key: "a", depends_on: ["missing"] }],
    [{ key: "a", depends_on: ["a"] }],
    [
      { key: "a", depends_on: [] },
      { key: "a", depends_on: [] },
    ],
  ])("rejects invalid graph %#", (...nodes) =>
    expect(validateDAG(nodes)).not.toBe(""),
  );
});
