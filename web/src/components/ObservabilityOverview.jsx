import { useMemo } from "react";
import {
  ApiOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  FundOutlined,
} from "@ant-design/icons";
import { Tag, Tooltip } from "antd";
import { diagnosticStatusLabel, diagnosticToolLabel } from "../utils/diagnosisDisplay";

const FINISHED = new Set(["COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"]);
const COMPLETED_WINDOWS = new Set(["COMPLETED", "INSUFFICIENT_EVIDENCE", "PARTIAL", "PARTIAL_COMPLETED"]);
const SUCCESSFUL_TOOLS = new Set(["COMPLETED", "DONE", "SUCCEEDED", "SUCCESS"]);

const COLLECTION_METHODS = {
  collect_sys_metrics: "Agent 读取 /proc 与进程计数器",
  start_perf_profile: "Linux perf_event 采样目标 PID",
  start_continuous_profile: "Linux perf 分窗口连续采样",
  start_pyspy_profile: "py-spy 附加到 Python 进程",
  start_jvm_profile: "async-profiler 附加到 JVM",
  collect_go_profile: "读取已登记的 Go pprof 端点",
  collect_memory_profile: "读取目标进程 smaps",
  start_ebpf_io_profile: "eBPF 观测内核块设备事件",
  collect_database_diagnostics: "只读查询数据库锁与等待视图",
  get_agent_status: "读取 Agent 心跳与采集能力",
};

function rows(value) {
  return Array.isArray(value) ? value : [];
}

