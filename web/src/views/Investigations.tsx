/**
 * 从自然语言到证据闭环的工作台：生成采集计划，经确认后由服务端推进采集、诊断和复测。
 * 源码模式只面向已登记项目，页面展示隔离副本的定位、最小补丁和验证结果，不直接编辑业务仓库。
 */
import { useContext, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, newIdempotencyKey, dateTime } from "../api";
import { UserContext, useCanWrite } from "../auth-context";
import { Busy, ErrorPanel } from "../components/ui";
import type { List } from "../types";
import "../styles/investigations.css";

type Step = { evidence_id: string; tool: string; observation: unknown };
type Plan = {
  kind: string;
  duration_seconds: number;
  frequency_hz: number;
  rationale: string;
  options?: Record<string, unknown>;
};
type Investigation = {
  id: string;
  goal: string;
  agent_id: string;
  parameters?: {
    scope?: string;
    pid?: number;
    target?: { type?: string; pid?: number; container_id?: string };
  };
  status: string;
  plan?: Plan;
  trace: Step[];
  baseline_task_id?: string;
  diagnosis_id?: string;
  verification_id?: string;
  code_repair_id?: string;
  code_project_id?: string;
  error_code?: string;
  created_at: string;
  report?: {
    comparison?: Comparison;
    candidate_report?: { summary: string };
    candidate_diagnosis_id?: string;
    diagnosis?: { summary: string };
    verification_status?: string;
  };
};
type Comparison = {
  comparable: boolean;
  kind: string;
  unit: string;
  baseline_weight: number;
  candidate_weight: number;
  outcome: string;
  root_cause_status: string;
  limitations: string[];
  differences: {
    symbol: string;
    baseline_self_percent: number;
    candidate_self_percent: number;
    delta_percentage_points: number;
  }[];
};
type Repair = {
  id: string;
  goal: string;
  project_id: string;
  source_revision: string;
  status: string;
  trace: Step[];
  error_code?: string;
  created_at: string;
  report?: {
    summary: string;
    locations: { path: string; line: number; reason: string }[];
    evidence_ids: string[];
    validation: {
      accepted: boolean;
      functional_regression: string;
      output_parity_cases: number;
      median_cpu_speedup: number;
      improved_pairs: number;
      root_cause_status: string;
      scope: string;
      patch: { path: string; sha256: string; old: string; new: string }[];
      pairs: { baseline_cpu_ns: number; candidate_cpu_ns: number }[];
    };
  };
};
type Project = {
  id: string;
  label: string;
  source_revision: string;
  editable_files: string[];
  runtime?: {
    agent_id: string;
    pid: number;
    start_time_ticks: number;
    uid: number;
    source_revision: string;
  };
};
type Agent = {
  id: string;
  hostname: string;
  status: string;
  resources?: {
    processes?: {
      pid: number;
      name: string;
      uid?: number;
      start_time_ticks?: number;
    }[];
  };
};
const finalStates = new Set(["success", "failed", "canceled"]);
const statusNames: Record<string, string> = {
  planning: "规划采集",
  awaiting_confirmation: "等待确认计划",
  queued: "等待采集",
  collecting: "采集中",
  diagnosing: "分析原采集",
  verifying: "复测与再次诊断",
  pending: "等待执行",
  running: "定位与验证补丁",
  success: "已完成",
  failed: "未通过",
  canceled: "已取消",
};
const toolNames: Record<string, string> = {
  list_collectors: "核对实际采集能力",
  propose_collection: "校验采集计划",
  profile_business_flow: "测量登记项目业务函数",
  read_source: "读取绑定版本源码",
  test_patch: "应用补丁并回归/对照",
};
// 工具观测按 evidence_id 保留，展开时以 React 文本节点显示 JSON，不把模型内容当 HTML 执行。
function Evidence({ steps }: { steps: Step[] }) {
  return (
    <ol className="investigation-evidence">
      {steps.map((step) => (
        <li key={step.evidence_id}>
          <details>
            <summary>
              {step.evidence_id} · {toolNames[step.tool] || step.tool}
            </summary>
            <pre>{JSON.stringify(step.observation, null, 2)}</pre>
          </details>
        </li>
      ))}
    </ol>
  );
}
// 对比结果要同时展示 comparable 和 limitations；复现同一热点不自动等于已经验证因果。
function ComparisonView({ value }: { value: Comparison }) {
  return (
    <section aria-label="自动复测对比">
      <h3>复测后自动分析</h3>
      <p>
        {value.comparable
          ? "目标身份与测量单位已核对"
          : "两次证据不满足可比条件"}{" "}
        · {value.kind} · {value.unit}
      </p>
      <p>
        原采集权重 {value.baseline_weight} / 复测权重 {value.candidate_weight}
        。这是采样权重，不能直接比较为业务耗时。
      </p>
      <div
        className="table-scroll"
        role="region"
        aria-label="热点百分比对比表，可横向滚动"
        tabIndex={0}
      >
        <table>
          <thead>
            <tr>
              <th>热点化名</th>
              <th>原self%</th>
              <th>复测self%</th>
              <th>差值（百分点）</th>
            </tr>
          </thead>
          <tbody>
            {value.differences.map((row) => (
              <tr key={row.symbol}>
                <td>{row.symbol}</td>
                <td>{row.baseline_self_percent.toFixed(2)}</td>
                <td>{row.candidate_self_percent.toFixed(2)}</td>
                <td>{row.delta_percentage_points.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>因果验证：本轮没有代码或配置干预，重复采样用于复现和补证。</p>
      <details>
        <summary>证据边界</summary>
        <ul>
          {value.limitations.map((text, i) => (
            <li key={i}>{text}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}

export default function Investigations() {
  const canWrite = useCanWrite(),
    user = useContext(UserContext),
    admin = !!user && ["admin", "platform_admin"].includes(user.role),
    qc = useQueryClient();
  const [mode, setMode] = useState<"performance" | "code">("performance"),
    [goal, setGoal] = useState(
      "当前进程的CPU开销集中在哪里？选择合适采集器定位热点，并自动复测核对。",
    ),
    [agentID, setAgentID] = useState(""),
    [pid, setPID] = useState(""),
    [scope, setScope] = useState("process"),
    [containerID, setContainerID] = useState(""),
    [consent, setConsent] = useState(false),
    [linkedCode, setLinkedCode] = useState(false),
    [selected, setSelected] = useState(""),
    [projectID, setProjectID] = useState("crm-python"),
    [codeGoal, setCodeGoal] = useState(
      "分析所选登记业务流程的CPU热点，定位真实源码行，保持输出和原测试语义，生成最小优化补丁并运行功能回归和交替性能对照。",
    ),
    [codeConsent, setCodeConsent] = useState(false),
    [selectedRepair, setSelectedRepair] = useState("");
  const requestKey = useRef<{ fingerprint: string; key: string } | undefined>(
      undefined,
    ),
    repairKey = useRef<{ fingerprint: string; key: string } | undefined>(
      undefined,
    );
  const agents = useQuery({
    queryKey: ["agents-investigation"],
    queryFn: ({ signal }) =>
      api<List<Agent>>("/agents?page_size=100", { signal }),
  });
  const projects = useQuery({
    queryKey: ["code-projects"],
    queryFn: ({ signal }) =>
      api<{ projects: Project[] }>("/code-projects", { signal }),
    enabled: admin,
    refetchInterval: 15000,
  });
  const history = useQuery({
    queryKey: ["investigations"],
    queryFn: ({ signal }) =>
      api<List<Investigation>>("/investigations", { signal }),
    refetchInterval: (q) =>
      q.state.data?.items.some((r) => !finalStates.has(r.status))
        ? 2000
        : false,
  });
  // 列表只做详情首屏种子；后续按所选 ID 单独查询并核对返回身份，避免旧列表缓存串到别的调查。
  const selectedInvestigationDetail = useQuery({
    queryKey: ["investigations", selected],
    queryFn: async ({ signal }) => {
      const detail = await api<Investigation>(
        `/investigations/${encodeURIComponent(selected)}`,
        { signal },
      );
      if (detail.id !== selected) throw new Error("所选调查记录的标识不匹配");
      return detail;
    },
    enabled: mode === "performance" && !!selected,
    initialData: () => history.data?.items.find((item) => item.id === selected),
    refetchInterval: (q) =>
      q.state.data && !finalStates.has(q.state.data.status) ? 2000 : false,
  });
  const repairs = useQuery({
    queryKey: ["code-repairs"],
    queryFn: ({ signal }) => api<List<Repair>>("/code-repairs", { signal }),
    enabled: admin,
    refetchInterval: (q) =>
      q.state.data?.items.some((r) => !finalStates.has(r.status))
        ? 2000
        : false,
  });
  // 源码修复也使用身份隔离的详情查询；关联调查切换到 Saber/CRM 时不能沿用另一条列表记录。
  const selectedRepairDetail = useQuery({
    queryKey: ["code-repairs", selectedRepair],
    queryFn: async ({ signal }) => {
      const detail = await api<Repair>(
        `/code-repairs/${encodeURIComponent(selectedRepair)}`,
        { signal },
      );
      if (detail.id !== selectedRepair)
        throw new Error("所选源码记录的标识不匹配");
      return detail;
    },
    enabled: admin && mode === "code" && !!selectedRepair,
    initialData: () =>
      repairs.data?.items.find((item) => item.id === selectedRepair),
    refetchInterval: (q) =>
      q.state.data && !finalStates.has(q.state.data.status) ? 2000 : false,
  });
  const run = selected
      ? selectedInvestigationDetail.data?.id === selected
        ? selectedInvestigationDetail.data
        : history.data?.items.find((r) => r.id === selected)
      : history.data?.items[0],
    repair = selectedRepair
      ? selectedRepairDetail.data?.id === selectedRepair
        ? selectedRepairDetail.data
        : repairs.data?.items.find((r) => r.id === selectedRepair)
      : repairs.data?.items[0];
  const targetAgent = agents.data?.items.find((a) => a.id === agentID),
    processes = targetAgent?.resources?.processes || [];
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["investigations"] });
    void qc.invalidateQueries({ queryKey: ["code-repairs"] });
    void qc.invalidateQueries({ queryKey: ["tasks"] });
  };
  // 相同目标与问题重试复用幂等键；表单内容改变后才分配新键，防止丢回包后重复创建。
  const start = useMutation({
    mutationFn: () => {
      const parameters =
        scope === "host"
          ? { scope, target: { type: "host" } }
          : scope === "container"
            ? {
                scope,
                target: {
                  type: "container",
                  runtime: "docker",
                  container_id: containerID,
                },
              }
            : { scope, pid: Number(pid) };
      const body = {
        goal,
        target_agent_id: agentID,
        parameters,
        consent,
        ...(linkedCode
          ? { code_project_id: projectID, code_consent: true }
          : {}),
      };
      const fingerprint = JSON.stringify(body);
      if (requestKey.current?.fingerprint !== fingerprint)
        requestKey.current = { fingerprint, key: newIdempotencyKey() };
      return api<Investigation>("/investigations", {
        method: "POST",
        body: fingerprint,
        idempotencyKey: requestKey.current.key,
      });
    },
    onSuccess: (created) => {
      setSelected(created.id);
      qc.setQueryData(["investigations", created.id], created);
      requestKey.current = undefined;
      invalidate();
    },
  });
  // 计划生成与采集执行分开；只有用户确认后，后端才推进后续采集、诊断与一次复测。
  const confirm = useMutation({
    mutationFn: () =>
      api(`/investigations/${run!.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      }),
    onSuccess: invalidate,
  });
  const cancel = useMutation({
    mutationFn: () =>
      api(`/investigations/${run!.id}/cancel`, { method: "POST", body: "{}" }),
    onSuccess: invalidate,
  });
  // 修复请求只引用登记项目与同意项；源码读取、白名单补丁及隔离测试均由后端受限工具完成。
  const startRepair = useMutation({
    mutationFn: () => {
      const body = {
        project_id: projectID,
        goal: codeGoal,
        consent: codeConsent,
      };
      const fingerprint = JSON.stringify(body);
      if (repairKey.current?.fingerprint !== fingerprint)
        repairKey.current = { fingerprint, key: newIdempotencyKey() };
      return api<Repair>("/code-repairs", {
        method: "POST",
        body: fingerprint,
        idempotencyKey: repairKey.current.key,
      });
    },
    onSuccess: (created) => {
      setSelectedRepair(created.id);
      qc.setQueryData(["code-repairs", created.id], created);
      repairKey.current = undefined;
      invalidate();
    },
  });
  const cancelRepair = useMutation({
    mutationFn: () =>
      api(`/code-repairs/${repair!.id}/cancel`, { method: "POST", body: "{}" }),
    onSuccess: invalidate,
  });
  return (
    <div className="investigation-page">
      <div className="page-header">
        <div>
          <p className="eyebrow">INVESTIGATE / VERIFY</p>
          <h1>智能性能调查</h1>
          <p className="muted">从问题开始采集，以实际测量和回归结束。</p>
        </div>
      </div>
      <div className="investigation-modes" role="group" aria-label="调查方式">
        <button
          type="button"
          className={`button ${mode === "performance" ? "" : "secondary"}`}
          aria-pressed={mode === "performance"}
          onClick={() => setMode("performance")}
        >
          性能采集与复测
        </button>
        {admin && (
          <button
            type="button"
            className={`button ${mode === "code" ? "" : "secondary"}`}
            aria-pressed={mode === "code"}
            onClick={() => setMode("code")}
          >
            源码定位与修复
          </button>
        )}
      </div>
      {mode === "performance" ? (
        <>
          <section className="panel">
            <h2>描述问题，生成采集计划</h2>
            <p className="muted">
              模型依据所选目标的实际能力选择采集器。确认计划后，平台执行原采集、诊断、最多一次复测和再次分析。
            </p>
            {canWrite && (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  start.mutate();
                }}
                className="investigation-form"
              >
                <label htmlFor="investigation-goal">需要调查的问题</label>
                <textarea
                  id="investigation-goal"
                  rows={3}
                  maxLength={600}
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                  required
                />
                <div className="investigation-fields">
                  <label>
                    目标节点
                    <select
                      value={agentID}
                      required
                      onChange={(e) => {
                        setAgentID(e.target.value);
                        setPID("");
                      }}
                    >
                      <option value="">选择节点</option>
                      {agents.data?.items.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.hostname} · {a.status}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    目标范围
                    <select
                      value={scope}
                      onChange={(e) => setScope(e.target.value)}
                    >
                      <option value="process">进程</option>
                      <option value="container">容器</option>
                      <option value="host">主机资源</option>
                    </select>
                  </label>
                  {scope === "process" && (
                    <label>
                      目标进程
                      <select
                        value={pid}
                        required
                        onChange={(e) => setPID(e.target.value)}
                      >
                        <option value="">选择进程</option>
                        {processes.map((p) => (
                          <option key={p.pid} value={p.pid}>
                            {p.name} · PID {p.pid}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {scope === "container" && (
                    <label>
                      已授权容器ID
                      <input
                        value={containerID}
                        required
                        pattern="[a-fA-F0-9]{12,64}"
                        onChange={(e) => setContainerID(e.target.value)}
                      />
                    </label>
                  )}
                </div>
                {projects.data?.projects.map(
                  (p) =>
                    p.runtime && (
                      <button
                        key={p.id}
                        type="button"
                        className="button secondary"
                        onClick={() => {
                          setAgentID(p.runtime!.agent_id);
                          setPID(String(p.runtime!.pid));
                          setScope("process");
                          setProjectID(p.id);
                        }}
                      >
                        {p.id === "crm-python"
                          ? "选择已接入的 Python CRM"
                          : "选择已接入的 " + p.label}
                      </button>
                    ),
                )}
                {admin && (
                  <label className="investigation-consent">
                    <input
                      type="checkbox"
                      checked={linkedCode}
                      onChange={(e) => setLinkedCode(e.target.checked)}
                    />
                    复测后继续定位所选登记项目的源码，允许发送登记片段并在隔离副本自动修改、回归和性能验证
                  </label>
                )}
                <label className="investigation-consent">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                  />
                  同意发送问题与统计；确认计划后允许原采集、一次复测及自动模型分析（每次采集最多60秒）
                </label>
                <button
                  className="button"
                  disabled={!consent || !agentID || start.isPending}
                >
                  {start.isPending ? "正在提交…" : "生成采集计划"}
                </button>
              </form>
            )}
            {[agents.error, start.error].filter(Boolean).map((error, i) => (
              <ErrorPanel key={i} error={error} />
            ))}
          </section>
          <section className="panel">
            <div className="section-heading">
              <h2>调查记录</h2>
              {history.isFetching && <Busy />}
            </div>
            {history.error && <ErrorPanel error={history.error} />}
            {selected && selectedInvestigationDetail.isPending && (
              <p role="status">正在加载调查记录 #{selected}…</p>
            )}
            {selected && selectedInvestigationDetail.error && (
              <>
                <p role="alert">无法读取所选调查记录 #{selected}。</p>
                <ErrorPanel error={selectedInvestigationDetail.error} />
              </>
            )}
            {run?.code_repair_id && (
              <p role="status">
                已关联源码定位与修复 #{run.code_repair_id}。
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    setSelectedRepair(run.code_repair_id!);
                    if (run.code_project_id) setProjectID(run.code_project_id);
                    setMode("code");
                    void qc.invalidateQueries({ queryKey: ["code-repairs"] });
                  }}
                >
                  查看补丁及实际验证
                </button>
              </p>
            )}
            <label>
              选择调查
              <select
                value={selected || run?.id || ""}
                onChange={(e) => setSelected(e.target.value)}
              >
                {selected &&
                  !history.data?.items.some((r) => r.id === selected) && (
                    <option value={selected}>
                      #{selected} ·{" "}
                      {run
                        ? statusNames[run.status] || run.status
                        : "读取所选调查"}
                    </option>
                  )}
                {history.data?.items.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} · {statusNames[r.status] || r.status} ·{" "}
                    {dateTime(r.created_at)}
                  </option>
                ))}
              </select>
            </label>
            {run ? (
              <>
                <p className="muted">
                  调查记录 #{run.id} · 节点 {run.agent_id}
                  {(run.parameters?.pid || run.parameters?.target?.pid) && (
                    <>
                      {" "}
                      · PID {run.parameters.pid || run.parameters.target?.pid}
                    </>
                  )}
                  {run.parameters?.target?.container_id && (
                    <> · 容器 {run.parameters.target.container_id}</>
                  )}
                </p>
                <p role="status">{statusNames[run.status] || run.status}</p>
                <p>{run.goal}</p>
                {run.error_code && (
                  <p role="alert">
                    调查未完成：{run.error_code}。原始任务和工具证据保留。
                  </p>
                )}
                {run.plan && (
                  <div className="investigation-plan">
                    <h3>采集计划</h3>
                    <p>
                      {run.plan.kind} · {run.plan.duration_seconds}秒 ·{" "}
                      {run.plan.frequency_hz}Hz
                    </p>
                    <p>{run.plan.rationale}</p>
                    {run.plan.options && (
                      <pre>{JSON.stringify(run.plan.options, null, 2)}</pre>
                    )}
                    {run.status === "awaiting_confirmation" && canWrite && (
                      <button
                        className="button"
                        disabled={confirm.isPending || start.isPending}
                        onClick={() => confirm.mutate()}
                      >
                        确认计划并执行闭环
                      </button>
                    )}
                  </div>
                )}
                {run.baseline_task_id && (
                  <p>
                    <Link to={`/tasks/${run.baseline_task_id}`}>
                      查看原采集任务 #{run.baseline_task_id}
                    </Link>
                  </p>
                )}
                {run.trace && <Evidence steps={run.trace} />}
                <p>
                  {run.report?.candidate_report?.summary ||
                    run.report?.diagnosis?.summary}
                </p>
                {run.report?.comparison && (
                  <ComparisonView value={run.report.comparison} />
                )}{" "}
                {run.report?.verification_status && (
                  <p>该采集类型需要补充完整配置后人工安排复测。</p>
                )}
                {!finalStates.has(run.status) && canWrite && (
                  <button
                    className="button secondary"
                    disabled={cancel.isPending}
                    onClick={() => cancel.mutate()}
                  >
                    取消调查
                  </button>
                )}
                {[confirm.error, cancel.error]
                  .filter(Boolean)
                  .map((error, i) => (
                    <ErrorPanel key={i} error={error} />
                  ))}
              </>
            ) : selected ? (
              <p className="muted">所选调查记录 #{selected} 暂未取得。</p>
            ) : (
              <p className="muted">暂无调查，先描述问题并选择目标。</p>
            )}
          </section>
        </>
      ) : (
        <>
          <section className="panel">
            <h2>对真实源码定位并验证修复</h2>
            <p className="muted">
              仅处理已登记、锁定版本的源码。在独立副本应用补丁，运行原测试和6组交替A/B；保留CPU时间、输出一致性及失败证据。
            </p>
            {projects.error && <ErrorPanel error={projects.error} />}
            <form
              className="investigation-form"
              onSubmit={(e) => {
                e.preventDefault();
                startRepair.mutate();
              }}
            >
              <label>
                已接入的项目
                <select
                  value={projectID}
                  required
                  onChange={(e) => setProjectID(e.target.value)}
                >
                  {projects.data?.projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor="repair-goal">定位和修复目标</label>
              <textarea
                id="repair-goal"
                rows={3}
                value={codeGoal}
                maxLength={600}
                required
                onChange={(e) => setCodeGoal(e.target.value)}
              />
              <label className="investigation-consent">
                <input
                  type="checkbox"
                  checked={codeConsent}
                  onChange={(e) => setCodeConsent(e.target.checked)}
                />
                同意登记源码片段发送模型，并在隔离副本自动修改、回归和性能验证
              </label>
              <button
                className="button"
                disabled={
                  !codeConsent ||
                  startRepair.isPending ||
                  !projects.data?.projects.length
                }
              >
                {startRepair.isPending ? "正在提交…" : "开始源码调查与修复"}
              </button>
            </form>
            {startRepair.error && <ErrorPanel error={startRepair.error} />}
          </section>
          <section className="panel">
            <h2>源码调查记录</h2>
            {repairs.error && <ErrorPanel error={repairs.error} />}
            {selectedRepair && selectedRepairDetail.isPending && (
              <p role="status">正在加载源码记录 #{selectedRepair}…</p>
            )}
            {selectedRepair && selectedRepairDetail.error && (
              <>
                <p role="alert">无法读取所选源码记录 #{selectedRepair}。</p>
                <ErrorPanel error={selectedRepairDetail.error} />
              </>
            )}
            <label>
              选择记录
              <select
                value={selectedRepair || repair?.id || ""}
                onChange={(e) => setSelectedRepair(e.target.value)}
              >
                {selectedRepair &&
                  !repairs.data?.items.some((r) => r.id === selectedRepair) && (
                    <option value={selectedRepair}>
                      #{selectedRepair} ·{" "}
                      {repair
                        ? statusNames[repair.status] || repair.status
                        : "读取所选记录"}
                    </option>
                  )}
                {repairs.data?.items.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} · {statusNames[r.status] || r.status} ·{" "}
                    {dateTime(r.created_at)}
                  </option>
                ))}
              </select>
            </label>
            {repair ? (
              <>
                <p className="muted">
                  源码记录 #{repair.id} · 项目{" "}
                  {projects.data?.projects.find(
                    (p) => p.id === repair.project_id,
                  )?.label || repair.project_id}
                </p>
                <p role="status">
                  {statusNames[repair.status] || repair.status}
                </p>
                <p>{repair.goal}</p>
                <p className="muted">
                  绑定源码版本 {repair.source_revision.slice(0, 16)}
                </p>
                {repair.error_code && (
                  <p role="alert">
                    本轮未通过：{repair.error_code}。查看工具证据了解失败阶段。
                  </p>
                )}
                <Evidence steps={repair.trace} />
                {repair.report && (
                  <>
                    <h3>定位与修复结论</h3>
                    <p>{repair.report.summary}</p>
                    <ul>
                      {repair.report.locations.map((location) => (
                        <li key={location.path + location.line}>
                          <code>
                            {location.path}:{location.line}
                          </code>{" "}
                          — {location.reason}
                        </li>
                      ))}
                    </ul>
                    <div className="investigation-result">
                      <p>
                        原功能回归：
                        {repair.report.validation.functional_regression ===
                        "passed"
                          ? "通过"
                          : "未通过"}
                        ；输出一致性：
                        {repair.report.validation.output_parity_cases}组。
                      </p>
                      <p>
                        受控CPU时间加速比：
                        {repair.report.validation.median_cpu_speedup.toFixed(2)}
                        ×；6组交替对照中{" "}
                        {repair.report.validation.improved_pairs}
                        组达到实用门槛。
                      </p>
                      <p>
                        验证范围：
                        {repair.report.validation.scope ||
                          "当前登记项目的受控用例，尚未提供具体范围"}
                        。补丁已应用到隔离副本；受控结果不能等同生产P95/P99改善。
                      </p>
                    </div>
                    {repair.report.validation.patch?.map((edit, index) => (
                      <details key={`${edit.path}:${index}`}>
                        <summary>查看实际应用的补丁 · {edit.path}</summary>
                        <p>替换前</p>
                        <pre>{edit.old}</pre>
                        <p>替换后</p>
                        <pre>{edit.new}</pre>
                      </details>
                    ))}
                  </>
                )}
                {!finalStates.has(repair.status) && (
                  <button
                    className="button secondary"
                    disabled={cancelRepair.isPending}
                    onClick={() => cancelRepair.mutate()}
                  >
                    取消修复
                  </button>
                )}
                {cancelRepair.error && (
                  <ErrorPanel error={cancelRepair.error} />
                )}
              </>
            ) : selectedRepair ? (
              <p className="muted">所选源码记录 #{selectedRepair} 暂未取得。</p>
            ) : (
              <p className="muted">暂无源码调查记录。</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
