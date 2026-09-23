import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import ManagedServicesPanel from "./ManagedServicesPanel";
import { listManagedServices, startManagedServiceDiagnosis } from "../api/client";
vi.mock("../api/client", () => ({ listManagedServices: vi.fn(), startManagedServiceDiagnosis: vi.fn() }));
const entry = { id: "office", name: "办公助手", status: "OBSERVED", instances: [], entry_path: "/api/office/" };
beforeEach(() => vi.clearAllMocks());
it("starts diagnosis by service identity and symptoms without sending a PID", async () => {
  listManagedServices.mockResolvedValue({ items: [entry] });
  startManagedServiceDiagnosis.mockResolvedValue({ diagnosis_id: "insight-office" });
  const open = vi.fn();
  render(<ManagedServicesPanel onOpenDiagnosis={open} />);
  await screen.findByText("办公助手");
  fireEvent.change(screen.getByLabelText("有异常现象？写在这里（可选）"), { target: { value: "知识库查询很慢" } });
  fireEvent.click(screen.getByRole("button", { name: "诊断这个后台" }));
  await waitFor(() => expect(open).toHaveBeenCalledWith("insight-office"));
  expect(startManagedServiceDiagnosis).toHaveBeenCalledWith("office", { query: "知识库查询很慢", mode: "AUTONOMOUS" });
});
it("disables diagnosis for stale observations", async () => {
  listManagedServices.mockResolvedValue({ items: [{ ...entry, status: "STALE" }] });
  render(<ManagedServicesPanel />);
  await screen.findByText("进程快照已过期");
  expect(screen.getByRole("button", { name: "诊断这个后台" })).toBeDisabled();
});
it("checks the current state without requiring a fault description", async () => {
  listManagedServices.mockResolvedValue({ items: [entry] });
  startManagedServiceDiagnosis.mockResolvedValue({ diagnosis_id: "insight-normal" });
  render(<ManagedServicesPanel />);
  await screen.findByText("办公助手");
  fireEvent.click(screen.getByRole("button", { name: "检查当前状态" }));
  await waitFor(() => expect(startManagedServiceDiagnosis).toHaveBeenCalledWith("office", {
    query: "检查当前状态", mode: "AUTONOMOUS", health_check: true,
  }));
});

it("shows real RAG stages and leaves unavailable stages empty", async () => {
  const row = { request_id: "b".repeat(32), operation: "rag.question", method: "POST", status: 200,
    duration_ms: 320, ended_at: "2026-09-23T12:00:00Z", business_result: "COMPLETED",
    stage_ms: { rewrite_ms: 50, retrieval_ms: 30, generation_ms: 200 } };
  listManagedServices.mockResolvedValue({ items: [{ ...entry, observation_source: "agi_office_rag_snapshot",
    business_observations: true, business_requests: { status: "AVAILABLE", items: [row] } }] });
  render(<ManagedServicesPanel />);
  await screen.findByText("办公助手");
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByText(/320 ms/));
  expect(screen.getByLabelText("知识库请求阶段耗时")).toHaveTextContent("生成：200 ms");
  expect(screen.getByLabelText("知识库请求阶段耗时")).toHaveTextContent("向量化：未执行或未采集");
});
it("shows a real upload's chunking, indexing and process usage", async () => {
  const row = { request_id: "d".repeat(32), operation: "rag.ingest", method: "POST", status: 200,
    duration_ms: 2600, ended_at: "2026-09-23T12:00:00Z", business_result: "COMPLETED",
    content_chars: 100000, chunk_count: 500, process_cpu_ms: 900, rss_peak_mib: 140,
    embed_calls: 500, embed_failures: 500, vector_indexed_count: 0,
    service_cpu_ms: 1200, service_memory_peak_mib: 210, service_memory_limit_mib: 512,
    stage_ms: { parse_and_http_ms: 100, split_ms: 250, embedding_ms: 600, index_ms: 1400 } };
  listManagedServices.mockResolvedValue({ items: [{ ...entry, id: "agi-office-backend", observation_source: "agi_office_rag_snapshot",
    business_observations: true, business_requests: { status: "AVAILABLE", items: [row] } }] });
  render(<ManagedServicesPanel />);
  await screen.findByText("办公助手");
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByText(/2600 ms/));
  const stages = screen.getByLabelText("文档导入阶段耗时");
  expect(stages).toHaveTextContent("文档分块：250 ms");
  expect(stages).toHaveTextContent("索引入库：1400 ms");
  expect(stages).toHaveTextContent("RSS 140 MiB");
  expect(stages).toHaveTextContent("服务组 CPU 1200 ms / 内存峰值 210 MiB（限额 512 MiB）");
  expect(stages).toHaveTextContent("主要耗时：索引入库 1400 ms");
  expect(stages).toHaveTextContent("向量化 500 次均失败；本次完成的是本地词法索引");
});
it("does not retain actionable status after a failed refresh", async () => {
  listManagedServices.mockResolvedValueOnce({ items: [entry] }).mockRejectedValueOnce(new Error("offline"));
  render(<ManagedServicesPanel />);
  await screen.findByText("已发现后台进程");
  fireEvent.click(screen.getByRole("button", { name: "刷新服务" }));
  await screen.findByText("服务状态读取失败");
  expect(screen.getByRole("button", { name: "诊断这个后台" })).toBeDisabled();
});

