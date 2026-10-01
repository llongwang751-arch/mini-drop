import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import EngineeringDiagnosisSummary from "./EngineeringDiagnosisSummary";
import index from "../../public/report-assets/engineering-diagnosis/index.json";

afterEach(() => vi.unstubAllGlobals());
it("shows engineering acceptance separately from localization, refutation and untouched cases", async () => {
  const open = vi.fn();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => index }));
  render(<EngineeringDiagnosisSummary onOpenDiagnosis={open} />);
  expect(await screen.findByText("工程诊断判断通过 14/21")).toBeInTheDocument();
  expect(screen.getByText("异常路径定位 4/21")).toBeInTheDocument();
  expect(screen.getByText("已注册 21 类 · 待验收 0 类")).toBeInTheDocument();
  expect(screen.getAllByText("判断通过 · 异常假设被反驳")).toHaveLength(5);
  expect(screen.getByText(/18 类新真机实验、3 类此前真实记录/)).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "查看判断" })[0]);
  expect(open).toHaveBeenCalledWith(index.cases[0].diagnosis_id);
});

it("rejects a malformed projection that promotes bounded records to causal success", async () => {
  const bad = structuredClone(index);
  bad.cases[0].causal_root_cause_verified = true;
  const loaded = vi.fn();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => bad }));
  render(<EngineeringDiagnosisSummary onLoaded={loaded} />);
  expect(await screen.findByText("工程诊断成绩读取失败，不推测通过数量")).toBeInTheDocument();
  expect(loaded).not.toHaveBeenCalledWith(bad);
  expect(screen.queryByText("工程诊断判断通过 14/21")).not.toBeInTheDocument();
});

it("allows a failed index load to be retried", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ ok: true, json: async () => index }));
  render(<EngineeringDiagnosisSummary />);
  fireEvent.click(await screen.findByRole("button", { name: "重试工程验收" }));
  expect(await screen.findByText("工程诊断判断通过 14/21")).toBeInTheDocument();
});

it("rejects an index that mislabels regraded records as a fresh complete campaign", async () => {
  const bad = structuredClone(index);
  bad.fresh_live_run = true;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => bad }));
  render(<EngineeringDiagnosisSummary />);
  expect(await screen.findByText("工程诊断成绩读取失败，不推测通过数量")).toBeInTheDocument();
});
