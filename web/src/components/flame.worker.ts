/// <reference lib="webworker" />
/**
 * 火焰图布局 Worker：把校验后的调用树转换为标准化矩形和逐深度索引。
 * 细小帧只影响 SVG 是否绘制，仍保留在完整布局数据中，供搜索和键盘定位使用。
 */
import { hierarchy, partition } from "d3-hierarchy";
import type { FlameNode } from "../types";
import { prepareFlameTree, type FlameOptions } from "./flame-analysis";

export interface FlameRect {
  id: string;
  name: string;
  nameLower: string;
  color: string;
  renderable: boolean;
  x: number;
  width: number;
  depth: number;
  value: number;
  self: number;
  node: FlameNode;
}
export interface FlameLayout {
  rects: FlameRect[];
  rows: { start: number; end: number }[];
  height: number;
  renderableRows: number;
  tinyFrames: number;
  total: number;
  hiddenSelf: number;
  hiddenFrames: number;
}
const palette = [
  "#b97656",
  "#cf8d5e",
  "#daa36c",
  "#d98969",
  "#a46a59",
  "#e8b17a",
  "#b78c65",
  "#c59c7d",
  "#d59e87",
];
const coolPalette = [
  "#88b9df",
  "#8ebad5",
  "#a3bbd9",
  "#a0ccca",
  "#9db1d4",
  "#b4c9e2",
];
const monoPalette = ["#a3bbd9", "#b5c9e3", "#91aed0", "#c4d4e8", "#a9bedb"];
function color(name: string, selected: FlameOptions["palette"]) {
  let hash = 0;
  for (let i = 0; i < name.length; i++)
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  const colors =
    selected === "ocean"
      ? coolPalette
      : selected === "mono"
        ? monoPalette
        : palette;
  return colors[Math.abs(hash) % colors.length];
}
export function buildFlameLayout(
  data: FlameNode,
  options: FlameOptions = {},
): FlameLayout {
  const prepared = prepareFlameTree(data, options);
  // 用 self 求和得到每个节点的总权重，再按 1000 单位的标准坐标分区，实际像素缩放交给 SVG。
  const root = hierarchy(prepared.root, (node) => node.children).sum(
    (node) => node.self,
  );
  if (options.sort === "name")
    root.sort((a, b) => a.data.name.localeCompare(b.data.name));
  else if (options.sort !== "input")
    root.sort((a, b) => (b.value || 0) - (a.value || 0));
  const tree = partition<FlameNode>().size([1000, root.height + 1])(root);
  const rects: FlameRect[] = [];
  let renderableRows = 1,
    tinyFrames = 0;
  const rows = Array.from({ length: root.height + 1 }, () => ({
    start: -1,
    end: -1,
  }));
  // 广度优先遍历让相同深度的节点在数组中连续，主线程可按可见深度快速切片。
  tree.each((node) => {
    const index = rects.length;
    if (rows[node.depth].start < 0) rows[node.depth].start = index;
    rows[node.depth].end = index + 1;
    // 细帧标记只控制绘制；仍将每个节点放入 rects，不能因视觉裁剪丢掉证据。
    const renderable = node.depth === 0 || node.x1 - node.x0 >= 0.6;
    if (renderable) renderableRows = Math.max(renderableRows, node.depth + 1);
    else tinyFrames++;
    rects.push({
      id: String(index + 1),
      name: node.data.name,
      nameLower: node.data.name.toLowerCase(),
      color: color(node.data.name, options.palette),
      renderable,
      x: node.x0,
      width: node.x1 - node.x0,
      depth: node.depth,
      value: node.value || 0,
      self: node.data.self,
      node: node.data,
    });
  });
  return {
    rects,
    rows,
    height: root.height + 1,
    renderableRows,
    tinyFrames,
    total: root.value || 0,
    hiddenSelf: prepared.hiddenSelf,
    hiddenFrames: prepared.hiddenFrames,
  };
}
if (typeof self !== "undefined") {
  self.onmessage = (
    event: MessageEvent<{ root: FlameNode; options?: FlameOptions }>,
  ) => {
    try {
      self.postMessage(buildFlameLayout(event.data.root, event.data.options));
    } catch (error) {
      self.postMessage({
        error: error instanceof Error ? error.message : "火焰图布局失败",
      });
    }
  };
}
