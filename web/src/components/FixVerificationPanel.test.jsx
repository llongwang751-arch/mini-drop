import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import FixVerificationPanel from "./FixVerificationPanel";

vi.mock("../api/client", () => ({
  listFixVerifications: vi.fn(),
  verifyDiagnosisFix: vi.fn(),
}));

import * as api from "../api/client";

describe("FixVerificationPanel", () => {
  beforeEach(() => {
    api.listFixVerifications.mockResolvedValue([
      {
        id: "fix_1",
        outcome: "VERIFIED",
        before_task_id: "task-before",
        after_task_id: "task-after",
        comparison: { reason: "热点 calculate_price 占比由 90.0% 降至 20.0%" },
      },
    ]);
  });

  it("renders past verification records", async () => {
    render(<FixVerificationPanel diagnosisId="diag-1" />);
    expect(await screen.findByText("VERIFIED")).toBeInTheDocument();
    expect(screen.getByText("task-before")).toBeInTheDocument();
    expect(screen.getByText("task-after")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/calculate_price/)).toBeInTheDocument(),
    );
  });
});
