import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Input,
  Modal,
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
import { CloudServerOutlined, ExperimentOutlined, UploadOutlined, DownloadOutlined } from "@ant-design/icons";
import {
  getRealWorldBenchmarkCatalog,
  getRealWorldComparisons,
  getRealWorldBenchmarkRun,
  getRealWorldComparisonInput,
  startRealWorldBenchmark,
  submitRealWorldComparison,
} from "../api/client";

const { Link, Paragraph, Text } = Typography;

const SNAPSHOT_ROLE_LABEL = {
  baseline: "基线",
  incident: "故障",
  verification: "恢复",
};

const STATUS_LABEL = {
  SCORED: "已评分",
  FROZEN: "结果已冻结",
  UNSCORED: "未评分",
  NOT_EXECUTED_IN_THIS_WORKSPACE: "本环境尚未实测",
  EXECUTED_PROVIDER_AUTH_BLOCKED: "已执行，但模型认证失败",
  EXECUTED_CAPABILITY_PASS: "能力对照已通过",
};

const PRODUCT_META = {
  rcaeval: {
    name: "RCAEval 根因评测基准",
    kind: "公开评测基准",
    focus: "比较多服务场景下的根因排序与证据定位",
    relation: "Mini-Drop 多了在线采集、人工审批和恢复复测",
    boundary: "需要额外数据集和较高硬件资源，当前云环境暂未执行。",
  },
  openrca: {
    name: "OpenRCA 开放根因评测",
    kind: "开放评测框架",
    focus: "比较模型对日志、指标和调用链的综合推理",
    relation: "Mini-Drop 强调真实工具执行和证据来源校验",
    boundary: "官方建议较大内存和存储，当前云环境暂未执行。",
  },
  holmesgpt: {
    name: "HolmesGPT 运维诊断智能体",
    kind: "开源运维智能体",
    focus: "比较自然语言调查、工具调用和运维知识推理",
    relation: "Mini-Drop 进一步加入反证门禁、人工纠正和 Skill 回滚",
    boundary: "已按统一输入调用，但当时模型服务认证失败，因此不宣称诊断质量得分。",
  },
  "grafana-pyroscope": {
    name: "Grafana Pyroscope 持续性能剖析",
    kind: "成熟性能产品",
    focus: "比较持续采样、火焰图和热点函数定位",
    relation: "Pyroscope 强在采集展示，Mini-Drop 强在循证根因判断",
    boundary: "已完成同一 CPU 热点工作负载的采集对照；该赛道只比较性能剖析能力。",
  },
};

const TECH_LABEL = {
  top1_root_cause: "首选根因命中",
  source_location: "源码或模块定位",
  evidence_citation: "证据引用",
  three_phase_snapshot: "基线、故障、恢复快照",
  continuous_profiling: "持续性能剖析",
  flamegraph: "火焰图",
  tool_calling: "工具调用",
  abstention: "证据不足时主动停止",
  offline_multi_service_rca: "离线多服务根因分析",
  top_k_accuracy: "根因排序准确率",
  metrics_logs_traces: "指标、日志和调用链",
  llm_tool_use: "大模型工具调用",
  large_telemetry_context: "大规模遥测上下文",
  root_element_localization: "根因对象定位",
  iterative_investigation: "多轮调查",
  kubernetes_and_observability_tools: "容器与可观测工具",
  read_only_governance: "只读安全治理",
  continuous_profiles: "持续性能数据",
  time_window_query: "时间窗口查询",
  flamegraph_exploration: "火焰图探索",
  live_profiling_agent: "在线性能采集节点",
  human_approval_workflow: "人工审批流程",
  perf_ebpf_collection: "perf 与 eBPF 采集",
  process_level_profiler_control_plane: "进程级采集控制面",
  oracle_scored_benchmark: "隐藏标准答案评分",
  ai_root_cause_reasoning: "AI 根因推理",
  fault_injection_campaign: "真实故障注入实验",
};