function number(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function compact(value, digits = 1) {
  const parsed = number(value);
  if (parsed === null) return "未采集";
  return parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function evidenceMetadata(item) {
  return item?.envelope?.observation?.metadata || item?.observation?.metadata || {};
}

function evidenceSource(item) {
  return item?.envelope?.source || item?.source || {};
}

function isSystemMetrics(item) {
  const source = evidenceSource(item);
  const type = String(item?.envelope?.evidence_type || "").toUpperCase();
  const metadata = evidenceMetadata(item);
  return type === "SYS_METRICS_SYS_METRICS"
    || (["sys_metrics", "collect_sys_metrics"].includes(source.tool_name)
      && metadata.summary
      && Object.keys(metadata.summary).length > 0);
}

export function buildObservationModel(detail = {}, resources = {}) {
  const evidence = rows(resources.evidence);
  const toolCalls = rows(resources.toolCalls);
  const reports = rows(resources.reports);
  const metricEvidence = evidence.filter(isSystemMetrics).at(-1) || null;
  const metricMetadata = metricEvidence ? evidenceMetadata(metricEvidence) : {};
  const summary = metricMetadata.summary || {};
  const processIdentity = metricMetadata.process_identity || {};
  const target = detail.target || {};
  const binding = target.process_binding || {};
  const status = String(detail.status || "").toUpperCase();
  const verified = reports.some((report) => String(report?.verification?.status || "").toUpperCase() === "VERIFIED");
  const supportCount = evidence.filter((item) => {
    const role = String(item?.role || "").toUpperCase();
    return ["SUPPORT", "SUPPORTS", "SUPPORTED"].includes(role)
      && item?.classification?.can_support_conclusion === true;
  }).length;
  const applicationMetrics = metricMetadata.application_metrics;
  const hasApplicationMetrics = applicationMetrics !== null
    && applicationMetrics !== undefined
    && (typeof applicationMetrics !== "object" || Object.keys(applicationMetrics).length > 0);
  const applicationDelta = applicationMetrics?.delta || {};
  const applicationMax = applicationMetrics?.max || {};
  const requestCount = number(applicationDelta.http_requests);
  const failureCount = number(applicationDelta.http_failures);
  const durationMs = number(applicationDelta.http_duration_ms);
  const averageLatency = requestCount > 0 && durationMs !== null ? durationMs / requestCount : null;

  let assessment = {
    code: "WAITING",
    title: "正在建立观测窗口",
    detail: "目标绑定后会显示进程指标、采集器和证据产物。",
  };
  if (verified) {
    assessment = {
      code: "FAULT_VERIFIED",
      title: "已定位有证据支持的异常",
      detail: "根因结论已通过当前报告的证据门禁，可继续查看调用栈与修复验证。",
    };
  } else if (COMPLETED_WINDOWS.has(status) && metricEvidence && supportCount === 0) {
    assessment = {
      code: "NO_VERIFIED_FAULT",
      title: "本次观测窗口未确认故障",
      detail: "系统指标已经采集，但没有形成可验证的异常根因。这表示当前窗口未发现已验证故障，不代表服务永久健康。",
    };
  } else if (["FAILED", "CANCELLED"].includes(status)) {
    assessment = {
      code: "INCOMPLETE",
      title: status === "CANCELLED" ? "诊断已取消" : "诊断执行失败",
      detail: metricEvidence
        ? "页面保留已采到的进程快照，但本次流程未完成，不能据此判断正常或异常。"
        : "本次流程未完成，也没有形成可展示的系统指标基线。",
    };
  } else if (metricEvidence) {
    assessment = {
      code: "OBSERVED",
      title: "已采到目标进程，诊断仍在进行",
      detail: "下方数字来自当前目标和时间窗，结论会在独立证据完成后更新。",
    };
  } else if (FINISHED.has(status)) {
    assessment = {
      code: "NO_BASELINE",
      title: "诊断结束，但缺少系统指标基线",
      detail: "本次没有可展示的进程 CPU、内存或线程快照，无法判断当前窗口是否正常。",
    };
  }

  const processCpu = number(summary.process_cpu_percent ?? summary.process_cpu_core_usage);
  const hostCpu = [summary.avg_cpu_user_pct, summary.avg_cpu_sys_pct]
    .map(number)
    .filter((value) => value !== null)
    .reduce((total, value) => total + value, 0);
  const hasHostCpu = number(summary.avg_cpu_user_pct) !== null || number(summary.avg_cpu_sys_pct) !== null;

  return {
    assessment,
    target: {
      service: target.service || detail.service || "未指定服务",
      pid: target.pid ?? binding.pid ?? processIdentity.pid,
      namespacePid: binding.namespace_pid ?? processIdentity.namespace_pid,
      executable: binding.executable_identity,
      agent: target.agent_id || binding.agent_id,
      environment: target.environment || detail.environment,
      bindingVerified: Boolean(binding.boot_id && binding.process_start_ticks),
    },
    metrics: [
      { key: "process-cpu", label: "进程 CPU", value: processCpu === null ? "未采集" : `${compact(processCpu)}%`, hint: "目标进程在采样窗口中的 CPU 使用" },
      { key: "host-cpu", label: "主机 CPU", value: hasHostCpu ? `${compact(hostCpu)}%` : "未采集", hint: "用户态与内核态 CPU 之和" },
      { key: "rss", label: "进程 RSS", value: number(summary.vmrss_mb) === null ? "未采集" : `${compact(summary.vmrss_mb)} MiB`, hint: `窗口峰值 ${number(summary.vmrss_mb_max) === null ? "未采集" : `${compact(summary.vmrss_mb_max)} MiB`}` },
      { key: "threads", label: "线程", value: compact(summary.thread_count, 0), hint: `窗口峰值 ${compact(summary.thread_count_max, 0)}` },
      { key: "fd", label: "文件描述符", value: compact(summary.fd_count, 0), hint: `窗口峰值 ${compact(summary.fd_count_max, 0)}` },
      { key: "iowait", label: "CPU I/O wait", value: number(summary.avg_cpu_iowait_pct) === null ? "未采集" : `${compact(summary.avg_cpu_iowait_pct)}%`, hint: "主机等待块设备 I/O 的 CPU 时间" },
    ],
    metricEvidence,
    sampleCount: number(metricMetadata.sample_count ?? metricEvidence?.envelope?.quality?.sample_count),
    windowSeconds: number(metricMetadata.window_duration_seconds),
    hasApplicationMetrics,
    applicationMetrics,
    businessMetrics: [
      { key: "requests", label: "窗口请求", value: requestCount === null ? "未采集" : compact(requestCount, 0) },
      { key: "failures", label: "服务端失败", value: failureCount === null ? "未采集" : compact(failureCount, 0) },
      { key: "average-latency", label: "平均耗时", value: averageLatency === null ? "未采集" : `${compact(averageLatency)} ms` },
      { key: "p95-latency", label: "近期 P95", value: number(applicationMax.http_recent_p95_latency_ms) === null ? "未采集" : `${compact(applicationMax.http_recent_p95_latency_ms)} ms` },
    ],
    evidenceCount: evidence.length,
    admittedCount: evidence.filter((item) => item?.classification?.decision && !String(item.classification.decision).startsWith("REJECT")).length,
    toolCalls,
  };
}

function TargetValue({ label, value, mono = false }) {
  return (
    <div className="observation-target-item">
      <span>{label}</span>
      <strong className={mono ? "is-mono" : ""}>{value || "未返回"}</strong>
    </div>
  );
}

export default function ObservabilityOverview({ detail, resources }) {
  const model = useMemo(() => buildObservationModel(detail, resources), [detail, resources]);
  const { assessment, target } = model;
  const healthyWindow = assessment.code === "NO_VERIFIED_FAULT";
  const verifiedFault = assessment.code === "FAULT_VERIFIED";

  return (
    <section className="diagnosis-observation" aria-label="目标进程与性能观测">
      <header className={`observation-assessment is-${assessment.code.toLowerCase()}`}>
        <span className="observation-assessment-icon">
          {verifiedFault ? <ExclamationCircleOutlined /> : healthyWindow ? <CheckCircleOutlined /> : <FundOutlined />}
        </span>
        <div>
          <h3>{assessment.title}</h3>
          <p>{assessment.detail}</p>
        </div>
        <Tag color={verifiedFault ? "red" : healthyWindow ? "green" : "blue"}>
          {diagnosticStatusLabel(detail?.status, "状态同步中")}
        </Tag>
      </header>

      <div className="observation-target-summary">
        <strong>{target.service}</strong>
        <span>{target.executable || "进程名未返回"}</span>
        <code>{target.pid ? `PID ${target.pid}` : "PID 未返回"}</code>
        <Tag color={target.bindingVerified ? "green" : "gold"}>
          {target.bindingVerified ? "进程身份已校验" : "进程身份校验不完整"}
        </Tag>
      </div>

      <div className="observation-metrics" aria-label="当前性能数字">
        {model.metrics.filter((metric) => ["process-cpu", "rss", "threads", "fd"].includes(metric.key)).map((metric) => (
          <Tooltip title={metric.hint} key={metric.key}>
            <div className={`observation-metric ${metric.value === "未采集" ? "is-missing" : ""}`}>
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              <small>{metric.hint}</small>
            </div>
          </Tooltip>
        ))}
      </div>

      <details className="observation-details">
        <summary>查看采集过程、完整指标和业务接入情况</summary>
        <div className="observation-detail-body">
          <div className="observation-target-strip">
            <TargetValue label="服务" value={target.service} />
            <TargetValue label="进程" value={target.executable} mono />
            <TargetValue label="PID" value={target.pid} mono />
            <TargetValue label="Agent" value={target.agent} mono />
            <div className="observation-target-item">
              <span>进程绑定</span>
              <strong>{target.bindingVerified ? "PID + 启动时间已校验" : "仅当前 PID，身份校验不完整"}</strong>
            </div>
          </div>

          <div className="observation-secondary-metrics">
            {model.metrics.filter((metric) => ["host-cpu", "iowait"].includes(metric.key)).map((metric) => (
              <div key={metric.key}>
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
              </div>
            ))}
          </div>

          {model.hasApplicationMetrics && (
            <section className="observation-business-metrics" aria-label="业务请求指标">
              <div>
                <h4>业务请求</h4>
                <p>来自目标进程内的 ASGI 聚合指标，只记录计数与耗时，不采集 URL、正文和凭据。</p>
              </div>
              <div className="observation-business-grid">
                {model.businessMetrics.map((metric) => (
                  <div key={metric.key}>
                    <span>{metric.label}</span>
                    <strong>{metric.value}</strong>
                  </div>
                ))}
              </div>
            </section>
          )}

          <div className="observation-provenance">
            <div className="observation-section-heading">
              <div>
                <h4>采集过程</h4>
                <p>实际执行的是受控探针，系统不会执行模型生成的任意 Shell 命令。</p>
              </div>
              <div className="observation-evidence-counts">
                <Tag>{model.toolCalls.length} 个探针</Tag>
                <Tag>{model.evidenceCount} 份证据</Tag>
                <Tag color="blue">{model.admittedCount} 份通过或有限准入</Tag>
              </div>
            </div>

            {model.toolCalls.length > 0 ? (
              <div className="observation-probe-list">
                {model.toolCalls.map((tool, index) => {
                  const args = tool.arguments_json || tool.arguments || {};
                  const status = String(tool.status || "").toUpperCase();
                  return (
                    <article key={tool.tool_call_id || `${tool.tool_name}:${index}`}>
                      <span className={`observation-probe-status ${SUCCESSFUL_TOOLS.has(status) ? "is-done" : ""}`} />
                      <div>
                        <strong>{diagnosticToolLabel(tool.tool_name)}</strong>
                        <small>{COLLECTION_METHODS[tool.tool_name] || "受控采集器"}</small>
                      </div>
                      <code>{args.pid ? `PID ${args.pid}` : args.agent_id || "已绑定目标"}{args.duration_seconds ? ` · ${args.duration_seconds}s` : ""}{args.sample_rate ? ` · ${args.sample_rate}Hz` : ""}</code>
                      <Tag color={SUCCESSFUL_TOOLS.has(status) ? "green" : status === "FAILED" ? "red" : "processing"}>
                        {diagnosticStatusLabel(status, status || "等待")}
                      </Tag>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="observation-empty-row"><ClockCircleOutlined /> 尚未执行采集探针</div>
            )}
          </div>

          <div className="observation-instrumentation">
            <div>
              <DatabaseOutlined />
              <span><strong>系统级观测</strong>无需改业务代码，通过 Agent、/proc、perf 和运行时 profiler 采集。</span>
            </div>
            <div className={model.hasApplicationMetrics ? "is-connected" : "is-missing"}>
              <ApiOutlined />
              <span><strong>应用级观测</strong>{model.hasApplicationMetrics ? "已随本次采集返回业务指标。" : "未接入 OTel/业务指标，当前无法展示接口阶段、SQL、下游调用和业务延迟。"}</span>
            </div>
            <div>
              <CodeOutlined />
              <span><strong>故障注入</strong>故障广场调用白名单场景的固定启停接口并自动撤销，不执行用户输入命令。</span>
            </div>
          </div>

          {model.metricEvidence && (
            <footer className="observation-window-note">
              指标窗口：{model.windowSeconds === null ? "时长未返回" : `${compact(model.windowSeconds, 0)} 秒`}
              <span>·</span>{model.sampleCount === null ? "样本数未返回" : `${compact(model.sampleCount, 0)} 个样本`}
              <span>·</span>证据 {model.metricEvidence.evidence_id}
            </footer>
          )}
        </div>
      </details>
    </section>
  );
}
