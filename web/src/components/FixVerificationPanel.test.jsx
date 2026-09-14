import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import FixVerificationPanel from "./FixVerificationPanel";

vi.mock("../api/client", () => ({
  listFixVerifications: vi.fn(),
  verifyDiagnosisFix: vi.fn(),
}));

import * as api from "../api/client";

describe("FixVerificationPanel", () => {
  it("shows an explicit unverified outcome when no repair records exist", async () => {
    api.listFixVerifications.mockResolvedValueOnce([]);
    render(<FixVerificationPanel diagnosisId="diag-empty" canVerify={false} />);
    expect(await screen.findByText(/尚无修复复测记录/)).toBeInTheDocument();
    expect(screen.queryByText("对比验证")).not.toBeInTheDocument();
  });
  it("does not mistake a load failure for an absent repair", async () => {
    api.listFixVerifications.mockRejectedValueOnce(new Error("network"));
    render(<FixVerificationPanel diagnosisId="diag-error" canVerify={false} />);
    expect(await screen.findByText(/修复记录读取失败/)).toBeInTheDocument();
    expect(screen.queryByText(/尚无修复复测记录/)).not.toBeInTheDocument();
  });
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
    expect(await screen.findByText("修复验证通过")).toBeInTheDocument();
    expect(screen.queryByText("VERIFIED")).not.toBeInTheDocument();
    expect(screen.getByText("task-before")).toBeInTheDocument();
    expect(screen.getByText("task-after")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/calculate_price/)).toBeInTheDocument(),
    );
  });
});
