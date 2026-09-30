import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import catalog from "../../public/report-assets/engineering-cases/index.json";
import EngineeringCasesPanel, { validateCatalog } from "./EngineeringCasesPanel";
import { getFaultPlaza } from "../api/client";
vi.mock("../api/client", () => ({ getFaultPlaza: vi.fn() }));
beforeEach(() => {
  getFaultPlaza.mockResolvedValue({ scenarios: [] });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok:true, json:async () => structuredClone(catalog) }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("shows actual before/after evidence and does not claim automated AI root acceptance", async () => {
  render(<EngineeringCasesPanel />);
  expect(await screen.findByText("4 个缺陷闭环已验证")).toBeInTheDocument();
  expect(screen.getByText("修复前：2/9 项失败")).toBeInTheDocument();
  expect(screen.getByText("修复后：9/9 项通过，0 跳过")).toBeInTheDocument();
  expect(screen.getByText("工程缺陷修复回归；不计入 AI 自动根因成绩。")).toBeInTheDocument();
  expect(screen.getByRole("link", { name:"cache-empty-before.xml" })).toHaveAttribute(
    "href", "/report-assets/engineering-cases/empty-query-cache/cache-empty-before.xml");
});

it.each([
  data => { data.cases[0].after.passed = 0; },
  data => { data.cases[0].after.skipped = 1; data.cases[0].after.passed = 0; },
  data => { data.cases[0].after.selected = true; },
  data => { data.cases[0].before.failed = 0; data.cases[0].before.passed = 1; },
  data => { data.model_auto_root_cause = "VERIFIED"; },
  data => { data.cases.push(data.cases[0]); },
  data => { data.cases[0].evidence[0].filename = "parallel-timing/.."; },
])("rejects malformed evidence instead of manufacturing a green case", mutate => {
  const data = structuredClone(catalog);
  mutate(data);
  expect(() => validateCatalog(data)).toThrow();
});

it("keeps the historical stop entrance visible when a fault is active", async () => {
  getFaultPlaza.mockResolvedValue({ scenarios:[{ active:true }] });
  const onOpenHistorical = vi.fn();
  render(<EngineeringCasesPanel onOpenHistorical={onOpenHistorical} />);
  expect(await screen.findByText("仍有 1 个历史故障处于启用状态")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:"查看历史故障实验" }));
  expect(onOpenHistorical).toHaveBeenCalledOnce();
});

it("does not assume historical faults are inactive when the API is unavailable", async () => {
  getFaultPlaza.mockRejectedValue(new Error("offline"));
  render(<EngineeringCasesPanel />);
  expect(await screen.findByText("历史故障运行状态未读取")).toBeInTheDocument();
});

it("shows loading and permits retry after a failed fetch without stale success", async () => {
  fetch.mockRejectedValueOnce(new Error("offline"));
  render(<EngineeringCasesPanel />);
  expect(screen.getByText("正在读取案例证据…")).toBeInTheDocument();
  expect(await screen.findByText("无法验证案例数据")).toBeInTheDocument();
  expect(screen.queryByText("4 个缺陷闭环已验证")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:"重试" }));
  expect(await screen.findByText("4 个缺陷闭环已验证")).toBeInTheDocument();
});
