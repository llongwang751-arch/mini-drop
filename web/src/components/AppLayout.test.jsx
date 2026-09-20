import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AppLayout from "./AppLayout";

vi.mock("../api/client", () => ({
  createEventSource: vi.fn(),
  createDiagnosisEventSource: vi.fn(),
  syncBrowserSession: vi.fn(),
  getStoredApiKey: vi.fn(() => ""),
  saveApiKey: vi.fn(),
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

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<div>page-body</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("AppLayout shell SSE", () => {
  beforeEach(() => {
    createEventSource.mockReset();
    // 默认给一个可用的假连接：凭据变更等路径会触发重连，缺实现会让
    // useSSE 在 undefined 上挂 onopen 直接崩溃。
    createEventSource.mockImplementation(() => makeFakeES());
    syncBrowserSession.mockReset();
    syncBrowserSession.mockResolvedValue(true);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the fallback indicator until the shared stream opens", async () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    renderLayout();

    expect(screen.getByText("page-body")).toBeInTheDocument();
    expect(screen.getByText("轮询兜底中")).toBeInTheDocument();
    await waitFor(() => expect(createEventSource).toHaveBeenCalledTimes(1));
    act(() => {
      es.onopen();
    });
    expect(screen.getByText("事件流已连接")).toBeInTheDocument();
    expect(screen.getByText("实时")).toBeInTheDocument();
  });

  it("returns to the fallback indicator when the stream errors", async () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    renderLayout();
    await waitFor(() => expect(createEventSource).toHaveBeenCalledTimes(1));

    act(() => {
      es.onopen();
    });
    expect(screen.getByText("事件流已连接")).toBeInTheDocument();
    act(() => {
      es.onerror();
    });
    expect(screen.getByText("轮询兜底中")).toBeInTheDocument();
  });

  it("opens exactly one control connection for the whole shell", async () => {
    const es = makeFakeES();
    createEventSource.mockReturnValue(es);
    renderLayout();

    await waitFor(() => expect(createEventSource).toHaveBeenCalledTimes(1));
    expect(createEventSource).toHaveBeenCalledTimes(1);
  });

  it("re-syncs the browser session when credentials change", async () => {
    renderLayout();
    await waitFor(() => expect(syncBrowserSession).toHaveBeenCalledTimes(1));
    act(() => {
      window.dispatchEvent(new CustomEvent("mini-drop:credentials-changed"));
    });
    await waitFor(() => expect(syncBrowserSession).toHaveBeenCalledTimes(2));
  });
});
