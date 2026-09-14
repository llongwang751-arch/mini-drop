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
  fireEvent.change(screen.getByLabelText("这个后台出现了什么性能问题？"), { target: { value: "知识库查询很慢" } });
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
  fireEvent.change(screen.getByLabelText("这个后台出现了什么性能问题？"), { target: { value: "保存笔记明显变慢" } });
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
