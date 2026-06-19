import type { FlameNode } from "../../types";

export function FlameChart({ tree }: { tree: FlameNode }) {
  const leaves: FlameNode[] = [];
  const walk = (node: FlameNode) => {
    if (node.children?.length) {
      node.children.forEach(walk);
      return;
    }
    leaves.push(node);
  };
  walk(tree);
  const max = Math.max(...leaves.map((leaf) => leaf.value), 1);

  return (
    <div className="flame">
      {leaves.map((leaf, index) => (
        <div key={`${leaf.name}-${index}`} style={{ height: `${44 + (leaf.value / max) * 120}px` }}>
          <span>{leaf.name}</span>
          <b>{leaf.value}</b>
        </div>
      ))}
    </div>
  );
}
