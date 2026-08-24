import { useState } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import usePolling from "./usePolling";

function PollingHarness({ callback, enabled = true, interval = 1000 }) {
  const { inFlight, lastRefreshed } = usePolling(callback, { enabled, interval });
  return (
    <div>
      <span>{inFlight ? "running" : "idle"}</span>
      <span>{lastRefreshed === null ? "never" : "refreshed"}</span>
    </div>
  );
}

function setVisibility(value) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("usePolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("does not overlap async refreshes", async () => {
    let finish;
    const callback = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    render(<PollingHarness callback={callback} />);

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(callback).toHaveBeenCalledTimes(1);
    expect(screen.getByText("running")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(callback).toHaveBeenCalledTimes(1);

    await act(async () => { finish(); await Promise.resolve(); });
    expect(screen.getByText("idle")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(callback).toHaveBeenCalledTimes(2);
  });

  it("pauses while hidden and resumes after visibility returns", async () => {
    const callback = vi.fn().mockResolvedValue(undefined);
    render(<PollingHarness callback={callback} />);

    act(() => setVisibility("hidden"));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(callback).not.toHaveBeenCalled();

    act(() => setVisibility("visible"));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(callback).toHaveBeenCalledTimes(1);
    expect(screen.getByText("refreshed")).toBeInTheDocument();
  });

  it("does not schedule refreshes when disabled", async () => {
    const callback = vi.fn().mockResolvedValue(undefined);
    render(<PollingHarness callback={callback} enabled={false} />);

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(callback).not.toHaveBeenCalled();
  });
});
