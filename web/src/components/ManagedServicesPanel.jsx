import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Input, Select, Space, Tag, Typography, message } from "antd";
import { listManagedServices, startManagedServiceDiagnosis } from "../api/client";
import { collectorMeta } from "../utils/collectors";

const { Paragraph, Text } = Typography;
const states = { OBSERVED: "已发现后台进程", OFFLINE: "未发现后台进程", STALE: "进程快照已过期", UNAVAILABLE: "暂时无法确认进程" };
const operations = { "rag.question": "知识库问答", "rag.ingest": "文档分块入库", "memo.records": "笔记读写", "memo.attachments": "笔记附件", "files.resources": "文件与目录", "files.download": "文件下载", "files.search": "文件搜索", "bookmarks.records": "书签读写", "bookmarks.tags": "书签标签", "notifications.subscribe": "订阅或读取消息", "notifications.publish": "通知发布" };
const ragStages = [["rewrite_ms", "查询改写"], ["embedding_ms", "向量化"], ["retrieval_ms", "检索"], ["rerank_ms", "候选重排"], ["generation_ms", "生成"]];
const ingestStages = [["parse_and_http_ms", "接收与解析"], ["split_ms", "文档分块"], ["embedding_ms", "向量化"], ["index_ms", "索引入库"]];
function dominantIngestStage(row) {
  const measured = ingestStages.map(([key, label]) => ({ label, ms: Number(row.stage_ms?.[key]) }))
    .filter(stage => Number.isFinite(stage.ms) && stage.ms >= 0);
  return measured.sort((a, b) => b.ms - a.ms)[0] || null;
}
const requestStates = { NOT_CONFIGURED: "未配置请求观测", NO_DATA: "尚无业务请求", NO_RECENT_DATA: "最近 24 小时暂无可用请求", UNAVAILABLE: "请求观测暂时无法读取", INVALID: "请求观测存在冲突，暂不可使用" };
const exerciseQuestion = "员工年假申请须提前多久提交？";
const exercisePhases = [["baseline", "正常"], ["fault", "故障"], ["recovery", "撤销后"]];

