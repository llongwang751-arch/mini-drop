import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import {
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  Row,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  CloudServerOutlined,
  ReloadOutlined,
  ApiOutlined,
  HddOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useParams, useNavigate } from "react-router-dom";
import { listAgents, listTasks } from "../api/client";
import StatusTag from "../components/StatusTag";
import ErrorAlert from "../components/ErrorAlert";
import { COLORS, FONT_SIZES, SPACING } from "../theme";
import usePolling from "../hooks/usePolling";
import echarts from "../lib/echarts";
import { appendMetricSample, agentMetric } from "../utils/agentMetrics";

export default function AgentDetail() {
  const { agentId } = useParams();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [agent, setAgent] = useState(null);
  const [agentTasks, setAgentTasks] = useState([]);
  const [taskSearch, setTaskSearch] = useState("");
  const [cpuHistory, setCpuHistory] = useState([]);
  const [rssHistory, setRssHistory] = useState([]);
  const chartRef = useRef(null);
  const chartInst = useRef(null);

  const requestGeneration = useRef(0);
  const mounted = useRef(false);
  const currentAgentId = useRef(agentId);
  currentAgentId.current = agentId;

  const load = useCallback(async () => {
    const generation = ++requestGeneration.current;
    const isCurrent = () => mounted.current && currentAgentId.current === agentId && generation === requestGeneration.current;
    setError("");
    try {
      const [agents, tasks] = await Promise.all([listAgents(), listTasks()]);
      if (!isCurrent()) return;
      const found = agents.find((a) => a.id === agentId);
      setAgent(found || null);
      setAgentTasks((tasks || [])
        .filter((t) => t.agent_id === agentId)
        .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)));
      const metrics = found?.latest_metrics;
      setCpuHistory((prev) => appendMetricSample(prev, metrics?.sampled_at_unix_ms, metrics?.self?.cpu_percent));
      setRssHistory((prev) => appendMetricSample(prev, metrics?.sampled_at_unix_ms, metrics?.self?.rss_mb));
    } catch (err) {
      if (isCurrent()) setError(err.message || "读取 Agent 失败");
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    mounted.current = true;
    setLoading(true);
    setAgent(null);
    setAgentTasks([]);
    setCpuHistory([]);
    setRssHistory([]);
    setTaskSearch("");
    load();
    return () => { mounted.current = false; ++requestGeneration.current; };
  }, [load]);

  // Keep checking offline agents so recovery becomes visible without a manual reload.
  usePolling(load, { interval: 10000 });

  // ── 渲染 ECharts 指标折线图 ──────────────────────────
  useEffect(() => {
    if (!chartRef.current || cpuHistory.length < 2) return;

    // 先销毁旧实例
    if (chartInst.current) {
      chartInst.current.dispose();
      chartInst.current = null;
    }

    const inst = echarts.init(chartRef.current);
    chartInst.current = inst;

    const cpuData = cpuHistory.map((p) => [
      p.ts,
      p.value,
    ]);
    const rssData = rssHistory.map((p) => [
      p.ts,
      p.value,
    ]);

    inst.setOption({
        animation: false,
        tooltip: { trigger: "axis" },
        legend: { data: ["CPU %", "RSS MB"], bottom: 0 },
        grid: { left: 50, right: 45, top: 20, bottom: 55 },
        xAxis: { type: "time", splitNumber: 4, axisLabel: { hideOverlap: true, formatter: (ts) => new Date(ts).toLocaleTimeString() } },
        yAxis: [
          { type: "value", name: "CPU %", min: 0 },
          { type: "value", name: "MB" },
        ],
        series: [
          {
            name: "CPU %",
            type: "line",
            data: cpuData,
            smooth: true,
            areaStyle: { opacity: 0.15 },
            itemStyle: { color: COLORS.primary },
          },
          {
            name: "RSS MB",
            type: "line",
            yAxisIndex: 1,
            data: rssData,
            smooth: true,
            areaStyle: { opacity: 0.1 },
            itemStyle: { color: COLORS.success },
          },
        ],
    });

    const onResize = () => inst.resize();
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      inst.dispose();
      if (chartInst.current === inst) chartInst.current = null;
    };
  }, [cpuHistory, rssHistory]);

  // 任务搜索过滤
  const filteredTasks = useMemo(() => {
    if (!taskSearch.trim()) return agentTasks;
    const q = taskSearch.toLowerCase();
    return agentTasks.filter(
      (t) =>
        (t.name || "").toLowerCase().includes(q) ||
        (t.id || "").toLowerCase().includes(q) ||
        (t.collector_type || "").toLowerCase().includes(q)
    );
  }, [agentTasks, taskSearch]);

  const taskColumns = [
    {
      title: "任务",
      dataIndex: "name",
      ellipsis: true,
      render: (value, record) => (
        <Typography.Link
          onClick={() => navigate(`/task/${record.id}`)}
          style={{ cursor: "pointer" }}
        >
          {value || record.id}
        </Typography.Link>
      ),
    },
    {
      title: "采集器",
      dataIndex: "collector_type",
      width: 130,
      render: (v) => <Tag>{v}</Tag>,
    },
    {
      title: "PID",
      dataIndex: "target_pid",
      width: 80,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (v) => <StatusTag status={v} />,
    },
    {
      title: "时间",
      dataIndex: "created_at",
      width: 180,
      render: (v) => (v ? new Date(v).toLocaleString() : "-"),
    },
  ];

  if (loading) {
    return (
      <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
        <Skeleton.Input active size="small" style={{ width: 200 }} />
        <Row gutter={SPACING.lg}>
          <Col xs={24} lg={12}>
            <Card size="small">
              <Skeleton active paragraph={{ rows: 6 }} />
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card size="small">
              <Skeleton.Input active block style={{ height: 240, borderRadius: 8 }} />
            </Card>
          </Col>
        </Row>
        <Card size="small">
          <Skeleton active paragraph={{ rows: 5 }} />
        </Card>
      </Space>
    );
  }

  if (!agent) {
    return (
      <Space direction="vertical" style={{ width: "100%" }}>
      <ErrorAlert error={error} onClose={() => setError("")} />
      <Empty
        description={error ? "暂时无法读取 Agent 信息" : `Agent "${agentId}" 未找到`}
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      >
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/tasks")}>
          返回任务面板
        </Button>
        <Button onClick={load}>重试</Button>
      </Empty>
      </Space>
    );
  }

  return (
    <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
      {/* 页头 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <Space align="center">
          <Button
            icon={<ArrowLeftOutlined />}
            type="text"
            onClick={() => navigate("/tasks")}
          >
            返回
          </Button>
          <CloudServerOutlined
            style={{ fontSize: 20, color: agent.status === "ONLINE" ? COLORS.success : COLORS.offline }}
          />
          <Typography.Title level={4} style={{ margin: 0 }}>
            {agent.hostname || agent.id}
          </Typography.Title>
          <StatusTag status={agent.status} />
        </Space>
        <Button icon={<ReloadOutlined />} onClick={load}>
          刷新
        </Button>
      </div>

      <ErrorAlert error={error} onClose={() => setError("")} />

      {/* Agent 详情 + 指标 */}
      <Row gutter={SPACING.lg}>
        <Col xs={24} lg={12}>
          <Card
            title={
              <Space>
                <HddOutlined style={{ color: COLORS.primary }} />
                Agent 详细信息
              </Space>
            }
            size="small"
          >
            <Descriptions column={1} size="small" bordered labelStyle={{ width: 140 }} contentStyle={{ overflowWrap: "anywhere" }}>
              <Descriptions.Item label="Agent ID">
                <Typography.Text copyable style={{ fontSize: FONT_SIZES.sm }}>
                  {agent.id}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <StatusTag status={agent.status} />
              </Descriptions.Item>
              <Descriptions.Item label="主机名（Hostname）">{agent.hostname}</Descriptions.Item>
              <Descriptions.Item label="IP">{agent.ip_addr}</Descriptions.Item>
              <Descriptions.Item label="版本">{agent.version || "未上报"}</Descriptions.Item>
              <Descriptions.Item label="OS">{agent.os_info || "unknown"}</Descriptions.Item>
              <Descriptions.Item label="最后心跳" span={2}>
                {agent.last_heartbeat_at
                  ? new Date(agent.last_heartbeat_at).toLocaleString()
                  : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="注册时间" span={2}>
                {agent.created_at
                  ? new Date(agent.created_at).toLocaleString()
                  : "-"}
              </Descriptions.Item>
            </Descriptions>

            {/* 能力标签 */}
            {agent.capabilities?.length > 0 && (
              <div style={{ marginTop: SPACING.md }}>
                <Typography.Text type="secondary" style={{ fontSize: FONT_SIZES.sm }}>
                  采集能力：
                </Typography.Text>
                <Space size={[4, 4]} wrap style={{ marginTop: 4 }}>
                  {(agent.capabilities || []).map((cap) => (
                    <Tag key={cap} color="blue" style={{ fontSize: 11 }}>
                      {cap}
                    </Tag>
                  ))}
                </Space>
              </div>
            )}

            {/* 实时资源 */}
            {agent.latest_metrics?.self && (
              <div style={{ marginTop: SPACING.md }}>
                <Typography.Text type="secondary" style={{ fontSize: FONT_SIZES.sm }}>
                  Agent 自身开销（不是整机或业务进程）：
                </Typography.Text>
                <Space size={SPACING.sm} wrap style={{ marginTop: 4 }}>
                  <Tag color="blue">
                    CPU {agentMetric(agent.latest_metrics.self.cpu_percent, "%")}
                  </Tag>
                  <Tag color="green">
                    RSS {agentMetric(agent.latest_metrics.self.rss_mb, " MB")}
                  </Tag>
                  <Tag>
                    IO R/W {agentMetric(agent.latest_metrics.self.read_kb_s)}/{agentMetric(agent.latest_metrics.self.write_kb_s)} KB/s
                  </Tag>
                  <Tag>
                    子进程 {agentMetric(agent.latest_metrics.self.children_count, "", 0)}
                  </Tag>
                </Space>
              </div>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card
            title={
              <Space>
                <ApiOutlined style={{ color: COLORS.warning }} />
                Agent 自身开销趋势
                {cpuHistory.length > 0 && (
                  <Tag style={{ fontSize: 10 }}>
                    过去 {cpuHistory.length} 个采样点
                  </Tag>
                )}
              </Space>
            }
            size="small"
          >
            {cpuHistory.length >= 2 ? (
              <div ref={chartRef} style={{ width: "100%", height: 260 }} />
            ) : (
              <Empty
                description="等待至少两个不同时间的心跳样本…"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}
          </Card>
        </Col>
      </Row>

      {/* 关联任务 */}
      <Card
        title={
          <Space>
            历史任务
            <Tag>{filteredTasks.length}</Tag>
          </Space>
        }
        size="small"
        extra={
          <Input
            size="small"
            style={{ width: 200 }}
            placeholder="搜索任务…"
            prefix={<SearchOutlined />}
            allowClear
            value={taskSearch}
            onChange={(e) => setTaskSearch(e.target.value)}
          />
        }
      >
        <Table
          rowKey="id"
          columns={taskColumns}
          dataSource={filteredTasks}
          pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
          size="middle"
          scroll={{ x: 700 }}
          locale={{ emptyText: taskSearch ? "无匹配任务" : "该 Agent 暂无任务记录" }}
        />
      </Card>
    </Space>
  );
}
