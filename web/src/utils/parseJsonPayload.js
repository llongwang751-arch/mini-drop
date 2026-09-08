function limitTree(root, maxNodes, maxDepth) {
  if (!root || typeof root !== "object") {
    return { nodeCount: 0, truncated: false };
  }

  const queue = [{ node: root, depth: 0 }];
  let cursor = 0;
  let nodeCount = 0;
  let truncated = false;

  while (cursor < queue.length) {
    const { node, depth } = queue[cursor];
    cursor += 1;
    nodeCount += 1;

    const children = Array.isArray(node.children) ? node.children : [];
    if (children.length === 0) continue;
    if (depth >= maxDepth) {
      node.children = [];
      truncated = true;
      continue;
    }

    // Keep enough room for nodes that are already queued. This guarantees the
    // object transferred back to the UI never exceeds maxNodes.
    const queued = queue.length - cursor;
    const available = Math.max(0, maxNodes - nodeCount - queued);
    const accepted = children.slice(0, available);
    if (accepted.length < children.length) truncated = true;
    node.children = accepted;
    for (const child of accepted) {
      if (child && typeof child === "object") {
        queue.push({ node: child, depth: depth + 1 });
      }
    }
  }

  return { nodeCount, truncated };
}

export function parseJsonPayload(source, options = {}) {
  const value = JSON.parse(source);
  let limits = null;
  if (options.treeRootKey && Number.isFinite(options.maxNodes) && Number.isFinite(options.maxDepth)) {
    limits = limitTree(value?.[options.treeRootKey], options.maxNodes, options.maxDepth);
  }
  return { value, limits };
}
