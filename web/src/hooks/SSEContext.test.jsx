import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { SSEProvider, useControlEvents, useControlSSE } from "./SSEContext";

vi.mock("../api/client", () => ({
  createEventSource: vi.fn(),
  createDiagnosisEventSource: vi.fn(),
  syncBrowserSession: vi.fn(),
}));

import { createEventSource, syncBrowserSession } from "../api/client";

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

function Probe({ tag, handler }) {
  useControlEvents({ onTaskChanged: handler });
  return <div data-testid={tag} />;
}

function ConnectedProbe() {
  const { connected } = useControlSSE() || { connected: false };
  return <div>{connected ? "shared-online" : "shared-offline"}</div>;
}

describe("SSEContext", () => {
  beforeEach(() => {
    createEventSource.mockReset();
    syncBrowserSession.mockReset();
    syncBrowserSession.mockResolvedValue(true);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shares a single control connection across consumers", async () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    const first = vi.fn();
    const second = vi.fn();
    render(
      <SSEProvider>
        <Probe tag="a" handler={first} />
        <Probe tag="b" handler={second} />
      </SSEProvider>,
    );

    await waitFor(() => expect(createEventSource).toHaveBeenCalledTimes(1));
    act(() => {
      es._emit("task_changed", { id: 1 });
    });
    expect(first).toHaveBeenCalledWith({ id: 1 });
    expect(second).toHaveBeenCalledWith({ id: 1 });
    expect(createEventSource).toHaveBeenCalledTimes(1);
  });

  it("waits for the browser session sync before enabling the connection", async () => {
    let resolveSync;
    syncBrowserSession.mockReturnValue(new Promise((resolve) => { resolveSync = resolve; }));
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    render(
      <SSEProvider>
        <ConnectedProbe />
      </SSEProvider>,
    );

    expect(createEventSource).not.toHaveBeenCalled();
    await act(async () => {
      resolveSync(true);
    });
    await waitFor(() => expect(createEventSource).toHaveBeenCalledTimes(1));
    act(() => {
      es.onopen();
    });
    expect(screen.getByText("shared-online")).toBeInTheDocument();
  });

  it("falls back to a standalone connection without a provider", async () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    const handler = vi.fn();
    render(<Probe tag="solo" handler={handler} />);

    await waitFor(() => expect(createEventSource).toHaveBeenCalledTimes(1));
    act(() => {
      es._emit("task_changed", { id: 2 });
    });
    expect(handler).toHaveBeenCalledWith({ id: 2 });
  });

  it("stops delivering events to subscribers that unmounted", async () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    const handler = vi.fn();
    const { unmount } = render(
      <SSEProvider>
        <Probe tag="a" handler={handler} />
      </SSEProvider>,
    );
    await waitFor(() => expect(createEventSource).toHaveBeenCalledTimes(1));
    unmount();
    act(() => {
      es._emit("task_changed", { id: 3 });
    });
    expect(handler).not.toHaveBeenCalled();
  });
});
