import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import PerformanceDiagnosisSummary from "./PerformanceDiagnosisSummary";

afterEach(() => vi.unstubAllGlobals());
function report() {
  return { schema: "mini-drop.performance-failure-audit.v1", historical_case_count: 21,
    historical_root_passes: 0, recorded_chain_consistent_count: 18,
    download_url: "/report-assets/performance-audit/index.json",
    cases: Array.from({ length: 21 }, (_, index) => ({ scenario_id: `case-${index}`,
      failure_category: "OUTCOME_REJECTED_BY_LINEAGE_WRAPPER", chain_audit: { recorded_chain_consistent: true } })) };
}
it.each([0, 21])("does not fetch or render retired scores even when an old endpoint still offers %i passes", async passes => {
  const fetch = vi.fn().mockResolvedValue({ ok: true,
    json: async () => ({ ...report(), historical_root_passes: passes }) });
  vi.stubGlobal("fetch", fetch);
  const { container, rerender } = render(<PerformanceDiagnosisSummary />);
  rerender(<PerformanceDiagnosisSummary />);
  await Promise.resolve();
  expect(fetch).not.toHaveBeenCalled();
  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByText(/原始因果根因|历史复盘|为什么旧 21/)).not.toBeInTheDocument();
});
it("keeps the retired compatibility entry silent when legacy assets are unavailable", async () => {
  const fetch = vi.fn().mockRejectedValue(new Error("retired"));
  vi.stubGlobal("fetch", fetch);
  render(<PerformanceDiagnosisSummary />);
  await Promise.resolve();
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
});
