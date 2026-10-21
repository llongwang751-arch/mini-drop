/** 方法库将编辑与发布分开：草稿不参与诊断，发布/退役必须管理员明确复核。 */
import "../styles/agent-extensions.css";
import { useContext, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, dateTime } from "../api";
import { UserContext, useCanWrite } from "../auth-context";
import { Busy, ErrorPanel } from "./ui";
import type { DiagnosticSkill } from "../diagnostic-knowledge";
import type { List } from "../types";

type Playbook = {
  id: string;
  logical_key: string;
  version: number;
  status: "draft" | "published" | "retired";
  skill: DiagnosticSkill;
  group_id?: string;
  project_id?: string;
  content_digest: string;
  reviewer_id?: string;
  reviewed_at?: string;
  expires_at: string;
};

function PlaybookCard({ item, admin }: { item: Playbook; admin: boolean }) {
  const qc = useQueryClient();
  const [reviewed, setReviewed] = useState(false);
  const review = useMutation({
    mutationFn: (action: "publish" | "retire") =>
      api<Playbook>(
        `/diagnostic-playbooks/${encodeURIComponent(item.id)}/review`,
        {
          method: "POST",
          body: JSON.stringify({ action, review_confirmed: true }),
        },
      ),
    onSuccess: () => {
      setReviewed(false);
      void qc.invalidateQueries({ queryKey: ["diagnostic-playbooks"] });
    },
  });
  return (
    <article className="panel knowledge-card">
      <div className="knowledge-card-heading">
        <h3>{item.skill.title}</h3>
        <span className="badge neutral">
          v{item.version} ·{" "}
          {
            { draft: "草稿", published: "已发布", retired: "已退役" }[
              item.status
            ]
          }
        </span>
      </div>
      <p>
        {item.group_id ? `团队 ${item.group_id}` : "个人方法"}
        {item.project_id ? ` · 项目 ${item.project_id}` : ""}
      </p>
      <details>
        <summary>审阅步骤与适用边界</summary>
        <p>
          适用采集：{item.skill.task_kinds.join("、")}；必要工具：
          {item.skill.required_tools.join("、")}
        </p>
        {(
          [
            ["排查步骤", item.skill.steps],
            ["反证", item.skill.counter_evidence],
            ["停止条件", item.skill.stop_conditions],
            ["验收要求", item.skill.verification],
          ] as const
        ).map(([title, values]) => (
          <div key={title}>
            <h4>{title}</h4>
            <ul>
              {values.map((text, i) => (
                <li key={i}>{text}</li>
              ))}
            </ul>
          </div>
        ))}
        <p>有效至 {dateTime(item.expires_at)}</p>
        <p className="mono">内容 SHA：{item.content_digest}</p>
        {item.reviewed_at && (
          <p>
            复核发布：{dateTime(item.reviewed_at)} · {item.reviewer_id}
          </p>
        )}
      </details>
      {admin && item.status !== "retired" && (
        <div className="agent-extension-stack">
          <label className="agent-extension-check">
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(e) => setReviewed(e.target.checked)}
            />
            已复核内容、作用域及工具权限；方法不能代替当前证据
          </label>
          <button
            className={`button ${item.status === "published" ? "secondary" : ""}`}
            disabled={!reviewed || review.isPending}
            onClick={() =>
              review.mutate(item.status === "draft" ? "publish" : "retire")
            }
          >
            {item.status === "draft" ? "发布此版本" : "退役此版本"}
          </button>
        </div>
      )}
      {review.error && <ErrorPanel error={review.error} />}
      {review.isPending && <Busy>保存复核决定</Busy>}
      {review.isSuccess && <p role="status">方法状态已更新。</p>}
    </article>
  );
}