const TRACK_LABEL = {
  offline_service_rca: "离线服务根因分析",
  offline_llm_tool_rca: "离线 AI 工具诊断",
  live_agent_investigation: "在线智能体调查",
  continuous_profiling_experience: "持续性能剖析体验",
};

const SAME_CONDITION_RESULT = {
  source: "artifacts/comparison-20260824/comparison-summary.json",
  ours: {
    name: "Mini-Drop",
    valid: true,
    completed: 27,
    total: 27,
    top1: 88.89,
    mechanism: 81.48,
    evidence: 100,
    abstention: 81.48,
    revision: 100,
  },
  reference: {
    name: "外部智能体参考实现",
    valid: false,
    completed: 22,
    total: 27,
    top1: 77.78,
    mechanism: 66.67,
    evidence: 81.48,
    abstention: 77.78,
    revision: 62.5,
  },
};

function readableList(values = []) {
  return values.map((value) => TECH_LABEL[value] || String(value).replaceAll("_", " "));
}

const STAGE_ITEMS = [
  ["PREFLIGHT", "安全预检"],
  ["BASELINE", "基线快照"],
  ["INCIDENT", "故障复现"],
  ["DIAGNOSIS", "循证诊断"],
  ["VERIFICATION", "修复复测"],
  ["COMPLETED", "完成"],
];

function stageIndex(stage) {
  const index = STAGE_ITEMS.findIndex(([key]) => key === stage);
  return index < 0 ? 0 : index;
}

function executionStatusMeta(status) {
  switch (status) {
    case "RUNNING":
      return { color: "processing", alert: "info", label: "执行中" };
    case "COMPLETED":
      return { color: "default", alert: "warning", label: "执行完成" };
    case "FAILED":
      return { color: "error", alert: "error", label: "执行失败" };
    case "INTERRUPTED":
      return { color: "warning", alert: "warning", label: "执行中断" };
    default:
      return { color: "default", alert: "warning", label: `未知状态（${status || "未提供"}）` };
  }
}

function scoringStatus(run) {
  if (run?.scoring_status) return run.scoring_status;
  if (run?.execution_fidelity === "MECHANISM_REPRO") return "UNSCORED";
  return run?.result?.passed == null ? "UNSCORED" : "SCORED";
}

function verificationLabel(value) {
  if (value === true) return "是";
  if (value === false) return "否";
  return "未提供";
}

function safeList(value) {
  return Array.isArray(value) && value.length ? value.map(String).join("、") : "未提供";
}

function snapshotColumns() {
  return [
    { title: "证据角色", dataIndex: "role", render: (value) => <Tag color={value === "incident" ? "error" : value === "verification" ? "success" : "blue"}>{SNAPSHOT_ROLE_LABEL[value] || value}</Tag> },
    { title: "GC 后存活对象", dataIndex: "alive_after_gc" },
    { title: "仍存活回调", dataIndex: "registry_entries" },
    { title: "进程 RSS", dataIndex: "rss_kib", render: (value) => value ? `${value} KiB` : "-" },
    { title: "引用机制", dataIndex: "mechanism", render: (value) => value || "无注册回调" },
    { title: "时间", dataIndex: "recorded_at", render: (value) => value ? new Date(value).toLocaleTimeString() : "-" },
  ];
}

