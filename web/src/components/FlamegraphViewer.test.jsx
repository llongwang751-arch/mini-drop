import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import FlamegraphViewer from "./FlamegraphViewer";

vi.mock("../api/client", () => ({
  getTaskArtifactContent: vi.fn(),
}));

import * as api from "../api/client";

// Build a tree with ~11k nodes: root + depth-4 fanout-10 (10^4 leaves).
function buildLargeTree() {
  let id = 0;
  function node(depth) {
    if (depth <= 0) {
      return { name: `fn_${id++}`, value: 1 };
    }
    const children = [];
    for (let i = 0; i < 10; i += 1) {
      children.push(node(depth - 1));
    }
    return { name: `node_${id++}`, value: children.length + 1, children };
  }
  const root = { name: "root", value: 10 ** 4, children: [node(4)] };
  return root;
}

describe("FlamegraphViewer", () => {
  beforeEach(() => {
    api.getTaskArtifactContent.mockResolvedValue(buildLargeTree());
  });

  it("renders a 10k-node flamegraph without crashing", async () => {
    const { container } = render(<FlamegraphViewer taskId="task-1" />);
    await waitFor(
      () => expect(container.querySelector("svg")).toBeTruthy(),
      { timeout: 15000 },
    );
  });

  it("shows an empty state for a tree without samples", async () => {
    api.getTaskArtifactContent.mockResolvedValue({ name: "root", value: 0, children: [] });
    const { container } = render(<FlamegraphViewer taskId="task-empty" />);
    await waitFor(
      () => expect(container.querySelector(".ant-empty")).toBeTruthy(),
      { timeout: 10000 },
    );
  });
});
