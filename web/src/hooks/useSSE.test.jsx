import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import useSSE from "./useSSE";

vi.mock("../api/client", () => ({
  createEventSource: vi.fn(),
  createDiagnosisEventSource: vi.fn(),
}));

import { createDiagnosisEventSource, createEventSource } from "../api/client";

function makeFakeES() {
  const listeners = {};
  const es = {
    onopen: null,
    onerror: null,
    onmessage: null,
    close: vi.fn(),
    addEventListener: vi.fn((type, cb) => {
      listeners[type] = cb;
    }),
    _emit(type, data) {
      const cb = listeners[type];
      if (cb) cb({ data: JSON.stringify(data) });
    },
  };
  return es;
}

describe("useSSE", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    createEventSource.mockReset();
    createDiagnosisEventSource.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("connects and reports connected on open", () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    const onConnectionChange = vi.fn();
    const { result } = renderHook(() => useSSE({ onConnectionChange }));
    act(() => {
      es.onopen();
    });
    expect(result.current.connected).toBe(true);
    expect(onConnectionChange).toHaveBeenCalledWith(true);
  });

  it("reconnects with exponential backoff after an error", () => {
    const streams = [];
    createEventSource.mockImplementation(() => {
      const es = makeFakeES();
      streams.push(es);
      return es;
    });
    renderHook(() => useSSE({}));
    // First error -> 1000ms backoff.
    act(() => {
      streams[0].onerror();
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(streams).toHaveLength(2);
    // Second error -> 2000ms backoff, so nothing at +1000ms...
    act(() => {
      streams[1].onerror();
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(streams).toHaveLength(2);
    // ...and a reconnect at +2000ms.
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(streams).toHaveLength(3);
  });

  it("dispatches task_changed to the handler", () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    const onTaskChanged = vi.fn();
    renderHook(() => useSSE({ onTaskChanged }));
    act(() => {
      es._emit("task_changed", { task_id: "t1" });
    });
    expect(onTaskChanged).toHaveBeenCalledWith({ task_id: "t1" });
  });

  it("dispatches diagnosis_progress to the handler", () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    const onDiagnosisProgress = vi.fn();
    renderHook(() => useSSE({ onDiagnosisProgress }));
    act(() => {
      es._emit("diagnosis_progress", { diagnosis_id: "diag-1", sequence: 4 });
    });
    expect(onDiagnosisProgress).toHaveBeenCalledWith({ diagnosis_id: "diag-1", sequence: 4 });
  });

  it("uses the dedicated diagnosis stream when requested", () => {
    const es = makeFakeES();
    createDiagnosisEventSource.mockReturnValue(es);

    renderHook(() => useSSE({ channel: "diagnosis", resourceId: "diag-1" }));

    expect(createDiagnosisEventSource).toHaveBeenCalledWith("diag-1", 0);
    expect(createEventSource).not.toHaveBeenCalled();
  });

  it("replays the diagnosis stream from the last received sequence", () => {
    const streams = [];
    createDiagnosisEventSource.mockImplementation(() => {
      const es = makeFakeES();
      streams.push(es);
      return es;
    });
    renderHook(() => useSSE({ channel: "diagnosis", resourceId: "diag-1" }));

    act(() => streams[0]._emit("diagnosis_progress", { diagnosis_id: "diag-1", sequence: 7 }));
    act(() => streams[0].onerror());
    act(() => vi.advanceTimersByTime(1000));

    expect(createDiagnosisEventSource).toHaveBeenLastCalledWith("diag-1", 7);
  });

  it("closes the stream and clears the timer on unmount", () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    const { unmount } = renderHook(() => useSSE({}));
    act(() => {
      es.onerror();
    });
    unmount();
    expect(es.close).toHaveBeenCalled();
  });

  it("keeps a single live stream when reconnect is requested manually", () => {
    const streams = [];
    createEventSource.mockImplementation(() => {
      const es = makeFakeES();
      streams.push(es);
      return es;
    });
    const { result, unmount } = renderHook(() => useSSE({}));

    act(() => result.current.reconnect());

    expect(streams).toHaveLength(2);
    expect(streams[0].close).toHaveBeenCalled();
    unmount();
    expect(streams[1].close).toHaveBeenCalled();
  });

  it("does not reconnect after the component unmounts", () => {
    const streams = [];
    createEventSource.mockImplementation(() => {
      const es = makeFakeES();
      streams.push(es);
      return es;
    });
    const { unmount } = renderHook(() => useSSE({}));
    act(() => streams[0].onerror());

    unmount();
    act(() => vi.advanceTimersByTime(30000));

    expect(streams).toHaveLength(1);
  });
});
