import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import DiagnosisCaseList from "./DiagnosisCaseList";

describe("DiagnosisCaseList replay labels", () => {
  it("separates FULL_LATS frozen replay sessions from ordinary live diagnoses", () => {
    render(
      <DiagnosisCaseList
        cases={[
          {
            diagnosis_id: "replay-1",
            selection_key: "drop_insight_v2:replay-1",
            query: "Python 热点冻结回放",
            canonical_status: "PLANNING",
            target: {
              kind: "FROZEN_LATS_SHOWCASE",
              snapshot_id: "snapshot-first-frame",
            },
          },
          {
            diagnosis_id: "live-1",
            selection_key: "drop_insight_v2:live-1",
            query: "线上 CPU 诊断",
            canonical_status: "COLLECTING",
            mode: "AUTONOMOUS",
            execution_mode: "BUDGETED_LATS",
          },
        ]}
        filter="all"
        onFilterChange={vi.fn()}
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    );

    expect(screen.getByText("FULL_LATS · 冻结回放")).toBeInTheDocument();
    expect(screen.getByText("Drop Insight V2")).toBeInTheDocument();
  });
});
