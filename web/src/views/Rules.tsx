import { useContext, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { UserContext } from "../auth-context";
import type { List } from "../types";
import { PageHeading, ErrorPanel, Skeleton } from "../components/ui";
const ruleTitles: Record<string, string> = {
  allocation: "内存分配热点",
  lock: "同步与锁开销",
  gc: "垃圾回收热点",
};
interface Rule {
  key: string;
  enabled: boolean;
  severity: string;
  min_samples: number;
  threshold: number;
  version: number;
  config: Record<string, unknown>;
}
function RuleEditor({ rule, editable }: { rule: Rule; editable: boolean }) {
  const qc = useQueryClient(),
    [enabled, setEnabled] = useState(rule.enabled),
    [severity, setSeverity] = useState(rule.severity),
    [minimum, setMinimum] = useState(rule.min_samples),
    [threshold, setThreshold] = useState(rule.threshold);
  const write = useMutation({
    mutationFn: () =>
      api("/rules/" + encodeURIComponent(rule.key), {
        method: "PATCH",
        body: JSON.stringify({
          enabled,
          severity,
          min_samples: minimum,
          threshold,
        }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rules"] }),
  });
  return (
    <form
      className="panel form-section rule-editor"
      onSubmit={(e) => {
        e.preventDefault();
        write.mutate();
      }}
    >
      <div className="section-heading">
        <div>
          <h2>{ruleTitles[rule.key] || rule.key}</h2>
          <span className="section-note">
            版本 {rule.version} · 修改仅影响后续诊断
          </span>
        </div>
        <label className="consent-line">
          <input
            disabled={!editable}
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          启用
        </label>
      </div>
      <div className="form-grid">
        <label className="field">
          <span>严重度</span>
          <select
            disabled={!editable}
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
          >
            <option value="info">提示</option>
            <option value="warning">需关注</option>
            <option value="critical">严重</option>
          </select>
        </label>
        <label className="field">
          <span>最低样本数</span>
          <input
            disabled={!editable}
            type="number"
            min="1"
            max="10000000"
            required
            value={minimum}
            onChange={(e) => setMinimum(Number(e.target.value))}
          />
        </label>
        <label className="field">
          <span>触发阈值</span>
          <input
            disabled={!editable}
            type="number"
            min="0"
            max="100"
            step="0.1"
            required
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
          <small>比例取值 0～100%；建议附带匹配信号与置信度。</small>
        </label>
      </div>
      {write.error && <ErrorPanel error={write.error} />}
      {write.isSuccess && <p role="status">规则已保存。</p>}
      {editable && (
        <div className="form-footer">
          <button className="button secondary" disabled={write.isPending}>
            保存规则
          </button>
        </div>
      )}
    </form>
  );
}
export default function Rules() {
  const user = useContext(UserContext),
    editable = ["admin", "platform_admin"].includes(user?.role || "");
  const q = useQuery({
    queryKey: ["rules"],
    queryFn: ({ signal }) => api<List<Rule>>("/rules", { signal }),
  });
  return (
    <>
      <PageHeading
        eyebrow="DIAGNOSTIC / RULES"
        title="可追溯的诊断规则"
        description="按明确的采样信号生成建议，记录阈值、版本与证据。"
      />
      {q.error ? (
        <ErrorPanel error={q.error} />
      ) : q.isLoading ? (
        <Skeleton />
      ) : (
        <div className="rule-editors">
          {q.data?.items?.map((r) => (
            <RuleEditor
              key={r.key + ":" + r.version}
              rule={r}
              editable={editable}
            />
          ))}
        </div>
      )}
    </>
  );
}
