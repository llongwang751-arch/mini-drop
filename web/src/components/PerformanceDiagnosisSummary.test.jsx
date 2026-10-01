import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import PerformanceDiagnosisSummary from "./PerformanceDiagnosisSummary";

afterEach(() => vi.unstubAllGlobals());
function report() {
  return { schema: "mini-drop.performance-failure-audit.v1", historical_case_count: 21,
    historical_root_passes: 0, recorded_chain_consistent_count: 17,
    download_url: "/report-assets/performance-audit/index.json",
    cases: Array.from({ length: 21 }, (_, index) => ({ scenario_id: `case-${index}`,
      failure_category: "OUTCOME_REJECTED_BY_LINEAGE_WRAPPER", chain_audit: { recorded_chain_consistent: true } })) };
}
it("shows every old scenario while keeping recorded lineage separate from causal root success", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => report() }));
  render(<PerformanceDiagnosisSummary />);
  fireEvent.click(await screen.findByText("为什么旧 21 类根因为 0：查看逐项复盘"));
  expect(screen.getByText("原始因果根因 0/21")).toBeInTheDocument();
  expect(screen.getByText("归档记录证据链一致 17/21")).toBeInTheDocument();
  expect(screen.getByText("新版本云端根因：尚未验收")).toBeInTheDocument();
  expect(screen.getAllByText("未验证")).toHaveLength(21);
  expect(screen.getByRole("link", { name: "下载逐项复盘与原始文件 SHA" })).toHaveAttribute("download");
});
it.each(["http", "invalid"])("fails closed for %s and can retry without injecting faults", async kind => {
  const fetch = vi.fn().mockResolvedValueOnce(kind === "http" ? { ok: false } : {
    ok: true, json: async () => ({ ...report(), historical_root_passes: 21 }),
  }).mockResolvedValue({ ok: true, json: async () => report() });
  vi.stubGlobal("fetch", fetch);
  render(<PerformanceDiagnosisSummary />);
  expect(await screen.findByText("历史复盘读取失败，不显示推测成绩")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByText("为什么旧 21 类根因为 0：查看逐项复盘")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls.every(call => call[0].startsWith("/report-assets/"))).toBe(true);
});
