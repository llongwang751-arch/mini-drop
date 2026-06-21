import type { FlameNode } from "../../types";

export function FlameChart({ tree }: { tree: FlameNode }) {
  const rows: FlameNode[][] = [];
  const walk = (node: FlameNode, depth = 0) => {
    rows[depth] = rows[depth] ?? [];
    rows[depth].push(node);
    node.children?.forEach((child) => walk(child, depth + 1));
  };
  walk(tree);
  const palette = ["#d8efe5", "#b9dccd", "#8ebca8", "#6f9d87", "#4d806c", "#2f604f"];

  return (
    <div className="flame">
      {rows.map((row, depth) => (
        <div className="flame-row" key={depth}>
          {row.map((node, index) => (
            <div
              className="flame-frame"
              key={`${node.name}-${depth}-${index}`}
              title={`${node.name}: ${node.value}`}
              style={{
                flexGrow: Math.max(node.value, 1),
                background: palette[depth % palette.length],
                color: depth > 3 ? "#fff" : "#10201a",
              }}
            >
              <span>{node.name}</span>
              <b>{node.value}</b>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