function VerifiedProductComparisonCard() {
  return (
    <Card size="small" className="verified-product-comparison" title="同条件量化结果 · 27 次统一输入">
      <Alert
        showIcon
        type="warning"
        message="外部参考实现只有 22/27 次返回合法结构，因此这组数据用于发现差距，不作为正式产品排名"
        description={<>冻结报告：<Text code>{SAME_CONDITION_RESULT.source}</Text>。未实际运行的成熟产品不会被填成零分。</>}
        style={{ marginBottom: 12 }}
      />
      <Row gutter={[12, 12]}>
        {[SAME_CONDITION_RESULT.ours, SAME_CONDITION_RESULT.reference].map((item) => (
          <Col xs={24} xl={12} key={item.name}>
            <Card
              size="small"
              title={item.name}
              extra={<Tag color={item.valid ? "success" : "warning"}>{item.completed}/{item.total} 次有效</Tag>}
            >
              <Row gutter={[12, 16]}>
                <Col xs={12} md={8}><Statistic title="首选根因命中" value={item.top1} precision={2} suffix="%" /></Col>
                <Col xs={12} md={8}><Statistic title="机理判断命中" value={item.mechanism} precision={2} suffix="%" /></Col>
                <Col xs={12} md={8}><Statistic title="必需证据覆盖" value={item.evidence} precision={2} suffix="%" /></Col>
                <Col xs={12} md={8}><Statistic title="证据不足时正确停手" value={item.abstention} precision={2} suffix="%" /></Col>
                <Col xs={12} md={8}><Statistic title="人工纠正后修订" value={item.revision} precision={2} suffix="%" /></Col>
              </Row>
            </Card>
          </Col>
        ))}
      </Row>
      <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
        本结果只代表当前统一测试集和冻结运行条件。RCAEval、OpenRCA、HolmesGPT、Grafana Pyroscope 接入同一输入适配器并真实执行后，才展示各自正式分数。
      </Paragraph>
    </Card>
  );
}

