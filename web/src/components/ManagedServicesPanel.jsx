import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Input, Select, Space, Tag, Typography, message } from "antd";
import { listManagedServices, startManagedServiceDiagnosis } from "../api/client";
import { collectorMeta } from "../utils/collectors";

const { Paragraph, Text } = Typography;
const states = { OBSERVED: "已发现后台进程", OFFLINE: "未发现后台进程", STALE: "进程快照已过期", UNAVAILABLE: "暂时无法确认进程" };
const operations = { "rag.question": "知识库问答", "memo.records": "笔记读写", "memo.attachments": "笔记附件", "files.resources": "文件与目录", "files.download": "文件下载", "files.search": "文件搜索", "bookmarks.records": "书签读写", "bookmarks.tags": "书签标签", "notifications.subscribe": "订阅或读取消息", "notifications.publish": "通知发布" };
const ragStages = [["rewrite_ms", "查询改写"], ["embedding_ms", "向量化"], ["retrieval_ms", "检索"], ["rerank_ms", "候选重排"], ["generation_ms", "生成"]];
const requestStates = { NOT_CONFIGURED: "未配置请求观测", NO_DATA: "尚无业务请求", NO_RECENT_DATA: "最近 24 小时暂无可用请求", UNAVAILABLE: "请求观测暂时无法读取", INVALID: "请求观测存在冲突，暂不可使用" };
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

  async function diagnose(item, suggestedQuery = "") {
    const query = (suggestedQuery || queries[item.id] || "").trim();
    if (query.length < 3) { message.info("请先描述后台服务的性能现象"); return; }
    setStarting(item.id);
    try {
      const requestId = selectedRequests[item.id];
      if (requestId && !item.business_requests?.items?.some(row => row.request_id === requestId)) {
        message.info("所选请求已不在当前列表，请重新选择"); return;
      }
      const created = await startManagedServiceDiagnosis(item.id, { query, mode: "AUTONOMOUS", ...(requestId ? { request_id: requestId } : {}) });
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
            label: `${new Date(row.ended_at).toLocaleTimeString()} · ${operations[row.operation] || row.operation} · ${row.method} · ${row.duration_ms} ms · HTTP ${row.status}` }))} />
        {item.business_requests?.status !== "AVAILABLE" && <Paragraph type="secondary">{requestStates[item.business_requests?.status] || "请求数据尚未返回"}</Paragraph>}
        {selectedRequests[item.id] && <details><summary>查看请求 ID 与阶段耗时</summary><Paragraph copyable style={{ overflowWrap: "anywhere" }}>请求 ID：{selectedRequests[item.id]}</Paragraph>
        {item.observation_source === "agi_office_rag_snapshot" && selectedRequests[item.id] && (() => {
          const chosen = item.business_requests?.items?.find(row => row.request_id === selectedRequests[item.id]);
          if (!chosen) return null;
          return <div className="office-request-stages" aria-label="知识库请求阶段耗时">
            <Text strong>这次知识库问答 · {chosen.duration_ms} ms · {chosen.business_result === "COMPLETED" ? "已完成" : chosen.business_result === "FAILED" ? "失败" : "已中断"}</Text>
            <div>{ragStages.map(([key, label]) => <span key={key}>{label}：{chosen.stage_ms?.[key] == null ? "未执行或未采集" : `${chosen.stage_ms[key]} ms`}</span>)}</div>
            <Text type="secondary">当前部署使用本地词法检索。向量化和重排没有启用时保留空值；阶段耗时属于已结束的业务请求。</Text>
          </div>;
        })()}
        <Paragraph type="secondary">{item.observation_source === "agi_office_rag_snapshot" ? "业务阶段来自 AGI-saber 进程内计时；随后采集的是当前复现窗口，不能还原已结束请求的调用栈。" : "耗时从网关接收请求计到连接结束。HTTP 成功不等于业务结果正确；稍后采集的是当前复现窗口，无法还原已结束请求的调用栈。"}</Paragraph></details>}
      </div>}
      <label htmlFor={`service-query-${item.id}`}>有异常现象？写在这里（可选）</label>
      <Input.TextArea id={`service-query-${item.id}`} rows={2} maxLength={2000}
        placeholder="描述你做了什么、哪里变慢或失败。例如：上传文件时，其他页面也明显变慢。"
        value={queries[item.id] || ""} onChange={event => setQueries(old => ({ ...old, [item.id]: event.target.value }))}
        style={{ margin: "8px 0 12px" }} />
      <div className="managed-service-actions"><Button type="primary" aria-label="检查当前状态" loading={starting === item.id}
        disabled={Boolean(error) || item.status !== "OBSERVED" || Boolean(starting)} onClick={() => diagnose(item, "检查当前业务和进程是否存在可验证的性能故障")}>
        检查当前状态
      </Button>
      <Button aria-label="诊断这个后台" loading={starting === item.id}
        disabled={Boolean(error) || item.status !== "OBSERVED" || Boolean(starting) || !(queries[item.id] || "").trim()} onClick={() => diagnose(item)}>
        排查描述的异常
      </Button></div>
    </Card>)}
  </Space>;
}
