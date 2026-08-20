import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Progress,
  Row,
  Space,
  Statistic,
  Steps,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from "antd";
import { ExperimentOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  getDiagnosisCampaign,
  listDiagnosisCampaignScenarios,
  startDiagnosisCampaign,
} from "../api/client";

const { Text, Paragraph } = Typography;

const STAGE_ORDER = [
  "PRECHECK_PASSED",
  "BASELINE_CAPTURED",
  "FAULT_INJECTED",
  "FAULT_CONFIRMED",
  "TASK_LINKED",
  "DIAGNOSIS_COMPLETED",
  "ORACLE_COMPARED",
  "RECOVERY_VERIFIED",
];

const STEP_ITEMS = [
  { title: "安全预检", description: "目标健康、故障开关可回滚" },
  { title: "基线快照", description: "先记录无故障指标" },
  { title: "真实注入", description: "启动白名单受控故障" },
  { title: "异常确认", description: "确认指标相对基线变化" },
  { title: "任务取证", description: "下发 sys_metrics 任务" },
  { title: "循证诊断", description: "决策树、证据引用与置信度" },
  { title: "Oracle 对比", description: "诊断后才读取标准答案" },
  { title: "恢复验证", description: "finally 清理并保存恢复快照" },
];

function stageIndex(stage) {
  if (stage === "COMPLETED") return STEP_ITEMS.length;
  const index = STAGE_ORDER.indexOf(stage);
  return index < 0 ? 0 : index;
}

function WrappedStatistic({ title, value }) {
  return (
    <Statistic
      title={title}
      value={value || "-"}
      valueStyle={{
        fontSize: 20,
        lineHeight: 1.35,
        whiteSpace: "normal",
        overflowWrap: "anywhere",
        wordBreak: "break-word",
      }}
    />
  );
}

function SnapshotTable({ snapshots }) {
  const rows = ["baseline_snapshot", "fault_snapshot", "recovery_snapshot"]
    .map((role) => snapshots?.[role])
    .filter(Boolean);
  const metricDefinitions = [
    { title: "CPU", dataIndex: "process_cpu_percent", render: (value) => `${value ?? "-"}%` },
    { title: "RSS", dataIndex: "process_rss_mb", render: (value) => `${value ?? "-"} MB` },
    { title: "保留内存", dataIndex: "retained_memory_mb", render: (value) => `${value ?? "-"} MB` },
    { title: "进程写入", dataIndex: "process_write_bytes", render: (value) => value == null ? "-" : `${(value / 1024 / 1024).toFixed(1)} MB` },
    { title: "依赖延迟", dataIndex: "upstream_latency_ms", render: (value) => value == null ? "-" : `${value} ms` },
    { title: "下游注入", dataIndex: "downstream_delay_ms", render: (value) => value == null ? "-" : `${value} ms` },
    { title: "网络注入", dataIndex: "network_delay_ms", render: (value) => value == null ? "-" : `${value} ms` },
    { title: "GC 暂停", dataIndex: "gc_collection_time_ms", render: (value) => value == null ? "-" : `${value} ms` },
    { title: "Java 堆", dataIndex: "heap_used_mb", render: (value) => value == null ? "-" : `${value} MB` },
    { title: "热点函数", dataIndex: "hot_function", render: (value) => value || "-" },
    { title: "源码位置", key: "source", render: (_, row) => row.source_file ? `${row.source_file}:${row.source_line}` : "-" },
    { title: "栈样本", dataIndex: "hot_function_samples", render: (value) => value ?? "-" },
    { title: "Peer CPU", dataIndex: "peer_cpu_ticks", render: (value) => value ?? "-" },
    { title: "流量到达", dataIndex: "load_offered_rps", render: (value) => value == null ? "-" : `${value} rps` },
    { title: "完成吞吐", dataIndex: "load_completed_rps", render: (value) => value == null ? "-" : `${value} rps` },
    { title: "Queue lag", dataIndex: "queue_lag", render: (value) => value ?? "-" },
    { title: "消费速率", dataIndex: "consumer_rate", render: (value) => value == null ? "-" : `${value} rps` },
  ];
  // 每个故障只显示实际存在的关键指标，避免 20 多列把页面撑成横向长表。
  const visibleMetrics = metricDefinitions
    .filter((column) => column.key === "source"
      ? rows.some((row) => row.source_file)
      : rows.some((row) => row[column.dataIndex] != null))
    .slice(0, 7);
  const faultColumn = {
    title: "故障开关",
    key: "fault",
    render: (_, row) => {
      const value = row.queue_fault_active ?? row.load_fault_active ?? row.noisy_neighbor_active ?? row.source_fault_active ?? row.gc_fault_active ?? row.network_fault_active ?? row.downstream_fault_active ?? row.io_fault_active ?? row.memory_fault_active ?? row.fault_active;
      return <Tag color={value ? "error" : "success"}>{value ? "ON" : "OFF"}</Tag>;
    },
  };
  return (
    <Table
      size="small"
      pagination={false}
      rowKey="snapshot_id"
      dataSource={rows}
      scroll={{ x: 760 }}
      columns={[
        { title: "证据角色", dataIndex: "role", fixed: "left", width: 110, render: (value) => <Tag>{value}</Tag> },
        ...visibleMetrics,
        faultColumn,
      ]}
    />
  );
}

