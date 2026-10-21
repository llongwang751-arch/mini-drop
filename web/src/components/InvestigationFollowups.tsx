/** 补证分支需要独立审阅；页面只提交决定，后台复用正常采集与再次诊断流程。 */
import "../styles/agent-extensions.css";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, kindName } from "../api";
import { useCanWrite } from "../auth-context";
import { Busy, ErrorPanel } from "./ui";
import type { List } from "../types";

type Followup = {
  id: string;
  status: string;
  parent_task_id: string;
  source_diagnosis_id: string;
  task_id?: string;
  diagnosis_id?: string;
  error_code?: string;
  plan: {
    plan: {
      kind: string;
      duration_seconds: number;
      frequency_hz: number;
      rationale: string;
      options?: Record<string, unknown>;
    };
    evidence_ids: string[];
  };
};

const labels: Record<string, string> = {
  proposed: "待审阅",
  queued: "已确认，等待采集",
  collecting: "补充采集中",
  diagnosing: "新证据诊断中",
  success: "补证完成",
  failed: "补证未通过",
  rejected: "未批准",
  canceled: "已取消",
};

function FollowupCard({
  item,
  investigationId,
}: {
  item: Followup;
  investigationId: string;
}) {
  const canWrite = useCanWrite();
  const qc = useQueryClient();
  const [reviewed, setReviewed] = useState(false);
  const decision = useMutation({
    mutationFn: (confirm: boolean) =>
      api<Followup>(
        `/investigations/${encodeURIComponent(investigationId)}/followups/${encodeURIComponent(item.id)}/decision`,
        {
          method: "POST",
          body: JSON.stringify({ confirm, reviewed: true }),
        },
      ),
    onSuccess: () => {
      setReviewed(false);
      void qc.invalidateQueries({
        queryKey: ["investigation-followups", investigationId],
      });
    },
  });
  const plan = item.plan.plan;
  return (
    <article className="panel knowledge-card">
      <div className="knowledge-card-heading">
        <h4>
          补证 #{item.id} · {kindName(plan.kind)}
        </h4>
        <span className="badge neutral">
          {labels[item.status] || item.status}
        </span>
      </div>
      <p>{plan.rationale}</p>
      <dl>
        <dt>采集预算</dt>
        <dd>
          {plan.duration_seconds} 秒 ·{" "}
          {plan.frequency_hz > 0 ? `${plan.frequency_hz} Hz` : "频率不适用"}
        </dd>
        <dt>计划来源</dt>
        <dd>
          诊断 #{item.source_diagnosis_id} · {item.plan.evidence_ids.join("、")}
        </dd>
      </dl>
      <p className="muted">
        目标继承原调查；新的采集类型不直接与旧单位计算提升或因果。
      </p>
      {plan.options && (
        <details>
          <summary>查看采集参数</summary>
          <pre>{JSON.stringify(plan.options, null, 2)}</pre>
        </details>
      )}
      {item.status === "proposed" && canWrite && (
        <div className="agent-extension-stack">
          <label className="agent-extension-check">
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(e) => setReviewed(e.target.checked)}
            />
            已审阅目标、采集类型和预算，允许后台采集后再次诊断
          </label>
          <div className="agent-extension-actions">
            <button
              className="button"
              disabled={!reviewed || decision.isPending}
              onClick={() => decision.mutate(true)}
            >
              确认补采集
            </button>
            <button
              className="button secondary"
              disabled={!reviewed || decision.isPending}
              onClick={() => decision.mutate(false)}
            >
              不执行此计划
            </button>
          </div>
        </div>
      )}
      {decision.isPending && <Busy>正在保存补证决定</Busy>}
      {decision.error && <ErrorPanel error={decision.error} />}
      {decision.isSuccess && <p role="status">补证决定已保存。</p>}
      {item.task_id && (
        <Link to={`/tasks/${encodeURIComponent(item.task_id)}`}>
          查看补证任务 #{item.task_id}
          {item.diagnosis_id ? ` · 诊断 #${item.diagnosis_id}` : ""}
        </Link>
      )}
      {item.error_code && <p role="alert">未通过：{item.error_code}</p>}
    </article>
  );
}

function InvestigationFollowupList({
  investigationId,
}: {
  investigationId: string;
}) {
  const canWrite = useCanWrite();
  const qc = useQueryClient();
  const [stopOpen, setStopOpen] = useState(false);
  const [stopReviewed, setStopReviewed] = useState(false);
  const stop = useMutation({
    mutationFn: () =>
      api<{ canceled: boolean }>(
        `/investigations/${encodeURIComponent(investigationId)}/cancel`,
        { method: "POST", body: "{}" },
      ),
    onSuccess: () => {
      setStopOpen(false);
      setStopReviewed(false);
      void qc.invalidateQueries({
        queryKey: ["investigation-followups", investigationId],
      });
      void qc.invalidateQueries({ queryKey: ["investigations"] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
  const query = useQuery({
    queryKey: ["investigation-followups", investigationId],
    queryFn: ({ signal }) =>
      api<List<Followup>>(
        `/investigations/${encodeURIComponent(investigationId)}/followups`,
        { signal },
      ),
    refetchInterval: 2500,
  });
  return (
    <section aria-label="动态补证分支" className="agent-extension-stack">
      <h3>动态补证</h3>
      <p className="muted">
        Agent
        根据已取得的证据提出计划。每次调查最多两条补证分支，确认后执行采集和新的诊断。
      </p>
      {query.isPending && <Busy>读取补证计划</Busy>}
      {query.error && <ErrorPanel error={query.error} />}
      {query.data?.items.length === 0 && <p>当前没有追加采集计划。</p>}
      {query.data?.items.map((item) => (
        <FollowupCard
          key={item.id}
          item={item}
          investigationId={investigationId}
        />
      ))}
      {canWrite &&
        query.data?.items.some((item) =>
          ["queued", "collecting", "diagnosing"].includes(item.status),
        ) &&
        !stop.isSuccess && (
          <div className="panel knowledge-card agent-extension-stack">
            <button
              className="button secondary"
              type="button"
              aria-expanded={stopOpen}
              onClick={() => {
                setStopReviewed(false);
                setStopOpen(!stopOpen);
              }}
            >
              停止后续补证
            </button>
            {stopOpen && (
              <div
                role="group"
                aria-label="确认停止后续补证"
                className="agent-extension-stack"
              >
                <p>
                  将停止本次调查的后续补证及尚未完成的关联工作，已完成的观测记录保留。
                </p>
                <label className="agent-extension-check">
                  <input
                    type="checkbox"
                    checked={stopReviewed}
                    onChange={(event) => setStopReviewed(event.target.checked)}
                  />
                  已确认停止后续执行，并保留已有证据
                </label>
                <button
                  className="button secondary"
                  type="button"
                  disabled={!stopReviewed || stop.isPending}
                  onClick={() => stop.mutate()}
                >
                  {stop.isPending ? "正在停止…" : "确认停止后续补证"}
                </button>
              </div>
            )}
          </div>
        )}
      {stop.error && <ErrorPanel error={stop.error} />}
      {stop.isSuccess && (
        <p role="status">停止请求已保存，后台将停止尚未完成的关联工作。</p>
      )}
    </section>
  );
}

// 切换调查时重建审批状态，上一条记录的勾选或成功结果不能套用到新目标。
export default function InvestigationFollowups({
  investigationId,
}: {
  investigationId: string;
}) {
  return (
    <InvestigationFollowupList
      key={investigationId}
      investigationId={investigationId}
    />
  );
}
