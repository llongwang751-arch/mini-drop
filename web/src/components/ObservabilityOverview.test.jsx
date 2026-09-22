import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import ObservabilityOverview, { buildObservationModel } from "./ObservabilityOverview";

const detail = {
  status: "COMPLETED",
  target: {
    service: "orders-api",
    agent_id: "worker-1",
    pid: 4201,
    process_binding: {
      executable_identity: "python3.12",
      boot_id: "boot-1",
      process_start_ticks: 123,
      namespace_pid: 17,
    },
  },
};

const systemEvidence = {
  evidence_id: "ev-sys-1",
  role: "NEUTRAL",
  envelope: {
    evidence_type: "SYS_METRICS_SYS_METRICS",
    source: { tool_name: "sys_metrics" },
    quality: { sample_count: 15 },
    observation: {
      metadata: {
        sample_count: 15,
        window_duration_seconds: 14,
        process_identity: { pid: 4201 },
        summary: {
          process_cpu_core_usage: 23.4,
          vmrss_mb: 128.2,
          vmrss_mb_max: 130,
          thread_count: 12,
          thread_count_max: 12,
          fd_count: 31,
          fd_count_max: 32,
        },
        application_metrics: null,
      },
    },
  },
  classification: { decision: "ACCEPT_LIMITED", can_support_conclusion: false },
};

const resources = {
  evidence: [systemEvidence, {
    evidence_id: "ev-sys-manifest",
    envelope: {
      evidence_type: "SYS_METRICS_MANIFEST",
      source: { tool_name: "sys_metrics" },
      observation: { metadata: {} },
    },
    classification: { decision: "ACCEPT_LIMITED", can_support_conclusion: false },
  }],
  reports: [{ verification: { status: "INSUFFICIENT_EVIDENCE" } }],
  toolCalls: [{
    tool_call_id: "tool-1",
    tool_name: "collect_sys_metrics",
    status: "COMPLETED",
    arguments: { agent_id: "worker-1", pid: 4201, duration_seconds: 15 },
  }],
};

describe("ObservabilityOverview", () => {
  it("treats a completed measured window without support as no verified fault", () => {
    const model = buildObservationModel(detail, resources);
    expect(model.assessment.code).toBe("NO_VERIFIED_FAULT");
    expect(model.target.pid).toBe(4201);
    expect(model.metrics.find((item) => item.key === "process-cpu")?.value).toBe("23.4%");
    expect(model.hasApplicationMetrics).toBe(false);
  });

  it("keeps the default view concise and exposes provenance on demand", () => {
    render(<ObservabilityOverview detail={detail} resources={resources} />);
    const panel = screen.getByLabelText("目标进程与性能观测");
    expect(within(panel).getByText("本次观测窗口未确认故障")).toBeInTheDocument();
    expect(within(panel).getByText("23.4%")).toBeInTheDocument();
    expect(within(panel).getByText("128.2 MiB")).toBeInTheDocument();
    expect(within(panel).getByText("进程身份已校验")).toBeInTheDocument();

    const disclosure = within(panel).getByText("查看采集过程、完整指标和业务接入情况");
    expect(disclosure.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(disclosure);
    expect(disclosure.closest("details")).toHaveAttribute("open");
    expect(within(panel).getByText(/未接入 OTel\/业务指标/)).toBeInTheDocument();
    expect(within(panel).getByText("Agent 读取 /proc 与进程计数器")).toBeInTheDocument();
  });

  it("does not claim a normal window when no system baseline exists", () => {
    const model = buildObservationModel(detail, { evidence: [], reports: [], toolCalls: [] });
    expect(model.assessment.code).toBe("NO_BASELINE");
  });

  it("does not turn a failed run with partial metrics into a healthy window", () => {
    const model = buildObservationModel({ ...detail, status: "FAILED" }, resources);
    expect(model.assessment.code).toBe("INCOMPLETE");
  });

  it("shows process-bound HTTP business metrics inside the disclosure", () => {
    const withBusinessMetrics = structuredClone(resources);
    withBusinessMetrics.evidence[0].envelope.observation.metadata.application_metrics = {
      delta: { http_requests: 8, http_failures: 1, http_duration_ms: 400 },
      max: { http_recent_p95_latency_ms: 120 },
    };

    render(<ObservabilityOverview detail={detail} resources={withBusinessMetrics} />);
    fireEvent.click(screen.getByText("查看采集过程、完整指标和业务接入情况"));

    const business = screen.getByLabelText("业务请求指标");
    expect(within(business).getByText("8")).toBeInTheDocument();
    expect(within(business).getByText("50 ms")).toBeInTheDocument();
    expect(within(business).getByText("120 ms")).toBeInTheDocument();
    expect(screen.getByText("已随本次采集返回业务指标。")).toBeInTheDocument();
  });
});
