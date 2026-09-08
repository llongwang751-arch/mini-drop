import { afterEach, describe, expect, it, vi } from "vitest";
import { parseJsonOffMainThread } from "./parseJsonOffMainThread";
import { parseJsonPayload } from "./parseJsonPayload";

describe("parseJsonOffMainThread", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the value and limit metadata returned by a Worker", async () => {
    class WorkerMock {
      terminate = vi.fn();

      postMessage({ source, options }) {
        queueMicrotask(() => {
          this.onmessage({ data: { ok: true, ...parseJsonPayload(source, options) } });
        });
      }
    }
    vi.stubGlobal("Worker", WorkerMock);

    const result = await parseJsonOffMainThread(JSON.stringify({
      data: { name: "root", children: [{ name: "one" }, { name: "two" }] },
    }), {
      treeRootKey: "data",
      maxNodes: 2,
      maxDepth: 8,
    });

    expect(result.value.data.children).toHaveLength(1);
    expect(result.limits).toEqual({ nodeCount: 2, truncated: true });
  });
});