it("submits only the selected request ID and rejects an expired selection", async () => {
  const row = { request_id: "a".repeat(32), operation: "memo.records", method: "POST", status: 200, duration_ms: 140, ended_at: "2026-09-13T16:00:00Z" };
  const observed = { ...entry, business_observations: true, business_requests: { status: "AVAILABLE", items: [row] } };
  listManagedServices.mockResolvedValue({ items: [observed] });
  startManagedServiceDiagnosis.mockResolvedValue({ id: "insight-request" });
  render(<ManagedServicesPanel />);
  await screen.findByText("办公助手");
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByText(/140 ms/));
  fireEvent.change(screen.getByLabelText("有异常现象？写在这里（可选）"), { target: { value: "保存笔记明显变慢" } });
  fireEvent.click(screen.getByRole("button", { name: "诊断这个后台" }));
  await waitFor(() => expect(startManagedServiceDiagnosis).toHaveBeenCalledWith("office", {
    query: "保存笔记明显变慢", mode: "AUTONOMOUS", request_id: row.request_id,
  }));
  listManagedServices.mockResolvedValue({ items: [{ ...observed, business_requests: { status: "NO_RECENT_DATA", items: [] } }] });
  fireEvent.click(screen.getByRole("button", { name: "刷新服务" }));
  await screen.findByText("最近 24 小时暂无可用请求");
  fireEvent.click(screen.getByRole("button", { name: "诊断这个后台" }));
  expect(startManagedServiceDiagnosis).toHaveBeenCalledTimes(1);
});

it("accepts scoped fault and recovery with the measured semantic retrieval variance", async () => {
  const office = { ...entry, id: "agi-office-backend", observation_source: "agi_office_rag_snapshot" };
  const phases = ["baseline", "fault", "recovery"];
  let current = -1;
  const rows = phases.map((phase, index) => ({ request_id: String(index + 1).repeat(32),
    exercise_phase: phase, injected_delay_ms: phase === "fault" ? 2500 : 0,
    duration_ms: [2519, 4770, 2867][index], business_result: "COMPLETED",
    stage_ms: { retrieval_ms: [1063, 3407, 1448][index] }, pid: 123, version: "test-v1" }));
  listManagedServices.mockImplementation(async () => ({ items: [{ ...office,
    business_requests: { status: "AVAILABLE", items: current < 0 ? [] : [rows[current]] } }] }));
  startManagedServiceDiagnosis.mockResolvedValue({ diagnosis_id: "insight-exercise" });
  localStorage.setItem("agi_auth_token", "test-token");
  const originalFetch = global.fetch;
  global.fetch = vi.fn(async (_url, options) => {
    current += 1;
    expect(options.headers["X-Mini-Drop-Exercise"]).toBe(phases[current]);
    return { ok: true, status: 200, json: async () => ({ trace_id: rows[current].request_id }) };
  });
  try {
    const open = vi.fn();
    render(<ManagedServicesPanel onOpenDiagnosis={open} />);
    await screen.findByLabelText("AGI-saber 真实问答验收");
    fireEvent.click(screen.getByRole("button", { name: "运行三段验收" }));
    expect(await screen.findByText("受控慢检索已定位并撤销：同一进程与版本下，检索耗时回落")).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledTimes(3);
    expect(startManagedServiceDiagnosis).toHaveBeenCalledWith("agi-office-backend", expect.objectContaining({ request_id: rows[1].request_id }));
    fireEvent.click(screen.getByRole("button", { name: "查看本次诊断与证据树" }));
    expect(open).toHaveBeenCalledWith("insight-exercise");
  } finally {
    global.fetch = originalFetch;
    localStorage.removeItem("agi_auth_token");
  }
});
