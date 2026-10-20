/**
 * 双任务对比页：先校验采集与分析完成、类型和指标可比，再加载各自结果。
 * 展示的是函数权重和分布差异，不将采样百分比直接包装成业务性能提升。
 */
import { lazy, Suspense, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowsLeftRight,
  Info,
  MagnifyingGlass,
} from "@phosphor-icons/react";
import { api, dateTime, kindName, number } from "../api";
import type { Task } from "../types";
import { loadAnalysisResult } from "../profile-loader";
import { PageHeading, ErrorPanel, Skeleton } from "../components/ui";
import {
  compareProfiles,
  comparisonEligibility,
  comparisonRows,
} from "../comparison";
import type { ComparisonMode, ProfileComparison } from "../comparison";

const Flamegraph = lazy(() => import("../components/Flamegraph"));
const isTaskId = (value: string) =>
  /^[1-9]\d{0,18}$/.test(value) && BigInt(value) <= 9223372036854775807n;

export default function Compare() {
  const [params, setParams] = useSearchParams();
  const baselineId = params.get("baseline") || "",
    candidateId = params.get("candidate") || "";
  const validIds =
    isTaskId(baselineId) && isTaskId(candidateId) && baselineId !== candidateId;
  const [mode, setMode] = useState<ComparisonMode>("self");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("absolute");
  const [visibleCount, setVisibleCount] = useState(25);
  const query = useQuery({
    queryKey: ["task-comparison", baselineId, candidateId],
    enabled: validIds,
    retry: false,
    queryFn: async ({ signal }) => {
      const [baselineTask, candidateTask] = await Promise.all([
        api<Task>(`/tasks/${baselineId}`, { signal }),
        api<Task>(`/tasks/${candidateId}`, { signal }),
      ]);
      const reason = comparisonEligibility([baselineTask, candidateTask]);
      if (reason) throw new Error(reason);
      const [baselineResult, candidateResult] = await Promise.all([
        loadAnalysisResult(baselineId, signal),
        loadAnalysisResult(candidateId, signal),
      ]);
      return { baselineTask, candidateTask, baselineResult, candidateResult };
    },
  });
  const comparison = useMemo<{
    profile?: ProfileComparison;
    error?: Error;
  }>(() => {
    if (!query.data) return {};
    const { baselineTask, baselineResult, candidateTask, candidateResult } =
      query.data;
    try {
      return {
        profile: compareProfiles(
          baselineTask,
          baselineResult,
          candidateTask,
          candidateResult,
        ),
      };
    } catch (error) {
      return {
        error: error instanceof Error ? error : new Error("结果无法比较"),
      };
    }
  }, [query.data]);
  const rows = useMemo(() => {
    if (!comparison.profile) return [];
    const rows = comparisonRows(comparison.profile, mode).filter((row) =>
      row.name.toLowerCase().includes(search.trim().toLowerCase()),
    );
    if (sort !== "absolute")
      rows.sort((a, b) =>
        sort === "increase"
          ? b.deltaPercentagePoints - a.deltaPercentagePoints
          : a.deltaPercentagePoints - b.deltaPercentagePoints,
      );
    return rows;
  }, [comparison.profile, mode, search, sort]);
  const data = query.data;
  return (
    <div className="comparison-workspace">
      <Link to="/tasks" className="back-link">
        <ArrowLeft size={15} />
        返回诊断任务
      </Link>
      <PageHeading
        eyebrow="DIAGNOSTICS / COMPARISON"
        title="任务对比"
        description="把两次诊断放在同一口径下，观察热点的权重分布变化。"
        action={
          validIds ? (
            <button
              className="button secondary"
              onClick={() => {
                setParams({ baseline: candidateId, candidate: baselineId });
                setVisibleCount(25);
              }}
            >
              <ArrowsLeftRight size={16} />
              交换基准与对照
            </button>
          ) : undefined
        }
      />
      {!validIds ? (
        <div className="panel comparison-empty">
          <h2>先选择两个已完成的任务</h2>
          <p>在诊断任务列表勾选两个同类型任务，点击“对比结果”。</p>
          <Link className="button primary" to="/tasks">
            选择诊断任务
          </Link>
        </div>
      ) : query.error ? (
        <ErrorPanel error={query.error} retry={() => query.refetch()} />
      ) : comparison.error ? (
        <ErrorPanel error={comparison.error} />
      ) : !data || !comparison.profile ? (
        <Skeleton rows={8} />
      ) : (
        <>
          <div className="comparison-profiles">
            {[
              {
                title: "基准",
                task: data.baselineTask,
                result: data.baselineResult,
              },
              {
                title: "对照",
                task: data.candidateTask,
                result: data.candidateResult,
              },
            ].map(({ title, task, result }) => (
              <section
                className="panel comparison-profile"
                key={title}
                aria-label={`${title}任务`}
              >
                <span className="comparison-role">
                  {title}
                  <span className="mono">#{task.id}</span>
                </span>
                <h2>
                  <Link to={`/tasks/${task.id}`}>{task.name}</Link>
                </h2>
                <p>
                  {kindName(task.kind)} · {dateTime(task.created_at)}
                </p>
                <dl>
                  <div>
                    <dt>总权重</dt>
                    <dd className="mono">
                      {number(result.total_samples)}{" "}
                      <small>{result.summary.unit}</small>
                    </dd>
                  </div>
                  <div>
                    <dt>采集时长</dt>
                    <dd>
                      {result.summary.duration_seconds == null
                        ? "未提供"
                        : `${result.summary.duration_seconds} s`}
                    </dd>
                  </div>
                  <div>
                    <dt>采样率</dt>
                    <dd>
                      {result.summary.frequency_hz == null
                        ? "未提供"
                        : `${result.summary.frequency_hz} Hz`}
                    </dd>
                  </div>
                  <div>
                    <dt>来源</dt>
                    <dd>
                      {result.summary.source === "import"
                        ? "用户导入"
                        : result.summary.source}
                    </dd>
                  </div>
                </dl>
              </section>
            ))}
          </div>
          <div className="comparison-explanation">
            <Info size={19} />
            <p>
              每份结果分别按自身总权重归一化，变化使用百分点（pp）。总权重与采样时长可以不同；占比变化不能直接换算成耗时、吞吐或性能提升。总计权重包含下游调用，多个函数的占比不相加。
            </p>
          </div>
          <section
            className="panel comparison-top"
            aria-labelledby="comparison-top-title"
          >
            <div className="section-heading">
              <div>
                <h2 id="comparison-top-title">热点权重变化</h2>
                <span className="section-note">
                  来自完整调用树 · {number(rows.length)} 个匹配调用帧
                </span>
              </div>
              <div className="segmented" aria-label="对比权重口径">
                {(["self", "total"] as const).map((value) => (
                  <button
                    key={value}
                    aria-pressed={mode === value}
                    className={mode === value ? "selected" : ""}
                    onClick={() => {
                      setMode(value);
                      setVisibleCount(25);
                    }}
                  >
                    {value === "self" ? "自身" : "总计"}
                  </button>
                ))}
              </div>
            </div>
            <div className="comparison-controls">
              <label className="search-field">
                <MagnifyingGlass size={16} />
                <span className="sr-only">筛选对比函数</span>
                <input
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setVisibleCount(25);
                  }}
                  placeholder="搜索函数或调用帧…"
                />
              </label>
              <label className="select-wrap">
                <span className="sr-only">对比排序</span>
                <select
                  value={sort}
                  onChange={(event) => {
                    setSort(event.target.value);
                    setVisibleCount(25);
                  }}
                >
                  <option value="absolute">按变化幅度</option>
                  <option value="increase">占比增加优先</option>
                  <option value="decrease">占比减少优先</option>
                </select>
              </label>
            </div>
            <div
              className="table-scroll comparison-table-scroll"
              tabIndex={0}
              role="region"
              aria-label="热点对比表，可横向滚动"
            >
              <table className="data-table comparison-table">
                <caption className="sr-only">
                  两任务{mode === "self" ? "自身" : "总计"}
                  权重及各自占比，变化列为对照减去基准的百分点差。
                </caption>
                <thead>
                  <tr>
                    <th scope="col">函数 / 调用帧</th>
                    <th scope="col">基准</th>
                    <th scope="col">对照</th>
                    <th scope="col">变化（pp）</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, visibleCount).map((row) => (
                    <tr key={row.name}>
                      <th scope="row" className="comparison-function mono">
                        {row.name}
                      </th>
                      <td>
                        <strong className="mono">
                          {row.baselinePercent.toFixed(2)}%
                        </strong>
                        <small>
                          {number(row.baseline)} {comparison.profile!.unit}
                        </small>
                      </td>
                      <td>
                        <strong className="mono">
                          {row.candidatePercent.toFixed(2)}%
                        </strong>
                        <small>
                          {number(row.candidate)} {comparison.profile!.unit}
                        </small>
                      </td>
                      <td
                        className={`comparison-delta ${row.deltaPercentagePoints > 0 ? "increased" : row.deltaPercentagePoints < 0 ? "decreased" : ""}`}
                      >
                        <strong className="mono">
                          {row.deltaPercentagePoints > 0 ? "+" : ""}
                          {row.deltaPercentagePoints.toFixed(2)}
                        </strong>
                        <small>
                          {row.deltaPercentagePoints > 0
                            ? "占比增加"
                            : row.deltaPercentagePoints < 0
                              ? "占比减少"
                              : "占比相同"}
                        </small>
                      </td>
                    </tr>
                  ))}
                  {!rows.length && (
                    <tr>
                      <td colSpan={4} className="comparison-no-results">
                        没有匹配的调用帧。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {rows.length > visibleCount && (
              <div className="comparison-load-more">
                <button
                  className="button secondary"
                  onClick={() => setVisibleCount((count) => count + 50)}
                >
                  再显示 50 项（已显示 {visibleCount} / {rows.length}）
                </button>
              </div>
            )}
          </section>
          <div className="comparison-flames">
            {[
              { label: "基准火焰图", result: data.baselineResult },
              { label: "对照火焰图", result: data.candidateResult },
            ].map(({ label, result }) => (
              <section className="panel" key={label}>
                <div className="section-heading">
                  <h2>{label}</h2>
                  <span className="section-note">各自归一化显示</span>
                </div>
                <Suspense fallback={<Skeleton rows={5} />}>
                  <Flamegraph
                    data={result.flamegraph}
                    unit={result.summary.unit}
                  />
                </Suspense>
              </section>
            ))}
          </div>
          {(data.baselineResult.summary.limitations.length > 0 ||
            data.candidateResult.summary.limitations.length > 0) && (
            <details className="panel comparison-limitations">
              <summary>数据来源与解释边界</summary>
              {Array.from(
                new Set([
                  ...data.baselineResult.summary.limitations,
                  ...data.candidateResult.summary.limitations,
                ]),
              ).map((text) => (
                <p key={text}>{text}</p>
              ))}
            </details>
          )}
        </>
      )}
    </div>
  );
}
