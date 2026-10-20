import ConfirmDialog from "../components/ConfirmDialog";
import { pollingInterval } from "../query-policy";
import { subscribeTaskEvents } from "../event-stream";
import { UserContext } from "../auth-context";
import { useState, useEffect, useRef, useContext, lazy, Suspense } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowClockwise,
  DownloadSimple,
  Check,
  Clock,
  Cpu,
  FileText,
  Lightning,
  Stop,
  Stack,
  WarningCircle,
  ArrowUpRight,
  CaretRight,
} from "@phosphor-icons/react";
import {
  api,
  dateTime,
  isTerminal,
  kindName,
  number,
  bytes,
  newIdempotencyKey,
} from "../api";
import type { Task, List, TaskEvent, Artifact } from "../types";
import { Badge, ErrorPanel, Skeleton, Busy } from "../components/ui";
import {
  TaskSharing,
  SuggestionFeedback,
} from "../components/TaskCollaboration";
import AnalysisSections from "../components/AnalysisSections";
import { parseFlameThreads } from "../components/flame-analysis";
import PerfEvidence from "../components/PerfEvidence";
import { SafeMarkdown } from "../components/SafeMarkdown";
import { loadAnalysisResult, loadFlameArtifact } from "../profile-loader";
import { selectFlameArtifact } from "../profile-data";
import { failureDetails, taskStages, timelineTone } from "../task-failure";
import TaskFailurePanel from "../components/TaskFailurePanel";
import PartialAnalysisPanel from "../components/PartialAnalysisPanel";
import {
  loadPartialAnalysis,
  partialIdentity,
  partialQueryKey,
} from "../partial-analysis";
const Flamegraph = lazy(() => import("../components/Flamegraph"));
const DiagnosisAgent = lazy(() => import("../components/DiagnosisAgent"));
const eventLabels: Record<string, string> = {
  DIAGNOSIS_QUEUED: "诊断 Agent 已排队",
  DIAGNOSIS_TOOL_COMPLETED: "诊断工具已完成",
  DIAGNOSIS_COMPLETED: "诊断报告已生成",
  DIAGNOSIS_FAILED: "诊断未完成",
  DIAGNOSIS_VERIFICATION_CREATED: "诊断复测任务已创建",
  TASK_CREATED: "创建诊断任务",
  DISPATCH_QUEUED: "加入调度队列",
  TASK_DELIVERED: "Agent 已接收",
  RUNNER_STARTED: "开始采集",
  RAW_ARTIFACT_UPLOADED: "原始制品已上传",
  COLLECTION_SUCCEEDED: "采集完成",
  ANALYSIS_STARTED: "开始分析",
  ANALYSIS_PROGRESS: "分析阶段更新",
  RESULT_ARTIFACT_UPLOADED: "分析制品已上传",
  ANALYSIS_SUCCEEDED: "分析完成",
  TASK_FAILED: "任务失败",
  TASK_CANCELED: "任务已取消",
  ANALYSIS_FAILED: "分析失败",
  TASK_RETRIED: "重新执行任务",
};
export default function TaskDetail({
  readOnly = false,
}: {
  readOnly?: boolean;
}) {
  const currentUser = useContext(UserContext);
  const platform = ["admin", "platform_admin"].includes(
    currentUser?.role || "",
  );
  const { id } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const remove = useMutation({
    mutationFn: () => api("/tasks/" + id, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      navigate("/tasks");
    },
  });
  const [tab, setTab] = useState("result");
  const [topMode, setTopMode] = useState<"self" | "total">("self");
  const [flameFocus, setFlameFocus] = useState<{
    name: string;
    token: number;
  }>();
  const [streamState, setStreamState] = useState("connecting");
  const retryKey = useRef(newIdempotencyKey());
  const q = useQuery({
    queryKey: ["task", id],
    queryFn: ({ signal }) => api<Task>(`/tasks/${id}`, { signal }),
    refetchInterval: (query) =>
      pollingInterval(
        query,
        5000,
        !!query.state.data && isTerminal(query.state.data),
      ),
  });
  const task = q.data;
  const [partialBlockedAt, setPartialBlockedAt] = useState<string | null>(null);
  const partialKey = partialQueryKey(task);
  const partialKeyText = JSON.stringify(partialKey);
  const partialAvailable =
    !!partialIdentity(task) && partialBlockedAt !== partialKeyText;
  const partial = useQuery({
    queryKey: partialKey,
    queryFn: ({ signal }) => loadPartialAnalysis(task!, signal),
    enabled: partialAvailable,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  useEffect(
    () => () => {
      void qc.cancelQueries({ queryKey: ["partial-result", id] });
      qc.removeQueries({ queryKey: ["partial-result", id] });
    },
    [qc, id],
  );
  useEffect(() => {
    if (!partialAvailable) {
      void qc.cancelQueries({ queryKey: ["partial-result", id] });
      qc.removeQueries({ queryKey: ["partial-result", id] });
    }
  }, [qc, id, partialAvailable]);
  const result = useQuery({
    queryKey: ["result", id, task?.updated_at],
    queryFn: ({ signal }) => loadAnalysisResult(id!, signal),
    enabled: task?.analysis_status === "SUCCESS",
  });
  const flameArtifact = task && selectFlameArtifact(task);
  const artifactTree = useQuery({
    queryKey: [
      "flame-artifact",
      id,
      flameArtifact?.attempt_id,
      flameArtifact?.id,
      flameArtifact?.sha256,
    ],
    queryFn: ({ signal }) => loadFlameArtifact(id!, flameArtifact!, signal),
    enabled: !!flameArtifact && tab === "result",
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const events = useQuery({
    queryKey: ["events", id, task?.updated_at],
    queryFn: ({ signal }) =>
      api<List<TaskEvent>>(`/tasks/${id}/events`, { signal }),
    enabled: !!task,
  });
  const action = useMutation({
    onMutate: async () => {
      setPartialBlockedAt(partialKeyText);
      await qc.cancelQueries({ queryKey: ["partial-result", id] });
      qc.removeQueries({ queryKey: ["partial-result", id] });
    },
    mutationFn: (type: string) =>
      api<Task>(`/tasks/${id}/${type}`, {
        method: "POST",
        idempotencyKey: retryKey.current,
        body: "{}",
      }),
    onSuccess: () => {
      retryKey.current = newIdempotencyKey();
      qc.invalidateQueries();
    },
    onError: () => setPartialBlockedAt(null),
  });
  const download = useMutation({
    mutationFn: async (artifact: Artifact) => {
      const d = await api<{ url: string }>(
        `/tasks/${id}/artifacts/${artifact.id}/download`,
      );
      const u = new URL(d.url, window.location.origin);
      if (
        !["http:", "https:"].includes(u.protocol) ||
        u.origin !== window.location.origin
      )
        throw new Error("制品链接来源无效");
      window.location.assign(u.href);
    },
  });
  useEffect(() => {
    if (!task || isTerminal(task)) {
      setStreamState("complete");
      return;
    }
    return subscribeTaskEvents(task.id, {
      onState: setStreamState,
      onEvent: (event) => {
        if (event.type === "task_status")
          qc.invalidateQueries({ queryKey: ["task", id] });
      },
    });
  }, [id, !!task, task && isTerminal(task), qc]);
  if (q.error) return <ErrorPanel error={q.error} retry={() => q.refetch()} />;
  if (!task) return <Skeleton rows={8} />;
  const operationReadOnly = readOnly || task.permissions?.operate === false;
  const imported =
    task.kind === "IMPORT_COLLAPSED" || task.params.source === "import";
  const r =
    task.collection_status === "COLLECTED" && task.analysis_status === "SUCCESS"
      ? result.data
      : undefined;
  const flameTree = artifactTree.data || r?.flamegraph;
  const ruleSuggestions = r?.suggestions.filter((s) => s.engine !== "ai") || [];
  const visibleHotspots = r
    ? r.top_functions
        .filter((item) => Number.isFinite(item[topMode]) && item[topMode] > 0)
        .sort((a, b) => b[topMode] - a[topMode])
        .slice(0, 10)
    : [];
  const zeroHotspots = r
    ? r.top_functions.filter((item) => item[topMode] === 0).length
    : 0;
  const timeline = events.data?.items || task.events || [];
  const artifacts = task.artifacts || [];
  const stages = taskStages(task, timeline);
  const failure = failureDetails(task, timeline, {
    operate: !operationReadOnly,
    reanalyze: task.permissions?.reanalyze ?? platform,
  });
  return (
    <div className="task-detail">
      <Link to="/tasks" className="back-link">
        <ArrowLeft size={15} />
        返回诊断任务
      </Link>
      <ConfirmDialog
        open={confirmDelete}
        title="删除这次诊断？"
        description="任务将从工作空间移除，操作会写入审计。原始制品仍按保留期管理。"
        busy={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
      />
      {remove.error && <ErrorPanel error={remove.error} />}
      <div className="detail-heading">
        <div>
          <div className="eyebrow mono">DIAGNOSTIC / #{task.id}</div>
          <h1>{task.name}</h1>
          <div className="detail-meta">
            <div className="detail-state-pair">
              <span>采集</span>
              <Badge status={task.collection_status} />
              <span className="state-divider" aria-hidden="true">
                /
              </span>
              <span>分析</span>
              <Badge status={task.analysis_status} />
            </div>
            <span>{kindName(task.kind)}</span>
            <span className="mono">{dateTime(task.created_at)}</span>
          </div>
        </div>
        <div className="heading-actions">
          {!operationReadOnly &&
            (isTerminal(task) ? (
              task.collection_status !== "COLLECTED" ||
              task.analysis_status === "FAILED" ? (
                imported ? (
                  <Link className="button secondary" to="/tasks/new">
                    重新导入
                  </Link>
                ) : (
                  <button
                    className="button secondary"
                    disabled={action.isPending}
                    onClick={() => action.mutate("retry")}
                  >
                    {action.isPending ? (
                      <Busy />
                    ) : (
                      <>
                        <ArrowClockwise size={16} />
                        重试诊断
                      </>
                    )}
                  </button>
                )
              ) : null
            ) : (
              <button
                className="button secondary"
                disabled={action.isPending}
                onClick={() => action.mutate("cancel")}
              >
                <Stop size={16} />
                取消任务
              </button>
            ))}
          {(task.permissions?.reanalyze ?? platform) &&
            task.collection_status === "COLLECTED" &&
            ["SUCCESS", "FAILED"].includes(task.analysis_status) && (
              <button
                className="button secondary"
                disabled={action.isPending}
                onClick={() => action.mutate("reanalyze")}
              >
                重新分析原始数据
              </button>
            )}
          {!readOnly &&
            isTerminal(task) &&
            (platform || task.creator_id === currentUser?.id) && (
              <button
                className="button secondary"
                onClick={() => setConfirmDelete(true)}
              >
                删除任务
              </button>
            )}
          <button
            className="icon-button bordered"
            aria-label="刷新详情"
            onClick={() => q.refetch()}
          >
            <ArrowClockwise size={18} />
          </button>
        </div>
      </div>
      {action.error && <ErrorPanel error={action.error} />}
      <div
        className={`task-progress ${isTerminal(task) ? "is-terminal" : ""}`}
        aria-label="任务执行阶段"
      >
        {stages.map((s, i) => (
          <div
            className={`progress-stage ${s.state}`}
            data-stage-state={s.state}
            key={s.label}
          >
            <span>
              {s.state === "done" ? (
                <Check size={15} weight="bold" />
              ) : s.state === "failed" ? (
                <WarningCircle size={15} />
              ) : s.state === "canceled" ? (
                <Stop size={15} />
              ) : (
                String(i + 1).padStart(2, "0")
              )}
            </span>
            <strong>{s.label}</strong>
            {i < stages.length - 1 && <CaretRight size={15} />}
          </div>
        ))}
        <span
          className={`live-indicator ${streamState === "live" ? "connected" : ""}`}
        >
          <i />
          {streamState === "live"
            ? "实时同步"
            : streamState === "polling"
              ? "轮询同步"
              : isTerminal(task)
                ? "终态已归档"
                : "连接中"}
        </span>
      </div>
      <div className="detail-stats">
        {[
          {
            label: "目标节点",
            value: task.agent_hostname || task.target_agent_id || "离线导入",
            icon: <Cpu size={16} />,
          },
          {
            label: "采集时长",
            value: imported
              ? "—"
              : typeof task.params.duration_seconds === "number" &&
                  task.params.duration_seconds > 0
                ? `${task.params.duration_seconds} s`
                : "未知",
            icon: <Clock size={16} />,
          },
          {
            label: "采样权重",
            value: r
              ? `${number(r.total_samples)} ${r.summary.unit}`
              : "等待结果",
            icon: <Stack size={16} />,
          },
          {
            label: "原始调用栈",
            value: r ? `${number(r.unique_stacks)} stacks` : "等待分析",
            icon: <Lightning size={16} />,
          },
        ].map((s) => (
          <div key={s.label}>
            <span>
              {s.icon}
              {s.label}
            </span>
            <strong className="mono">{s.value}</strong>
          </div>
        ))}
      </div>
      {failure && (
        <TaskFailurePanel
          failure={failure}
          taskId={task.id}
          onTimeline={() => setTab("timeline")}
          onAction={(type) =>
            type === "reimport" ? navigate("/tasks/new") : action.mutate(type)
          }
          onDownload={(artifact) => download.mutate(artifact)}
          busy={action.isPending}
          downloadBusy={download.isPending}
          downloadError={download.error}
        />
      )}
      <div className="detail-tabs" role="tablist" aria-label="诊断详情">
        {[
          ["result", "分析结果"],
          ["diagnosis", "智能诊断"],
          ["timeline", "执行时间线"],
          ["artifacts", "制品文件"],
          ["parameters", "任务参数"],
          ["sharing", "共享权限"],
        ]
          .filter(
            ([key]) => key !== "sharing" || task.permissions?.share !== false,
          )
          .map(([key, label]) => (
            <button
              key={key}
              role="tab"
              id={`task-tab-${key}`}
              aria-controls={`task-panel-${key}`}
              aria-selected={tab === key}
              onClick={() => setTab(key)}
            >
              {label}
              {key === "artifacts" && <span>{artifacts.length}</span>}
            </button>
          ))}
      </div>
      <div
        role="tabpanel"
        id={`task-panel-${tab}`}
        aria-labelledby={`task-tab-${tab}`}
      >
        {tab === "diagnosis" && (
          <Suspense fallback={<Skeleton rows={4} />}>
            <DiagnosisAgent
              key={task.id}
              task={task}
              readOnly={operationReadOnly}
              hotspots={r?.top_functions.map((item) => item.name)}
            />
          </Suspense>
        )}
        {tab === "result" && (
          <>
            {partialAvailable && partial.data ? (
              <PartialAnalysisPanel result={partial.data} />
            ) : partialAvailable && partial.error ? (
              <ErrorPanel
                error={partial.error}
                retry={() => partial.refetch()}
              />
            ) : partialAvailable && partial.isPending ? (
              <div className="panel partial-analysis-loading" role="status">
                正在读取本次分析已保存的摘要…
              </div>
            ) : result.error && task.analysis_status === "SUCCESS" ? (
              <ErrorPanel error={result.error} retry={() => result.refetch()} />
            ) : !r ? (
              <div
                className={`panel result-pending ${failure ? "failed-result-summary" : ""}`}
              >
                <div className="empty-icon">
                  {isTerminal(task) ? (
                    <WarningCircle size={28} />
                  ) : (
                    <Lightning size={28} />
                  )}
                </div>
                <h2>
                  {isTerminal(task)
                    ? "本次任务未生成分析结果"
                    : "正在把现场数据转化为洞察"}
                </h2>
                <p>
                  {isTerminal(task)
                    ? "查看执行时间线中的错误原因，调整目标或参数后重试。"
                    : "采集完成后将自动开始分析，结果就绪时此页面会自动更新。"}
                </p>
                <button
                  className="button secondary"
                  onClick={() => setTab("timeline")}
                >
                  查看执行时间线
                  <ArrowUpRight size={15} />
                </button>
              </div>
            ) : (
              <>
                <AnalysisSections sections={r.sections} />
                {(!!flameTree?.value || !!flameArtifact) && (
                  <section
                    className="panel detail-flame-panel"
                    id="task-flamegraph"
                  >
                    <div className="section-heading">
                      <div>
                        <h2>调用栈火焰图</h2>
                        <span className="section-note">
                          {r.summary.source} · 区块宽度表示总计权重
                        </span>
                      </div>
                      <span className="mono dim">
                        {number(r.node_count)} nodes · {r.max_depth} depth
                      </span>
                    </div>
                    {flameArtifact && artifactTree.isPending && (
                      <p
                        role="status"
                        className="section-note flame-artifact-status"
                      >
                        正在验证并载入压缩火焰图制品…
                      </p>
                    )}
                    {artifactTree.error && (
                      <div className="flame-artifact-error">
                        <ErrorPanel
                          error={artifactTree.error}
                          retry={() => artifactTree.refetch()}
                        />
                        {r.flamegraph && (
                          <p className="section-note">
                            压缩制品载入失败；下方保留本次分析的内联调用树。
                          </p>
                        )}
                      </div>
                    )}
                    {artifactTree.data && (
                      <p
                        className="section-note flame-artifact-status"
                        data-flame-source="gzip-artifact"
                      >
                        已验证压缩火焰图制品 · 当前执行尝试
                      </p>
                    )}
                    {flameTree && (
                      <Suspense fallback={<Skeleton rows={5} />}>
                        <Flamegraph
                          data={flameTree}
                          unit={r.summary.unit}
                          threads={parseFlameThreads(r.sections?.perf)}
                          focusRequest={flameFocus}
                        />
                      </Suspense>
                    )}
                    <PerfEvidence section={r.sections?.perf} />
                    {r.summary.limitations?.length > 0 && (
                      <details className="result-limitations">
                        <summary>
                          <WarningCircle size={16} />
                          <span>数据来源与解读边界</span>
                          <span className="limitation-count">
                            {r.summary.limitations.length} 项说明
                          </span>
                          <CaretRight size={14} className="limitation-caret" />
                        </summary>
                        <ol>
                          {r.summary.limitations.map((limitation, i) => (
                            <li key={i}>{limitation}</li>
                          ))}
                        </ol>
                      </details>
                    )}
                  </section>
                )}
                <div
                  className={
                    "result-bottom " +
                    (!r.top_functions.length ? "without-hotspots" : "")
                  }
                >
                  {!!r.top_functions.length && (
                    <>
                      <section className="panel top-functions">
                        <div className="section-heading">
                          <div>
                            <h2>
                              热点{task.kind === "PROC_CPU" ? "线程" : "函数"}
                            </h2>
                            <span className="section-note">
                              TOP {visibleHotspots.length} · 已过滤零值
                              {zeroHotspots > 0 ? ` ${zeroHotspots} 项` : ""}
                            </span>
                          </div>
                          <div className="segmented" aria-label="热点权重口径">
                            <button
                              className={topMode === "self" ? "selected" : ""}
                              aria-pressed={topMode === "self"}
                              onClick={() => setTopMode("self")}
                            >
                              自身
                            </button>
                            <button
                              className={topMode === "total" ? "selected" : ""}
                              aria-pressed={topMode === "total"}
                              onClick={() => setTopMode("total")}
                            >
                              总计
                            </button>
                          </div>
                        </div>
                        <div className="hotspot-list" aria-live="polite">
                          {visibleHotspots.length ? (
                            visibleHotspots.map((f, i) => (
                              <div className="hotspot-row" key={f.name}>
                                <span className="hotspot-rank mono">
                                  {String(i + 1).padStart(2, "0")}
                                </span>
                                <div>
                                  <button
                                    type="button"
                                    className="hotspot-function mono"
                                    title={f.name}
                                    aria-label={`在火焰图中聚焦 ${f.name}`}
                                    onClick={() => {
                                      setFlameFocus({
                                        name: f.name,
                                        token: Date.now(),
                                      });
                                      document
                                        .getElementById("task-flamegraph")
                                        ?.scrollIntoView({
                                          block: "start",
                                          behavior: window.matchMedia(
                                            "(prefers-reduced-motion: reduce)",
                                          ).matches
                                            ? "instant"
                                            : "smooth",
                                        });
                                    }}
                                  >
                                    {f.name}
                                  </button>
                                  <div className="hotspot-track">
                                    <span
                                      style={{
                                        width: `${Math.min(100, f[`${topMode}_percent`])}%`,
                                      }}
                                    />
                                  </div>
                                </div>
                                <strong
                                  className="mono"
                                  title={`${number(f[topMode])} ${r.summary.unit}`}
                                >
                                  {f[`${topMode}_percent`] > 0 &&
                                  f[`${topMode}_percent`] < 0.1
                                    ? "<0.1"
                                    : f[`${topMode}_percent`].toFixed(1)}
                                  <small>%</small>
                                </strong>
                              </div>
                            ))
                          ) : (
                            <div className="hotspot-empty">
                              当前口径下没有非零权重的
                              {task.kind === "PROC_CPU" ? "线程" : "函数"}。
                            </div>
                          )}
                        </div>
                      </section>
                    </>
                  )}
                  <section className="panel suggestions">
                    <div className="section-heading">
                      <div>
                        <h2>诊断洞察</h2>
                        <span className="section-note">
                          基于采样证据的规则分析
                        </span>
                      </div>
                      <Lightning size={18} />
                    </div>
                    {ruleSuggestions.length ? (
                      ruleSuggestions.map((s, i) => (
                        <article className="suggestion" key={i}>
                          <div>
                            <Badge
                              status={
                                s.severity === "warning" ? "RETRY" : "SUCCESS"
                              }
                            />
                            <span className="mono">
                              置信度 {(s.confidence * 100).toFixed(0)}%
                            </span>
                          </div>
                          <h3>{s.title}</h3>
                          <SafeMarkdown>{s.content}</SafeMarkdown>
                          <details>
                            <summary>查看分析证据</summary>
                            <pre>{JSON.stringify(s.evidence, null, 2)}</pre>
                          </details>
                          <small className="mono">
                            {s.engine || "rules"} / v{s.version || "1.0.0"}
                          </small>
                        </article>
                      ))
                    ) : (
                      <div className="insight-empty">
                        <span className="insight-empty-icon">
                          <Check size={17} />
                        </span>
                        <div>
                          <strong>本次采样未触发额外诊断规则</strong>
                          <p>
                            规则仅覆盖部分性能信号，请结合热点和现场指标继续判断。
                          </p>
                        </div>
                      </div>
                    )}
                  </section>
                </div>
                <SuggestionFeedback
                  id={task.id}
                  suggestions={task.suggestions || r.suggestions}
                  readOnly={operationReadOnly}
                />
              </>
            )}
          </>
        )}
        {tab === "timeline" && (
          <section className="panel timeline-panel">
            <div className="section-heading">
              <div>
                <h2>从请求到结果</h2>
                <span className="section-note">持久化事件 · 按序回放</span>
              </div>
              <span className="mono dim">{timeline.length} events</span>
            </div>
            <div className="timeline">
              {timeline.map((event) => (
                <div className="timeline-item" key={event.sequence}>
                  <span
                    className={`timeline-marker ${timelineTone(event.type)}`}
                    data-event-tone={timelineTone(event.type)}
                    aria-label={
                      timelineTone(event.type) === "failed"
                        ? "执行失败"
                        : timelineTone(event.type) === "canceled"
                          ? "执行取消"
                          : timelineTone(event.type) === "success"
                            ? "阶段成功"
                            : "执行事件"
                    }
                  >
                    {timelineTone(event.type) === "failed" ? (
                      <WarningCircle size={12} />
                    ) : timelineTone(event.type) === "canceled" ? (
                      <Stop size={12} />
                    ) : timelineTone(event.type) === "success" ? (
                      <Check size={12} />
                    ) : null}
                  </span>
                  <div>
                    <strong>{eventLabels[event.type] || event.type}</strong>
                    <small className="mono">
                      {event.type} · {event.source}
                    </small>
                    {event.payload && Object.keys(event.payload).length > 0 && (
                      <details>
                        <summary>事件详情</summary>
                        <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                      </details>
                    )}
                  </div>
                  <time className="mono">{dateTime(event.created_at)}</time>
                </div>
              ))}
            </div>
            {task.attempts?.length ? (
              <div className="attempts">
                <h3>执行尝试</h3>
                {task.attempts.map((a) => (
                  <div key={a.id}>
                    <span className="mono">
                      #{a.attempt} · {a.id}
                    </span>
                    <Badge status={a.status} />
                    <span>{a.error_code || "—"}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </section>
        )}
        {tab === "artifacts" && (
          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>诊断制品</h2>
                <span className="section-note">
                  下载链接短期有效，访问独立鉴权
                </span>
              </div>
            </div>
            {download.error && <ErrorPanel error={download.error} />}
            <div className="artifact-list">
              {artifacts.length ? (
                artifacts.map((a) => (
                  <div className="artifact-row" key={a.id}>
                    <span className="artifact-icon">
                      <FileText size={23} />
                    </span>
                    <div>
                      <strong>{a.name}</strong>
                      {a.expires_at && (
                        <small>
                          保留至 {dateTime(a.expires_at)} ·{" "}
                          {a.status || "ready"}
                        </small>
                      )}
                      <small className="mono">
                        {a.kind} · {bytes(a.size)} · SHA256{" "}
                        {a.sha256?.slice(0, 12)}…
                      </small>
                    </div>
                    <button
                      className="icon-button bordered"
                      aria-label={`下载${a.name}`}
                      disabled={
                        download.isPending ||
                        a.download_allowed === false ||
                        (!!a.status && a.status !== "ready") ||
                        (!!a.expires_at &&
                          Date.parse(a.expires_at) <= Date.now())
                      }
                      onClick={() => download.mutate(a)}
                    >
                      <DownloadSimple size={18} />
                    </button>
                  </div>
                ))
              ) : (
                <div className="quiet-empty">尚无制品文件</div>
              )}
            </div>
          </section>
        )}
        {tab === "sharing" && <TaskSharing id={task.id} />}
        {tab === "parameters" && (
          <section className="panel parameters-panel">
            <h2>任务参数</h2>
            <dl>
              {Object.entries({
                task_id: task.id,
                kind: task.kind,
                target_agent_id: task.target_agent_id || "import",
                ...task.params,
                collection_status: task.collection_status,
                analysis_status: task.analysis_status,
                creator_id: task.creator_id,
                created_at: task.created_at,
              }).map(([k, v]) => (
                <div key={k}>
                  <dt className="mono">{k}</dt>
                  <dd className="mono">
                    {typeof v === "object" ? JSON.stringify(v) : String(v)}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        )}
      </div>
    </div>
  );
}