export default function DiagnosticPlaybooks() {
  const user = useContext(UserContext);
  const canWrite = useCanWrite();
  const admin = !!user && ["admin", "platform_admin"].includes(user.role);
  const qc = useQueryClient();
  const books = useQuery({
    queryKey: ["diagnostic-playbooks"],
    queryFn: ({ signal }) =>
      api<List<Playbook>>("/diagnostic-playbooks", { signal }),
  });
  const groups = useQuery({
    queryKey: ["diagnostic-playbook-groups"],
    queryFn: ({ signal }) =>
      api<List<{ id: string; name: string }>>("/groups", { signal }),
    enabled: canWrite,
  });
  const [formOpen, setFormOpen] = useState(false);
  const [key, setKey] = useState("");
  const [version, setVersion] = useState(1);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("PYTHON_CPU");
  const [groupId, setGroupId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [steps, setSteps] = useState(
    "核对采样单位、窗口和负载\n检查 self/total 热点及调用路径",
  );
  const [counter, setCounter] = useState(
    "高占比可能来自正常业务负载，不能直接判断回退",
  );
  const [stop, setStop] = useState(
    "没有可比对照或业务指标时，不确认根因与收益",
  );
  const [verification, setVerification] = useState(
    "相同负载补采集，同时验证功能语义和业务指标",
  );
  const lines = (value: string) =>
    value
      .split("\n")
      .map((text) => text.trim())
      .filter(Boolean);
  const create = useMutation({
    mutationFn: () =>
      api<Playbook>("/diagnostic-playbooks", {
        method: "POST",
        body: JSON.stringify({
          logical_key: key,
          version,
          ...(groupId ? { group_id: groupId } : {}),
          ...(projectId ? { project_id: projectId } : {}),
          expires_days: 90,
          skill: {
            title,
            task_kinds: [kind],
            required_tools: [
              "inspect_hotspots",
              "check_collection_quality",
              "inspect_call_paths",
            ],
            steps: lines(steps),
            counter_evidence: lines(counter),
            stop_conditions: lines(stop),
            verification: lines(verification),
          },
        }),
      }),
    onSuccess: () => {
      setFormOpen(false);
      void qc.invalidateQueries({ queryKey: ["diagnostic-playbooks"] });
    },
  });
  return (
    <section aria-label="项目与团队方法库" className="agent-extension-stack">
      <h2>项目与团队方法库</h2>
      <p>
        使用版本化方法保存项目经验。新版本明确替代旧版，最多加载两份匹配的方法；过期、退役或失去权限后停止复用。
      </p>
      {canWrite && (
        <button
          className="button secondary"
          aria-expanded={formOpen}
          onClick={() => setFormOpen(!formOpen)}
        >
          {formOpen ? "收起草稿表单" : "新建方法草稿"}
        </button>
      )}
      {formOpen && (
        <form
          className="panel agent-extension-stack agent-extension-form"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <label>
            方法标识
            <input
              required
              pattern="[a-zA-Z][a-zA-Z0-9_.-]{0,79}"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="例如 checkout-cpu"
            />
          </label>
          <label>
            版本
            <input
              type="number"
              min={1}
              max={10000}
              required
              value={version}
              onChange={(e) => setVersion(Number(e.target.value))}
            />
          </label>
          <label>
            标题
            <input
              required
              maxLength={60}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label>
            适用采集类型
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {[
                "PYTHON_CPU",
                "GO_CPU",
                "JAVA_CPU",
                "PERF_CPU",
                "PYTHON_MEMORY",
                "GO_HEAP",
                "JAVA_HEAP",
                "IO_TRACE",
                "IMPORT_COLLAPSED",
              ].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            共享范围
            <select
              value={groupId}
              onChange={(e) => setGroupId(e.target.value)}
            >
              <option value="">仅本人</option>
              {groups.data?.items.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
          </label>
          {groups.error && <ErrorPanel error={groups.error} />}
          {admin && (
            <label>
              绑定项目（可选）
              <input
                maxLength={80}
                pattern="[a-zA-Z][a-zA-Z0-9_.-]{0,79}"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                placeholder="填写已登记的源码项目标识"
              />
            </label>
          )}
          {(
            [
              ["排查步骤", steps, setSteps],
              ["需要寻找的反证", counter, setCounter],
              ["停止条件", stop, setStop],
              ["验收要求", verification, setVerification],
            ] as const
          ).map(([label, value, change]) => (
            <label key={label}>
              {label}
              <textarea
                required
                rows={3}
                maxLength={1200}
                value={value}
                onChange={(e) => change(e.target.value)}
              />
              <small>每行一项，最多六项；内容仅作方法指导。</small>
            </label>
          ))}
          <p className="muted">
            草稿不会立即进入
            Agent；管理员复核发布后才参与诊断。发布版本不可原地修改。
          </p>
          {create.error && <ErrorPanel error={create.error} />}
          <button className="button" disabled={create.isPending}>
            {create.isPending ? "保存中…" : "保存草稿"}
          </button>
        </form>
      )}
      {books.isPending && <Busy>读取方法库</Busy>}
      {books.error && <ErrorPanel error={books.error} />}
      {create.isSuccess && (
        <p role="status">草稿已保存，等待管理员复核发布。</p>
      )}
      {books.data?.items.length === 0 && (
        <p>尚无方法。内置四类诊断 Skill 仍正常提供。</p>
      )}
      <div className="knowledge-grid">
        {books.data?.items.map((item) => (
          <PlaybookCard key={item.id} item={item} admin={admin} />
        ))}
      </div>
    </section>
  );
}
