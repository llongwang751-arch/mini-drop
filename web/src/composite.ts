/**
 * 复合任务的依赖图预检查：识别重复节点、缺失依赖和环。
 * 前端反馈帮助修正草稿，实际 DAG 调度、取消和持久化由服务端负责。
 */
export interface DependencyNode {
  key: string;
  depends_on: string[];
}
export function validateDAG(nodes: DependencyNode[]): string {
  if (!nodes.length || nodes.length > 16) return "复合任务需要 1～16 个步骤";
  const map = new Map(nodes.map((n) => [n.key, n]));
  if (
    map.size !== nodes.length ||
    nodes.some((n) => !/^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(n.key))
  )
    return "步骤标识必须唯一，且以字母开头";
  const visiting = new Set<string>(),
    done = new Set<string>();
  function visit(key: string): boolean {
    if (visiting.has(key) || !map.has(key)) return false;
    if (done.has(key)) return true;
    visiting.add(key);
    for (const dep of map.get(key)!.depends_on) if (!visit(dep)) return false;
    visiting.delete(key);
    done.add(key);
    return true;
  }
  return nodes.every((n) => visit(n.key))
    ? ""
    : "依赖中存在环、自引用或缺失的步骤";
}
