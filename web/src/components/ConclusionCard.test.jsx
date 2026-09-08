import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ConclusionCard from "./ConclusionCard";

describe("ConclusionCard", () => {
  it("shows verification, conclusion and limitations with Chinese primary copy", () => {
    render(
      <ConclusionCard report={{
        confidence: 0.72,
        verification: { status: "PARTIAL_WITHOUT_COUNTER" },
        conclusion: "Continuous profile shows no synchronous write activity and instead reveals a CPU-bound hot path",
        next_actions: ["Continuous profiling over an extended window captures the synchronous write/fsync call path in the python3.12 target that single-shot probes missed due to transient I/O activity."],
        limitations: ["insufficient evidence"],
      }} />,
    );

    expect(screen.getByText("阶段性根因")).toBeInTheDocument();
    expect(screen.getByText("证据门禁：部分支持，缺少独立反证或对照")).toBeInTheDocument();
    expect(screen.getByText(/连续性能采集没有发现同步写活动/)).toBeInTheDocument();
    expect(screen.getByText(/延长连续性能采集窗口/)).toBeInTheDocument();
    expect(screen.getByText("结论边界：证据不足")).toBeInTheDocument();
  });

  it("keeps an unknown verification code in collapsed technical details", () => {
    render(
      <ConclusionCard report={{
        confidence: 0,
        verification: { status: "FUTURE_VERIFICATION" },
        conclusion: "暂无可信结论",
      }} />,
    );

    expect(screen.getByText("证据门禁：验证状态未知")).toBeInTheDocument();
    const details = screen.getByText("查看技术详情").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("FUTURE_VERIFICATION");
  });

  it("recovers a concrete Java allocation root cause from immutable legacy claims", () => {
    render(
      <ConclusionCard report={{
        confidence: 0.6,
        verification: { status: "PARTIAL_WITHOUT_COUNTER" },
        conclusion: "SUPPORTED：可信采集证据支持假设“JVM 可能存在业务热点、GC 压力或锁竞争”。",
        claims: [
          {
            claim_type: "HYPOTHESIS_PREDICATE",
            statement: "async-profiler alloc profile captured Java path Hotspot$$Lambda.0x00001.run at 100.0%",
            valid: true,
          },
          {
            claim_type: "TOP_FUNCTION_PERCENT",
            statement: "Hotspot.lambda$startWorkers$1 占 100.0% 样本",
            valid: true,
          },
          {
            claim_type: "TOP_FUNCTION_PERCENT",
            statement: "byte[] 占 100.0% 样本",
            valid: true,
          },
        ],
      }} />,
    );

    expect(screen.getByText("阶段性根因")).toBeInTheDocument();
    expect(screen.getByText(/Java 对象分配热点定位/)).toBeInTheDocument();
    expect(screen.getByText(/Hotspot\.lambda\$startWorkers\$1/)).toBeInTheDocument();
    expect(screen.getByText(/主要分配对象为/)).toBeInTheDocument();
    expect(screen.getByText(/没有独立证明 GC 暂停或锁竞争/)).toBeInTheDocument();
    expect(screen.queryByText(/SUPPORTED/)).not.toBeInTheDocument();
  });

  it("uses the final root-cause title only for a fully verified report", () => {
    render(
      <ConclusionCard report={{
        confidence: 0.91,
        verification: { status: "VERIFIED" },
        conclusion: "根因结论：Go CPU 热点定位在 `main.goCPUHotFunction`。",
      }} />,
    );

    expect(screen.getByText("根因结论")).toBeInTheDocument();
    expect(screen.getByText(/Go CPU 热点定位/)).toBeInTheDocument();
  });
});
