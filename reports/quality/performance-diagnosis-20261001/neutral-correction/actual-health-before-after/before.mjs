// These are observation thresholds, not a service SLO or proof of a root cause.
const finite = value => typeof value === "number" && Number.isFinite(value) ? value : null;
const nonnegative = value => finite(value) !== null && value >= 0 ? value : null;
const positiveInteger = value => Number.isSafeInteger(value) && value > 0;

export function assessObservationWindow(evidence, target = {}) {
  const metadata = evidence?.envelope?.observation?.metadata || {};
  const summary = metadata.summary || {};
  const identity = metadata.process_identity || {};
  const binding = target.process_binding || {};
  const scope = evidence?.envelope?.scope || {};
  const decision = evidence?.classification?.decision;
  const samples = metadata.sample_count ?? evidence?.envelope?.quality?.sample_count;
  const expectedPid = target.pid ?? binding.pid;
  const trusted = ["ACCEPT_SUPPORT", "ACCEPT_LIMITED"].includes(decision)
    && identity.verified === true
    && Boolean(binding.boot_id) && Boolean(target.agent_id) && scope.agent_id === target.agent_id
    && scope.pid === expectedPid
    && positiveInteger(expectedPid) && identity.pid === expectedPid
    && positiveInteger(binding.process_start_ticks) && identity.start_ticks === binding.process_start_ticks
    && Number.isSafeInteger(samples) && samples >= 5
    && nonnegative(metadata.window_duration_seconds) >= 1;
  if (!trusted) return { code: "INSUFFICIENT_OBSERVABILITY", checked: [], anomalies: [],
    detail: "采样数量、目标进程身份或证据准入不完整，不能判断本次检查正常。" };

  const checked = [];
  const anomalies = [];
  const cpu = nonnegative(summary.process_cpu_core_usage ?? summary.process_cpu_percent);
  const rss = nonnegative(summary.vmrss_mb);
  const rssDelta = finite(summary.vmrss_mb_delta);
  if (cpu !== null) {
    checked.push("进程 CPU < 50%（单核口径）");
    if (cpu >= 50) anomalies.push(`进程 CPU ${cpu.toFixed(1)}%`);
  }
  if (rss !== null && rssDelta !== null) {
    checked.push("窗口 RSS 增量 < 8 MiB");
    if (rssDelta >= 8) anomalies.push(`窗口 RSS 增量 ${rssDelta.toFixed(1)} MiB`);
  }
  // Writing bytes, retaining memory and a busy peer are not themselves faults.
  const abnormalSignals = {
    network_latency: "HTTP 网络路径耗时升高（未确认丢包或重传）",
    downstream_latency: "下游调用耗时升高", io_latency: "I/O 延迟升高",
    lock_contention: "锁等待", queue_backlog: "队列积压", load_saturation: "入口负载超过完成能力",
    jvm_gc: "分配与 GC 活动", http_service_degradation: "HTTP 耗时或失败率超出观测阈值",
  };
  for (const [name, label] of Object.entries(abnormalSignals)) {
    if (metadata.signals?.[name]?.detected === true) anomalies.push(label);
  }
  const app = metadata.application_metrics;
  const requests = nonnegative(app?.delta?.http_requests);
  const failures = nonnegative(app?.delta?.http_failures);
  const duration = nonnegative(app?.delta?.http_duration_ms);
  const p95 = nonnegative(app?.max?.http_recent_p95_latency_ms);
  if (app?.identity?.identity_verified === true && requests > 0 && failures !== null
      && failures <= requests && duration !== null && p95 !== null) {
    checked.push("HTTP 均值 < 500 ms、近期 P95 < 1000 ms、失败率 < 5%");
    if (duration / requests >= 500 || p95 >= 1000 || failures / requests >= 0.05)
      anomalies.push("HTTP 耗时或失败率超出观测阈值");
  }
  const unmeasured = "未检查磁盘操作延迟、丢包/重传、内存限额和全部业务接口。";
  if (anomalies.length) return { code: "ANOMALY_OBSERVED", checked, anomalies,
    detail: `已观测：${[...new Set(anomalies)].join("；")}。具体原因仍需独立验证。${unmeasured}` };
  if (cpu === null || rss === null || rssDelta === null) return {
    code: "INSUFFICIENT_OBSERVABILITY", checked, anomalies,
    detail: `缺少有效 CPU 或 RSS 趋势，不能把没有支持结论当作正常。${unmeasured}`,
  };
  return { code: "NORMAL_OBSERVED", checked, anomalies,
    detail: `已检查：${checked.join("；")}，本窗口未超出观测阈值。${unmeasured}` };
}
