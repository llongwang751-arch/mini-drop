import { describe, it, expect } from "vitest";
import { agentMetric } from "./agentMetrics";
describe("Agent telemetry", () => {
  it("distinguishes absent and invalid values from measured zero", () => {
    for (const value of [undefined, null, NaN, Infinity, -1, ""]) expect(agentMetric(value)).toBe("未上报");
    expect(agentMetric(0, "%")).toBe("0.0%");
    expect(agentMetric(10.25, " MB")).toBe("10.3 MB");
  });
});

import { appendMetricSample } from "./agentMetrics";
it("keeps source timestamps, deduplicates retries, rejects out-of-order samples and preserves unknown values", () => {
  const first = appendMetricSample([], 1000, 180);
  expect(first).toEqual([{ ts: 1000, value: 180 }]);
  expect(appendMetricSample(first, 1000, 190)).toBe(first);
  expect(appendMetricSample(first, 900, 10)).toBe(first);
  expect(appendMetricSample(first, undefined, 10)).toBe(first);
  expect(appendMetricSample(first, 2000, -1)[1]).toEqual({ ts: 2000, value: null });
  expect(appendMetricSample(first, 2000, 0)[1].value).toBe(0);
});
