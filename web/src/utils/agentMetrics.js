// Missing telemetry must never be rendered as a measured zero.
export function agentMetric(value, unit = "", digits = 1) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? `${value.toFixed(digits)}${unit}` : "未上报";
}

// Only a new source timestamp represents a new measurement. Preserve gaps for
// missing values and do not turn browser polling time into telemetry freshness.
export function appendMetricSample(history, timestamp, value) {
  if (!Number.isFinite(timestamp) || timestamp <= 0 ||
      (history.length && timestamp <= history[history.length - 1].ts)) return history;
  const measured = typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  return [...history, { ts: timestamp, value: measured }].slice(-60);
}
