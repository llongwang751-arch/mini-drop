/**
 * 展示分析器返回的类型化 sections：堆对象、引用路径、I/O、分配追踪、BOLT 等各有对应表格。
 * 页面负责单位、筛选和分页；没有测到的值显示未知/未测量，不用零值伪装完整证据。
 */
import { useState } from "react";
import type { ReactNode } from "react";
import { bytes, number } from "../api";

type Row = Record<string, unknown>;
const rows = (value: unknown): Row[] =>
  Array.isArray(value)
    ? (value.filter((v) => v && typeof v === "object") as Row[])
    : [];
const record = (value: unknown): Row =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Row)
    : {};
// 展示兜底不把缺失值当零；对象与数组只转为文本，不解释成可执行内容。
const display = (v: unknown): string =>
  v == null
    ? "未测量"
    : Array.isArray(v)
      ? v.map(display).join(" → ")
      : typeof v === "object"
        ? Object.entries(v)
            .map(([k, x]) => k + ": " + display(x))
            .join(" · ")
        : String(v);
const amount = (v: unknown, unit = ""): string =>
  typeof v === "number" && Number.isFinite(v)
    ? unit === "bytes"
      ? bytes(v)
      : number(Math.round(v * 100) / 100) + (unit ? " " + unit : "")
    : "未测量";