export default function CampaignPanel() {
  const [scenarios, setScenarios] = useState([]);
  const [selectedScenario, setSelectedScenario] = useState("LIVE-CPU-001");
  const [run, setRun] = useState(null);
  const [starting, setStarting] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    listDiagnosisCampaignScenarios().then(setScenarios).catch((error) => message.error(error.message));
    return () => window.clearTimeout(timerRef.current);
  }, []);

  const poll = useCallback(async (runId) => {
    try {
      const current = await getDiagnosisCampaign(runId);
      setRun(current);
      if (current.status === "RUNNING") {
        timerRef.current = window.setTimeout(() => poll(runId), 500);
      } else if (current.status === "COMPLETED") {
        message.success("真实故障 Campaign 已完成，故障已清理");
      } else {
        message.error(current.error || "Campaign 执行失败");
      }
    } catch (error) {
      message.error(error.message);
    }
  }, []);

  async function start() {
    setStarting(true);
    setRun(null);
    window.clearTimeout(timerRef.current);
    try {
      const created = await startDiagnosisCampaign(selectedScenario);
      setRun(created);
      poll(created.run_id);
    } catch (error) {
      message.error(error.message);
    } finally {
      setStarting(false);
    }
  }

  const timeline = useMemo(() => [...(run?.events || [])].reverse(), [run?.events]);
  const diagnosis = run?.diagnosis;
  const comparison = run?.comparison;
  const linkedTask = run?.linked_task;
  const runMessage = run?.status === "FAILED" && /resolve|NameResolution|ConnectionPool/i.test(run?.message || run?.error || "")
    ? "实验依赖服务未启动或不在同一 Docker 网络；请重建云端完整编排后重试"
    : (run?.message || run?.error || "正在准备实验");

  return (
    <Card
      title={<Space><ExperimentOutlined />真实故障 Campaign（先制造故障，再评测）</Space>}
      extra={(
        <Button type="primary" icon={run ? <ReloadOutlined /> : <ExperimentOutlined />} loading={starting || run?.status === "RUNNING"} onClick={start}>
          {run ? "重新执行实验" : "一键制造故障并评测"}
        </Button>
      )}
    >
      <Alert
        showIcon
        type="warning"
        message="这不是预填答案：系统会控制真实进程、采集三段快照，并在诊断结束后才读取 Oracle"
        description="按钮只调用白名单故障开关，不执行任意命令；无论中途哪一步失败，finally 都会停止故障并验证恢复。"
        style={{ marginBottom: 16 }}
      />
      {!run ? (
        <Row gutter={[16, 16]}>
          {scenarios.map((scenario) => (
            <Col xs={24} lg={12} key={scenario.scenario_id}>
              <Card
                size="small"
                type="inner"
                title={scenario.title}
                hoverable
                onClick={() => setSelectedScenario(scenario.scenario_id)}
                style={{
                  cursor: "pointer",
                  borderColor: selectedScenario === scenario.scenario_id ? "#1677ff" : undefined,
                  boxShadow: selectedScenario === scenario.scenario_id ? "0 0 0 2px rgba(22,119,255,.12)" : undefined,
                }}
              >
                <Paragraph>{scenario.description}</Paragraph>
                <Space wrap>
                  <Tag color="red">{scenario.fault_type}</Tag>
                  <Tag>{scenario.risk_level}</Tag>
                  <Tag color="green">自动清理</Tag>
                  <Tag color="blue">{scenario.benchmark_case_id}</Tag>
                  {selectedScenario === scenario.scenario_id && <Tag color="processing">已选择</Tag>}
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <div>
            <Space style={{ width: "100%", justifyContent: "space-between" }}>
              <Text strong>{runMessage}</Text>
              <Tag color={run.status === "COMPLETED" ? "success" : run.status === "FAILED" ? "error" : "processing"}>{run.status}</Tag>
            </Space>
            <Progress percent={run.progress || 0} status={run.status === "FAILED" ? "exception" : run.status === "RUNNING" ? "active" : "success"} />
          </div>
          <Steps size="small" responsive current={stageIndex(run.stage)} items={STEP_ITEMS} />

          <Row gutter={[16, 16]}>
            <Col xs={24} xl={9}>
              <Card size="small" title="真实执行时间线" style={{ height: "100%" }}>
                <Timeline items={timeline.map((event) => ({
                  color: event.stage === "FAILED" ? "red" : event.stage === "RECOVERY_VERIFIED" ? "green" : "blue",
                  children: <div><Text strong>{event.message}</Text><div><Text type="secondary">{event.stage} · {event.timestamp}</Text></div></div>,
                }))} />
              </Card>
            </Col>
            <Col xs={24} xl={15}>
              <Card size="small" title="基线 / 故障 / 恢复证据快照">
                <SnapshotTable snapshots={run.snapshots} />
              </Card>
              <Card size="small" title="Mini-Drop 真实任务关联" style={{ marginTop: 16 }}>
                {linkedTask ? (
                  <Descriptions size="small" column={{ xs: 1, md: 2 }}>
                    <Descriptions.Item label="任务 ID"><Text code>{linkedTask.task_id}</Text></Descriptions.Item>
                    <Descriptions.Item label="状态"><Tag color={linkedTask.status === "DONE" ? "success" : "warning"}>{linkedTask.status}</Tag></Descriptions.Item>
                    <Descriptions.Item label="Agent">{linkedTask.agent_id}</Descriptions.Item>
                    <Descriptions.Item label="采集器">{linkedTask.collector_type}</Descriptions.Item>
                    <Descriptions.Item label="TaskAttempt">{linkedTask.task_attempts?.[0]?.id || "-"}</Descriptions.Item>
                    <Descriptions.Item label="可信产物">
                      <Tag color={linkedTask.evidence_chain_verified ? "success" : "error"}>
                        {linkedTask.evidence_chain_verified ? "VERIFIED" : "未通过门禁"}
                      </Tag>
                      {(linkedTask.verified_artifact_ids || []).map((id) => <Text code key={id}>Artifact {id}</Text>)}
                    </Descriptions.Item>
                    <Descriptions.Item label="Analyzer Job" span={2}>
                      {(linkedTask.analysis_jobs || []).length
                        ? linkedTask.analysis_jobs.map((job) => <Tag key={job.id}>{job.id} · {job.status}</Tag>)
                        : "无独立 Analyzer Job"}
                    </Descriptions.Item>
                  </Descriptions>
                ) : <Text type="secondary">尚未关联任务；检查 Agent 是否在线且支持 sys_metrics。</Text>}
              </Card>
            </Col>
          </Row>

          {diagnosis && (
            <Card size="small" title="循证 AI 诊断（过程可追溯）">
              <Row gutter={[16, 16]}>
                <Col xs={24} lg={8}><Statistic title="诊断根因" value={diagnosis.root_cause} /></Col>
                <Col xs={12} lg={4}><Statistic title="置信度" value={Math.round((diagnosis.confidence || 0) * 100)} suffix="%" /></Col>
                <Col xs={12} lg={4}><Statistic title="证据数" value={diagnosis.evidence_refs?.length || 0} /></Col>
                <Col xs={24} lg={8}><Text>{diagnosis.recommended_action}</Text></Col>
              </Row>
              <Descriptions size="small" column={1} style={{ marginTop: 12 }}>
                <Descriptions.Item label="推理链">{diagnosis.reasoning.map((item) => <div key={item}>• {item}</div>)}</Descriptions.Item>
                <Descriptions.Item label="证据引用">{diagnosis.evidence_refs.map((item) => <Tag color="blue" key={item}>{item}</Tag>)}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}

          {comparison && (
            <Card size="small" title="隐藏 Oracle 对比与恢复门禁">
              <Row gutter={[16, 12]}>
                <Col xs={24} md={12} style={{ minWidth: 0 }}>
                  <WrappedStatistic title="标准根因" value={comparison.expected_root_cause} />
                </Col>
                <Col xs={24} md={12} style={{ minWidth: 0 }}>
                  <WrappedStatistic title="实际根因" value={comparison.actual_root_cause} />
                </Col>
                <Col xs={8} md={8} style={{ minWidth: 0 }}>
                  <WrappedStatistic title="根因命中" value={comparison.root_cause_match ? "PASS" : "FAIL"} />
                </Col>
                <Col xs={8} md={8} style={{ minWidth: 0 }}>
                  <WrappedStatistic title="证据完整" value={comparison.evidence_complete ? "PASS" : "FAIL"} />
                </Col>
                <Col xs={8} md={8} style={{ minWidth: 0 }}>
                  <WrappedStatistic title="恢复清理" value={run.cleanup?.succeeded ? "PASS" : "FAIL"} />
                </Col>
              </Row>
            </Card>
          )}
        </Space>
      )}
    </Card>
  );
}
