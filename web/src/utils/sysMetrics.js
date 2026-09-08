function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function lastKnown(values) {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] !== null) return values[index];
  }
  return null;
}

function trend(values) {
  const known = values.filter((value) => value !== null);
  if (known.length < 2) return "unknown";
  if (known[known.length - 1] > known[0]) return "increasing";
  if (known[known.length - 1] < known[0]) return "decreasing";
  return "stable";
}

function rounded(value, digits = 2) {
  if (value === null) return null;
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

/**
 * Normalize both the current summary contract and historical sys_metrics.v1.
 *
 * The native v1 collector only sampled per-process RSS, thread count and file
 * descriptors. Missing host CPU/load/network fields deliberately remain null:
 * compatibility must never turn "not collected" into a measured zero.
 */
export function normalizeSysMetrics(payload, fallbackMetadata = null) {
  const source = payload && typeof payload === "object" ? payload : fallbackMetadata;
  if (!source || typeof source !== "object") return null;

  if (source.summary && typeof source.summary === "object") {
    return {
      ...source,
      sample_count: finiteNumber(source.sample_count)
        ?? (Array.isArray(source.samples) ? source.samples.length : null),
      compatibility_mode: null,
    };
  }

  const rawSamples = Array.isArray(source.samples) ? source.samples : [];
  const samples = rawSamples
    .map((sample, index) => {
      if (!sample || typeof sample !== "object") return null;
      return {
        offset_sec: finiteNumber(sample.offset_sec) ?? index,
        rss_mb: rounded(
          finiteNumber(sample.rss_mb)
            ?? (finiteNumber(sample.rss_kb) === null ? null : finiteNumber(sample.rss_kb) / 1024),
        ),
        threads: finiteNumber(sample.threads ?? sample.thread_count),
        fd_count: finiteNumber(sample.fd_count),
      };
    })
    .filter(Boolean);

  if (!samples.length) return null;

  const rss = samples.map((sample) => sample.rss_mb);
  const threads = samples.map((sample) => sample.threads);
  const fileDescriptors = samples.map((sample) => sample.fd_count);
  const knownRss = rss.filter((value) => value !== null);

  return {
    ...source,
    schema_version: source.schema_version || "sys_metrics.v1",
    sample_count: samples.length,
    samples,
    compatibility_mode: "SYS_METRICS_V1_DERIVED",
    available_dimensions: ["process_rss", "threads", "file_descriptors"],
    summary: {
      avg_cpu_user_pct: null,
      avg_cpu_sys_pct: null,
      avg_cpu_iowait_pct: null,
      load1m: null,
      load5m: null,
      load15m: null,
      thread_count: lastKnown(threads),
      thread_trend: trend(threads),
      fd_count: lastKnown(fileDescriptors),
      fd_trend: trend(fileDescriptors),
      net_rx_kbps: null,
      net_tx_kbps: null,
      vmrss_mb: lastKnown(rss),
      vmrss_mb_max: knownRss.length ? Math.max(...knownRss) : null,
      ctx_nonvoluntary_rate: null,
    },
  };
}

export function formatMetric(value, suffix = "") {
  const number = finiteNumber(value);
  return number === null ? "未采集" : `${number}${suffix}`;
}
