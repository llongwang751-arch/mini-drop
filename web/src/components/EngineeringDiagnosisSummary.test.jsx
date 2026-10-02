import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import EngineeringDiagnosisSummary from "./EngineeringDiagnosisSummary";
import index from "../../public/report-assets/engineering-diagnosis/index.json";

afterEach(() => vi.unstubAllGlobals());
it("shows engineering acceptance separately from localization, refutation and untouched cases", async () => {
  const open = vi.fn();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => index }));
  render(<EngineeringDiagnosisSummary onOpenDiagnosis={open} />);
  expect(await screen.findByText("工程诊断判断通过 21/21")).toBeInTheDocument();
  expect(screen.getByText("异常路径定位 6/21")).toBeInTheDocument();
  expect(screen.getByText("有效反证 8")).toBeInTheDocument();
  expect(screen.getByText("已注册 21 类 · 待验收 0 类")).toBeInTheDocument();
  expect(screen.getAllByText("判断通过 · 异常假设被反驳")).toHaveLength(8);
  expect(screen.getByText(/7 类新真机实验、14 类此前真实记录/)).toBeInTheDocument();
  expect(index.current_campaign_id).toBe("seven-gaps-20261002");
  expect(index.cases.filter(c => c.campaign_id === index.current_campaign_id && c.fresh_live_run)).toHaveLength(7);
  expect(index.cases.filter(c => c.campaign_id !== index.current_campaign_id && !c.fresh_live_run)).toHaveLength(14);
  expect(screen.getByText(/汇总成绩不表示全部案例在本次重新运行/)).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "查看判断" })[0]);
  expect(open).toHaveBeenCalledWith(index.cases[0].diagnosis_id);
});

it("uses current engineering scores while ignoring legacy score fields from a mixed-version server", async () => {
  const mixed = { ...structuredClone(index), historical_root_passes: 0, historical_case_count: 21,
    latest_acceptance: { root_cause_accepted: false, passed: false } };
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => mixed });
  vi.stubGlobal("fetch", fetch);
  render(<EngineeringDiagnosisSummary />);
  expect(await screen.findByText("工程诊断判断通过 21/21")).toBeInTheDocument();
  expect(screen.getByText("异常路径定位 6/21")).toBeInTheDocument();
  expect(screen.getByText("有效反证 8")).toBeInTheDocument();
  expect(screen.queryByText(/0\/21|历史因果成绩|历史严格|原始因果根因|为什么旧 21/)).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith("/report-assets/engineering-diagnosis/index.json", { cache: "no-store" });
});

it("rejects a malformed projection that promotes bounded records to causal success", async () => {
  const bad = structuredClone(index);
  bad.cases[0].causal_root_cause_verified = true;
  const loaded = vi.fn();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => bad }));
  render(<EngineeringDiagnosisSummary onLoaded={loaded} />);
  expect(await screen.findByText("工程诊断成绩读取失败，不推测通过数量")).toBeInTheDocument();
  expect(loaded).not.toHaveBeenCalledWith(bad);
  expect(screen.queryByText("工程诊断判断通过 21/21")).not.toBeInTheDocument();
});

it("allows a failed index load to be retried", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ ok: true, json: async () => index }));
  render(<EngineeringDiagnosisSummary />);
  fireEvent.click(await screen.findByRole("button", { name: "重试工程验收" }));
  expect(await screen.findByText("工程诊断判断通过 21/21")).toBeInTheDocument();
});

it("rejects an index that mislabels regraded records as a fresh complete campaign", async () => {
  const bad = structuredClone(index);
  bad.fresh_live_run = true;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => bad }));
  render(<EngineeringDiagnosisSummary />);
  expect(await screen.findByText("工程诊断成绩读取失败，不推测通过数量")).toBeInTheDocument();
});
