import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import HealthCheckActions, { healthCheckResult } from "./HealthCheckActions";
import { startManagedServiceDiagnosis } from "../api/client";
vi.mock("../api/client", () => ({ startManagedServiceDiagnosis: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const detail = { diagnosis_id: "check-1", status: "COMPLETED" };
const result = { schema: "mini-drop.health-check.v1", diagnosis_id: "check-1", service_id: "service",
  code: "NORMAL_OBSERVED", checked: ["CPU"], anomalies: [], unmeasured: ["业务正确性"], evidence_refs: ["ev"], causal_root_cause_verified: false };
describe("completed measured checks", () => {
  it("accepts a matching server result without requiring a supported report", () => {
    expect(healthCheckResult(detail, { events: [{ event_type: "health_check.completed", payload: result }] })).toEqual(result);
    expect(healthCheckResult({ ...detail, diagnosis_id: "other" }, { events: [{ event_type: "health_check.completed", payload: result }] })).toBeNull();
    expect(healthCheckResult({ ...detail, status: "CANCELLED" }, { events: [{ event_type: "health_check.completed", payload: result }] })).toBeNull();
  });
  it.each(["NORMAL_OBSERVED", "INSUFFICIENT_OBSERVABILITY"])("creates a fresh sampling session for %s", async code => {
    const open = vi.fn(); startManagedServiceDiagnosis.mockResolvedValue({ diagnosis_id: "check-2" });
    render(<HealthCheckActions detail={detail} result={{ ...result, code }} onOpenDiagnosis={open} />);
    expect(screen.queryByRole("button", { name: "深入诊断" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: code === "NORMAL_OBSERVED" ? "再次检查" : "重新采集" }));
    await waitFor(() => expect(open).toHaveBeenCalledWith("check-2"));
    expect(startManagedServiceDiagnosis).toHaveBeenCalledWith("service", {
      query: "检查当前状态", mode: "AUTONOMOUS", health_check: true, follow_up_diagnosis_id: "check-1" });
  });
  it("opens a separate deep investigation only for observed anomalies", async () => {
    startManagedServiceDiagnosis.mockResolvedValue({ diagnosis_id: "incident-2" });
    render(<HealthCheckActions detail={detail} result={{ ...result, code: "ANOMALY_OBSERVED", anomalies: ["CPU 80%"] }} />);
    fireEvent.click(screen.getByRole("button", { name: "深入诊断" }));
    await waitFor(() => expect(startManagedServiceDiagnosis).toHaveBeenCalled());
    expect(startManagedServiceDiagnosis.mock.calls[0][1]).toMatchObject({ health_check: false, follow_up_diagnosis_id: "check-1" });
    expect(startManagedServiceDiagnosis.mock.calls[0][1].query).toContain("CPU 80%");
  });
  it("keeps the measured result and permits retry after a failed follow-up", async () => {
    startManagedServiceDiagnosis.mockRejectedValueOnce(new Error("进程已重启，请重试"));
    startManagedServiceDiagnosis.mockResolvedValueOnce({ diagnosis_id: "retry-check" });
    render(<HealthCheckActions detail={detail} result={result} />);
    fireEvent.click(screen.getByRole("button", { name: "再次检查" }));
    expect(await screen.findByText("进程已重启，请重试")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "再次检查" })).not.toHaveClass("ant-btn-loading"));
    fireEvent.click(screen.getByRole("button", { name: "再次检查" }));
    await waitFor(() => expect(startManagedServiceDiagnosis).toHaveBeenCalledTimes(2));
  });
});