export function ResultTable({
  title,
  data,
  columns,
}: {
  title: string;
  data: Row[];
  columns: [string, string, string?][];
}) {
  const [search, setSearch] = useState(""),
    [page, setPage] = useState(0);
  if (!data.length) return null;
  const filtered = data.filter((r) =>
    columns.some(([k]) =>
      display(r[k]).toLowerCase().includes(search.toLowerCase()),
    ),
  );
  // 只分页渲染，筛选仍基于完整结果数组；避免大表一次创建过多 DOM 节点。
  const pages = Math.max(1, Math.ceil(filtered.length / 20)),
    current = Math.min(page, pages - 1);
  return (
    <section className="panel analysis-table">
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          <span className="section-note">{number(filtered.length)} 条记录</span>
        </div>
        <input
          className="result-filter"
          type="search"
          aria-label={"筛选" + title}
          placeholder="筛选…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
        />
      </div>
      <div
        className="table-scroll"
        tabIndex={0}
        role="region"
        aria-label={title + "表格"}
      >
        <table className="data-table">
          <thead>
            <tr>
              {columns.map(([key, label]) => (
                <th key={key}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.slice(current * 20, (current + 1) * 20).map((r, i) => (
              <tr key={i}>
                {columns.map(([key, , unit]) => (
                  <td className="mono result-cell" key={key}>
                    {unit ? amount(r[key], unit) : display(r[key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="result-pagination">
          <button
            className="button secondary small"
            disabled={!current}
            onClick={() => setPage(current - 1)}
          >
            上一页
          </button>
          <span className="mono">
            {current + 1} / {pages}
          </span>
          <button
            className="button secondary small"
            disabled={current + 1 >= pages}
            onClick={() => setPage(current + 1)}
          >
            下一页
          </button>
        </div>
      )}
    </section>
  );
}

function Metrics({ values }: { values: [string, unknown, string?][] }) {
  return (
    <dl className="analysis-metrics">
      {values.map(([label, value, unit]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd className="mono">{amount(value, unit)}</dd>
        </div>
      ))}
    </dl>
  );
}
function Series({
  title,
  data,
  xKey,
  yKey,
  unit,
}: {
  title: string;
  data: Row[];
  xKey: string;
  yKey: string;
  unit: string;
}) {
  const points = data.filter(
    (r) =>
      typeof r[xKey] === "number" &&
      typeof r[yKey] === "number" &&
      Number.isFinite(r[yKey]),
  );
  if (!points.length) return null;
  const xs = points.map((p) => p[xKey] as number),
    ys = points.map((p) => p[yKey] as number);
  const minX = Math.min(...xs),
    spanX = Math.max(...xs) - minX || 1,
    maxY = Math.max(...ys, 1);
  const path = points
    .map(
      (p, i) =>
        (i ? "L" : "M") +
        (16 + (((p[xKey] as number) - minX) / spanX) * 768).toFixed(2) +
        "," +
        (134 - ((p[yKey] as number) / maxY) * 116).toFixed(2),
    )
    .join(" ");
  return (
    <figure className="result-series">
      <figcaption>
        <strong>{title}</strong>
        <span className="mono">
          峰值 {amount(Math.max(...ys), unit)} · {points.length} 个观测点
        </span>
      </figcaption>
      <svg
        role="img"
        aria-label={title + "，峰值 " + amount(Math.max(...ys), unit)}
        viewBox="0 0 800 150"
        preserveAspectRatio="none"
      >
        <path className="series-grid" d="M16 18H784M16 76H784M16 134H784" />
        <path className="series-line" d={path} fill="none" />
        {points.length === 1 && (
          <circle cx="16" cy={134 - (ys[0] / maxY) * 116} r="3" />
        )}
      </svg>
      <div className="series-axis">
        <span>观测开始</span>
        <span>观测结束</span>
      </div>
    </figure>
  );
}
function Limits({ section }: { section: Row }) {
  const values = Array.isArray(section.limitations) ? section.limitations : [];
  return values.length ? (
    <ul className="section-limitations">
      {values.map((v, i) => (
        <li key={i}>{display(v)}</li>
      ))}
    </ul>
  ) : null;
}
function Section({
  title,
  subtitle,
  children,
  data,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  data: Row;
}) {
  return (
    <section className="panel typed-result">
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          <span className="section-note">{subtitle}</span>
        </div>
      </div>
      {children}
      <Limits section={data} />
    </section>
  );
}
export default function AnalysisSections({
  sections,
}: {
  sections?: Record<string, Row>;
}) {
  if (!sections) return null;
  const heap = sections.heap,
    io = sections.io,
    memory = sections.memory,
    resources = sections.resources,
    binary = sections.binary,
    script = sections.script;
  return (
    <div className="analysis-sections">
      {heap && (
        <>
          <Section
            title="堆内存概览"
            subtitle={
              display(heap.format) +
              " · " +
              display(heap.profile_type || "对象保留关系")
            }
            data={heap}
          >
            <Metrics
              values={[
                ["对象数量", record(heap.totals).objects],
                ["浅层内存", record(heap.totals).shallow_bytes, "bytes"],
                ["GC Roots", record(heap.totals).root_count],
                [
                  "采样权重",
                  record(heap.totals).sample_weight,
                  String(heap.unit || ""),
                ],
              ]}
            />
          </Section>
          <ResultTable
            title="采样口径"
            data={rows(heap.sample_types)}
            columns={[
              ["type", "类型"],
              ["unit", "单位"],
              ["total", "总权重", ""],
            ]}
          />
          <ResultTable
            title="类实例与内存"
            data={rows(heap.classes)}
            columns={[
              ["class_name", "类"],
              ["instances", "实例数"],
              ["shallow_bytes", "浅层内存", "bytes"],
              ["retained_bytes", "保留内存", "bytes"],
            ]}
          />
          <ResultTable
            title="支配对象 · 按保留内存"
            data={rows(heap.dominators)}
            columns={[
              ["object_id", "对象"],
              ["class_name", "类型"],
              ["shallow_bytes", "浅层内存", "bytes"],
              ["retained_bytes", "保留内存", "bytes"],
            ]}
          />
          {!!rows(heap.leak_paths).length && (
            <section className="panel leak-paths">
              <div className="section-heading">
                <div>
                  <h2>大对象的 GC Root 引用路径</h2>
                  <span className="section-note">
                    候选对象需结合生命周期判断，不直接等同于泄漏
                  </span>
                </div>
              </div>
              {rows(heap.leak_paths).map((p, i) => (
                <details key={i}>
                  <summary>
                    <span className="mono">{display(p.object_id)}</span>
                    <strong>{amount(p.retained_bytes, "bytes")}</strong>
                  </summary>
                  <ol>
                    {rows(p.path).map((step, j) => (
                      <li key={j}>
                        <span className="mono">{display(step.object_id)}</span>
                        <strong>{display(step.class_name)}</strong>
                        {step.root_kind != null && (
                          <small>{display(step.root_kind)}</small>
                        )}
                      </li>
                    ))}
                  </ol>
                </details>
              ))}
            </section>
          )}
        </>
      )}
      {io && (
        <>
          <Section
            title="I/O 观测"
            subtitle="吞吐量、延迟分位与观测队列"
            data={io}
          >
            <Metrics
              values={[
                ["操作次数", io.operations],
                ["失败操作", io.error_operations],
                ["读 / 写字节", io.bytes, "bytes"],
                ["吞吐量", io.throughput_bytes_per_second, "B/s"],
                ["IOPS", io.iops],
                ["P50 延迟", record(io.latency_ns).p50, "ns"],
                ["P95 延迟", record(io.latency_ns).p95, "ns"],
                ["P99 延迟", record(io.latency_ns).p99, "ns"],
                ["队列峰值", record(io.queue).max_depth],
                ["丢失事件", io.lost_events],
              ]}
            />
            <Series
              title="每个时间桶的 I/O 字节"
              data={rows(io.timeseries)}
              xKey="time_ns"
              yKey="bytes"
              unit="bytes"
            />
          </Section>
          {[
            ["devices", "设备热点"],
            ["processes", "进程热点"],
            ["files", "文件热点"],
          ].map(([key, title]) => (
            <ResultTable
              key={key}
              title={title}
              data={rows(io[key])}
              columns={[
                ["name", "目标"],
                ["operations", "操作"],
                ["bytes", "字节", "bytes"],
                ["duration_ns", "累计延迟", "ns"],
              ]}
            />
          ))}
        </>
      )}
      {memory && (
        <>
          <Section
            title="内存分配与存活"
            subtitle="观测期间的分配、释放及缺页"
            data={memory}
          >
            <Metrics
              values={[
                ["累计分配", memory.allocated_bytes, "bytes"],
                ["累计释放", memory.freed_bytes, "bytes"],
                ["结束时存活", memory.live_bytes, "bytes"],
                ["存活峰值", memory.peak_live_bytes, "bytes"],
                ["分配次数", memory.allocations],
                ["分配失败", memory.failed_allocations],
                ["释放次数", memory.frees],
                ["轻微缺页", record(memory.pagefaults).minor],
                ["严重缺页", record(memory.pagefaults).major],
                ["未分类缺页", record(memory.pagefaults).unknown],
                ["未知释放", memory.unknown_frees],
                ["丢失事件", memory.lost_events],
              ]}
            />
            <Series
              title="存活内存"
              data={rows(memory.timeseries)}
              xKey="time_ns"
              yKey="live_bytes"
              unit="bytes"
            />
          </Section>
          <ResultTable
            title="观测结束仍存活的分配"
            data={rows(memory.survivors)}
            columns={[
              ["pid", "PID"],
              ["address", "地址"],
              ["bytes", "存活大小", "bytes"],
              ["stack", "分配栈"],
              ["classification", "判定依据"],
            ]}
          />
        </>
      )}
      {resources && (
        <Section
          title="资源观测与采集开销"
          subtitle={display(resources.sample_count) + " 个资源快照"}
          data={resources}
        >
          <Metrics
            values={[
              ["CPU 峰值", record(resources.peaks).cpu_percent, "%"],
              ["RSS 峰值", record(resources.peaks).rss_bytes, "bytes"],
              ["虚拟内存峰值", record(resources.peaks).vms_bytes, "bytes"],
              ["线程峰值", record(resources.peaks).threads],
              ["打开文件峰值", record(resources.peaks).fd_count],
              [
                "宿主机内存使用峰值",
                record(resources.peaks).memory_used_bytes,
                "bytes",
              ],
              [
                "宿主机可见总内存",
                record(resources.peaks).memory_total_bytes,
                "bytes",
              ],
            ]}
          />
          <Series
            title="CPU 使用率"
            data={rows(resources.samples)}
            xKey="timestamp_ns"
            yKey="cpu_percent"
            unit="%"
          />
          <Series
            title="常驻内存 RSS"
            data={rows(resources.samples)}
            xKey="timestamp_ns"
            yKey="rss_bytes"
            unit="bytes"
          />
          {typeof resources.network_scope === "string" && (
            <p className="section-note">
              网络统计范围：
              {resources.network_scope === "target_network_namespace_total"
                ? "目标的 network namespace 总量，同 namespace 内其他进程也会计入。"
                : resources.network_scope}
            </p>
          )}
          <Series
            title="网络累计接收"
            data={rows(resources.samples)}
            xKey="timestamp_ns"
            yKey="network_rx_bytes"
            unit="bytes"
          />
          <Series
            title="网络累计发送"
            data={rows(resources.samples)}
            xKey="timestamp_ns"
            yKey="network_tx_bytes"
            unit="bytes"
          />
          {resources.context_switch_scope != null && (
            <p className="section-note">
              上下文切换计数范围：{display(resources.context_switch_scope)}
            </p>
          )}
          <ResultTable
            title="资源原始观测点"
            data={rows(resources.samples)}
            columns={[
              ["cpu_percent", "CPU", "%"],
              ["rss_bytes", "RSS", "bytes"],
              ["read_bytes", "累计读取", "bytes"],
              ["write_bytes", "累计写入", "bytes"],
              ["voluntary_context_switches", "主动切换"],
              ["involuntary_context_switches", "被动切换"],
              ["collector_rss_bytes", "采集器 RSS", "bytes"],
            ]}
          />
          {!!Object.keys(record(resources.collector_overhead)).length && (
            <>
              <h3>采集器自身开销</h3>
              <Metrics
                values={[
                  [
                    "用户态 CPU",
                    record(resources.collector_overhead).user_cpu_seconds,
                    "s",
                  ],
                  [
                    "内核态 CPU",
                    record(resources.collector_overhead).system_cpu_seconds,
                    "s",
                  ],
                  [
                    "子进程用户态 CPU",
                    record(resources.collector_overhead)
                      .children_user_cpu_seconds,
                    "s",
                  ],
                  [
                    "子进程内核态 CPU",
                    record(resources.collector_overhead)
                      .children_system_cpu_seconds,
                    "s",
                  ],
                ]}
              />
              <p className="section-note">
                统计范围：{display(record(resources.collector_overhead).scope)}
              </p>
            </>
          )}
          {!!Object.keys(record(resources.dmesg)).length && (
            <div className="resource-kernel-log">
              <h3>内核日志证据</h3>
              <p className="section-note">
                {record(resources.dmesg).status === "available"
                  ? "已读取授权范围内的内核日志"
                  : "未取得内核日志"}{" "}
                · {display(record(resources.dmesg).reason)}
              </p>
              {Array.isArray(record(resources.dmesg).lines) &&
                (record(resources.dmesg).lines as unknown[]).length > 0 && (
                  <pre>
                    {(record(resources.dmesg).lines as unknown[])
                      .map(display)
                      .join("\n")}
                  </pre>
                )}
              {record(resources.dmesg).truncated === true && (
                <small>输出已达到安全上限，显示已截断。</small>
              )}
            </div>
          )}
        </Section>
      )}
      {binary && (
        <>
          <Section
            title="BOLT 二进制优化证据"
            subtitle={display(binary.format)}
            data={binary}
          >
            <Metrics
              values={[
                [
                  "分支观测",
                  binary.mode === "branch"
                    ? record(binary.totals).branches
                    : undefined,
                ],
                [
                  "分支预测失败",
                  binary.mode === "branch"
                    ? record(binary.totals).mispredictions
                    : undefined,
                ],
              ]}
            />
            {binary.mode === "basic_samples" && (
              <p className="muted">
                本次仅采集基础样本，未测量分支及预测失败。
              </p>
            )}
          </Section>
          <ResultTable
            title="函数采样与分支"
            data={rows(binary.functions)}
            columns={[
              ["name", "函数"],
              ["samples", "样本"],
              ["branches", "分支"],
              ["mispredictions", "预测失败"],
            ]}
          />
          <ResultTable
            title="分支跳转热点"
            data={rows(binary.branches)}
            columns={[
              ["from_function", "来源函数"],
              ["from_offset", "偏移"],
              ["to_function", "目标函数"],
              ["to_offset", "偏移"],
              ["count", "次数"],
              ["mispredictions", "预测失败"],
            ]}
          />
        </>
      )}
      {script && (
        <Section
          title="受控诊断输出"
          subtitle={
            display(script.template) + " · exit " + display(script.exit_code)
          }
          data={script}
        >
          <Metrics values={[["执行耗时", script.duration_ms, "ms"]]} />
          {script.truncated === true && (
            <p className="field-error">输出已达到采集上限，内容已截断。</p>
          )}
          <h3>标准输出</h3>
          <pre className="script-output">{display(script.stdout)}</pre>
          {script.stderr ? (
            <>
              <h3>错误输出</h3>
              <pre className="script-output">{display(script.stderr)}</pre>
            </>
          ) : null}
        </Section>
      )}
    </div>
  );
}
