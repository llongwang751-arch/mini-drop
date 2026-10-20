import TargetFields, {
  emptyTarget,
  targetPayload,
} from "../components/TargetFields";
import GroupScope from "../components/GroupScope";
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Clock, Plus, Trash, X, CalendarDots } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { api, dateTime, kindName } from "../api";
import { pollingInterval } from "../query-policy";
import ParameterFields from "../components/ParameterFields";
import { initialOptions, normalizeOptions } from "../task-schema";
import { useCanWrite } from "../auth-context";
import type { Schedule, List, Agent, TaskKind } from "../types";
import {
  PageHeading,
  Empty,
  ErrorPanel,
  Skeleton,
  Busy,
} from "../components/ui";
export default function Schedules() {
  const canWrite = useCanWrite();
  const qc = useQueryClient();
  const [groupScope, setGroupScope] = useState({
    group_id: "",
    visibility: "private",
  });
  const [show, setShow] = useState(false);
  const [name, setName] = useState("");
  const [agent, setAgent] = useState("");
  const [kind, setKind] = useState("PROC_CPU");
  const [pid, setPid] = useState("");
  const [targetDetails, setTargetDetails] = useState(emptyTarget());
  const [interval, setInterval] = useState(60);
  const [cronMode, setCronMode] = useState(true);
  const [cron, setCron] = useState("0 9 * * 1-5");
  const [timezone, setTimezone] = useState("Asia/Shanghai");
  const [options, setOptions] = useState<
    Record<string, string | number | boolean>
  >({});
  const kinds = useQuery({
    queryKey: ["kinds"],
    queryFn: ({ signal }) => api<TaskKind[]>("/task-kinds", { signal }),
    enabled: show,
  });
  const [removeId, setRemoveId] = useState<string>();
  const [history, setHistory] = useState<Schedule>();
  const records = useQuery({
    queryKey: ["schedule-records", history?.id],
    queryFn: ({ signal }) =>
      api<
        List<{
          id: string;
          scheduled_at: string;
          task_id?: string;
          status: string;
          error_message?: string;
        }>
      >(`/schedules/${history!.id}/records`, { signal }),
    enabled: !!history,
    refetchInterval: (query) =>
      history ? pollingInterval(query, 10000) : false,
  });
  useEffect(() => {
    if (!show && !removeId) return;
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShow(false);
        setRemoveId(undefined);
      }
      if (event.key === "Tab") {
        const items = document.querySelectorAll<HTMLElement>(
          ".modal button:not(:disabled),.modal input,.modal select,.modal textarea",
        );
        const first = items[0],
          last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [show, removeId]);
  const q = useQuery({
    queryKey: ["schedules"],
    queryFn: ({ signal }) => api<List<Schedule>>("/schedules", { signal }),
    refetchInterval: (query) => pollingInterval(query, 15000),
  });
  const agents = useQuery({
    queryKey: ["agents", "schedules"],
    queryFn: ({ signal }) =>
      api<List<Agent>>("/agents?page_size=100", { signal }),
    enabled: show,
  });
  const mutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api(`/schedules/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });
  const create = useMutation({
    mutationFn: () =>
      api("/schedules", {
        method: "POST",
        body: JSON.stringify({
          name,
          ...(groupScope.group_id ? groupScope : { visibility: "private" }),
          ...(cronMode ? { cron } : { interval_minutes: interval }),
          timezone,
          template: {
            name,
            kind,
            target_agent_id: agent,
            parameters: {
              ...targetPayload({ ...targetDetails, agent_id: agent, pid }),
              duration_seconds: 10,
              frequency_hz:
                kinds.data?.find((k) => k.id === kind)?.default_frequency_hz ||
                1,
              scope: "process",
              options: normalizeOptions(
                kinds.data?.find((k) => k.id === kind),
                options,
              ),
            },
          },
        }),
      }),
    onSuccess: () => {
      setShow(false);
      qc.invalidateQueries({ queryKey: ["schedules"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/schedules/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setRemoveId(undefined);
      qc.invalidateQueries({ queryKey: ["schedules"] });
    },
  });
  return (
    <>
      <PageHeading
        eyebrow="AUTOMATION / SCHEDULES"
        title="让诊断按时发生"
        description="周期采集、持续观察，让偶发问题留下线索。"
        action={
          canWrite && (
            <button className="button primary" onClick={() => setShow(true)}>
              <Plus size={17} />
              创建计划
            </button>
          )
        }
      />
      <div className="info-banner">
        <Clock size={19} />
        <p>
          支持 Cron
          与固定间隔，按所选时区计算触发时间。每次运行创建普通诊断任务，并保留不可变的模板快照；错过的时点跳至下一次执行。
        </p>
      </div>
      {mutation.error && <ErrorPanel error={mutation.error} />}
      <section className="panel">
        {q.error ? (
          <ErrorPanel error={q.error} />
        ) : q.isLoading ? (
          <Skeleton />
        ) : !q.data?.items.length ? (
          <Empty
            title="把观察变成习惯"
            description="为关键进程设定诊断计划，周期性记录性能现场。"
            icon={<CalendarDots size={30} />}
            action={
              canWrite && (
                <button
                  className="button secondary"
                  onClick={() => setShow(true)}
                >
                  <Plus size={16} />
                  创建第一个计划
                </button>
              )
            }
          />
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>计划名称</th>
                  <th>采样方式</th>
                  <th>触发规则 / 时区</th>
                  <th>下次执行</th>
                  <th>启用</th>
                  <th>管理</th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <button
                        className="text-link schedule-name"
                        onClick={() => setHistory(s)}
                      >
                        {s.name}
                      </button>
                      <small className="block mono dim">
                        #{s.id} · {s.template.target_agent_id}
                      </small>
                    </td>
                    <td>{kindName(s.template.kind)}</td>
                    <td className="mono">
                      {s.cron || s.interval_minutes + " min"}
                      <small className="block dim">{s.timezone}</small>
                    </td>
                    <td className="mono dim">
                      {s.enabled
                        ? dateTime(s.next_run_at, s.timezone)
                        : "已暂停"}
                    </td>
                    <td>
                      <button
                        className={`toggle ${s.enabled ? "on" : ""}`}
                        role="switch"
                        aria-checked={s.enabled}
                        aria-label={`${s.enabled ? "暂停" : "启用"}${s.name}`}
                        disabled={mutation.isPending || !canWrite}
                        onClick={() =>
                          mutation.mutate({ id: s.id, enabled: !s.enabled })
                        }
                      >
                        <span />
                      </button>
                    </td>
                    <td>
                      {canWrite && (
                        <button
                          className="icon-button"
                          aria-label={`删除${s.name}`}
                          onClick={() => setRemoveId(s.id)}
                        >
                          <Trash size={17} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {history && (
        <section className="panel">
          <div className="section-heading">
            <div>
              <h2>执行记录 · {history.name}</h2>
              <span className="section-note">最近 100 次</span>
            </div>
            <button
              className="icon-button"
              aria-label="关闭执行记录"
              onClick={() => setHistory(undefined)}
            >
              <X size={18} />
            </button>
          </div>
          {records.error ? (
            <ErrorPanel error={records.error} />
          ) : records.isLoading ? (
            <Skeleton />
          ) : records.data?.items.length ? (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>计划触发时间</th>
                    <th>诊断任务</th>
                    <th>触发状态</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {records.data.items.map((r) => (
                    <tr key={r.id}>
                      <td className="mono">{dateTime(r.scheduled_at)}</td>
                      <td>
                        {r.task_id ? (
                          <Link
                            className="text-link mono"
                            to={`/tasks/${r.task_id}`}
                          >
                            #{r.task_id}
                          </Link>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>{r.status}</td>
                      <td className="dim">{r.error_message || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="quiet-empty">尚未到达首次触发时间。</div>
          )}
        </section>
      )}
      {show && (
        <div
          className="modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget) setShow(false);
          }}
        >
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="schedule-title"
          >
            <div className="modal-heading">
              <div>
                <span className="eyebrow">NEW SCHEDULE</span>
                <h2 id="schedule-title">创建诊断计划</h2>
              </div>
              <button
                className="icon-button"
                aria-label="关闭"
                onClick={() => setShow(false)}
              >
                <X size={20} />
              </button>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                create.mutate();
              }}
            >
              <label className="field">
                <span>计划名称</span>
                <input
                  autoFocus
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={100}
                />
              </label>
              <label className="field">
                <span>采集方式</span>
                <select
                  value={kind}
                  onChange={(e) => {
                    setKind(e.target.value);
                    setOptions(
                      initialOptions(
                        kinds.data?.find((k) => k.id === e.target.value),
                      ),
                    );
                    setAgent("");
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
              <TargetFields
                kind={kinds.data?.find((k) => k.id === kind)}
                agents={agents.data?.items || []}
                value={{ ...targetDetails, agent_id: agent, pid }}
                onChange={(v) => {
                  setAgent(v.agent_id);
                  setPid(v.pid);
                  setTargetDetails(v);
                }}
              />
              <div className="form-grid">
                {!cronMode && (
                  <label className="field">
                    <span>间隔（分钟）</span>
                    <input
                      required
                      type="number"
                      min="1"
                      max="10080"
                      value={interval}
                      onChange={(e) => setInterval(Number(e.target.value))}
                    />
                  </label>
                )}
              </div>
              <label className="field">
                <span>触发方式</span>
                <select
                  value={cronMode ? "cron" : "interval"}
                  onChange={(e) => setCronMode(e.target.value === "cron")}
                >
                  <option value="cron">Cron · 按日历与时区</option>
                  <option value="interval">固定分钟间隔</option>
                </select>
              </label>
              {cronMode && (
                <label className="field">
                  <span>Cron 表达式</span>
                  <input
                    required
                    value={cron}
                    onChange={(e) => setCron(e.target.value)}
                    maxLength={100}
                    placeholder="0 9 * * 1-5"
                  />
                  <small>
                    分钟 小时 日期 月份 星期；例如工作日上午9点：0 9 * * 1-5。
                  </small>
                </label>
              )}
              <label className="field">
                <span>IANA 时区</span>
                <input
                  required
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  list="timezones"
                  maxLength={64}
                />
                <datalist id="timezones">
                  {[
                    "Asia/Shanghai",
                    "Asia/Tokyo",
                    "UTC",
                    "America/New_York",
                    "Europe/London",
                  ].map((z) => (
                    <option key={z} value={z} />
                  ))}
                </datalist>
                <small>
                  采用所选时区的日历与夏令时规则；执行记录使用绝对时间去重。
                </small>
              </label>
              <ParameterFields
                kind={kinds.data?.find((k) => k.id === kind)}
                value={options}
                onChange={setOptions}
              />
              <GroupScope value={groupScope} onChange={setGroupScope} />
              {create.error && <ErrorPanel error={create.error} />}
              <div className="form-footer">
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => setShow(false)}
                >
                  取消
                </button>
                <button className="button primary" disabled={create.isPending}>
                  {create.isPending ? <Busy /> : "创建计划"}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
      {removeId && (
        <div className="modal-backdrop">
          <section
            className="modal small-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-title"
          >
            <h2 id="delete-title">删除诊断计划？</h2>
            <p className="dim">删除后将停止后续触发，已创建的任务仍可查看。</p>
            {remove.error && <ErrorPanel error={remove.error} />}
            <div className="form-footer">
              <button
                className="button secondary"
                onClick={() => setRemoveId(undefined)}
              >
                保留计划
              </button>
              <button
                className="button danger"
                disabled={remove.isPending}
                onClick={() => remove.mutate(removeId)}
              >
                删除计划
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
