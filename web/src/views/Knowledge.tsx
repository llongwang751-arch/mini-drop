/**
 * 展示版本化诊断 Skill 与当前用户保存的案例；知识是排查先验，不会替代本轮工具证据。
 * 撤销影响后续复用，既有诊断引用仍可追溯；真实案例可见性与来源有效性由后端逐条校验。
 */
import { useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Info } from "@phosphor-icons/react";
import { api, dateTime, kindName } from "../api";
import { caseOutcomeLabel, correctionLabel } from "../diagnostic-knowledge";
import type { DiagnosticCase, DiagnosticSkill } from "../diagnostic-knowledge";
import { useCanWrite } from "../auth-context";
import {
  Busy,
  Empty,
  ErrorPanel,
  PageHeading,
  Skeleton,
} from "../components/ui";
import "../styles/knowledge.css";

function SkillCard({ skill }: { skill: DiagnosticSkill }) {
  return (
    <article className="panel knowledge-card" id={`skill-${skill.id}`}>
      <div className="knowledge-card-heading">
        <h3>{skill.title}</h3>
        <span className="badge active">v{skill.version}</span>
      </div>
      <div className="knowledge-tags">
        {skill.task_kinds.map((kind) => (
          <span className="knowledge-tag" key={kind}>
            {kindName(kind)}
          </span>
        ))}
      </div>
      <p>按步骤检查信号、寻找反证，满足停止条件时保留不确定性。</p>
      <details>
        <summary>查看排查步骤与验收标准</summary>
        <h4>排查步骤</h4>
        <ol>
          {skill.steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
        <h4>需要寻找的反证</h4>
        <ul>
          {skill.counter_evidence.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
        <h4>停止条件</h4>
        <ul>
          {skill.stop_conditions.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
        <h4>验收与验证</h4>
        <ul>
          {skill.verification.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
        <h4>所需工具</h4>
        <p className="mono">{skill.required_tools.join(" · ")}</p>
      </details>
    </article>
  );
}

function CaseCard({
  item,
  canWrite,
}: {
  item: DiagnosticCase;
  canWrite: boolean;
}) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const revoked = item.status === "revoked" || !!item.revoked_at;
  const revoke = useMutation({
    mutationFn: () =>
      api<DiagnosticCase>(
        `/diagnostic-cases/${encodeURIComponent(item.id)}/revoke`,
        { method: "POST", body: "{}" },
      ),
    onSuccess: () => {
      setConfirm(false);
      void qc.invalidateQueries({ queryKey: ["diagnostic-cases"] });
    },
  });
  return (
    <article className="panel knowledge-card" id={`case-${item.id}`}>
      <div className="knowledge-card-heading">
        <h3>案例 #{item.id}</h3>
        <span className={`badge ${revoked ? "neutral" : "active"}`}>
          {revoked ? "已撤销" : "参与后续匹配"}
        </span>
      </div>
      <p>
        {caseOutcomeLabel(item.outcome)} · {kindName(item.task_kind)}
      </p>
      <dl>
        {item.correction_code && (
          <>
            <dt>复核类别</dt>
            <dd>{correctionLabel(item.correction_code)}</dd>
          </>
        )}
        <dt>保存时间</dt>
        <dd>{dateTime(item.created_at)}</dd>
        <dt>有效期限</dt>
        <dd>{dateTime(item.expires_at)}</dd>
        {item.project_id && (
          <>
            <dt>绑定项目</dt>
            <dd>{item.project_id}</dd>
          </>
        )}
        {item.source_revision && (
          <>
            <dt>源码版本</dt>
            <dd className="mono">{item.source_revision.slice(0, 16)}</dd>
          </>
        )}
        {item.revoked_at && (
          <>
            <dt>撤销时间</dt>
            <dd>{dateTime(item.revoked_at)}</dd>
          </>
        )}
      </dl>
      <div className="knowledge-card-footer">
        <div className="knowledge-source-links">
          <Link to={`/tasks/${encodeURIComponent(item.task_id)}`}>
            来源任务 #{item.task_id}
          </Link>
          {item.code_repair_id && (
            <Link
              to={`/investigations?repair=${encodeURIComponent(item.code_repair_id)}`}
            >
              修复记录 #{item.code_repair_id}
            </Link>
          )}
        </div>
        {canWrite && !revoked && !revoke.isSuccess && !confirm && (
          <button
            className="button secondary"
            type="button"
            onClick={() => setConfirm(true)}
          >
            撤销案例
          </button>
        )}
        {confirm && (
          <div className="knowledge-revoke">
            <p>
              确认撤销案例 #{item.id}
              ？后续诊断将不再复用它，已有引用保留用于追溯。
            </p>
            <div>
              <button
                className="button secondary"
                type="button"
                disabled={revoke.isPending}
                onClick={() => revoke.mutate()}
              >
                {revoke.isPending ? <Busy /> : "确认撤销"}
              </button>
              <button
                className="button ghost"
                type="button"
                disabled={revoke.isPending}
                onClick={() => setConfirm(false)}
              >
                保留案例
              </button>
            </div>
          </div>
        )}
      </div>
      {revoke.error && <ErrorPanel error={revoke.error} />}
      {revoke.isSuccess && <p role="status">案例已撤销，不再用于后续匹配。</p>}
    </article>
  );
}

export default function Knowledge() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "cases" ? "cases" : "skills";
  const canWrite = useCanWrite();
  const skills = useQuery({
    queryKey: ["diagnostic-skills"],
    queryFn: ({ signal }) =>
      api<{ items: DiagnosticSkill[] }>("/diagnostic-skills", { signal }),
  });
  const cases = useQuery({
    queryKey: ["diagnostic-cases"],
    queryFn: ({ signal }) =>
      api<{ items: DiagnosticCase[] }>("/diagnostic-cases", { signal }),
    enabled: tab === "cases",
  });
  const activeQuery = tab === "skills" ? skills : cases;
  // 分包和查询完成后再定位锚点，避免目标尚未渲染时丢失定位。
  useEffect(() => {
    if (!location.hash) return;
    try {
      document
        .getElementById(decodeURIComponent(location.hash.slice(1)))
        ?.scrollIntoView();
    } catch {
      /* 非法 URL 编码不影响读取。 */
    }
  }, [location.hash, skills.dataUpdatedAt, cases.dataUpdatedAt]);
  const select = (value: string) =>
    setParams(value === "cases" ? { tab: "cases" } : {});
  return (
    <div className="knowledge-page">
      <PageHeading
        eyebrow="DIAGNOSTIC / KNOWLEDGE"
        title="让排查方法留下来"
        description="查看版本化 Skill，管理经过复核的历史案例。每次判断仍以本轮证据为准。"
      />
      <nav className="knowledge-tabs" role="tablist" aria-label="诊断知识分类">
        {[
          { value: "skills", label: "诊断 Skill" },
          { value: "cases", label: "我的复核案例" },
        ].map((item, index) => (
          <button
            key={item.value}
            id={`knowledge-tab-${item.value}`}
            role="tab"
            type="button"
            aria-selected={tab === item.value}
            aria-controls={`knowledge-panel-${item.value}`}
            tabIndex={tab === item.value ? 0 : -1}
            onClick={() => select(item.value)}
            onKeyDown={(event) => {
              const next =
                event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? 1
                    : ["ArrowLeft", "ArrowRight"].includes(event.key)
                      ? 1 - index
                      : undefined;
              if (next !== undefined) {
                event.preventDefault();
                select(next ? "cases" : "skills");
                event.currentTarget.parentElement
                  ?.querySelectorAll<HTMLButtonElement>("button")
                  [next]?.focus();
              }
            }}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="knowledge-boundary">
        <Info size={20} aria-hidden="true" />
        <p>
          Skill
          描述排查方法，案例提供已复核的先验。它们不能充当本次采集证据，也不会放宽工具权限或自动证明根因。案例只在当前权限与来源仍有效时参与匹配。
        </p>
      </div>
      <section
        role="tabpanel"
        id={`knowledge-panel-${tab}`}
        aria-labelledby={`knowledge-tab-${tab}`}
        tabIndex={0}
      >
        <div className="knowledge-section-heading">
          <div>
            <h2>
              {tab === "skills" ? "版本化排查方法" : "由我确认保存的案例"}
            </h2>
            <p>
              {tab === "skills"
                ? "每份 Skill 明确步骤、反证、停止条件与验收要求。"
                : "在诊断报告中保存固定纠正类别，或从已通过的关联修复中保存案例。撤销后停止后续复用。"}
            </p>
          </div>
          <span className="mono">
            {activeQuery.data?.items.length ?? "—"} 项
          </span>
        </div>
        {activeQuery.isPending ? (
          <div role="status" aria-label="正在加载诊断知识">
            <Skeleton />
          </div>
        ) : activeQuery.error ? (
          <ErrorPanel
            error={activeQuery.error}
            retry={() => {
              void activeQuery.refetch();
            }}
          />
        ) : activeQuery.data?.items.length ? (
          <div className="knowledge-grid">
            {tab === "skills"
              ? skills.data?.items.map((skill) => (
                  <SkillCard
                    skill={skill}
                    key={`${skill.id}:${skill.version}`}
                  />
                ))
              : cases.data?.items.map((item) => (
                  <CaseCard item={item} canWrite={canWrite} key={item.id} />
                ))}
          </div>
        ) : (
          <Empty
            title={tab === "skills" ? "暂无可用诊断 Skill" : "还没有复核案例"}
            description={
              tab === "skills"
                ? "服务端尚未返回可用方法，可稍后重试。"
                : "完成本次证据复核后，在诊断报告或通过的关联修复中主动保存。系统不会自动将未复核报告加入经验。"
            }
            icon={<BookOpen size={28} />}
            action={
              <Link className="button secondary" to="/tasks">
                查看诊断任务
              </Link>
            }
          />
        )}
      </section>
    </div>
  );
}
