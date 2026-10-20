/**
 * 直接调用真实布局函数检查完整节点与深度索引；原生 Worker 调度由浏览器套件另测。
 */
import { describe, expect, it } from "vitest";
import { buildFlameLayout } from "./flame.worker";
import { graphHeight, visibleDepthRange } from "./Flamegraph";
import type { FlameNode } from "../types";

const leaf = (name: string, value: number): FlameNode => ({
  name,
  value,
  self: value,
  children: [],
});

describe("flame layout and vertical viewport boundaries", () => {
  it("keeps shallow viewports compact while preserving deep row spacing", () => {
    expect(graphHeight(1)).toBeLessThanOrEqual(120);
    expect(graphHeight(2)).toBeLessThanOrEqual(120);
    expect(graphHeight(125)).toBe(125 * 28 + 20);
    expect(visibleDepthRange(graphHeight(2), 0, 550, 2)).toEqual({
      start: 0,
      end: 2,
    });
  });
  it("omits blank nonrenderable rows without losing their searchable data", () => {
    let tiny = leaf("tiny-leaf", 1);
    for (let depth = 0; depth < 100; depth++)
      tiny = { name: "tiny-" + depth, value: 1, self: 0, children: [tiny] };
    const layout = buildFlameLayout({
      name: "root",
      value: 1000000,
      self: 999999,
      children: [tiny],
    });
    expect(layout.rects).toHaveLength(102);
    expect(layout.height).toBe(102);
    expect(layout.renderableRows).toBe(1);
    expect(layout.tinyFrames).toBe(101);
    expect(graphHeight(layout.renderableRows)).toBeLessThanOrEqual(120);
    expect(layout.rects.at(-1)?.name).toBe("tiny-leaf");
  });
  it("preserves actual weights and contiguous depth indexes", () => {
    const layout = buildFlameLayout({
      name: "all",
      value: 10,
      self: 0,
      children: [leaf("a", 4), leaf("b", 6)],
    });
    expect(layout.total).toBe(10);
    expect(layout.rects).toHaveLength(3);
    expect(
      layout.rows.map((row) =>
        layout.rects.slice(row.start, row.end).map((r) => r.depth),
      ),
    ).toEqual([[0], [1, 1]]);
    expect(layout.rects[0].width).toBe(1000);
    expect(
      layout.rects.every(
        (r) => r.nameLower === r.name.toLowerCase() && r.color.startsWith("#"),
      ),
    ).toBe(true);
  });
  it("retains tiny nodes for complete search statistics without drawing tiny rectangles", () => {
    const layout = buildFlameLayout({
      name: "all",
      value: 10000,
      self: 0,
      children: [leaf("needle", 1), leaf("large", 9999)],
    });
    expect(layout.rects).toHaveLength(3);
    expect(layout.rects.find((r) => r.name === "needle")?.renderable).toBe(
      false,
    );
    expect(
      layout.rects.filter((r) => r.nameLower.includes("needle")),
    ).toHaveLength(1);
  });
  it("sorts real siblings and changes palette without altering their weights", () => {
    const data = {
      name: "all",
      value: 10,
      self: 0,
      children: [leaf("z", 6), leaf("a", 4)],
    };
    const named = buildFlameLayout(data, { sort: "name", palette: "ocean" });
    const original = buildFlameLayout(data, { sort: "input" });
    expect(named.rects.slice(1).map((item) => item.name)).toEqual(["a", "z"]);
    expect(original.rects.slice(1).map((item) => item.name)).toEqual([
      "z",
      "a",
    ]);
    expect(named.total).toBe(original.total);
    expect(named.rects.find((item) => item.name === "z")?.value).toBe(6);
    expect(named.rects[0].color).not.toBe(original.rects[0].color);
  });
  it("renders bottom root rows initially and top rows only when scrolled there", () => {
    const pixels = 125 * 28 + 20;
    const bottom = visibleDepthRange(pixels, pixels + 30 - 550, 550, 125);
    const top = visibleDepthRange(pixels, 0, 550, 125);
    expect(bottom.start).toBe(0);
    expect(bottom.end).toBeLessThanOrEqual(24);
    expect(top.start).toBeGreaterThan(98);
    expect(top.end).toBe(125);
    expect(visibleDepthRange(220, 0, 550, 3)).toEqual({ start: 0, end: 3 });
  });
  it("accepts exactly 100,000 nodes without discarding the tree", () => {
    const layout = buildFlameLayout({
      name: "all",
      value: 99999,
      self: 0,
      children: Array.from({ length: 99999 }, (_, i) =>
        leaf("function-" + i, 1),
      ),
    });
    expect(layout.rects).toHaveLength(100000);
    expect(layout.total).toBe(99999);
    expect(layout.rects.at(-1)?.name).toBe("function-99998");
  });
  it("rejects invalid inclusive weights and malformed resource-heavy trees", () => {
    expect(() =>
      buildFlameLayout({
        name: "all",
        value: 10,
        self: 0,
        children: [leaf("bad", 9)],
      }),
    ).toThrow();
    expect(() =>
      buildFlameLayout({
        name: "all",
        value: 100000,
        self: 0,
        children: Array.from({ length: 100000 }, (_, index) =>
          leaf(String(index), 1),
        ),
      }),
    ).toThrow("节点数量");
    let root = leaf("leaf", 1);
    for (let i = 0; i < 129; i++)
      root = { name: "deep", value: 1, self: 0, children: [root] };
    expect(() => buildFlameLayout(root)).toThrow("深度");
  });
});
