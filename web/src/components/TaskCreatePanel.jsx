import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  InputNumber,
  Row,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import { FireOutlined, ThunderboltOutlined } from "@ant-design/icons";
import {
  createTask,
  listAgents,
  listTaskKinds,
  listTopProcesses,
} from "../api/client";
import { collectorMeta } from "../utils/collectors";

export default function TaskCreatePanel({ onTaskCreated }) {
  const [agents, setAgents] = useState([]);
  const [taskKinds, setTaskKinds] = useState([]);
  const [agentId, setAgentId] = useState("");
  const [collector, setCollector] = useState("perf_cpu");
  const [pid, setPid] = useState(null);
  const [duration, setDuration] = useState(collectorMeta("perf_cpu").defaultDuration);
  const [processes, setProcesses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [processLoading, setProcessLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const selectedAgent = agents.find((item) => item.id === agentId);
  const kindByID = useMemo(
    () => Object.fromEntries(taskKinds.map((item) => [item.name || item.id, item])),
    [taskKinds],
  );
  const availableKinds = useMemo(() => {
    const capabilities = new Set(selectedAgent?.capabilities || []);
    return taskKinds.filter((item) => capabilities.has(item.name || item.id));
  }, [selectedAgent, taskKinds]);
  const kind = kindByID[collector];
  const meta = {
    ...collectorMeta(collector),
    label: kind?.display_name || kind?.label || collectorMeta(collector).label,
    resultLabel: kind?.result_label || collectorMeta(collector).resultLabel,
    description: kind?.description || collectorMeta(collector).description,
    defaultDuration: kind?.default_duration_seconds ?? kind?.default_duration_sec ?? collectorMeta(collector).defaultDuration,
    defaultSampleRate: kind?.default_sample_rate ?? collectorMeta(collector).defaultSampleRate,
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([listAgents(), listTaskKinds()])
      .then(([agentRows, kindRows]) => {
        if (cancelled) return;
        setAgents(agentRows || []);
        setTaskKinds(kindRows || []);
        const first = (agentRows || []).find((item) => item.status === "ONLINE") || agentRows?.[0];
        setAgentId(first?.id || "");
      })
      .catch((reason) => !cancelled && setError(reason.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!agentId) {
      setProcesses([]);
      return;
    }
    setPid(null);
    setProcessLoading(true);
    listTopProcesses(agentId, 30)
      .then((items) => setProcesses(items || []))
      .catch(() => setProcesses([]))
      .finally(() => setProcessLoading(false));
  }, [agentId]);

  useEffect(() => {
    if (!selectedAgent || availableKinds.length === 0) return;
    if (availableKinds.some((item) => (item.name || item.id) === collector)) return;
    const fallback = availableKinds[0];
    const fallbackID = fallback.name || fallback.id;
    setCollector(fallbackID);
    setDuration(fallback.default_duration_seconds ?? fallback.default_duration_sec ?? 30);
  }, [availableKinds, collector, selectedAgent]);

  function changeCollector(value) {
    const next = kindByID[value];
    setCollector(value);
    setDuration(next?.default_duration_seconds ?? next?.default_duration_sec ?? collectorMeta(value).defaultDuration);
  }

  async function submit() {
    if (!agentId || !pid) {
      setError("请选择在线 Agent 和目标进程");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const result = await createTask({
        name: `${meta.label}: PID ${pid}`,
        agent_id: agentId,
        target_pid: pid,
        collector_type: collector,
        sample_rate: meta.defaultSampleRate,
        duration_sec: duration,
        options: { source: "web_task_panel" },
      });
      message.success(`任务已创建，完成后展示 ${meta.resultLabel}`);
      onTaskCreated?.(result.task_id);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card
      title={<Space><ThunderboltOutlined /><Typography.Text strong>新建性能采集</Typography.Text><Tag color="blue">Go API</Tag></Space>}
      style={{ marginBottom: 16 }}
      loading={loading}
    >
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
      <Alert
        type={meta.flamegraph ? "success" : "info"}
        showIcon
        message={`完成后展示：${meta.resultLabel}`}
        description="这里只创建采集 Task；需要根因判断时，请把产物交给 AI 诊断工作台完成证据门禁。"
        style={{ marginBottom: 12 }}
      />
      <Row gutter={[12, 12]} align="bottom">
        <Col xs={24} md={12} lg={6}>
          <Typography.Text type="secondary">目标 Agent</Typography.Text>
          <Select
            value={agentId || undefined}
            onChange={setAgentId}
            style={{ width: "100%", marginTop: 4 }}
            options={agents.map((item) => ({
              value: item.id,
              label: `${item.hostname || item.id} · ${item.status}`,
              disabled: item.status !== "ONLINE",
            }))}
          />
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Typography.Text type="secondary">可信进程快照</Typography.Text>
          <Select
            showSearch
            loading={processLoading}
            value={pid || undefined}
            onChange={setPid}
            style={{ width: "100%", marginTop: 4 }}
            optionFilterProp="label"
            options={processes.map((item) => ({
              value: item.pid,
              label: `${item.pid} · ${item.comm || item.service_hint || "unknown"}`,
            }))}
          />
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Typography.Text type="secondary">采集器</Typography.Text>
          <Select
            value={collector}
            onChange={changeCollector}
            style={{ width: "100%", marginTop: 4 }}
            options={availableKinds.map((item) => ({
              value: item.name || item.id,
              label: item.display_name || item.label || item.name || item.id,
            }))}
          />
        </Col>
        <Col xs={12} md={6} lg={3}>
          <Typography.Text type="secondary">时长（秒）</Typography.Text>
          <InputNumber min={1} value={duration} onChange={setDuration} style={{ width: "100%", marginTop: 4 }} />
        </Col>
        <Col xs={12} md={6} lg={3}>
          <Button type="primary" block icon={<FireOutlined />} loading={submitting} onClick={submit}>开始采集</Button>
        </Col>
      </Row>
    </Card>
  );
}
