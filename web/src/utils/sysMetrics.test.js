import { describe, expect, it } from "vitest";
import { normalizeSysMetrics } from "./sysMetrics";

describe("normalizeSysMetrics", () => {
  it("restores historical v1 samples without inventing missing dimensions", () => {
    const result = normalizeSysMetrics({
      schema_version: "sys_metrics.v1",
      pid: 277641,
      samples: [
        { offset_sec: 0, rss_kb: 9516, threads: 6, fd_count: 6 },
        { offset_sec: 1, rss_kb: 9532, threads: 7, fd_count: 8 },
      ],
    });

    expect(result.compatibility_mode).toBe("SYS_METRICS_V1_DERIVED");
    expect(result.sample_count).toBe(2);
    expect(result.summary.vmrss_mb).toBeCloseTo(9.31, 2);
    expect(result.summary.thread_count).toBe(7);
    expect(result.summary.thread_trend).toBe("increasing");
    expect(result.summary.fd_count).toBe(8);
    expect(result.summary.avg_cpu_sys_pct).toBeNull();
    expect(result.summary.net_rx_kbps).toBeNull();
  });

  it("preserves the current summary contract", () => {
    const source = {
      schema_version: "sys_metrics.v2",
      sample_count: 15,
      summary: { avg_cpu_sys_pct: 4.2, thread_count: 8 },
    };

    expect(normalizeSysMetrics(source)).toEqual({
      ...source,
      compatibility_mode: null,
    });
  });

  it("rejects an object with neither a summary nor samples", () => {
    expect(normalizeSysMetrics({ schema_version: "sys_metrics.v1" })).toBeNull();
  });
});
