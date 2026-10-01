import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import EngineeringDiagnosisSummary from "./EngineeringDiagnosisSummary";
import index from "../../public/report-assets/engineering-diagnosis/index.json";

afterEach(() => vi.unstubAllGlobals());

it.each(["inflated", "boolean", "negative", "changed_outcome"])(
  "rejects an inconsistent refutation aggregate: %s", async (fault) => {
    const bad = structuredClone(index);
    if (fault === "inflated") bad.refuted += 1;
    else if (fault === "boolean") bad.refuted = true;
    else if (fault === "negative") bad.refuted = -1;
    else {
      const counter = bad.cases.find(c => c.outcome === "REFUTED");
      expect(counter).toBeDefined();
      counter.outcome = "INSUFFICIENT_EVIDENCE";
    }
    const loaded = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => bad }));
    render(<EngineeringDiagnosisSummary onLoaded={loaded} />);
    expect(await screen.findByText("工程诊断成绩读取失败，不推测通过数量")).toBeInTheDocument();
    expect(loaded).not.toHaveBeenCalledWith(bad);
    expect(screen.queryByText(`有效反证 ${bad.refuted}`)).not.toBeInTheDocument();
  }
);
