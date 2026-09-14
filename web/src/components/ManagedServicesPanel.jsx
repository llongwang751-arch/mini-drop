import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Input, Select, Space, Tag, Typography, message } from "antd";
import { listManagedServices, startManagedServiceDiagnosis } from "../api/client";
import { collectorMeta } from "../utils/collectors";

const { Paragraph, Text } = Typography;
const states = { OBSERVED: "已发现后台进程", OFFLINE: "未发现后台进程", STALE: "进程快照已过期", UNAVAILABLE: "暂时无法确认进程" };
const operations = { "memo.records": "笔记读写", "memo.attachments": "笔记附件", "files.resources": "文件与目录", "files.download": "文件下载", "files.search": "文件搜索", "bookmarks.records": "书签读写", "bookmarks.tags": "书签标签", "notifications.subscribe": "订阅或读取消息", "notifications.publish": "通知发布" };
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

  async function diagnose(item) {
    const query = (queries[item.id] || "").trim();
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

  return <Space direction="vertical" size={16} style={{ width: "100%" }}>
    <Card title="已接入的后台服务" extra={<Button aria-label="刷新服务" loading={loading} onClick={refresh}>刷新服务</Button>}>
      <Paragraph>先在原业务页面完成一次操作，再选择该后台及对应请求发起诊断。Mini-Drop 会带入操作时间与耗时，核对当前进程后采集运行证据。</Paragraph>
      <Text type="secondary">进程存在不代表业务健康。服务重启后会重新发现进程；每次诊断都重新绑定身份。这里不展示历史实验成绩。</Text>
    </Card>
    {error && <Alert type="error" showIcon message="服务状态读取失败" description={error} />}
    {data && !data.items?.length && <Alert type="info" message="尚未配置接入服务" description="请按后台服务接入文档登记服务名与负责采集的 Agent。" />}
    {(data?.items || []).map(item => <Card key={item.id} title={item.name}>
      <Space wrap style={{ marginBottom: 12 }}>
        <Tag color={!error && item.status === "OBSERVED" ? "blue" : "default"}>{error ? "状态待刷新" : states[item.status] || item.status}</Tag>
        <Tag>{item.runtime}</Tag><Tag>环境：{item.environment}</Tag>
        {businessLink(item) && <Button href={businessLink(item)} target="_blank" rel="noopener noreferrer">{item.entry_path === "/api/office/" ? "打开办公助手" : "打开业务页面"}</Button>}
        {item.version && <Tag>版本 {item.version}</Tag>}
      </Space>
      <Paragraph>{item.description}</Paragraph>
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
      {item.business_observations && <div style={{ marginBottom: 16 }}>
        <label htmlFor={`business-request-${item.id}`}>关联哪次业务请求？</label>
        <Select id={`business-request-${item.id}`} aria-label={`${item.name}的业务请求`} allowClear
          style={{ width: "100%", margin: "8px 0" }} placeholder="选择一次操作，也可以只描述当前持续故障"
          disabled={Boolean(error) || item.business_requests?.status !== "AVAILABLE"}
          value={selectedRequests[item.id]}
          onChange={value => setSelectedRequests(old => ({ ...old, [item.id]: value }))}
          options={(item.business_requests?.items || []).map(row => ({ value: row.request_id,
            label: `${new Date(row.ended_at).toLocaleTimeString()} · ${operations[row.operation] || row.operation} · ${row.method} · ${row.duration_ms} ms · HTTP ${row.status}` }))} />
        {item.business_requests?.status !== "AVAILABLE" && <Paragraph type="secondary">{requestStates[item.business_requests?.status] || "请求数据尚未返回"}</Paragraph>}
        {selectedRequests[item.id] && <Paragraph copyable style={{ overflowWrap: "anywhere" }}>请求 ID：{selectedRequests[item.id]}</Paragraph>}
        <Paragraph type="secondary">耗时从网关接收请求计到连接结束。HTTP 成功不等于业务结果正确；稍后采集的是当前复现窗口，无法还原已结束请求的调用栈。</Paragraph>
      </div>}
      <label htmlFor={`service-query-${item.id}`}>这个后台出现了什么性能问题？</label>
      <Input.TextArea id={`service-query-${item.id}`} rows={3} maxLength={2000}
        placeholder="描述你做了什么、哪里变慢或失败。例如：上传文件时，其他页面也明显变慢。"
        value={queries[item.id] || ""} onChange={event => setQueries(old => ({ ...old, [item.id]: event.target.value }))}
        style={{ margin: "8px 0 12px" }} />
      <Button type="primary" aria-label="诊断这个后台" loading={starting === item.id}
        disabled={Boolean(error) || item.status !== "OBSERVED" || Boolean(starting)} onClick={() => diagnose(item)}>
        诊断这个后台
      </Button>
    </Card>)}
  </Space>;
}
