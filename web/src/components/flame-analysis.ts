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

// Explicit symbol conventions only. Unknown addresses and user frames stay.
export const systemFramePattern =
  /^(?:\[kernel(?:\.kallsyms)?\]|\[vdso\]|__x64_sys_|__arm64_sys_|__libc_|entry_SYSCALL|do_syscall_|el0_svc)/;

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
  // Validate the entire input before pruning so filters cannot hide malformed,
  // cyclic or excessive branches from the resource bounds.
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
    // Preserve measured total weight; removed self cost is explicitly attributed
    // to the displayed root instead of being reassigned to a business function.
    root = {
      ...root,
      self: root.self + hiddenSelf,
      value: root.value + hiddenSelf,
    };
  }
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
