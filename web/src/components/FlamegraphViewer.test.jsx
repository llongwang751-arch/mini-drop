import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, render, waitFor } from "@testing-library/react";
import FlamegraphViewer from "./FlamegraphViewer";

vi.mock("../api/client", () => ({
  getTaskArtifactContentText: vi.fn(),
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
    api.getTaskArtifactContentText.mockImplementation(async () => JSON.stringify({
      code: 0,
      message: "ok",
      data: buildLargeTree(),
    }));
  });

  it("renders a 10k-node flamegraph without crashing", async () => {
    const { container } = render(<FlamegraphViewer taskId="task-1" />);
    await waitFor(
      () => expect(container.querySelector("svg")).toBeTruthy(),
      { timeout: 15000 },
    );
  });

  it("shows an empty state for a tree without samples", async () => {
    api.getTaskArtifactContentText.mockResolvedValue(JSON.stringify({
      code: 0,
      message: "ok",
      data: { name: "root", value: 0, children: [] },
    }));
    const { container } = render(<FlamegraphViewer taskId="task-empty" />);
    await waitFor(
      () => expect(container.querySelector(".ant-empty")).toBeTruthy(),
      { timeout: 10000 },
    );
  });

  it("renders Java HTML as SVG data without inserting an executable iframe", async () => {
    api.getTaskArtifactContentText.mockResolvedValue(JSON.stringify({
      code: 0, data: { text: "<script>const cpool = ['all', ' Hotspot.run']; unpack(cpool);\nn(3,5)\nu(8)\n</script>" },
    }));
    const { container } = render(<FlamegraphViewer taskId="java-task" artifactType="java_flamegraph_html" />);
    await waitFor(() => expect(container.querySelector("svg")).toBeTruthy());
    expect(container.querySelector("iframe")).toBeNull();
    await waitFor(() => expect(container.textContent).toContain("Hotspot.run"));
    expect(api.getTaskArtifactContentText).toHaveBeenCalledWith("java-task", "java_flamegraph_html", {});
  });

  it("does not show a late response from the previously selected task", async () => {
    let finishOld;
    const payload = name => JSON.stringify({ code: 0, data: { name, value: 5, children: [{ name: `${name}.run`, value: 5 }] } });
    api.getTaskArtifactContentText.mockImplementation(task => task === "old"
      ? new Promise(resolve => { finishOld = resolve; })
      : Promise.resolve(payload("current")));
    const { container, rerender } = render(<FlamegraphViewer taskId="old" />);
    rerender(<FlamegraphViewer taskId="current" />);
    await waitFor(() => expect(container.textContent).toContain("current.run"));
    await act(async () => {
      finishOld(payload("outdated"));
      await new Promise(resolve => setTimeout(resolve, 60));
    });
    expect(container.textContent).toContain("current.run");
    expect(container.textContent).not.toContain("outdated.run");
  });
});
