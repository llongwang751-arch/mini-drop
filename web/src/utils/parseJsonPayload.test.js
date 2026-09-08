import { describe, expect, it } from "vitest";
import { parseJsonPayload } from "./parseJsonPayload";

describe("parseJsonPayload", () => {
  it("caps large trees before they return to the UI thread", () => {
    const children = Array.from({ length: 20 }, (_, index) => ({
      name: `child-${index}`,
      children: [{ name: `leaf-${index}` }],
    }));
    const result = parseJsonPayload(JSON.stringify({ code: 0, data: { name: "root", children } }), {
      treeRootKey: "data",
      maxNodes: 10,
      maxDepth: 8,
    });

    expect(result.limits).toEqual({ nodeCount: 10, truncated: true });
    expect(result.value.data.children).toHaveLength(9);
  });

  it("caps excessive depth without discarding the retained parent", () => {
    const result = parseJsonPayload(JSON.stringify({
      data: { name: "root", children: [{ name: "one", children: [{ name: "two" }] }] },
    }), {
      treeRootKey: "data",
      maxNodes: 100,
      maxDepth: 1,
    });

    expect(result.limits).toEqual({ nodeCount: 2, truncated: true });
    expect(result.value.data.children[0].children).toEqual([]);
  });
});
