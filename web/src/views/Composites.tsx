import TargetFields, {
  emptyTarget,
  targetPayload,
} from "../components/TargetFields";
import type { TargetForm } from "../components/TargetFields";
import GroupScope from "../components/GroupScope";
import { pollingInterval } from "../query-policy";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Plus,
  Trash,
  ArrowRight,
  GitBranch,
  Stop,
} from "@phosphor-icons/react";
import { api, dateTime, newIdempotencyKey, number, kindName } from "../api";
import { validateDAG } from "../composite";
import {
  initialOptions,
  validateOptions,
  normalizeOptions,
} from "../task-schema";
import type { Agent, List, Task, TaskKind } from "../types";
import { useCanWrite } from "../auth-context";
import { PageHeading, ErrorPanel, Skeleton, Busy } from "../components/ui";
import ParameterFields from "../components/ParameterFields";
import { SafeMarkdown } from "../components/SafeMarkdown";
interface Draft {
  target: TargetForm;
  key: string;
  required: boolean;
  depends_on: string[];
  kind: string;
  duration: number;
  options: Record<string, string | number | boolean>;
}
interface Composite {
  report?: {
    results: {
      node_key: string;
      task_id: string;
      summary: {
        kind: string;
        total_samples?: number;
        summary?: { unit?: string; source?: string };
      };
    }[];
    suggestions: {
      node_key: string;
      task_id: string;
      suggestion: {
        title: string;
        content: string;
        engine: string;
        version: string;
      };
    }[];
    limitations: string[];
  };
  id: string;
  name: string;
  status: string;
  deadline_at: string;
  created_at: string;
  allow_partial_success: boolean;
  summary: Record<string, number>;
  nodes: {
    key: string;
    required: boolean;
    depends_on: string[];
    status: string;
    task_id?: string;
    error_code?: string;
    task?: Task;
  }[];
}
const draft = (index: number): Draft => ({
  target: emptyTarget(),
  key: "step_" + index,
  required: true,
  depends_on: [],
  kind: "PROC_CPU",
  duration: 10,
  options: {},
});
export default function Composites() {
  const canWrite = useCanWrite(),
    navigate = useNavigate(),
    qc = useQueryClient();
  const [groupScope, setGroupScope] = useState({
    group_id: "",
    visibility: "private",
  });
  const [show, setShow] = useState(false),
    [name, setName] = useState(""),
    [nodes, setNodes] = useState<Draft[]>([draft(1)]),
    [partial, setPartial] = useState(true),
    [deadline, setDeadline] = useState(300);
  const key = useRef<{ hash: string; value: string } | undefined>(undefined);
  const q = useQuery({
    queryKey: ["composites"],
    queryFn: ({ signal }) => api<List<Composite>>("/composites", { signal }),
    refetchInterval: (query) => pollingInterval(query, 5000),
  });
  const agents = useQuery({
    queryKey: ["agents", "composite"],
    queryFn: ({ signal }) =>
      api<List<Agent>>("/agents?page_size=100", { signal }),
    enabled: show,
  });
  const kinds = useQuery({
    queryKey: ["kinds"],
    queryFn: ({ signal }) => api<TaskKind[]>("/task-kinds", { signal }),
    enabled: show,
  });
  const update = (i: number, value: Partial<Draft>) =>
    setNodes((v) => v.map((n, j) => (j === i ? { ...n, ...value } : n)));
  const create = useMutation({
    mutationFn: () => {
      const graphError = validateDAG(nodes);
      if (graphError) throw new Error(graphError);
      for (const n of nodes) {
        const error = validateOptions(
          kinds.data?.find((k) => k.id === n.kind),
          n.options,
        );
        if (error) throw new Error(n.key + "：" + error);
      }
      const body = JSON.stringify({
        name,
        ...(groupScope.group_id ? groupScope : { visibility: "private" }),
        deadline_seconds: deadline,
        allow_partial_success: partial,
        nodes: nodes.map((n) => ({
          key: n.key,
          required: n.required,
          depends_on: n.depends_on,
          task: {
            name: name + " / " + n.key,
            kind: n.kind,
            target_agent_id: n.target.agent_id,
            parameters: {
              ...targetPayload(n.target),
              duration_seconds: n.duration,
              frequency_hz:
                kinds.data?.find((k) => k.id === n.kind)
                  ?.default_frequency_hz || 10,
              options: normalizeOptions(
                kinds.data?.find((k) => k.id === n.kind),
                n.options,
              ),
            },
          },
        })),
      });
      if (key.current?.hash !== body)
        key.current = { hash: body, value: newIdempotencyKey() };
      return api<Composite>("/composites", {
        method: "POST",
        body,
        idempotencyKey: key.current.value,
      });
    },
    onSuccess: (c) => {
      qc.invalidateQueries();
      navigate("/composites/" + c.id);
    },
  });
  return (
    <>
      <PageHeading
        eyebrow="DIAGNOSTIC / ORCHESTRATION"
        title="把多次观察，连成一条证据链"
        description="编排依赖、并行采样和必选步骤，集中查看结果与部分失败。"
        action={
          canWrite && (
            <button className="button primary" onClick={() => setShow(!show)}>
              <Plus size={17} />
              编排复合诊断
            </button>
          )
        }
      />
      {show && (
        <form
          className="composite-form"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <section className="panel form-section">
            <h2>诊断编排</h2>
            <div className="form-grid">
              <label className="field">
                <span>名称</span>
                <input
                  autoFocus
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={100}
                />
              </label>
              <label className="field">
                <span>整体超时（秒）</span>
                <input
                  type="number"
                  required
                  min="1"
                  max="3600"
                  value={deadline}
                  onChange={(e) => setDeadline(Number(e.target.value))}
                />
              </label>
            </div>
            <GroupScope value={groupScope} onChange={setGroupScope} />
            <label className="consent-line">
              <input
                type="checkbox"
                checked={partial}
                onChange={(e) => setPartial(e.target.checked)}
              />
              允许可选步骤失败时仍返回部分成功
            </label>
          </section>
          {nodes.map((n, i) => {
            const k = kinds.data?.find((k) => k.id === n.kind);
            return (
              <section className="panel form-section composite-step" key={i}>
                <div className="section-heading">
                  <h2>
                    <span className="step-number mono">{i + 1}</span>
                    {n.key}
                  </h2>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={"删除步骤" + (i + 1)}
                    disabled={nodes.length === 1}
                    onClick={() =>
                      setNodes((v) =>
                        v
                          .filter((_, j) => i !== j)
                          .map((x) => ({
                            ...x,
                            depends_on: x.depends_on.filter((d) => d !== n.key),
                          })),
                      )
                    }
                  >
                    <Trash size={18} />
                  </button>
                </div>
                <div className="form-grid">
                  <label className="field">
                    <span>步骤标识</span>
                    <input
                      required
                      pattern="[A-Za-z][A-Za-z0-9_-]{0,31}"
                      value={n.key}
                      onChange={(e) => {
                        const next = e.target.value;
                        setNodes((v) =>
                          v.map((x, j) =>
                            j === i
                              ? { ...x, key: next }
                              : {
                                  ...x,
                                  depends_on: x.depends_on.map((d) =>
                                    d === n.key ? next : d,
                                  ),
                                },
                          ),
                        );
                      }}
                    />
                  </label>
                  <label className="field">
                    <span>采样方式</span>
                    <select
                      value={n.kind}
                      onChange={(e) => {
                        const next = kinds.data?.find(
                          (k) => k.id === e.target.value,
                        );
                        update(i, {
                          kind: e.target.value,
                          options: initialOptions(next),
                          target: { ...n.target, agent_id: "" },
                        });
                      }}
                    >
                      {kinds.data
                        ?.filter(
                          (k) =>
                            !["import", "composite"].includes(k.runner) &&
                            k.id !== "COMPOSITE",
                        )
                        .map((k) => (
                          <option key={k.id} value={k.id}>
                            {k.display_name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label className="field">
                    <span>采集时长（秒）</span>
                    <input
                      type="number"
                      min="1"
                      max={k?.max_duration_seconds || 60}
                      value={n.duration}
                      onChange={(e) =>
                        update(i, { duration: Number(e.target.value) })
                      }
                    />
                  </label>
                </div>
                <TargetFields
                  kind={k}
                  agents={agents.data?.items || []}
                  value={n.target}
                  onChange={(v) => update(i, { target: v })}
                />
                <ParameterFields
                  kind={k}
                  value={n.options}
                  onChange={(v) => update(i, { options: v })}
                />
                <label className="consent-line">
                  <input
                    type="checkbox"
                    checked={n.required}
                    onChange={(e) => update(i, { required: e.target.checked })}
                  />
                  必选步骤
                </label>
                <fieldset className="dependency-picks">
                  <legend>等待以下步骤成功后执行（不选则立即执行）</legend>
                  {nodes
                    .filter((x) => x !== n)
                    .map((other) => (
                      <label key={other.key}>
                        <input
                          type="checkbox"
                          checked={n.depends_on.includes(other.key)}
                          onChange={(e) =>
                            update(i, {
                              depends_on: e.target.checked
                                ? [...n.depends_on, other.key]
                                : n.depends_on.filter((d) => d !== other.key),
                            })
                          }
                        />
                        {other.key}
                      </label>
                    ))}
                </fieldset>
              </section>
            );
          })}
          {create.error && <ErrorPanel error={create.error} />}
          <div className="form-footer">
            <button
              type="button"
              className="button secondary"
              disabled={nodes.length >= 16}
              onClick={() =>
                setNodes((v) => [
                  ...v,
                  draft(
                    Math.max(
                      ...v.map((n) => Number(n.key.replace("step_", "")) || 0),
                    ) + 1,
                  ),
                ])
              }
            >
              <Plus size={16} />
              添加步骤
            </button>
            <button className="button primary" disabled={create.isPending}>
              {create.isPending ? <Busy /> : "创建复合诊断"}
            </button>
          </div>
        </form>
      )}
      {q.error ? (
        <ErrorPanel error={q.error} />
      ) : q.isLoading ? (
        <Skeleton />
      ) : (
        <section className="panel composite-list">
          {q.data?.items.length ? (
            q.data.items.map((c) => (
              <Link
                className="composite-row"
                key={c.id}
                to={"/composites/" + c.id}
              >
                <GitBranch size={22} />
                <div>
                  <strong>{c.name}</strong>
                  <small>
                    {dateTime(c.created_at)} ·{" "}
                    {c.nodes?.length || c.summary?.total || 0} 个步骤
                  </small>
                </div>
                <span className="status-chip">{c.status}</span>
                <ArrowRight size={17} />
              </Link>
            ))
          ) : (
            <div className="quiet-empty">
              还没有复合诊断。从几个相关观察开始，建立你的诊断流程。
            </div>
          )}
        </section>
      )}
    </>
  );
}
export function CompositeDetail() {
  const { id } = useParams(),
    qc = useQueryClient(),
    canWrite = useCanWrite();
  const q = useQuery({
    queryKey: ["composite", id],
    queryFn: ({ signal }) => api<Composite>("/composites/" + id, { signal }),
    refetchInterval: (query) =>
      pollingInterval(
        query,
        3000,
        !!query.state.data &&
          !["PENDING", "RUNNING"].includes(query.state.data.status),
      ),
  });
  const cancel = useMutation({
    mutationFn: () =>
      api("/composites/" + id + "/cancel", { method: "POST", body: "{}" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["composite", id] }),
  });
  if (q.error) return <ErrorPanel error={q.error} />;
  if (!q.data) return <Skeleton />;
  const c = q.data;
  const completed =
      (c.summary.succeeded || 0) +
      (c.summary.failed || 0) +
      (c.summary.skipped || 0),
    total = c.summary.total || c.nodes.length;
  return (
    <>
      <Link className="back-link" to="/composites">
        返回复合诊断
      </Link>
      <PageHeading
        eyebrow={"COMPOSITE / " + c.status}
        title={c.name}
        description={
          "整体截止 " +
          dateTime(c.deadline_at) +
          " · " +
          (c.allow_partial_success ? "允许部分成功" : "全部必选步骤须成功")
        }
        action={
          canWrite &&
          ["PENDING", "RUNNING"].includes(c.status) && (
            <button
              className="button secondary"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
            >
              <Stop size={17} />
              取消所有未完成步骤
            </button>
          )
        }
      />
      {cancel.error && <ErrorPanel error={cancel.error} />}
      <section className="panel composite-progress">
        <div>
          <strong>
            {completed} / {total}
          </strong>
          <span>步骤已结束 · {c.status}</span>
        </div>
        <progress
          value={completed}
          max={total || 1}
          aria-label="复合诊断进度"
        />
      </section>
      {c.report && (
        <section className="panel form-section composite-report">
          <div className="section-heading">
            <div>
              <h2>诊断汇总</h2>
              <span className="section-note">
                保留各采样器的度量口径，依据子任务证据解读。
              </span>
            </div>
          </div>
          <div className="dag-nodes">
            {c.report.results.map((r) => (
              <Link
                className="composite-evidence"
                key={r.node_key}
                to={"/tasks/" + r.task_id}
              >
                <small>
                  {r.node_key} · {kindName(r.summary.kind)}
                </small>
                <strong className="mono">
                  {r.summary.total_samples == null
                    ? "无可加总权重"
                    : number(r.summary.total_samples) +
                      " " +
                      (r.summary.summary?.unit || "")}
                </strong>
                <span>{r.summary.summary?.source || "查看子任务"}</span>
              </Link>
            ))}
          </div>
          {c.report.suggestions.map((s, i) => (
            <article className="suggestion" key={i}>
              <h3>{s.suggestion.title}</h3>
              <SafeMarkdown>{s.suggestion.content}</SafeMarkdown>
              <Link className="text-link" to={"/tasks/" + s.task_id}>
                {s.node_key} · 查看原始证据
              </Link>
              <small className="mono">
                {s.suggestion.engine} / {s.suggestion.version}
              </small>
            </article>
          ))}
          <ul className="section-limitations">
            {c.report.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </section>
      )}
      <div className="dag-nodes">
        {c.nodes.map((n) => (
          <section className="panel dag-node" key={n.key}>
            <div className="section-heading">
              <h2>{n.key}</h2>
              <span className="status-chip">{n.status}</span>
            </div>
            <p>
              {n.required ? "必选步骤" : "可选步骤"}
              {n.depends_on.length
                ? " · 等待 " + n.depends_on.join("、")
                : " · 起始步骤"}
            </p>
            {n.error_code && <p className="field-error">{n.error_code}</p>}
            {n.task_id && (
              <Link
                className="button secondary small"
                to={"/tasks/" + n.task_id}
              >
                查看诊断结果
                <ArrowRight size={14} />
              </Link>
            )}
          </section>
        ))}
      </div>
    </>
  );
}
