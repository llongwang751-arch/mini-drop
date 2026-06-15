import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowClockwise, CheckCircle, ClockCounterClockwise, Database, Heartbeat,
  ListMagnifyingGlass, Plus, Pulse,
  UserFocus, WarningCircle, X
} from "@phosphor-icons/react";
import "./style.css";

const statusText = { PENDING: "等待执行", RUNNING: "正在采集", UPLOADING: "正在分析", DONE: "已完成", FAILED: "失败" };
const collectorText = { perf: "CPU / perf", ebpf: "eBPF 内核探针", pyspy: "Python 调用栈" };
const reasonText = {
  "task accepted": "任务已创建，等待 Agent 领取",
  "claimed by agent": "Agent 已领取任务",
  "collector finished; uploading": "采集完成，正在上传分析",
  "analyzer produced visualizations": "分析器已生成可视化结果",
  "agent connected or recovered": "Agent 已连接或恢复",
  "no heartbeat for 30s": "超过 30 秒未收到心跳",
};
const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
const api = async (path, options) => {
  const response = await fetch(`/api${path}`, { headers: { "content-type": "application/json" }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.status);
  return data;
};

function Status({ value }) {
  return <span className={`status ${value}`}>{statusText[value] || value}</span>;
}

function Metric({ label, value, note, icon: Icon }) {
  return <article className="metric"><Icon size={18} weight="duotone" /><span>{label}</span><strong>{value}</strong><small>{note}</small></article>;
}

function TaskTable({ tasks, selected, onSelect }) {
  if (!tasks.length) return <div className="empty"><ListMagnifyingGlass size={30} /><b>暂无性能任务</b><span>创建第一个任务，Agent 会自动领取并分析。</span></div>;
  return <div className="table-wrap"><table>
    <thead><tr><th>任务 ID</th><th>采集器</th><th>目标 PID</th><th>状态</th><th>最近进展</th><th>创建时间</th></tr></thead>
    <tbody>{tasks.map(task => <tr key={task.id} className={selected === task.id ? "selected" : ""} onClick={() => onSelect(task.id)}>
      <td><code>{task.id}</code></td><td>{collectorText[task.collector]}</td><td>{task.pid}</td><td><Status value={task.status} /></td>
      <td>{reasonText[task.reason] || task.reason}</td><td>{fmtTime(task.created_at)}</td>
    </tr>)}</tbody>
  </table></div>;
}

function Flame({ tree }) {
  const leaves = [];
  const walk = node => node.children?.length ? node.children.forEach(walk) : leaves.push(node);
  walk(tree);
  const max = Math.max(...leaves.map(x => x.value), 1);
  return <div className="flame">{leaves.map((leaf, index) =>
    <div key={`${leaf.name}-${index}`} style={{ height: `${44 + leaf.value / max * 120}px` }}><span>{leaf.name}</span><b>{leaf.value}</b></div>
  )}</div>;
}

function TaskDetail({ task, onClose }) {
  if (!task) return null;
  const result = task.result;
  return <section className="panel detail">
    <div className="panel-head"><div><p className="eyebrow">PROFILE DETAIL</p><h2>任务 <code>{task.id}</code></h2></div><button className="icon-btn" onClick={onClose} aria-label="关闭详情"><X size={18} /></button></div>
    <div className="detail-meta"><span>{collectorText[task.collector]}</span><span>PID {task.pid}</span>{result && <span>{result.collector_meta.backend}</span>}<Status value={task.status} /></div>
    {result ? <div className="analysis-grid">
      <article className="analysis">
        <h3>{task.collector === "pyspy" ? "Python 语言调用栈" : "采样火焰图"}</h3>
        {task.collector === "pyspy" ? <div className="language-stack">{result.top.map((x, i) => <div key={x.name}><i>{String(i + 1).padStart(2, "0")}</i><span>{x.name}</span><b>{x.samples}</b></div>)}</div> : <Flame tree={result.flamegraph} />}
        {!!result.histogram?.length && <><h3>内核写入分布</h3><div className="histogram">{result.histogram.map(x => <div key={x.bucket}><span>{x.bucket}</span><b style={{ width: `${Math.min(100, 12 + x.count / 8)}%` }}>{x.count}</b></div>)}</div></>}
      </article>
      <aside className="analysis"><h3>热点函数 TopN</h3>{result.top.map(x => <div className="top-row" key={x.name}><span>{x.name}</span><b>{x.samples}</b></div>)}
        <h3>可验证归因</h3>{result.attribution.map((x, i) => <div className="attribution" key={i}><b>{x.conclusion}</b><code>证据 {JSON.stringify(x.evidence)}</code><code>规则 {x.rule}</code></div>)}
      </aside>
    </div> : <div className="empty"><Pulse size={30} /><b>正在等待分析结果</b><span>Agent 完成采集后，这里会显示可视化结果。</span></div>}
    <div className="history"><h3>状态历史</h3>{task.transitions.map(x => <div key={x.id}><b>{statusText[x.from_status] || "新建"} → {statusText[x.to_status]}</b><span>{reasonText[x.reason] || x.reason} · {fmtTime(x.created_at)}</span></div>)}</div>
  </section>;
}

function TaskDialog({ agents, onClose, onCreated }) {
  const [form, setForm] = useState({ agent_id: agents.find(x => x.online)?.id || "", pid: 1, duration: 5, rate: 49, collector: "perf", continuous: false });
  const [error, setError] = useState("");
  const update = event => setForm({ ...form, [event.target.name]: event.target.type === "checkbox" ? event.target.checked : event.target.value });
  const submit = async event => {
    event.preventDefault();
    try {
      await api("/tasks", { method: "POST", body: JSON.stringify(form) });
      onCreated(); onClose();
    } catch (e) { setError(e.message); }
  };
  return <div className="modal-backdrop"><form className="modal" onSubmit={submit}>
    <div className="panel-head"><div><p className="eyebrow">NEW PROFILE</p><h2>创建采集任务</h2></div><button type="button" className="icon-btn" onClick={onClose}><X size={18} /></button></div>
    <label>目标 Agent<select name="agent_id" value={form.agent_id} onChange={update}>{agents.filter(x => x.online).map(x => <option key={x.id}>{x.id}</option>)}</select></label>
    <label>目标进程 PID<input name="pid" type="number" min="1" value={form.pid} onChange={update} /></label>
    <div className="form-grid"><label>采样时长（秒）<input name="duration" type="number" min="1" max="300" value={form.duration} onChange={update} /></label><label>采样频率（Hz）<input name="rate" type="number" min="1" max="999" value={form.rate} onChange={update} /></label></div>
    <label>采集器<select name="collector" value={form.collector} onChange={update}><option value="perf">CPU / perf</option><option value="ebpf">eBPF 内核探针</option><option value="pyspy">Python 调用栈</option></select></label>
    <label className="check"><input name="continuous" type="checkbox" checked={form.continuous} onChange={update} /><span><b>持续性能采集</b><small>完成后自动创建下一个时间切片</small></span></label>
    {error && <div className="form-error">{error}</div>}<button className="primary" type="submit">下发采集任务</button>
  </form></div>;
}

function Continuous({ agents, onSelect }) {
  const [agent, setAgent] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [slices, setSlices] = useState([]);
  useEffect(() => { if (!agent && agents.length) setAgent(agents[0].id); }, [agents, agent]);
  const load = async () => {
    const query = new URLSearchParams();
    if (start) query.set("start", new Date(start).getTime() / 1000);
    if (end) query.set("end", new Date(end).getTime() / 1000);
    setSlices(await api(`/continuous/${agent}?${query}`));
  };
  return <section className="panel"><div className="panel-head"><div><p className="eyebrow">CONTINUOUS PROFILING</p><h2>持续采样时间轴</h2></div><div className="range"><select value={agent} onChange={e => setAgent(e.target.value)}>{agents.map(x => <option key={x.id}>{x.id}</option>)}</select><input type="datetime-local" value={start} onChange={e => setStart(e.target.value)} /><input type="datetime-local" value={end} onChange={e => setEnd(e.target.value)} /><button className="secondary" onClick={load}>查询窗口</button></div></div>
    <div className="slices">{slices.length ? slices.map(x => <button key={x.id} onClick={() => onSelect(x.id)}><code>{x.id}</code><span>{fmtTime(x.updated_at)} · {collectorText[x.collector]}</span><b>{x.result?.fingerprint}</b></button>) : <div className="empty compact"><ClockCounterClockwise size={26} /><b>暂无时间轴数据</b><span>创建持续采集任务后，可在这里回看切片。</span></div>}</div>
  </section>;
}

function App() {
  const [page, setPage] = useState("overview");
  const [agents, setAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [audit, setAudit] = useState([]);
  const [selected, setSelected] = useState(null);
  const [modal, setModal] = useState(false);
  const [error, setError] = useState("");
  const [prompt, setPrompt] = useState("");
  const [planning, setPlanning] = useState(false);
  const refresh = async () => {
    try {
      const [nextAgents, nextTasks, nextAudit] = await Promise.all([api("/agents"), api("/tasks"), api("/audit")]);
      setAgents(nextAgents); setTasks(nextTasks); setAudit(nextAudit); setError("");
      if (selected) setSelected(await api(`/tasks/${selected.id}`));
    } catch (e) { setError(e.message); }
  };
  useEffect(() => { refresh(); const timer = setInterval(refresh, 2000); return () => clearInterval(timer); }, [selected?.id]);
  const metrics = useMemo(() => ({
    agents: agents.filter(x => x.online).length,
    running: tasks.filter(x => ["RUNNING", "UPLOADING"].includes(x.status)).length,
    done: tasks.filter(x => x.status === "DONE").length,
    failed: tasks.filter(x => x.status === "FAILED").length,
  }), [agents, tasks]);
  const selectTask = async id => setSelected(await api(`/tasks/${id}`));
  const planTask = async event => {
    event.preventDefault();
    setPlanning(true);
    try {
      const response = await api("/natural-language", { method: "POST", body: JSON.stringify({ text: prompt }) });
      setPrompt("");
      await refresh();
      setSelected(await api(`/tasks/${response.task.id}`));
    } catch (e) { setError(e.message); } finally { setPlanning(false); }
  };
  return <>
    <header className="topbar"><div className="brand"><b>MD</b><span><strong>Mini-Drop</strong><small>性能诊断控制台</small></span></div>
      <nav>{[["overview", "任务总览"], ["agents", "采集节点"], ["audit", "审计日志"]].map(x => <button key={x[0]} className={page === x[0] ? "active" : ""} onClick={() => setPage(x[0])}>{x[1]}</button>)}</nav>
      <div className="top-actions"><span className="online"><i />控制平面在线</span><button className="primary" onClick={() => setModal(true)}><Plus size={16} weight="bold" />新建任务</button></div>
    </header>
    <main id="main">
      {error && <div className="banner"><WarningCircle size={18} />数据刷新失败：{error}</div>}
      {page === "overview" && <><div className="heading"><div><p className="eyebrow">PERFORMANCE WORKSPACE</p><h1>性能任务</h1><span>下发真实采集任务，跟踪状态，定位内核与进程热点。</span></div><button className="refresh" onClick={refresh}><ArrowClockwise size={17} />每 2 秒自动刷新</button></div>
        <section className="metrics"><Metric label="在线 Agent" value={metrics.agents} note="可接收任务" icon={Heartbeat} /><Metric label="执行中" value={metrics.running} note="采集或分析中" icon={Pulse} /><Metric label="已完成" value={metrics.done} note="结果可查看" icon={CheckCircle} /><Metric label="失败" value={metrics.failed} note="需要排查" icon={WarningCircle} /></section>
        <form className="command" onSubmit={planTask}><div><p className="eyebrow">NATURAL LANGUAGE PROFILE</p><b>一句话创建可验证任务</b><span>例如：对 PID 1234 做 8 秒 py-spy 采集，频率 77Hz</span></div><input value={prompt} onChange={e => setPrompt(e.target.value)} required placeholder="必须包含目标 PID" /><button className="primary" disabled={planning}>{planning ? "正在规划" : "解析并下发"}</button></form>
        <section className="panel"><div className="panel-head"><div><p className="eyebrow">RECENT PROFILES</p><h2>最近任务</h2></div><span className="panel-note">点击任务查看完整分析</span></div><TaskTable tasks={tasks} selected={selected?.id} onSelect={selectTask} /></section>
        <Continuous agents={agents} onSelect={selectTask} /><TaskDetail task={selected} onClose={() => setSelected(null)} /></>}
      {page === "agents" && <><div className="heading"><div><p className="eyebrow">AGENT FLEET</p><h1>采集节点</h1><span>查看 Agent 心跳、环境与在线状态。</span></div></div><section className="agent-list">{agents.map(x => <article key={x.id}><UserFocus size={22} weight="duotone" /><div><b>{x.id}</b><span>{x.hostname}</span></div><code>{x.version}</code><span>最后心跳 {fmtTime(x.last_seen)}</span><Status value={x.online ? "DONE" : "FAILED"} /></article>)}</section></>}
      {page === "audit" && <><div className="heading"><div><p className="eyebrow">CONNECTIVITY HISTORY</p><h1>审计日志</h1><span>记录 Agent 上线、离线与恢复事件。</span></div></div><section className="panel audit">{audit.map(x => <article key={x.id}><Database size={18} /><div><b>{x.kind} · {x.subject}</b><span>{reasonText[x.message] || x.message}</span></div><time>{fmtTime(x.created_at)}</time></article>)}</section></>}
    </main>
    {modal && <TaskDialog agents={agents} onClose={() => setModal(false)} onCreated={refresh} />}
  </>;
}

createRoot(document.getElementById("root")).render(<App />);