function OfficeExercise({ item, onOpenDiagnosis }) {
  const [running, setRunning] = useState(false);
  const [rows, setRows] = useState([]);
  const [diagnosisId, setDiagnosisId] = useState("");
  const [error, setError] = useState("");

  async function oneRequest(phase) {
    const token = localStorage.getItem("agi_auth_token");
    if (!token) throw new Error("请先在办公助手登录验收账号，再回到本页运行");
    const response = await fetch("/api/office/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, "X-Mini-Drop-Exercise": phase },
      body: JSON.stringify({ message: exerciseQuestion, use_rag: true }),
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(`办公助手问答失败（HTTP ${response.status}）`);
    const requestId = String(result.trace_id || "").replaceAll("-", "").toLowerCase();
    if (!/^[a-f0-9]{32}$/.test(requestId)) throw new Error("业务响应缺少可关联的请求 ID");
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const services = await listManagedServices();
      const service = services.items?.find(row => row.id === item.id);
      const observation = service?.business_requests?.items?.find(row => row.request_id === requestId);
      if (observation) {
        if (observation.exercise_phase !== phase || observation.business_result !== "COMPLETED") {
          throw new Error(`${phase} 请求未形成成功的业务阶段记录`);
        }
        return observation;
      }
      await new Promise(resolve => setTimeout(resolve, 400));
    }
    throw new Error("问答已返回，但 Mini-Drop 尚未读到对应阶段记录");
  }

  async function run() {
    setRunning(true); setRows([]); setDiagnosisId(""); setError("");
    let faultAttempted = false;
    let faultRow = null;
    try {
      const baseline = await oneRequest("baseline");
      setRows([baseline]);
      faultAttempted = true;
      faultRow = await oneRequest("fault");
      setRows([baseline, faultRow]);
      if (faultRow.injected_delay_ms !== 2500 || faultRow.exercise_phase !== "fault") {
        throw new Error("故障请求没有观测到预期的检索延迟，不能宣称故障已复现");
      }
      const created = await startManagedServiceDiagnosis(item.id, {
        query: "验收请求的知识库检索阶段变慢；请核对业务阶段、当前进程和竞争解释。该请求含受控检索延迟，勿当作自然故障。",
        mode: "AUTONOMOUS", request_id: faultRow.request_id,
      });
      setDiagnosisId(created.diagnosis_id || created.id || "");
    } catch (err) {
      setError(err?.message || "业务验收未完成");
    } finally {
      if (faultAttempted) {
        try {
          const recovery = await oneRequest("recovery");
          setRows(old => [...old, recovery]);
        } catch (err) {
          setError(old => `${old ? `${old}；` : ""}撤销后复测失败：${err?.message || "未知错误"}`);
        }
      }
      setRunning(false);
    }
  }

  const baseline = rows.find(row => row.exercise_phase === "baseline");
  const fault = rows.find(row => row.exercise_phase === "fault");
  const recovery = rows.find(row => row.exercise_phase === "recovery");
  const restored = Boolean(baseline && fault && recovery && baseline.injected_delay_ms === 0 &&
    fault.injected_delay_ms === 2500 && recovery.injected_delay_ms === 0 &&
    baseline.pid === fault.pid && fault.pid === recovery.pid &&
    baseline.version === fault.version && fault.version === recovery.version &&
    fault.stage_ms?.retrieval_ms >= baseline.stage_ms?.retrieval_ms + 1500 &&
    fault.stage_ms?.retrieval_ms >= recovery.stage_ms?.retrieval_ms + 1500);
  return <div className="office-exercise" aria-label="AGI-saber 真实问答验收">
    <Text strong>真实问答：故障与恢复</Text>
    <Paragraph type="secondary">用已登录的办公助手账号，对同一个知识库问题依次发出正常、受控慢检索和撤销后请求。故障仅作用于带标记的这一次请求。</Paragraph>
    <Button onClick={run} loading={running} disabled={running || item.status !== "OBSERVED"}>运行三段验收</Button>
    {error && <Alert type="warning" showIcon message={error} style={{ marginTop: 12 }} />}
    {rows.length > 0 && <div className="office-exercise-results">
      {exercisePhases.map(([phase, label]) => {
        const row = rows.find(value => value.exercise_phase === phase);
        return <div key={phase}><strong>{label}</strong><span>{row ? `${row.duration_ms} ms · 检索 ${row.stage_ms?.retrieval_ms ?? "未采集"} ms · ${row.business_result === "COMPLETED" ? "完成" : "失败"}` : "待执行"}</span></div>;
      })}
      <Text type={restored ? "success" : "secondary"}>{restored ? "受控慢检索已定位并撤销：同一进程与版本下，检索耗时回落" : "尚不能确认恢复；请核对三段原始请求"}</Text>
      <details><summary>查看注入方式与请求证据</summary>
        <Paragraph>注入点：AGI-saber 的 HybridStore.search_multi；仅故障请求额外等待 2500 ms。撤销方式：下一请求不带故障阶段标记；没有全局开关或残留任务。</Paragraph>
        {rows.map(row => <Paragraph key={row.request_id} copyable>请求 {row.request_id} · {row.exercise_phase} · 注入 {row.injected_delay_ms} ms · PID {row.pid}</Paragraph>)}
        <Paragraph type="secondary">这是配置级故障演练。单次请求受模型波动影响；阶段记录与进程采样分属不同时间窗，诊断报告的证据门禁仍独立生效。</Paragraph>
      </details>
      {diagnosisId && <Button onClick={() => onOpenDiagnosis?.(diagnosisId)}>查看本次诊断与证据树</Button>}
    </div>}
  </div>;
}
function businessLink(item) {
  if (item.entry_path === "/api/office/") return item.entry_path;
  try { const url = new URL(item.entry_path); return url.protocol === "https:" && !url.username && !url.password ? url.href : null; } catch { return null; }
}

