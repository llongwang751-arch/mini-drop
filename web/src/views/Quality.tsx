/** 管理员只读展示真实执行摘要；未知指标保留未验收，下载文件不包含现场原始数据。 */
import { useContext, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  ArrowClockwise,
  DownloadSimple,
  TestTube,
  ArrowUpRight,
  Info,
} from "@phosphor-icons/react";
import { api, dateTime } from "../api";
import { UserContext } from "../auth-context";
import { PageHeading, Busy, ErrorPanel, Empty } from "../components/ui";
import type { List } from "../types";
import {
  qualityLabels,
  qualityStatus,
  environmentLabels,
  metricText,
  filterQuality,
} from "../quality";
import type { QualityRecord, QualityRun } from "../quality";
import "../styles/quality.css";

function Status({ status }: { status: keyof typeof qualityStatus }) {
  return (
    <span
      className={`badge ${status === "passed" ? "success" : status === "failed" ? "danger" : "neutral"}`}
    >
      {qualityStatus[status]}
    </span>
  );
}
function Details({ record }: { record: QualityRecord }) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<unknown>(null);
  const run = record.run;
  async function download() {
    setExporting(true);
    setExportError(null);
    try {
      // 下载时重新读取以重验当前会话；不使用缓存绕过已撤销的管理员角色。
      const current = await api<QualityRecord>(
        `/quality/runs/${encodeURIComponent(run.id)}`,
      );
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(current, null, 2) + "\n"], {
          type: "application/json",
        }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = `${run.id}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setExportError(error);
    } finally {
      setExporting(false);
    }
  }
  return (
    <section
      className="panel quality-detail"
      aria-labelledby="quality-detail-title"
    >
      <div className="quality-detail-heading">
        <div>
          <div className="eyebrow">EXECUTION RECEIPT</div>
          <h2 id="quality-detail-title">{run.title}</h2>
        </div>
        <Status status={run.status} />
      </div>
      <p className="quality-scope">{run.fixture_scope}</p>
      <dl className="quality-facts">
        <div>
          <dt>执行环境</dt>
          <dd>{environmentLabels[run.environment]}</dd>
        </div>
        <div>
          <dt>数据来源</dt>
          <dd>
            {run.provenance === "actual_execution"
              ? "实际执行记录"
              : "导入的合成夹具"}
          </dd>
        </div>
        <div>
          <dt>开始时间</dt>
          <dd>{dateTime(run.started_at)}</dd>
        </div>
        <div>
          <dt>结束时间</dt>
          <dd>{dateTime(run.finished_at)}</dd>
        </div>
      </dl>
      <div className="quality-counts" aria-label="本次套件测试计数">
        <span>
          <strong>{run.counts.passed}</strong>通过
        </span>
        <span>
          <strong>{run.counts.failed}</strong>失败
        </span>
        <span>
          <strong>{run.counts.skipped}</strong>跳过
        </span>
        <span>
          <strong>{run.counts.total}</strong>总计
        </span>
      </div>
      {run.metrics.length > 0 && (
        <div className="quality-metrics">
          {run.metrics.map((m) => (
            <div key={m.id}>
              <span>{m.label}</span>
              <strong>{metricText(m.value, m.unit)}</strong>
            </div>
          ))}
        </div>
      )}
      <h3>关键检查</h3>
      {run.checks.length ? (
        <ul className="quality-checks">
          {run.checks.map((c) => (
            <li key={c.id}>
              <div>
                <strong>{c.label}</strong>
                <Status status={c.status} />
              </div>
              {c.detail && <p>{c.detail}</p>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">本记录未列出检查明细，请核对来源收据。</p>
      )}
      <div className="quality-limits">
        <h3>
          <Info size={18} />
          验收边界
        </h3>
        <ul>
          {run.limits.map((limit, i) => (
            <li key={i}>{limit}</li>
          ))}
        </ul>
      </div>
      <details className="quality-digests">
        <summary>来源与完整性摘要</summary>
        <dl>
          <dt>执行源码提交</dt>
          <dd>{run.source_commit ?? "未登记提交；以执行源码摘要为准"}</dd>
          <dt>执行来源收据 SHA-256</dt>
          <dd>{run.source_sha256}</dd>
          <dt>原记录字节 SHA-256</dt>
          <dd>{record.record_sha256}</dd>
        </dl>
        <p>
          摘要用于核对来源字节，不代表数字签名或独立人工评审。导出包含响应包装，原记录摘要对应其中
          run 的服务器原文件。
        </p>
      </details>
      <button
        className="button secondary"
        disabled={exporting}
        onClick={() => void download()}
      >
        <DownloadSimple size={17} />
        {exporting ? "正在读取" : "导出记录 JSON"}
      </button>
      {exportError != null && <ErrorPanel error={exportError} />}
    </section>
  );
}

export default function Quality() {
  const user = useContext(UserContext);
  const admin = !!user && ["admin", "platform_admin"].includes(user.role);
  const [params, setParams] = useSearchParams();
  const category = params.get("category") ?? "",
    suite = params.get("suite") ?? "",
    status = params.get("status") ?? "";
  const query = useQuery({
    queryKey: ["quality-runs"],
    queryFn: ({ signal }) =>
      api<List<QualityRecord>>("/quality/runs", { signal }),
    enabled: admin,
  });
  if (!admin)
    return (
      <Empty
        title="需要管理员权限"
        description="质量记录包含平台验收信息，请使用管理员账号查看。"
        action={false}
      />
    );
  const records = query.data?.items ?? [],
    visible = filterQuality(records, category, suite, status),
    selected =
      visible.find((x) => x.run.id === params.get("run")) ??
      (params.has("run") ? undefined : visible[0]);
  const suites = [
    ...new Set(
      records
        .filter((x) => !category || x.run.category === category)
        .map((x) => x.run.suite),
    ),
  ];
  function select(key: string, value: string) {
    const next = new URLSearchParams(params);
    value ? next.set(key, value) : next.delete(key);
    next.delete("run");
    if (key === "category") next.delete("suite");
    setParams(next);
  }
  function choose(run: QualityRun) {
    const next = new URLSearchParams(params);
    next.set("run", run.id);
    setParams(next);
  }
  const timeline = [...records.filter((x) => !!suite && x.run.suite === suite)]
    .sort(
      (a, b) => Date.parse(a.run.finished_at) - Date.parse(b.run.finished_at),
    )
    .slice(-12);
  return (
    <div className="quality-page">
      <PageHeading
        eyebrow="QUALITY / EXECUTIONS"
        title="质量验证"
        description="从测试设计到失败复现，每次执行都留下可核对的记录。"
        action={
          <button
            className="button secondary"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <ArrowClockwise size={17} />
            {query.isFetching ? "正在刷新" : "刷新记录"}
          </button>
        }
      />
      <div className="quality-overview" aria-label="执行记录概览">
        <div>
          <span>执行记录</span>
          <strong>{query.isSuccess ? records.length : "—"}</strong>
        </div>
        <div>
          <span>失败记录</span>
          <strong>
            {query.isSuccess
              ? records.filter((x) => x.run.status === "failed").length
              : "—"}
          </strong>
        </div>
        <div>
          <span>未完成记录</span>
          <strong>
            {query.isSuccess
              ? records.filter((x) => x.run.status === "incomplete").length
              : "—"}
          </strong>
        </div>
        <div>
          <span>测试套件</span>
          <strong>
            {query.isSuccess
              ? new Set(records.map((x) => x.run.suite)).size
              : "—"}
          </strong>
        </div>
      </div>
      <div className="quality-toolbar">
        <label>
          测试方向
          <select
            aria-label="测试方向"
            value={category}
            onChange={(e) => select("category", e.target.value)}
          >
            <option value="">全部方向</option>
            {Object.entries(qualityLabels).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          测试套件
          <select
            aria-label="测试套件"
            value={suite}
            onChange={(e) => select("suite", e.target.value)}
          >
            <option value="">全部套件</option>
            {suites.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
        <label>
          执行结果
          <select
            aria-label="执行结果"
            value={status}
            onChange={(e) => select("status", e.target.value)}
          >
            <option value="">全部结果</option>
            {["passed", "failed", "incomplete"].map((id) => (
              <option key={id} value={id}>
                {qualityStatus[id as keyof typeof qualityStatus]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {query.isPending ? (
        <Busy />
      ) : query.error ? (
        <ErrorPanel error={query.error} />
      ) : records.length === 0 ? (
        <Empty
          icon={<TestTube size={28} />}
          title="还没有发布的执行记录"
          description="完成实际实验并发布审阅摘要后，记录会出现在这里。"
          action={false}
        />
      ) : (
        <>
          {suite && (
            <section
              className="panel quality-timeline"
              aria-label="同一套件运行趋势"
            >
              <h2>套件运行时间线</h2>
              <p>
                同一套件最近 {timeline.length}{" "}
                次记录；不同环境和负载的性能数值仍需分别比较。
              </p>
              <ol>
                {timeline.map(({ run }) => (
                  <li key={run.id}>
                    <button
                      onClick={() => {
                        const next = new URLSearchParams(params);
                        next.delete("status");
                        next.set("run", run.id);
                        setParams(next);
                      }}
                    >
                      <Status status={run.status} />
                      <time dateTime={run.finished_at}>
                        {dateTime(run.finished_at)}
                      </time>
                      <span>{environmentLabels[run.environment]}</span>
                    </button>
                  </li>
                ))}
              </ol>
            </section>
          )}
          <div className="quality-workspace">
            <section className="quality-list" aria-label="执行记录列表">
              <div className="quality-list-heading">
                <h2>执行记录</h2>
                <span>{visible.length} 条</span>
              </div>
              {visible.length ? (
                visible.map(({ run }) => (
                  <button
                    className={`quality-run ${selected?.run.id === run.id ? "selected" : ""}`}
                    key={run.id}
                    aria-pressed={selected?.run.id === run.id}
                    onClick={() => choose(run)}
                  >
                    <div>
                      <span className="quality-category">
                        {qualityLabels[run.category]}
                      </span>
                      <Status status={run.status} />
                    </div>
                    <strong>{run.title}</strong>
                    <p>
                      {environmentLabels[run.environment]} · {run.counts.passed}
                      /{run.counts.total} 通过
                      {run.counts.skipped
                        ? ` · ${run.counts.skipped} 跳过`
                        : ""}
                    </p>
                    <footer>
                      <time dateTime={run.finished_at}>
                        {dateTime(run.finished_at)}
                      </time>
                      <ArrowUpRight size={16} />
                    </footer>
                  </button>
                ))
              ) : (
                <p className="muted">没有符合筛选条件的记录。</p>
              )}
            </section>
            {selected ? (
              <Details key={selected.run.id} record={selected} />
            ) : (
              <section className="panel quality-detail">
                <h2>请选择执行记录</h2>
                <p>链接中的记录不存在，或不符合当前筛选条件。</p>
              </section>
            )}
          </div>
        </>
      )}
    </div>
  );
}
