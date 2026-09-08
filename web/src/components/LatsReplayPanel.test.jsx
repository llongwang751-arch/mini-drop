import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LatsReplayPanel from "./LatsReplayPanel";

vi.mock("../api/client", () => ({
  getLatsReplayShowcases: vi.fn(),
  runLatsReplayShowcase: vi.fn(),
}));

import * as api from "../api/client";

describe("LatsReplayPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.getLatsReplayShowcases.mockResolvedValue({
      kind: "FROZEN_LATS_REPLAY",
      status: "READY",
      scenarios: [{
        scenario_id: "python-hotspot-tree-v1",
        title: "Python 热点分支回放",
        summary: "在同一份冻结调用栈观察上比较两个候选分支",
        execution_mode: "FULL_LATS",
        environment_semantics: "FROZEN_REPLAY",
        selection_policy: "UCT",
        truth_boundary: "仅覆盖录制时的 Python 热点观察",
      }],
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("creates a fresh UUID session and opens the returned diagnosis immediately", async () => {
    const uuidSpy = vi.spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValueOnce("11111111-1111-4111-8111-111111111111")
      .mockReturnValueOnce("22222222-2222-4222-8222-222222222222");
    api.runLatsReplayShowcase
      .mockResolvedValueOnce({
        diagnosis_id: "replay-diagnosis-1",
        status: "PLANNING",
        mode: "REPLAY",
        execution_mode: "FULL_LATS",
        snapshot: { snapshot_id: "snapshot-python-1", snapshot_digest: "abc", frozen: true },
      })
      .mockResolvedValueOnce({
        diagnosis_id: "replay-diagnosis-2",
        status: "PLANNING",
        mode: "REPLAY",
        execution_mode: "FULL_LATS",
        snapshot: { snapshot_id: "snapshot-python-1", snapshot_digest: "abc", frozen: true },
      });
    const onOpenDiagnosis = vi.fn().mockResolvedValue(undefined);
    const onCasesChanged = vi.fn().mockResolvedValue(undefined);
    render(<LatsReplayPanel onOpenDiagnosis={onOpenDiagnosis} onCasesChanged={onCasesChanged} />);

    expect(await screen.findByText("Python 热点分支回放")).toBeInTheDocument();
    expect(screen.getByText("冻结算法回放，不是实时故障证据")).toBeInTheDocument();
    expect(screen.getByText(/兄弟分支试探（rollout）/)).toBeInTheDocument();
    expect(screen.getByText(/不会调用实时采集器/)).toBeInTheDocument();
    const runButton = screen.getByRole("button", { name: "运行冻结回放：Python 热点分支回放" });
    fireEvent.click(runButton);

    await waitFor(() => expect(api.runLatsReplayShowcase).toHaveBeenNthCalledWith(
      1,
      "python-hotspot-tree-v1",
      "11111111-1111-4111-8111-111111111111",
    ));
    expect(onOpenDiagnosis).toHaveBeenNthCalledWith(1, "replay-diagnosis-1");
    expect(await screen.findByText("观察快照 snapshot-python-1")).toBeInTheDocument();

    await waitFor(() => expect(runButton).not.toBeDisabled());
    fireEvent.click(runButton);
    await waitFor(() => expect(api.runLatsReplayShowcase).toHaveBeenNthCalledWith(
      2,
      "python-hotspot-tree-v1",
      "22222222-2222-4222-8222-222222222222",
    ));
    expect(onOpenDiagnosis).toHaveBeenNthCalledWith(2, "replay-diagnosis-2");
    expect(uuidSpy).toHaveBeenCalledTimes(2);
  });

  it("announces a catalog error and offers a retry", async () => {
    api.getLatsReplayShowcases.mockRejectedValueOnce(new Error("服务暂不可用"));
    render(<LatsReplayPanel />);

    const alert = (await screen.findByText("冻结回放场景加载失败")).closest('[role="alert"]');
    expect(alert).toHaveTextContent("冻结回放场景加载失败");
    expect(alert).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
