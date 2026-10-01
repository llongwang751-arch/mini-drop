import { describe, it, expect } from "vitest";
import { assessObservationWindow } from "./observationAssessment";

const target = { agent_id: "a", pid: 10, process_binding: { boot_id: "boot", process_start_ticks: 20 } };
function baseline() {
  return { classification: { decision: "ACCEPT_LIMITED" }, envelope: { scope: { agent_id: "a", pid: 10 }, observation: { metadata: {
    sample_count: 15, window_duration_seconds: 14,
    process_identity: { pid: 10, start_ticks: 20, verified: true },
    summary: { process_cpu_core_usage: 10, vmrss_mb: 100, vmrss_mb_delta: 0 }, signals: {},
  } } } };
}
describe("scope-limited health assessment", () => {
  it("explicitly reports normal only for the measured scope", () => {
    const result = assessObservationWindow(baseline(), target);
    expect(result.code).toBe("NORMAL_OBSERVED");
    expect(result.checked).toHaveLength(2);
    expect(result.detail).toContain("未检查磁盘操作延迟、丢包/重传");
  });
  it.each([50, 99, 240])("does not call CPU %s%% normal without a supported root", cpu => {
    const evidence = baseline();
    evidence.envelope.observation.metadata.summary.process_cpu_core_usage = cpu;
    expect(assessObservationWindow(evidence, target).code).toBe("ANOMALY_OBSERVED");
  });
  it.each([true, "10", null, -1, NaN, Infinity])("rejects invalid CPU %s", cpu => {
    const evidence = baseline();
    evidence.envelope.observation.metadata.summary.process_cpu_core_usage = cpu;
    expect(assessObservationWindow(evidence, target).code).toBe("INSUFFICIENT_OBSERVABILITY");
  });
  it.each(["pid", "start_ticks", "verified", "sample_count", "window_duration_seconds", "classification", "rss-trend"])("rejects incomplete %s", field => {
    const evidence = baseline();
    const metadata = evidence.envelope.observation.metadata;
    if (field === "classification") evidence.classification.decision = "REJECT_SCOPE";
    else if (field === "rss-trend") delete metadata.summary.vmrss_mb_delta;
    else if (field === "sample_count") metadata.sample_count = 1;
    else if (field === "window_duration_seconds") metadata.window_duration_seconds = 0;
    else metadata.process_identity[field] = field === "verified" ? false : 999;
    expect(assessObservationWindow(evidence, target).code).toBe("INSUFFICIENT_OBSERVABILITY");
  });
  it("separates writing activity from abnormal dependency latency", () => {
    const evidence = baseline();
    evidence.envelope.observation.metadata.signals.io_activity = { detected: true };
    expect(assessObservationWindow(evidence, target).code).toBe("NORMAL_OBSERVED");
    evidence.envelope.observation.metadata.signals.downstream_latency = { detected: true };
    expect(assessObservationWindow(evidence, target).code).toBe("ANOMALY_OBSERVED");
  });
  it("flags real RSS growth and HTTP failures independently of root evidence", () => {
    const evidence = baseline();
    evidence.envelope.observation.metadata.summary.vmrss_mb_delta = 8;
    expect(assessObservationWindow(evidence, target).code).toBe("ANOMALY_OBSERVED");
    evidence.envelope.observation.metadata.summary.vmrss_mb_delta = 0;
    evidence.envelope.observation.metadata.application_metrics = { identity: { identity_verified: true },
      delta: { http_requests: 10, http_failures: 1, http_duration_ms: 100 }, max: { http_recent_p95_latency_ms: 20 } };
    expect(assessObservationWindow(evidence, target).code).toBe("ANOMALY_OBSERVED");
  });
});
