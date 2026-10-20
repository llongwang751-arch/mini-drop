/**
 * 火焰图的纯数据处理：先验证树，再按线程、系统帧或自身开销生成展示视图。
 * 保留原树作为证据源，且始终校验总计 = 自身 + 子节点总计，避免过滤后权重失真。
 */
import type { FlameNode } from "../types";

export interface FlameOptions {
  metric?: "total" | "self";
  sort?: "weight" | "name" | "input";
  palette?: "warm" | "ocean" | "mono";
  hideSystem?: boolean;
  threadRoot?: string;
}
export interface FlameThread {
  pid: number;
  tid: number;
  comm: string;
  root_name: string;
  sample_count: number;
}

// 只识别明确的系统符号约定；未知地址和用户函数不能被猜测成系统栈后隐藏。
export const systemFramePattern =
  /^(?:\[kernel(?:\.kallsyms)?\]|\[vdso\]|__x64_sys_|__arm64_sys_|__libc_|entry_SYSCALL|do_syscall_|el0_svc)/;

// 使用显式栈校验，避免递归深度由不可信输入决定；环、重复引用和权重不守恒都拒绝。
export function validateFlameTree(data: FlameNode) {
  const pending = [{ node: data, depth: 0 }];
  const seen = new Set<FlameNode>();
  while (pending.length) {
    const { node, depth } = pending.pop()!;
    if (seen.has(node)) throw new Error("调用树包含重复或循环引用");
    seen.add(node);
    if (seen.size > 100000) throw new Error("节点数量超过限制");
    if (depth > 128) throw new Error("调用栈深度超过限制");
    if (
      !node ||
      typeof node.name !== "string" ||
      !Array.isArray(node.children) ||
      !Number.isSafeInteger(node.value) ||
      node.value < 0 ||
      !Number.isSafeInteger(node.self) ||
      node.self < 0
    )
      throw new Error("调用树节点格式无效");
    let inclusive = node.self;
    for (const child of node.children) {
      if (!child || !Number.isSafeInteger(child.value))
        throw new Error("调用树权重无效");
      inclusive += child.value;
      pending.push({ node: child, depth: depth + 1 });
    }
    if (inclusive !== node.value) throw new Error("调用树总计与自身权重不一致");
  }
}

export function prepareFlameTree(data: FlameNode, options: FlameOptions = {}) {
  // 过滤前先校验整棵树；否则被过滤的分支可能藏住环、坏节点或超限数据，绕过资源保护。
  validateFlameTree(data);
  let root = data;
  if (options.threadRoot) {
    const thread = data.children!.find(
      (node) => node.name === options.threadRoot,
    );
    if (!thread) throw new Error("所选线程不在当前原始调用树中");
    root = thread;
  }
  let hiddenSelf = 0;
  let hiddenFrames = 0;
  if (options.hideSystem) {
    const prune = (node: FlameNode, keep = false): FlameNode[] => {
      const children: FlameNode[] = [];
      for (const child of node.children || [])
        for (const promoted of prune(child)) children.push(promoted);
      if (!keep && systemFramePattern.test(node.name)) {
        hiddenSelf += node.self;
        hiddenFrames++;
        return children;
      }
      return [
        {
          ...node,
          children,
          value:
            node.self + children.reduce((sum, child) => sum + child.value, 0),
        },
      ];
    };
    root = prune(root, true)[0];
    // 保留测得的总权重；被隐藏帧的自身开销记到展示根节点，不能悄悄归给业务函数。
    root = {
      ...root,
      self: root.self + hiddenSelf,
      value: root.value + hiddenSelf,
    };
  }
  // 自身开销视图按函数名聚合 self，不把包含子调用的 total 再累加造成重复计费。
  if (options.metric === "self") {
    const costs = new Map<string, number>();
    const pending = [root];
    while (pending.length) {
      const node = pending.pop()!;
      if (node !== root && node.self > 0)
        costs.set(node.name, (costs.get(node.name) || 0) + node.self);
      for (const child of node.children || []) pending.push(child);
    }
    root = {
      name: "自身开销 · 按函数聚合",
      value: root.value,
      self: root.self,
      children: Array.from(costs, ([name, value]) => ({
        name,
        self: value,
        value,
        children: [],
      })),
    };
  }
  return { root, hiddenSelf, hiddenFrames };
}

// 关联路径始终从完整树计算，不能把当前 SVG 可见节点误当成全部调用关系。
export function functionRelations(data: FlameNode, name: string) {
  validateFlameTree(data);
  const callers = new Map<string, number>();
  const callees = new Map<string, number>();
  const pending = [data];
  let strongest: FlameNode | undefined;
  while (pending.length) {
    const node = pending.pop()!;
    if (
      node !== data &&
      node.name === name &&
      (!strongest || node.value > strongest.value)
    )
      strongest = node;
    for (const child of node.children || []) {
      if (node !== data && child.name === name)
        callers.set(node.name, (callers.get(node.name) || 0) + child.value);
      if (node !== data && node.name === name)
        callees.set(child.name, (callees.get(child.name) || 0) + child.value);
      pending.push(child);
    }
  }
  const order = (values: Map<string, number>) =>
    Array.from(values, ([name, weight]) => ({ name, weight })).sort(
      (a, b) => b.weight - a.weight || a.name.localeCompare(b.name),
    );
  return { callers: order(callers), callees: order(callees), strongest };
}

export function parseFlameThreads(
  section?: Record<string, unknown>,
): FlameThread[] {
  if (!Array.isArray(section?.threads)) return [];
  return section.threads
    .filter(
      (item): item is FlameThread =>
        !!item &&
        typeof item === "object" &&
        Number.isSafeInteger(item.pid) &&
        Number.isSafeInteger(item.tid) &&
        item.pid > 0 &&
        item.tid > 0 &&
        typeof item.comm === "string" &&
        typeof item.root_name === "string" &&
        Number.isSafeInteger(item.sample_count) &&
        item.sample_count >= 0,
    )
    .slice(0, 4096);
}
