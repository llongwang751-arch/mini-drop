/**
 * 任务内的智能诊断入口：针对已成功分析的当前证据提出问题、查看工具记录并审阅复测计划。
 * 模型调用与工具执行都在服务端进行；前端只提交目标、方式和确认，不把原始 profile 发给模型。
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, dateTime, newIdempotencyKey } from "../api";
import type { List, Task } from "../types";
import { Busy, ErrorPanel } from "./ui";
import "./DiagnosisAgent.css";

interface Diagnosis {
  id: string;
  attempt_id: string;
  analysis_job_id: string;
  analysis_run: number;
  goal: string;
  mode: "evidence" | "model";
  status: "pending" | "running" | "success" | "failed";
  error_code?: string;
  created_at: string;
  trace: { evidence_id: string; tool: string; observation: unknown }[];
  report?: {
    engine: string;
    model?: string;
    summary: string;
    hypotheses: {
      claim: string;
      evidence_ids: string[];
      verification: string;
    }[];
    limitations: string[];
    plan?: {
      kind: string;
      duration_seconds: number;
      frequency_hz: number;
      reason: string;
    };
  };
}
const tools: Record<string, string> = {
  read_profile_summary: "读取性能摘要",
  inspect_hotspots: "检查热点路径",
  inspect_call_paths: "展开热点调用链",
  check_collection_quality: "核对采集质量",
  propose_verification: "制定复测草案",
  compare_profiles: "比较复测证据",
};
const errors: Record<string, string> = {
  AI_UPSTREAM_ERROR: "模型服务调用失败，可重建诊断或使用本地证据检查。",
  AI_INVALID_REPORT: "模型报告未通过结构或证据引用校验，未采纳该结论。",
  AI_INSUFFICIENT_EVIDENCE: "模型未完成必要的证据检查，未采纳该结论。",
  AI_TOOL_REJECTED: "模型提出的工具调用未通过安全校验。",
  AI_BUDGET_EXCEEDED: "已达到调用预算，请缩小诊断问题。",
  AUTHORIZATION_REVOKED: "诊断权限已被撤销。",
  STALE_ATTEMPT: "采集或分析已更新，请针对当前结果重新诊断。",
  INPUT_CHANGED: "分析证据已改变，请重新诊断。",
  DIAGNOSIS_CANCELED: "诊断已取消，原采集与分析结果不受影响。",
  DIAGNOSIS_TIMEOUT: "诊断超时，请缩小问题或使用本地证据检查。",
  DIAGNOSIS_RETRY_EXHAUSTED: "服务恢复重试已用尽，请重新诊断。",
};

export default function DiagnosisAgent({
  task,
  readOnly,
  hotspots = [],
}: {
  task: Task;
  readOnly: boolean;
  hotspots?: string[];
}) {
  const qc = useQueryClient();
  const [goal, setGoal] = useState(
    "检查主要热点和采集质量，给出待验证的瓶颈假设与复测方法",
  );
  const [mode, setMode] = useState<"evidence" | "model">("model");
  const [consent, setConsent] = useState(false);
  const [selected, setSelected] = useState("");
  const [review, setReview] = useState(false);
  const [verificationTask, setVerificationTask] = useState<string>();
  const collectKey = useRef(newIdempotencyKey());
  const startKey = useRef<{ fingerprint: string; key: string } | undefined>(
    undefined,
  );
  const providers = useQuery({
    queryKey: ["diagnosis-providers"],
    queryFn: ({ signal }) =>
      api<{ ai: { enabled: boolean; name: string } }>("/auth/providers", {
        signal,
      }),
  });
  const runs = useQuery({
    queryKey: ["diagnoses", task.id],
    queryFn: ({ signal }) =>
      api<List<Diagnosis>>(`/tasks/${task.id}/diagnoses`, { signal }),
    refetchInterval: (q) =>
      q.state.data?.items.some((r) => ["pending", "running"].includes(r.status))
        ? 2000
        : false,
  });
  const run =
    runs.data?.items.find((r) => r.id === selected) || runs.data?.items[0];
  // 复测记录串联基线诊断、候选任务和再次诊断；页面轮询读取后台闭环结果，不在浏览器推断结论。
  const verifications = useQuery({
    queryKey: ["verifications", task.id],
    queryFn: ({ signal }) =>
      api<
        List<{
          id: string;
          diagnosis_id: string;
          candidate_task_id: string;
          candidate_diagnosis_id?: string;
          status: string;
          error_code?: string;
          report?: {
            outcome?: string;
            root_cause_status?: string;
            limitations?: string[];
          };
        }>
      >(`/tasks/${task.id}/verifications`, { signal }),
    refetchInterval: (q) =>
      q.state.data?.items?.some((v) =>
        ["waiting", "diagnosing", "comparing"].includes(v.status),
      )
        ? 2000
        : false,
  });
  useEffect(() => {
    if (runs.dataUpdatedAt)
      void qc.invalidateQueries({ queryKey: ["events", task.id] });
  }, [runs.dataUpdatedAt, task.id, qc]);
  const ready =
    task.collection_status === "COLLECTED" &&
    task.analysis_status === "SUCCESS";
  // 报告必须属于当前采集轮次和分析 job/run；重分析后旧报告仍可追溯，但不能继续执行它的复测计划。
  const currentAttempt = task.attempts?.length
    ? task.attempts.reduce((a, b) => (a.attempt > b.attempt ? a : b)).id
    : undefined;
  const stale =
    !!run &&
    ((!!currentAttempt && run.attempt_id !== currentAttempt) ||
      (!!task.analysis_identity &&
        (run.analysis_job_id !== task.analysis_identity.analysis_job_id ||
          run.analysis_run !== task.analysis_identity.analysis_run)));
  const active = run?.status === "pending" || run?.status === "running";
  const external = mode === "model";
  // 问题、诊断方式和同意项构成提交指纹，同一输入的网络重试不能生成多份诊断。
  const start = useMutation({
    mutationFn: () => {
      const fingerprint = JSON.stringify({
        goal,
        mode,
        consent: external && consent,
      });
      if (startKey.current?.fingerprint !== fingerprint)
        startKey.current = { fingerprint, key: newIdempotencyKey() };
      return api<Diagnosis>(`/tasks/${task.id}/diagnoses`, {
        method: "POST",
        body: fingerprint,
        idempotencyKey: startKey.current!.key,
      });
    },
    onSuccess: (created) => {
      setSelected(created.id);
      setReview(false);
      setVerificationTask(undefined);
      startKey.current = undefined;
      collectKey.current = newIdempotencyKey();
      qc.invalidateQueries({ queryKey: ["diagnoses", task.id] });
    },
  });
  const cancel = useMutation({
    mutationFn: () =>
      api(`/tasks/${task.id}/diagnoses/${run!.id}/cancel`, {
        method: "POST",
        body: "{}",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["diagnoses", task.id] }),
  });
  // 用户审阅后才创建复测任务；复用独立幂等键，避免重复点击造成重复采集。
  const collect = useMutation({
    mutationFn: () =>
      api<Task>(`/tasks/${task.id}/diagnoses/${run!.id}/collect`, {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
        idempotencyKey: collectKey.current,
      }),
    onSuccess: (created) => {
      setReview(false);
      setVerificationTask(created.id);
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["verifications", task.id] });
    },
  });
  const plan = run?.report?.plan;
  return (
    <section className="panel diagnosis-agent" aria-label="智能性能诊断">
      <div className="section-heading">
        <div>
          <h2>智能性能诊断</h2>
          <p className="section-note">
            围绕一个问题查证据、提假设、安排复测。每一步都可审阅。
          </p>
        </div>
        <span className="badge neutral">
          {providers.data?.ai.enabled ? "模型已配置" : "本地证据工具可用"}
        </span>
      </div>
      {!ready && (
        <p role="status">
          完整采集与分析成功后，才能运行诊断。失败结果仍可在时间线查看。
        </p>
      )}
      {providers.error && <ErrorPanel error={providers.error} />}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          start.mutate();
        }}
      >
        <label htmlFor="diagnosis-goal">你希望定位什么问题？</label>
        <textarea
          id="diagnosis-goal"
          rows={3}
          maxLength={600}
          value={goal}
          disabled={readOnly || !ready || start.isPending}
          onChange={(e) => setGoal(e.target.value)}
          aria-describedby="diagnosis-boundary"
        />
        <div className="diagnosis-options">
          <label htmlFor="diagnosis-mode">诊断方式</label>
          <select
            id="diagnosis-mode"
            value={mode}
            disabled={readOnly || start.isPending}
            onChange={(e) => setMode(e.target.value as typeof mode)}
          >
            <option value="model" disabled={!providers.data?.ai.enabled}>
              AI 工具诊断{!providers.data?.ai.enabled ? "（模型未配置）" : ""}
            </option>
            <option value="evidence">本地证据检查</option>
          </select>
          <button
            className="button primary"
            type="submit"
            disabled={
              readOnly ||
              !ready ||
              !goal.trim() ||
              start.isPending ||
              (external && (!consent || !providers.data?.ai.enabled))
            }
          >
            {start.isPending ? <Busy /> : "开始诊断"}
          </button>
        </div>
        {external && (
          <label className="diagnosis-consent">
            <input
              type="checkbox"
              checked={consent}
              disabled={readOnly}
              onChange={(e) => setConsent(e.target.checked)}
            />
            同意发送问题文本与脱敏统计；已确认问题中不含敏感信息
          </label>
        )}
        <p id="diagnosis-boundary" className="section-note">
          工具限定当前任务，最多4轮工具交互、8次工具；必要时额外1次报告纠错，总计最多150秒。原始文件与原符号留在平台。复测需单独确认。
        </p>
        {readOnly && (
          <p className="section-note">
            当前权限可查看诊断记录，不能发起诊断或复测。
          </p>
        )}
      </form>
      {[start.error, runs.error, cancel.error, collect.error]
        .filter(Boolean)
        .map((error, i) => (
          <ErrorPanel key={i} error={error} />
        ))}
      {runs.data?.items.length ? (
        <div className="diagnosis-results">
          <label htmlFor="diagnosis-history">诊断记录</label>
          <select
            id="diagnosis-history"
            value={run?.id || ""}
            onChange={(e) => {
              setSelected(e.target.value);
              setReview(false);
              setVerificationTask(undefined);
              collectKey.current = newIdempotencyKey();
              collect.reset();
            }}
          >
            {runs.data.items.map((r) => (
              <option key={r.id} value={r.id}>
                #{r.id} · {r.mode === "model" ? "AI" : "本地"} ·{" "}
                {dateTime(r.created_at)}
              </option>
            ))}
          </select>
          <div className="diagnosis-run-state" role="status">
            <strong>
              {active
                ? run?.status === "pending"
                  ? "等待诊断"
                  : "正在检查证据"
                : run?.status === "success"
                  ? "诊断报告已生成 · 待人工验证"
                  : "诊断未完成"}
            </strong>
            {active && !readOnly && (
              <button
                className="button secondary"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate()}
              >
                取消诊断
              </button>
            )}
          </div>
          <p className="dim">问题：{run?.goal}</p>
          {stale && (
            <p className="error-text">
              这条记录属于历史执行版本，请基于当前结果重新诊断。
            </p>
          )}
          {run?.error_code && (
            <p role="alert">
              {errors[run.error_code] || `诊断失败（${run.error_code}）`}
            </p>
          )}
          <ol className="diagnosis-trace" aria-label="工具执行证据">
            {run?.trace.map((step) => (
              <li
                key={step.evidence_id}
                id={`diagnosis-${run.id}-${step.evidence_id}`}
              >
                <details>
                  <summary>
                    <span className="mono">{step.evidence_id}</span>{" "}
                    {tools[step.tool] || step.tool}
                  </summary>
                  <pre>{JSON.stringify(step.observation, null, 2)}</pre>
                  {step.tool === "inspect_hotspots" &&
                    !stale &&
                    hotspots.length > 0 && (
                      <div>
                        <strong>平台内原符号（未发送模型）</strong>
                        <ol>
                          {hotspots.slice(0, 10).map((name, i) => (
                            <li key={i}>
                              <code>{name}</code>
                            </li>
                          ))}
                        </ol>
                      </div>
                    )}
                </details>
              </li>
            ))}
          </ol>
          {run?.report && (
            <article className="diagnosis-report">
              <div className="eyebrow">
                {run.report.engine === "model"
                  ? `AI / ${run.report.model}`
                  : "本地证据检查 / 无模型调用"}
              </div>
              <h3>诊断结论与验证</h3>
              <p>{run.report.summary}</p>
              {run.report.hypotheses.map((h, i) => (
                <section key={i} className="diagnosis-hypothesis">
                  <h4>待验证假设 {i + 1}</h4>
                  <p>{h.claim}</p>
                  <div className="diagnosis-refs">
                    {h.evidence_ids.map((ref) => (
                      <a key={ref} href={`#diagnosis-${run.id}-${ref}`}>
                        {ref}
                      </a>
                    ))}
                  </div>
                  <p>
                    <strong>如何验证：</strong>
                    {h.verification}
                  </p>
                </section>
              ))}
              <details>
                <summary>证据边界</summary>
                <ul>
                  {run.report.limitations.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </details>
              {plan && (
                <section className="diagnosis-plan">
                  <h3>复测草案</h3>
                  <p>
                    {plan.kind} · {plan.duration_seconds} 秒 ·{" "}
                    {plan.frequency_hz
                      ? `${plan.frequency_hz} Hz`
                      : "无需采样频率"}
                  </p>
                  <p>{plan.reason}</p>
                  <p className="section-note">
                    目标沿用原任务节点与进程身份，提交时重新校验权限、能力和目标。先核对任务参数中的
                    PID、启动时间及 UID。
                  </p>
                  {!readOnly && !stale && ready && !verificationTask && (
                    <button
                      className="button secondary"
                      disabled={active}
                      onClick={() => setReview(!review)}
                    >
                      审阅并发起复测
                    </button>
                  )}
                  {review && (
                    <div
                      className="diagnosis-confirm"
                      role="group"
                      aria-label="确认复测"
                    >
                      <p>
                        确认已核对原目标和采集开销？将创建复测任务，完成后自动再次调用模型诊断并比较两次证据。
                      </p>
                      <button
                        className="button primary"
                        disabled={collect.isPending}
                        onClick={() => collect.mutate()}
                      >
                        {collect.isPending ? <Busy /> : "确认创建复测任务"}
                      </button>
                      <button
                        className="button secondary"
                        disabled={collect.isPending}
                        onClick={() => setReview(false)}
                      >
                        暂不复测
                      </button>
                    </div>
                  )}
                  {verificationTask && (
                    <p role="status">
                      已创建复测任务{" "}
                      <Link to={`/tasks/${verificationTask}`}>
                        #{verificationTask}
                      </Link>
                      。完成后自动再次诊断，也可查看
                      <Link
                        to={`/compare?baseline=${task.id}&candidate=${verificationTask}`}
                      >
                        对比结果
                      </Link>
                      。
                    </p>
                  )}
                </section>
              )}
            </article>
          )}
        </div>
      ) : (
        <p className="empty-state">
          暂无诊断记录。可以先选择“本地证据检查”，再结合 AI 诊断安排复测。
        </p>
      )}
      {verifications.error && <ErrorPanel error={verifications.error} />}
      {verifications.data?.items
        ?.filter((v) => v.diagnosis_id === run?.id)
        .map((v) => (
          <section
            className="diagnosis-plan"
            key={v.id}
            aria-label="自动复测闭环"
          >
            <h3>自动复测闭环</h3>
            <p role="status">
              {(
                {
                  waiting: "等待复测采集与解析",
                  diagnosing: "正在自动再次诊断",
                  comparing: "正在比较证据",
                  success: "复测和再次诊断已完成",
                  failed: "复测闭环失败",
                  canceled: "已取消",
                } as Record<string, string>
              )[v.status] || v.status}
            </p>
            <Link to={`/tasks/${v.candidate_task_id}`}>
              查看复测与新诊断 #{v.candidate_task_id}
            </Link>
            {" · "}
            <Link
              to={`/compare?baseline=${task.id}&candidate=${v.candidate_task_id}`}
            >
              对比两次证据
            </Link>
            {v.report && (
              <p className="section-note">
                复测比较用于检查热点是否重复出现；确认根因还需要修改后的功能与性能对照。
                {v.report.limitations?.join("；")}
              </p>
            )}
            {v.error_code && (
              <p role="alert">{errors[v.error_code] || v.error_code}</p>
            )}
          </section>
        ))}
    </section>
  );
}