export default function ManagedServicesPanel({ onOpenDiagnosis }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState("");
  const [queries, setQueries] = useState({});
  const [selectedRequests, setSelectedRequests] = useState({});
  const generation = useRef(0);

  async function refresh() {
    const current = ++generation.current;
    setLoading(true);
    try {
      const result = await listManagedServices();
      if (current === generation.current) { setData(result); setError(""); }
    } catch (err) {
      if (current === generation.current) setError(err?.message || "接入服务读取失败");
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 15000);
    return () => { generation.current += 1; clearInterval(timer); };
  }, []);

  async function diagnose(item, suggestedQuery = "", healthCheck = false) {
    const query = (suggestedQuery || queries[item.id] || "").trim();
    if (query.length < 3) { message.info("请先描述后台服务的性能现象"); return; }
    setStarting(item.id);
    try {
      const requestId = selectedRequests[item.id];
      if (requestId && !item.business_requests?.items?.some(row => row.request_id === requestId)) {
        message.info("所选请求已不在当前列表，请重新选择"); return;
      }
      const created = await startManagedServiceDiagnosis(item.id, { query, mode: "AUTONOMOUS", ...(healthCheck ? { health_check: true } : {}), ...(requestId ? { request_id: requestId } : {}) });
      await onOpenDiagnosis?.(created.diagnosis_id || created.id);
    } catch (err) {
      message.error(err?.message || "创建服务诊断失败");
    } finally { setStarting(""); }
  }

  return <Space direction="vertical" size={16} className="managed-service-exam">
    <Card title="选择服务，开始体检" extra={<Button aria-label="刷新服务" loading={loading} onClick={refresh}>刷新服务</Button>}>
      <Paragraph>可以直接检查当前状态；如果刚完成一次业务操作，先选中对应请求，体检会带上这次操作的时间与耗时。</Paragraph>
      <div className="managed-service-steps" aria-label="服务体检流程"><span>1 选择服务</span><span>2 检查状态或描述异常</span><span>3 查看体检报告与排查树</span></div>
      <details><summary>体检会采集什么？</summary><Text type="secondary">系统会重新核对目标进程，采集进程 CPU、内存等当前窗口指标，并保存工具、证据和报告。进程存在不代表业务健康；历史请求耗时与稍后的进程采样属于不同时间窗。</Text></details>
    </Card>
    {error && <Alert type="error" showIcon message="服务状态读取失败" description={error} />}
    {data && !data.items?.length && <Alert type="info" message="尚未配置接入服务" description="请按后台服务接入文档登记服务名与负责采集的 Agent。" />}
    {(data?.items || []).map(item => <Card key={item.id} title={item.name} className="managed-service-card">
      <Space wrap style={{ marginBottom: 12 }}>
        <Tag color={!error && item.status === "OBSERVED" ? "blue" : "default"}>{error ? "状态待刷新" : states[item.status] || item.status}</Tag>
        <Tag>{item.runtime}</Tag><Tag>环境：{item.environment}</Tag>
        {businessLink(item) && <Button href={businessLink(item)} target="_blank" rel="noopener noreferrer">{item.entry_path === "/api/office/" ? "打开办公助手" : "打开业务页面"}</Button>}
        {item.version && <Tag>版本 {item.version}</Tag>}
      </Space>
      <Paragraph className="managed-service-description">{item.description}</Paragraph>
      {item.id === "agi-office-backend" && <Paragraph type="secondary">上传长文档后，可在下方选“文档分块入库”请求，查看本次分块、索引、CPU 与内存，再发起进程诊断。</Paragraph>}
      {item.id === "agi-office-backend" && <OfficeExercise item={item} onOpenDiagnosis={onOpenDiagnosis} />}
      {item.maintenance_notice && <Alert type="warning" showIcon message={item.maintenance_notice} style={{ marginBottom: 12 }} />}
      <details style={{ marginBottom: 16 }}>
      <summary style={{ cursor: "pointer", marginBottom: 12 }}>查看采集进程与能力</summary>
      <Paragraph type="secondary">{item.scope}</Paragraph>
      <Paragraph>负责采集：{item.agent_id}<br />服务名：{item.service_hint}<br />
        最近观测：{item.observed_at ? new Date(item.observed_at).toLocaleString() : "暂无"}</Paragraph>
      {(item.instances || []).map(instance => <Paragraph key={instance.pid}>
        当前进程：{instance.process} · PID {instance.pid}<br />
        Agent 上报的采集能力：{instance.capabilities.map(name => collectorMeta(name).label).join("、") || "未上报"}
      </Paragraph>)}
      <Paragraph type="secondary">实际使用的采集器还会按进程运行时和权限检查；例如 Python 后台不会使用 JVM 采集器。</Paragraph>
      </details>
      {item.business_observations && <div className="managed-service-request">
        <label htmlFor={`business-request-${item.id}`}>关联业务请求（可选）</label>
        <Select id={`business-request-${item.id}`} aria-label={`${item.name}的业务请求`} allowClear
          style={{ width: "100%", margin: "8px 0" }} placeholder="选择一次操作，也可以只描述当前持续故障"
          disabled={Boolean(error) || item.business_requests?.status !== "AVAILABLE"}
          value={selectedRequests[item.id]}
          onChange={value => setSelectedRequests(old => ({ ...old, [item.id]: value }))}
          options={(item.business_requests?.items || []).map(row => ({ value: row.request_id,
            label: `${new Date(row.ended_at).toLocaleTimeString()} · ${operations[row.operation] || row.operation}${row.operation === "rag.ingest" ? ` · ${row.content_chars?.toLocaleString() ?? "?"} 字 / ${row.chunk_count ?? "?"} 块` : ""} · ${row.duration_ms} ms · HTTP ${row.status}` }))} />
        {item.business_requests?.status !== "AVAILABLE" && <Paragraph type="secondary">{requestStates[item.business_requests?.status] || "请求数据尚未返回"}</Paragraph>}
        {selectedRequests[item.id] && (() => {
          const chosen = item.business_requests?.items?.find(row => row.request_id === selectedRequests[item.id]);
          if (!chosen) return null;
          const ingest = chosen.operation === "rag.ingest";
          const dominant = ingest ? dominantIngestStage(chosen) : null;
          return <div className="office-request-stages" aria-label={ingest ? "文档导入阶段耗时" : "知识库请求阶段耗时"}>
            <Text strong>这次{ingest ? "文档导入" : "知识库问答"} · {chosen.duration_ms} ms · {chosen.business_result === "COMPLETED" ? "已完成" : chosen.business_result === "FAILED" ? "失败" : "已中断"}</Text>
            {ingest && <div><span>正文 {chosen.content_chars?.toLocaleString() ?? "未采集"} 字</span><span>分块 {chosen.chunk_count ?? "未采集"}</span><span>向量已入库 {chosen.vector_indexed_count?.toLocaleString() ?? "未采集"}</span><span>进程 CPU {chosen.process_cpu_ms ?? "未采集"} ms / RSS {chosen.rss_peak_mib ?? "未采集"} MiB</span><span>服务组 CPU {chosen.service_cpu_ms ?? "未采集"} ms / 内存峰值 {chosen.service_memory_peak_mib ?? "未采集"} MiB（限额 {chosen.service_memory_limit_mib ?? "未采集"} MiB）</span></div>}
            {dominant && <Text>主要耗时：{dominant.label} {dominant.ms} ms</Text>}
            {ingest && chosen.embed_calls > 0 && chosen.embed_calls === chosen.embed_failures && <Text type="warning">向量化 {chosen.embed_calls} 次均失败；本次完成的是本地词法索引。</Text>}
            {ingest && chosen.embed_calls > chosen.embed_failures && chosen.vector_indexed_count === 0 && <Text type="warning">Embedding 已返回，但没有确认向量写入；请检查向量库。</Text>}
            <details><summary>查看阶段明细与请求 ID</summary>
              <Paragraph copyable style={{ overflowWrap: "anywhere" }}>请求 ID：{selectedRequests[item.id]}</Paragraph>
              <div>{(ingest ? ingestStages : ragStages).map(([key, label]) => <span key={key}>{label}：{chosen.stage_ms?.[key] == null ? "未执行或未采集" : `${chosen.stage_ms[key]} ms`}</span>)}</div>
              {ingest && <div><span>其中向量库写入：{chosen.stage_ms?.vector_write_ms == null ? "未采集" : `${chosen.stage_ms.vector_write_ms} ms`}</span></div>}
              {ingest && <Text type="secondary">向量化尝试 {chosen.embed_calls ?? "未采集"} 次，失败 {chosen.embed_failures ?? "未采集"} 次。CPU/RSS 属于该请求期间的整个进程，不是单函数用量。</Text>}
              {!ingest && <Text type="secondary">未执行或未采到的向量化、重排阶段保留空值；阶段耗时属于已结束的业务请求。</Text>}
              <Paragraph type="secondary">{item.observation_source === "agi_office_rag_snapshot" ? "业务阶段来自 AGI-saber 进程内计时；随后采集的是当前复现窗口，不能还原已结束请求的调用栈。" : "耗时从网关接收请求计到连接结束。HTTP 成功不等于业务结果正确；稍后采集的是当前复现窗口，无法还原已结束请求的调用栈。"}</Paragraph>
            </details>
          </div>;
        })()}
      </div>}
      <label htmlFor={`service-query-${item.id}`}>有异常现象？写在这里（可选）</label>
      <Input.TextArea id={`service-query-${item.id}`} rows={2} maxLength={2000}
        placeholder="描述你做了什么、哪里变慢或失败。例如：上传文件时，其他页面也明显变慢。"
        value={queries[item.id] || ""} onChange={event => setQueries(old => ({ ...old, [item.id]: event.target.value }))}
        style={{ margin: "8px 0 12px" }} />
      <div className="managed-service-actions"><Button type="primary" aria-label="检查当前状态" loading={starting === item.id}
        disabled={Boolean(error) || item.status !== "OBSERVED" || Boolean(starting)} onClick={() => diagnose(item, "检查当前状态", true)}>
        检查当前状态
      </Button>
      <Button aria-label="诊断这个后台" loading={starting === item.id}
        disabled={Boolean(error) || item.status !== "OBSERVED" || Boolean(starting) || !(queries[item.id] || "").trim()} onClick={() => diagnose(item)}>
        排查描述的异常
      </Button></div>
    </Card>)}
  </Space>;
}
