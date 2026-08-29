import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  AimOutlined,
  FireOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  nlpParse,
  createTask,
  listAgents,
  listTaskKinds,
  listTopProcesses,
} from "../api/client";
import { collectorMeta } from "../utils/collectors";

export default function NLPTaskInput({ onTaskCreated }) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [agents, setAgents] = useState([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [taskKinds, setTaskKinds] = useState([]);
  const [quickCollector, setQuickCollector] = useState("perf_cpu");
  const [quickAgentId, setQuickAgentId] = useState("");
  const [quickPid, setQuickPid] = useState(null);
  const [topProcesses, setTopProcesses] = useState([]);
  const [topProcessesLoading, setTopProcessesLoading] = useState(false);
  const [quickDuration, setQuickDuration] = useState(
    collectorMeta("perf_cpu").defaultDuration,
  );

  const onlineAgents = useMemo(
    () => agents.filter((agent) => agent.status === "ONLINE"),
    [agents],
  );

  const taskKindById = useMemo(
    () => Object.fromEntries(taskKinds.map((item) => [item.id, item])),
    [taskKinds],
  );

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === quickAgentId),
    [agents, quickAgentId],
  );

  const availableTaskKinds = useMemo(() => {
    if (!selectedAgent) return [];
    const capabilities = new Set(selectedAgent.capabilities || []);
    return taskKinds.filter((item) => capabilities.has(item.id));
  }, [selectedAgent, taskKinds]);

  const nlpProcessOptions = useMemo(() => {
    if (!result) return [];
    const needle = String(result.process_name || "")
      .trim()
      .toLowerCase();
    const matched = topProcesses.filter((candidate) => {
      if (!needle || needle === "unknown" || needle.startsWith("pid ")) {
        return false;
      }
      return [
        candidate.comm,
        candidate.service_hint,
        candidate.instance_hint,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
    return matched.length > 0 ? matched : topProcesses;
  }, [result, topProcesses]);

  function taskKindMeta(id) {
    const item = taskKindById[id];
    if (!item) return collectorMeta(id);
    return {
      ...collectorMeta(id),
      label: item.label,
      resultLabel: item.result_label,
      description: item.description,
      color: item.color,
      defaultDuration: item.default_duration_sec,
      maxDuration: item.max_duration_sec,
      defaultSampleRate: item.default_sample_rate,
      flamegraph: item.flamegraph,
    };
  }

  function selectCapableAgent(collectorType, items = agents) {
    const online = items.filter((agent) => agent.status === "ONLINE");
    const candidates = online.length > 0 ? online : items;
    return (
      candidates.find((agent) =>
        (agent.capabilities || []).includes(collectorType),
      ) || candidates[0]
    );
  }

  async function loadTopProcesses(agentId) {
    if (!agentId) {
      setTopProcesses([]);
      return;
    }
    setTopProcessesLoading(true);
    try {
      const items = await listTopProcesses(agentId, 20);
      setTopProcesses(items || []);
    } catch {
      setTopProcesses([]);
    } finally {
      setTopProcessesLoading(false);
    }
  }

  async function loadAgents() {
    setAgentsLoading(true);
    try {
      const items = await listAgents();
      setAgents(items || []);
      const preferred = selectCapableAgent(quickCollector, items || []);
      setQuickAgentId((current) =>
        (items || []).some((agent) => agent.id === current)
          ? current
          : preferred?.id || "",
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setAgentsLoading(false);
    }
  }

  async function loadTaskKinds() {
    try {
      const items = await listTaskKinds();
      setTaskKinds(items || []);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    loadAgents();
    loadTaskKinds();
  }, []);

  useEffect(() => {
    setQuickPid(null);
    setResult((current) =>
      current ? { ...current, selected_pid: null } : current,
    );
    loadTopProcesses(quickAgentId);
  }, [quickAgentId]);

  useEffect(() => {
    if (!selectedAgent || taskKinds.length === 0) return;
    if (availableTaskKinds.some((item) => item.id === quickCollector)) return;
    const fallback = availableTaskKinds[0];
    if (!fallback) return;
    setQuickCollector(fallback.id);
    setQuickDuration(fallback.default_duration_sec);
  }, [availableTaskKinds, quickCollector, selectedAgent, taskKinds.length]);

  function changeQuickCollector(value) {
    const meta = taskKindMeta(value);
    setQuickCollector(value);
    setQuickDuration(meta.defaultDuration);
  }

  async function handleQuickCreate() {
    if (!quickAgentId) {
      setError("请选择一个在线 Agent");
      return;
    }
    if (!quickPid || quickPid <= 0) {
      setError("请输入有效的目标 PID");
      return;
    }
    if (!quickDuration || quickDuration <= 0) {
      setError("请输入有效的采样时长");
      return;
    }
    const agent = agents.find((item) => item.id === quickAgentId);
    if (!(agent?.capabilities || []).includes(quickCollector)) {
      setError(
        `Agent ${quickAgentId} 不支持 ${taskKindMeta(quickCollector).label}`,
      );
      return;
    }

    const meta = taskKindMeta(quickCollector);
    setSubmitting(true);
    setError("");
    try {
      const taskResp = await createTask({
        name: `${meta.label}: PID ${quickPid}`,
        agent_id: quickAgentId,
        target_pid: quickPid,
        collector_type: quickCollector,
        sample_rate: meta.defaultSampleRate,
        duration_sec: quickDuration,
        options: { source: "web_quick_preset" },
      });
      message.success(`任务已创建，正在打开 ${meta.resultLabel}`);
      onTaskCreated?.(taskResp.task_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleParse() {
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await nlpParse(query.trim());
      const capableAgent = (selectedAgent?.capabilities || []).includes(
        data.collector_type,
      )
        ? selectedAgent
        : selectCapableAgent(data.collector_type);
      if (capableAgent?.id && capableAgent.id !== quickAgentId) {
        setQuickAgentId(capableAgent.id);
      }
      setResult({
        ...data,
        selected_pid: null,
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    if (!result) return;
    const pid = result.selected_pid;
    if (!pid) {
      setError("请从所选 Agent 的可信进程列表中选择目标 PID");
      return;
    }
    const agent = agents.find((item) => item.id === quickAgentId);
    if (!agent || agent.status !== "ONLINE") {
      setError("请选择一个在线 Agent");
      return;
    }
    if (!(agent.capabilities || []).includes(result.collector_type)) {
      setError(`当前 Agent 不支持 ${taskKindMeta(result.collector_type).label}`);
      return;
    }
    if (!topProcesses.some((candidate) => candidate.pid === pid)) {
      setError("目标 PID 已不在 Agent 的最新可信快照中，请刷新后重选");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const taskResp = await createTask({
        name: `NLP: ${result.process_name}`,
        agent_id: agent.id,
        target_pid: pid,
        collector_type: result.collector_type,
        sample_rate: result.sample_rate,
        duration_sec: result.duration_sec,
        options: { nlp_query: query.trim() },
      });
      setResult(null);
      setQuery("");
      message.success(`任务已创建 → Agent: ${agent.id}`);
      onTaskCreated?.(taskResp.task_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card
      title={
        <Space>
          <ThunderboltOutlined style={{ color: "#faad14" }} />
          <Typography.Text strong>新建性能采集</Typography.Text>
          <Tag color="blue">可视化</Tag>
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
      {error && (
        <Alert
          type="error"
          message={error}
          showIcon
          style={{ marginTop: 12 }}
        />
      )}

      <Tabs
        defaultActiveKey="quick"
        items={[
          {
            key: "quick",
            label: (
              <Space>
                <FireOutlined />
                快速可视化
              </Space>
            ),
            children: (
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <Alert
                  type={
                    taskKindMeta(quickCollector).flamegraph ? "success" : "info"
                  }
                  showIcon
                  message={`完成后展示：${taskKindMeta(quickCollector).resultLabel}`}
                  description={taskKindMeta(quickCollector).description}
                />
                <Row gutter={[12, 12]}>
                  <Col xs={24} md={12} lg={7}>
                    <Typography.Text type="secondary">
                      目标 Agent
                    </Typography.Text>
                    <Select
                      value={quickAgentId || undefined}
                      loading={agentsLoading}
                      placeholder="先选择在线 Agent"
                      style={{ width: "100%", marginTop: 4 }}
                      onChange={setQuickAgentId}
                      options={agents.map((agent) => ({
                        value: agent.id,
                        label: `${agent.hostname || agent.id} · ${agent.status}`,
                        disabled: agent.status !== "ONLINE",
                      }))}
                    />
                  </Col>
                  <Col xs={24} lg={9}>
                    <Typography.Text type="secondary">采集预设</Typography.Text>
                    <Select
                      value={quickCollector}
                      loading={taskKinds.length === 0}
                      disabled={!selectedAgent}
                      placeholder="由 Agent capability 决定"
                      options={availableTaskKinds.map((item) => ({
                        value: item.id,
                        label: `${item.label} · ${item.result_label}`,
                      }))}
                      onChange={changeQuickCollector}
                      style={{ width: "100%", marginTop: 4 }}
                    />
                  </Col>
                  <Col xs={24} md={6} lg={4}>
                    <Typography.Text type="secondary">目标 PID</Typography.Text>
                    <Select
                      showSearch
                      allowClear
                      loading={topProcessesLoading}
                      value={quickPid}
                      onChange={setQuickPid}
                      placeholder="选忙进程或输入"
                      optionFilterProp="label"
                      style={{ width: "100%", marginTop: 4 }}
                      options={topProcesses.map((p) => ({
                        value: p.pid,
                        label: `${p.pid} · ${p.service_hint || p.comm || "未命名进程"}`,
                      }))}
                    />
                    <Typography.Text
                      type="secondary"
                      style={{ fontSize: 11, display: "block", marginTop: 4 }}
                    >
                      {quickCollector === "go_pprof"
                        ? "go_pprof 自动采样 Go 服务的 /debug/pprof，无需忙 PID"
                        : "进程列表来自所选 Agent 的最新可信心跳快照；请选择目标服务对应 PID"}
                    </Typography.Text>
                  </Col>
                  <Col xs={12} md={6} lg={4}>
                    <Typography.Text type="secondary">
                      采样时长（秒）
                    </Typography.Text>
                    <InputNumber
                      min={1}
                      max={taskKindMeta(quickCollector).maxDuration || 300}
                      value={quickDuration}
                      onChange={setQuickDuration}
                      style={{ width: "100%", marginTop: 4 }}
                    />
                  </Col>
                </Row>
                <Space wrap>
                  <Button
                    type="primary"
                    icon={<AimOutlined />}
                    loading={submitting}
                    onClick={handleQuickCreate}
                  >
                    创建并查看结果
                  </Button>
                  <Typography.Text type="secondary">
                    {onlineAgents.length} 个 Agent
                    在线；选择 Agent 后只显示其实际支持的采集器
                  </Typography.Text>
                </Space>
              </Space>
            ),
          },
          {
            key: "nlp",
            label: (
              <Space>
                <ThunderboltOutlined />
                自然语言
              </Space>
            ),
            children: (
              <>
                <Input.Search
                  placeholder="描述性能问题，例如：mysqld CPU 飙高，帮我看看"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onSearch={handleParse}
                  loading={loading}
                  enterButton="解析意图"
                  size="large"
                  maxLength={200}
                />
                {result && (
                  <Card
                    size="small"
                    style={{ marginTop: 12, background: "#fafafa" }}
                    title="确认采集参数"
                    extra={
                      <Button
                        type="primary"
                        size="small"
                        loading={submitting}
                        onClick={handleCreate}
                      >
                        确认创建并查看
                      </Button>
                    }
                  >
                    <Descriptions column={2} size="small">
                      <Descriptions.Item label="目标 Agent">
                        <Select
                          size="small"
                          value={quickAgentId || undefined}
                          placeholder="选择支持该采集器的 Agent"
                          style={{ width: 260 }}
                          onChange={setQuickAgentId}
                          options={agents.map((agent) => ({
                            value: agent.id,
                            label: `${agent.hostname || agent.id} · ${agent.status}`,
                            disabled:
                              agent.status !== "ONLINE" ||
                              !(agent.capabilities || []).includes(
                                result.collector_type,
                              ),
                          }))}
                        />
                      </Descriptions.Item>
                      <Descriptions.Item label="采集器">
                        <Tag color={taskKindMeta(result.collector_type).color}>
                          {taskKindMeta(result.collector_type).label}
                        </Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="预期结果">
                        {taskKindMeta(result.collector_type).resultLabel}
                      </Descriptions.Item>
                      <Descriptions.Item label="意图中的进程">
                        {result.process_name}
                      </Descriptions.Item>
                      <Descriptions.Item label="采样时长">
                        {result.duration_sec}s
                      </Descriptions.Item>
                      <Descriptions.Item label="采样率">
                        {result.sample_rate} Hz
                      </Descriptions.Item>
                      <Descriptions.Item label="Agent 可信进程">
                        <Select
                          showSearch
                          size="small"
                          loading={topProcessesLoading}
                          value={result.selected_pid || undefined}
                          placeholder="从最新心跳快照中选择"
                          optionFilterProp="label"
                          style={{ width: 320 }}
                          onChange={(value) =>
                            setResult({ ...result, selected_pid: value })
                          }
                          options={nlpProcessOptions.map((candidate) => ({
                            value: candidate.pid,
                            label: `${candidate.pid} · ${candidate.service_hint || candidate.comm || "未命名进程"}`,
                          }))}
                        />
                      </Descriptions.Item>
                    </Descriptions>
                    {nlpProcessOptions.length === 0 && !topProcessesLoading && (
                      <Alert
                        type="warning"
                        showIcon
                        message="所选 Agent 暂无可信进程候选，请等待下一次心跳或切换 Agent"
                        style={{ marginTop: 8 }}
                      />
                    )}
                    <Typography.Paragraph
                      type="secondary"
                      style={{ margin: "8px 0 0", fontSize: 12 }}
                    >
                      {result.reasoning}。目标 PID 只从所选 Agent 的最新可信快照中确认，
                      不使用 Analysis Engine 容器里的本地进程列表。
                    </Typography.Paragraph>
                  </Card>
                )}
              </>
            ),
          },
        ]}
      />
    </Card>
  );
}