export default function RealWorldBenchmarkPanel() {
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadWarnings, setLoadWarnings] = useState([]);
  const [run, setRun] = useState(null);
  const [comparisons, setComparisons] = useState(null);
  const [comparisonTarget, setComparisonTarget] = useState(null);
  const [comparisonJson, setComparisonJson] = useState("");
  const [submittingComparison, setSubmittingComparison] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    Promise.allSettled([getRealWorldBenchmarkCatalog(), getRealWorldComparisons()])
      .then(([catalogResult, comparisonsResult]) => {
        const warnings = [];
        if (catalogResult.status === "fulfilled") {
          setCatalog(catalogResult.value);
        } else {
          warnings.push(`真实缺陷目录加载失败：${catalogResult.reason?.message || "未知错误"}`);
        }
        if (comparisonsResult.status === "fulfilled") {
          setComparisons(comparisonsResult.value);
        } else {
          warnings.push(`产品对照记录加载失败：${comparisonsResult.reason?.message || "未知错误"}`);
        }
        setLoadWarnings(warnings);
      })
      .finally(() => setLoading(false));
    return () => window.clearTimeout(pollRef.current);
  }, []);

  async function poll(runId) {
    try {
      const current = await getRealWorldBenchmarkRun(runId);
      setRun(current);
      if (current.status === "RUNNING") {
        pollRef.current = window.setTimeout(() => poll(runId), 350);
      } else if (current.status === "COMPLETED") {
        message.info("真实缺陷机制复现执行完成");
      } else if (current.status === "FAILED") {
        message.error(current.error || "真实缺陷实验失败");
      } else if (current.status === "INTERRUPTED") {
        message.warning(current.error || "真实缺陷实验已中断");
      } else {
        message.warning(`真实缺陷实验返回未知状态：${current.status || "未提供"}`);
      }
    } catch (error) {
      message.error(error.message);
    }
  }

  async function start(caseId) {
    window.clearTimeout(pollRef.current);
    setRun(null);
    try {
      const created = await startRealWorldBenchmark(caseId);
      setRun(created);
      poll(created.run_id);
    } catch (error) {
      message.error(error.message);
    }
  }

  function downloadJson(payload, filename) {
    const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function downloadComparisonInput() {
    if (!run?.run_id) return;
    try {
      const payload = await getRealWorldComparisonInput(run.run_id);
      downloadJson(payload, `mini-drop-comparison-input-${run.case_id}.json`);
      message.success("同条件对照输入已导出；该文件不含 Mini-Drop 结论和隐藏标准答案");
    } catch (error) {
      message.error(error.message);
    }
  }

  function downloadComparisonTemplate() {
    const payload = {
      product: "holmesgpt",
      runs: [{
        run_id: "PRODUCT_RUN_ID",
        case_id: "RW-GRAFANA-123359",
        source_run_id: "MINI_DROP_SOURCE_RUN_ID",
        comparison_input_hash: "sha256:COPY_FROM_EXPORTED_INPUT",
        execution_fidelity: "FULL_UPSTREAM_REPLAY",
        predicted_root_cause_id: "PREDICTED_ID_OR_NULL",
        predicted_locations: ["PATH_OR_SYMBOL"],
        evidence: [], evidence_refs: [], counter_evidence_refs: [],
        abstained: true, confidence: 0, duration_seconds: null, tool_calls: null,
      }],
    };
    downloadJson(payload, "mini-drop-comparison-result-template.json");
  }

  async function submitComparison() {
    let parsed;
    try {
      parsed = JSON.parse(comparisonJson);
    } catch {
      message.error("结果不是有效 JSON，请按模板填写");
      return;
    }
    setSubmittingComparison(true);
    try {
      const result = await submitRealWorldComparison(comparisonTarget.id, parsed);
      setComparisons(await getRealWorldComparisons());
      setComparisonTarget(null);
      setComparisonJson("");
      message.success(result.status === "SCORED" ? "对照结果已评分" : "对照结果已冻结，等待评测器评分");
    } catch (error) {
      message.error(error.message);
    } finally {
      setSubmittingComparison(false);
    }
  }

  const columns = [
    { title: "真实案例", dataIndex: "title", width: 250, render: (value, item) => <Space direction="vertical" size={0}><Text strong>{value}</Text><Text code>{item.case_id}</Text></Space> },
    { title: "项目 / 语言", width: 150, render: (_, item) => <Space direction="vertical" size={0}><Text>{item.project}</Text><Tag>{item.language}</Tag></Space> },
    { title: "业务症状", dataIndex: "query", ellipsis: true, width: 360 },
    { title: "上游证据", width: 120, render: (_, item) => <Link href={item.source_url} target="_blank" rel="noreferrer">查看 PR</Link> },
    {
      title: "复现状态",
      dataIndex: "web_execution",
      width: 190,
      render: (value) => value === "MECHANISM_REPRO_AVAILABLE"
        ? <Tag color="success">云端机制复现可执行</Tag>
        : <Tag>已定义，尚未完整回放</Tag>,
    },
    {
      title: "页面操作",
      width: 145,
      fixed: "right",
      render: (_, item) => (
        <Button
          type={item.web_execution === "MECHANISM_REPRO_AVAILABLE" ? "primary" : "default"}
          disabled={item.web_execution !== "MECHANISM_REPRO_AVAILABLE" || run?.status === "RUNNING"}
          icon={<ExperimentOutlined />}
          onClick={() => start(item.case_id)}
        >
          在云端运行
        </Button>
      ),
    },
  ];

  const result = run?.result;
  const statusMeta = executionStatusMeta(run?.status);
  const scoreStatus = scoringStatus(run);
  const isMechanismRun = run?.execution_fidelity === "MECHANISM_REPRO";
  const isUnscoredCompletion = run?.status === "COMPLETED" && scoreStatus === "UNSCORED";
  const passedLabel = result?.passed == null
    ? (scoreStatus === "UNSCORED" ? "未评分/不适用" : "未评分")
    : (result.passed ? "通过" : "未通过");
  return (
    <Card size="small" title={<Space><CloudServerOutlined />真实开源缺陷复现与产品对照</Space>}>
      {loadWarnings.length > 0 ? (
        <Alert
          showIcon
          type="warning"
          style={{ marginBottom: 16 }}
          message="部分实时数据暂时不可用"
          description={loadWarnings.join("；")}
        />
      ) : null}
      <Space direction="vertical" style={{ width: "100%" }} size={16}>
        <Alert
          showIcon
          type="warning"
          message="上游 PR 是测试依据，不等于本项目已经完成运行验证"
          description="绿色按钮表示当前云规格已有可执行适配器。灰色案例只展示公开症状、取证契约和来源，不进入通过率。完整仓库 A/B 回放与低资源机制复现会分开标记。"
        />
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6}><Statistic title="候选真实缺陷" value={catalog?.cases?.length || 0} suffix="个" /></Col>
          <Col xs={12} md={6}><Statistic title="页面可执行" value={catalog?.runnable_count || 0} suffix="个" /></Col>
          <Col xs={12} md={6}><Statistic title="完整上游已回放" value={catalog?.replayed_count || 0} suffix="个" /></Col>
          <Col xs={12} md={6}><Statistic title="对照产品" value={catalog?.comparators?.length || 0} suffix="个" /></Col>
        </Row>
        <VerifiedProductComparisonCard />
        <Table
          rowKey="case_id"
          loading={loading}
          size="small"
          scroll={{ x: 1200 }}
          columns={columns}
          dataSource={catalog?.cases || []}
          pagination={{ pageSize: 7 }}
          expandable={{ expandedRowRender: (item) => <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}><Descriptions.Item label="业务场景">{item.business_scenario}</Descriptions.Item><Descriptions.Item label="负载契约">{item.workload_contract}</Descriptions.Item><Descriptions.Item label="必需证据">{(item.required_evidence || []).join("、")}</Descriptions.Item><Descriptions.Item label="边界说明">{item.execution_note}</Descriptions.Item></Descriptions> }}
        />

        {run && (
          <Card size="small" type="inner" title={`真实执行过程：${run.case_id}`}>
            <Space direction="vertical" style={{ width: "100%" }} size={16}>
              <Alert
                showIcon
                type={statusMeta.alert}
                message={isUnscoredCompletion ? "机制复现执行完成，未进入正式评分" : (run.message || statusMeta.label)}
                description="该实验由云端服务执行白名单适配器，过程和快照实时回传到本页面；不是浏览器预填答案。执行完成只表示流程终止，不自动代表标准答案评分通过。"
              />
              <Descriptions bordered size="small" column={{ xs: 1, md: 3 }}>
                <Descriptions.Item label="执行状态"><Tag color={statusMeta.color}>{statusMeta.label}</Tag></Descriptions.Item>
                <Descriptions.Item label="执行方式"><Tag>{run.execution_fidelity === "MECHANISM_REPRO" ? "低资源机制复现" : run.execution_fidelity === "FULL_UPSTREAM_REPLAY" ? "完整上游回放" : "未提供"}</Tag></Descriptions.Item>
                <Descriptions.Item label="评分状态"><Tag color={scoreStatus === "SCORED" ? "blue" : "warning"}>{STATUS_LABEL[scoreStatus] || scoreStatus}</Tag></Descriptions.Item>
              </Descriptions>
              {run.status === "COMPLETED" && (
                <Alert
                  showIcon
                  type="info"
                  message="可将本次冻结证据交给成熟产品做同条件诊断"
                  description={(
                    <Space wrap>
                      <Text>导出内容只有公开故障契约、基线/故障/修复快照和统一约束，不包含 Mini-Drop 的预测或隐藏标准答案。</Text>
                      <Button icon={<DownloadOutlined />} onClick={downloadComparisonInput}>下载同条件对照输入</Button>
                    </Space>
                  )}
                />
              )}
              <Progress percent={run.progress || 0} status={run.status === "FAILED" ? "exception" : run.status === "RUNNING" ? "active" : "normal"} />
              <Steps current={stageIndex(run.stage)} responsive size="small" items={STAGE_ITEMS.map(([, title]) => ({ title }))} />
              <Row gutter={[16, 16]}>
                <Col xs={24} xl={9}>
                  <Card size="small" title="实时执行时间线" style={{ height: "100%" }}>
                    <Timeline items={(run.events || []).map((event) => ({ color: event.stage === "COMPLETED" ? "green" : event.stage === "FAILED" ? "red" : "blue", children: <div><Text strong>{event.sequence}. {event.message}</Text><div><Text type="secondary">{event.stage} · {new Date(event.recorded_at).toLocaleTimeString()}</Text></div></div> }))} />
                  </Card>
                </Col>
                <Col xs={24} xl={15}>
                  <Card size="small" title="基线 / 故障 / 修复快照" style={{ height: "100%" }}>
                    <Table rowKey="role" size="small" pagination={false} dataSource={run.snapshots || []} columns={snapshotColumns()} scroll={{ x: 760 }} />
                  </Card>
                </Col>
              </Row>
              {result && (
                <Card size="small" title={isMechanismRun ? "机制验证结果（非正式评分）" : "诊断完成后才揭示标准答案"}>
                  <Alert
                    showIcon
                    type={scoreStatus === "UNSCORED" ? "warning" : result.passed === true ? "info" : result.passed === false ? "error" : "warning"}
                    message={scoreStatus === "UNSCORED" ? "本次结果未进入正式评分" : `评分结果：${passedLabel}`}
                    description={result.summary || "未提供结果摘要"}
                  />
                  <Descriptions bordered size="small" column={{ xs: 1, md: 2 }} style={{ marginTop: 12 }}>
                    <Descriptions.Item label="评分结论">{passedLabel}</Descriptions.Item>
                    <Descriptions.Item label="评分状态">{STATUS_LABEL[scoreStatus] || scoreStatus}</Descriptions.Item>
                    <Descriptions.Item label="机制已验证">{verificationLabel(result.mechanism_verified)}</Descriptions.Item>
                    <Descriptions.Item label="恢复已验证">{verificationLabel(result.recovery_verified)}</Descriptions.Item>
                    <Descriptions.Item label="纳入原因" span={2}>{result.admission_reason || "未提供"}</Descriptions.Item>
                    <Descriptions.Item label="系统预测"><Text code>{result.predicted_root_cause_id || "未提供"}</Text></Descriptions.Item>
                    <Descriptions.Item label="支持证据">{safeList(result.evidence_refs)}</Descriptions.Item>
                    <Descriptions.Item label="对照证据">{safeList(result.counter_evidence_refs)}</Descriptions.Item>
                    <Descriptions.Item label="限制" span={2}>{safeList(result.limitations)}</Descriptions.Item>
                  </Descriptions>
                </Card>
              )}
            </Space>
          </Card>
        )}

        <Card size="small" type="inner" title="成熟产品与公开基准同条件对照">
          <Alert showIcon type="info" message="公平比较要求同一故障窗口、同一遥测快照、同一模型和工具预算" description={catalog?.fair_comparison_rule} style={{ marginBottom: 12 }} />
          <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
            {(catalog?.comparators || []).map((item) => {
              const meta = PRODUCT_META[item.id] || { name: item.id, kind: "开源项目", focus: "待补充", relation: "待补充" };
              const latest = comparisons?.latest_by_comparator?.[item.id];
              return (
                <Col xs={24} md={12} key={item.id}>
                  <Card size="small" title={meta.name} extra={<Tag>{meta.kind}</Tag>}>
                    <Paragraph><Text strong>适合比较：</Text>{meta.focus}</Paragraph>
                    <Paragraph><Text strong>与 Mini-Drop 的关系：</Text>{meta.relation}</Paragraph>
                    <Paragraph type="secondary"><Text strong>当前边界：</Text>{meta.boundary}</Paragraph>
                    <Space wrap>
                      <Tag color={item.execution_status === "EXECUTED_CAPABILITY_PASS" ? "success" : "warning"}>
                        {STATUS_LABEL[item.execution_status] || (latest ? STATUS_LABEL[latest.status] : "尚未同条件实测")}
                      </Tag>
                      <Link href={item.url} target="_blank" rel="noreferrer">查看开源项目</Link>
                    </Space>
                  </Card>
                </Col>
              );
            })}
          </Row>
          <Space wrap style={{ marginBottom: 12 }}>
            <Button icon={<DownloadOutlined />} onClick={downloadComparisonTemplate}>下载统一结果模板</Button>
            <Tag color={comparisons?.evaluator_ready ? "success" : "warning"}>
              {comparisons?.evaluator_ready ? "隐藏标准答案评测器已就绪" : "评测器密钥未配置：只冻结结果，不出分"}
            </Tag>
            <Text>实际提交 {comparisons?.actual_submission_count || 0} 次；正式评分 {comparisons?.scored_submission_count || 0} 次</Text>
          </Space>
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={catalog?.comparators || []}
            columns={[
              { title: "项目", dataIndex: "id", render: (value, item) => <Link href={item.url} target="_blank" rel="noreferrer">{PRODUCT_META[value]?.name || value}</Link> },
              { title: "适合比较", dataIndex: "best_for", render: (value = []) => readableList(value).map((label) => <Tag key={label}>{label}</Tag>) },
              {
                title: "对照赛道",
                dataIndex: "comparison_track",
                width: 190,
                render: (value) => value ? <Tag color="blue">{TRACK_LABEL[value] || "专项能力对照"}</Tag> : "-",
              },
              { title: "不能等价比较", dataIndex: "not_equivalent_to", render: (value = []) => readableList(value).join("、") },
              {
                title: "实际对照状态",
                render: (_, item) => {
                  const latest = comparisons?.latest_by_comparator?.[item.id];
                  return latest
                    ? <Space direction="vertical" size={0}><Tag color={latest.status === "SCORED" ? "success" : "processing"}>{STATUS_LABEL[latest.status] || latest.status}</Tag><Text type="secondary">{latest.submitted_cases} 个案例 · 输入指纹 {latest.input_hash?.slice(0, 12)}…</Text></Space>
                    : <Tag color="warning">尚无实际结果</Tag>;
                },
              },
              {
                title: "证据优先得分",
                width: 130,
                render: (_, item) => {
                  const report = comparisons?.latest_by_comparator?.[item.id]?.report;
                  return report ? `${report.evidence_first_score}%` : "-";
                },
              },
              {
                title: "核心指标",
                width: 240,
                render: (_, item) => {
                  const report = comparisons?.latest_by_comparator?.[item.id]?.report;
                  if (!report) return <Text type="secondary">等待实际同条件运行</Text>;
                  return <Space direction="vertical" size={0}>
                    <Text>首选根因 {(100 * report.top1_exact_rate).toFixed(1)}% · 定位 {(100 * report.source_location_rate).toFixed(1)}%</Text>
                    <Text>证据 {(100 * report.evidence_citation_rate).toFixed(1)}% · 三阶段 {(100 * report.three_phase_snapshot_rate).toFixed(1)}%</Text>
                  </Space>;
                },
              },
              { title: "边界与下一步", render: (_, item) => PRODUCT_META[item.id]?.boundary || "等待补充同条件实测" },
              {
                title: "结果导入",
                fixed: "right",
                width: 130,
                render: (_, item) => <Button icon={<UploadOutlined />} onClick={() => { setComparisonTarget(item); setComparisonJson(""); }}>导入实测结果</Button>,
              },
            ]}
            scroll={{ x: 2010 }}
          />
          <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
            当前控制节点资源只适合低资源机制复现。RCAEval、OpenRCA、HolmesGPT 或 Pyroscope 只有在同一输入和同一预算下实际运行后，才会出现比较分数。
          </Paragraph>
        </Card>
      </Space>
      <Modal
        title={`导入成熟产品实测结果：${PRODUCT_META[comparisonTarget?.id]?.name || comparisonTarget?.id || ""}`}
        open={Boolean(comparisonTarget)}
        onCancel={() => setComparisonTarget(null)}
        onOk={submitComparison}
        okText="校验、冻结并提交"
        confirmLoading={submittingComparison}
        width={760}
      >
        <Alert
          showIcon
          type="warning"
          message="这里只接收成熟产品真实运行后的冻结输出"
          description="产品名称必须与所选产品一致；每条结果还必须填写导出包中的运行编号和输入摘要。服务端会绑定原始三阶段证据、校验工具和时间限制，并拒绝提前提交标准答案。未配置评测器密钥时只冻结结果，不显示虚假分数。"
          style={{ marginBottom: 12 }}
        />
        <Input.TextArea
          rows={16}
          value={comparisonJson}
          onChange={(event) => setComparisonJson(event.target.value)}
          placeholder='粘贴统一格式 JSON，例如 {"product":"holmesgpt","runs":[...]}'
        />
      </Modal>
    </Card>
  );
}
