import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  List,
  Progress,
  Row,
  Col,
  Space,
  Statistic,
  Steps,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from "antd";
import { CheckCircleOutlined, ExperimentOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  getDiagnosisEvalCatalog,
  getDiagnosisEvalPlan,
  getDiagnosisEvalGoldenRun,
  getExternalDiagnosisBenchmark,
  getExternalDiagnosisBenchmarkCase,
  startDiagnosisEvalGoldenRun,
} from "../api/client";
import CampaignPanel from "./CampaignPanel";

const { Text, Paragraph } = Typography;

const FAULT_LABELS = {
  CPU_HOTSPOT: "CPU 热点",
  IO_LATENCY: "I/O 延迟",
  MEMORY_PRESSURE: "内存压力",
  NETWORK_DEGRADATION: "网络劣化",
  JVM_GC: "JVM GC",
  DOWNSTREAM_DEPENDENCY: "下游依赖",
  QUEUE_CONGESTION: "队列积压",
  NOISY_NEIGHBOR: "噪声邻居",
  CONTAINER_RESOURCE_LIMIT: "容器限额",
  DATABASE_LOCK: "数据库锁",
};

const STAGES = [
  { key: "SUITE_LOADED", title: "载入测试集", description: "读取场景、Oracle 与版本指纹" },
  { key: "SCENARIO_STARTED", title: "执行诊断策略", description: "输入问题并生成诊断判断" },
  { key: "DECISION_BRANCH_EVALUATED", title: "性能决策树", description: "核对影响域与根因分支" },
  { key: "EVIDENCE_AND_FALSIFICATION_CHECKED", title: "循证核验", description: "检查证据、反证计划和安全门禁" },
  { key: "GATE_COMPLETED", title: "质量门禁", description: "汇总指标并与阈值比较" },
];

const CHECK_LABELS = {
  classification: "根因分类一致",
  finding_types: "异常特征覆盖",
  knowledge_refs: "知识引用命中",
  action_collectors: "采集器选择正确",
  report_verification: "证据引用完整",
  no_auto_execute: "没有越权自动执行",
  falsification_plan: "包含反证计划",
};

function percent(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

function stageIndex(stage) {
  if (stage === "SCENARIO_COMPLETED") return 3;
  const index = STAGES.findIndex((item) => item.key === stage);
  return index < 0 ? 0 : index;
}

function ScenarioResult({ result }) {
  const actual = result.actual || {};
  const expected = result.expected || {};
  return (
    <div className="eval-scenario-detail">
      <Descriptions size="small" column={{ xs: 1, md: 2 }} bordered>
        <Descriptions.Item label="标准根因（Oracle）">
          <Text code>{expected.classification || "-"}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="系统诊断结果">
          <Text code>{actual.classification || "-"}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="预期证据/知识引用">
          {(expected.knowledge_refs || []).map((item) => <Tag key={item}>{item}</Tag>)}
        </Descriptions.Item>
        <Descriptions.Item label="实际证据/知识引用">
          {(actual.knowledge_refs || []).map((item) => <Tag color="blue" key={item}>{item}</Tag>)}
        </Descriptions.Item>
        <Descriptions.Item label="预期采集器">
          {(expected.action_collectors || []).map((item) => <Tag key={item}>{item}</Tag>)}
        </Descriptions.Item>
        <Descriptions.Item label="实际采集器">
          {(actual.action_collectors || []).map((item) => <Tag color="cyan" key={item}>{item}</Tag>)}
        </Descriptions.Item>
      </Descriptions>
      <Space wrap style={{ marginTop: 12 }}>
        {Object.entries(result.checks || {}).map(([key, ok]) => (
          <Tag key={key} color={ok ? "success" : "error"}>
            {CHECK_LABELS[key] || key}：{ok ? "通过" : "失败"}
          </Tag>
        ))}
      </Space>
    </div>
  );
}

const TRACK_LABELS = {
  live_single_fault: "单故障",
  live_compound_fault: "复合故障 / 多机",
  negative_and_robustness: "健康负例与鲁棒性",
};

function ExternalCaseDetail({ detail, loading }) {
  if (loading) return <Text type="secondary">正在读取审计包与 evaluator-only Oracle……</Text>;
  if (!detail) return <Text type="secondary">选择一个案例后查看三次诊断过程。</Text>;
  const expected = detail.oracle?.expected || {};
  const requiredCollectors = detail.oracle?.evidence?.required_collectors || [];
  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Alert
        showIcon
        type="warning"
        message="Oracle 只在诊断完成后由评测器揭示"
        description={`标准答案：位置 ${expected.location_type || "-"}，故障域 ${expected.domain_type || "-"}，分类 ${expected.classification || "-"}。必需采集器：${requiredCollectors.join("、") || "无"}。`}
      />
      <Collapse
        items={(detail.runs || []).map((run) => ({
          key: run.diagnosis_id,
          label: (
            <Space wrap>
              <Tag color={run.exact_root_match ? "success" : "error"}>第 {run.repetition} 次</Tag>
              <Text code>{run.diagnosis_id}</Text>
              <Text>{run.score} 分</Text>
              <Tag>{run.exact_root_match ? "严格根因命中" : "严格根因未命中"}</Tag>
              <Text type="secondary">证据 {run.evidence_count} · 探针 {run.probe_count}</Text>
            </Space>
          ),
          children: (
            <Space direction="vertical" style={{ width: "100%" }} size={12}>
              <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="实际位置">{run.actual?.location_type || "-"}</Descriptions.Item>
                <Descriptions.Item label="实际故障域">{run.actual?.domain_type || "-"}</Descriptions.Item>
                <Descriptions.Item label="实际分类">{run.actual?.classification || "-"}</Descriptions.Item>
                <Descriptions.Item label="模型 / 规划器">{run.model_version || "-"} / {run.planner_version || "-"}</Descriptions.Item>
                <Descriptions.Item label="所需采集器">{(run.dimensions?.required_collectors || []).join("、") || "-"}</Descriptions.Item>
                <Descriptions.Item label="实际采集器">{(run.dimensions?.observed_collectors || []).join("、") || "-"}</Descriptions.Item>
                <Descriptions.Item label="证据引用">{run.dimensions?.citation_valid ? "有效" : "存在问题"}</Descriptions.Item>
                <Descriptions.Item label="不安全动作">{run.dimensions?.unsafe_actions?.length || 0}</Descriptions.Item>
                <Descriptions.Item label="诊断结论" span={2}>{run.conclusion_summary || "-"}</Descriptions.Item>
              </Descriptions>
              <Timeline
                items={(run.trace || []).map((event) => ({
                  color: event.stage === "report_verification" ? "green" : "blue",
                  children: (
                    <div>
                      <Space wrap>
                        <Text strong>{event.sequence}. {event.stage}</Text>
                        <Tag>{event.component}</Tag>
                        <Text type="secondary">{event.decision}</Text>
                      </Space>
                      <div>{event.summary}</div>
                      {event.evidence_refs?.length > 0 && (
                        <Text type="secondary">证据：{event.evidence_refs.join("、")}</Text>
                      )}
                    </div>
                  ),
                }))}
              />
            </Space>
          ),
        }))}
      />
    </Space>
  );
}

export default function EvalPanel() {
  const [catalog, setCatalog] = useState(null);
  const [plan, setPlan] = useState(null);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [run, setRun] = useState(null);
  const [external, setExternal] = useState(null);
  const [externalError, setExternalError] = useState("");
  const [externalCase, setExternalCase] = useState(null);
  const [externalCaseLoading, setExternalCaseLoading] = useState(false);
  const pollRef = useRef(null);

  const loadCatalog = useCallback(async () => {
    setLoadingCatalog(true);
    try {
      const [catalogResult, planResult, externalResult] = await Promise.all([
        getDiagnosisEvalCatalog(),
        getDiagnosisEvalPlan(),
        getExternalDiagnosisBenchmark().catch((err) => ({ __error: err.message })),
      ]);
      setCatalog(catalogResult);
      setPlan(planResult);
      if (externalResult?.__error) {
        setExternal(null);
        setExternalError(externalResult.__error);
      } else {
        setExternal(externalResult);
        setExternalError("");
      }
    } catch (err) {
      message.error(err.message);
    } finally {
      setLoadingCatalog(false);
    }
  }, []);

  useEffect(() => {
    loadCatalog();
    return () => window.clearTimeout(pollRef.current);
  }, [loadCatalog]);

  const pollRun = useCallback(async (runId) => {
    try {
      const current = await getDiagnosisEvalGoldenRun(runId);
      setRun(current);
      if (current.status === "RUNNING") {
        pollRef.current = window.setTimeout(() => pollRun(runId), 220);
      } else if (current.status === "COMPLETED") {
        message.success("逐场评测完成，可展开每个场景查看判定过程");
      } else {
        message.error(current.error || "评测执行失败");
      }
    } catch (err) {
      message.error(err.message);
    }
  }, []);

  async function startRun() {
    window.clearTimeout(pollRef.current);
    setRun(null);
    try {
      const created = await startDiagnosisEvalGoldenRun();
      setRun(created);
      pollRun(created.run_id);
    } catch (err) {
      message.error(err.message);
    }
  }

  async function loadExternalCase(caseId) {
    setExternalCaseLoading(true);
    setExternalCase(null);
    try {
      setExternalCase(await getExternalDiagnosisBenchmarkCase(caseId));
    } catch (err) {
      message.error(err.message);
    } finally {
      setExternalCaseLoading(false);
    }
  }

  const cases = catalog?.core_cases || [];
  const sources = catalog?.sources || [];
  const report = run?.report;
  const metrics = report?.metrics || {};
  const progress = run?.total ? Math.round((run.completed / run.total) * 100) : 0;
  const running = run?.status === "RUNNING";
  const events = run?.events || [];
  const scenarioResults = run?.scenario_results || report?.results || [];
  const latestEvents = useMemo(() => events.slice(-10).reverse(), [events]);

  const caseColumns = [
    { title: "用例", dataIndex: "case_id", width: 180, render: (value) => <Text code>{value}</Text> },
    { title: "故障类型", dataIndex: "fault_type", width: 130, render: (value) => <Tag>{FAULT_LABELS[value] || value}</Tag> },
    { title: "标准根因（Oracle）", dataIndex: "expected_root_cause", width: 260, ellipsis: true },
    {
      title: "必需证据",
      dataIndex: "required_evidence",
      width: 260,
      render: (values = []) => values.map((value) => <Tag key={value}>{value}</Tag>),
    },
    {
      title: "修复后验证",
      dataIndex: "snapshot_roles",
      width: 150,
      render: (values = []) => values.includes("verification")
        ? <Tag color="success">要求验证快照</Tag>
        : <Tag>未定义</Tag>,
    },
  ];

  const externalColumns = [
    { title: "案例", dataIndex: "case_id", width: 220, render: (value) => <Text code>{value}</Text> },
    { title: "类别", dataIndex: "track", width: 160, render: (value) => <Tag>{TRACK_LABELS[value] || value}</Tag> },
    { title: "公开症状（AI 可见）", dataIndex: "query", ellipsis: true },
    { title: "服务", dataIndex: "service_hint", width: 180 },
    { title: "运行", dataIndex: "run_count", width: 80 },
    { title: "严格命中", width: 100, render: (_, item) => `${item.exact_match_count}/${item.run_count}` },
    { title: "均分", dataIndex: "mean_score", width: 90 },
    { title: "过程", width: 90, render: (_, item) => <Button type="link" onClick={() => loadExternalCase(item.case_id)}>查看</Button> },
  ];

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={16}>
      <Alert
        showIcon
        type="info"
        message="方法与评测不是黑盒：诊断步骤、标准答案、实际输出和每项门禁都可追溯"
        description="100% 只表示当前版本通过了这组确定性的 Golden 回归用例，不代表线上诊断永远正确。真实环境还要执行故障注入 Campaign 并比较修复前后快照。"
      />

      <CampaignPanel />

      <Card size="small" title="组内统一测试集 ai_ops_v2：30 个案例 / 90 次真实诊断">
        {externalError ? (
          <Alert type="warning" showIcon message="外部统一测试集尚未安装" description={externalError} />
        ) : !external ? (
          <Text type="secondary">正在读取统一测试集……</Text>
        ) : (
          <Space direction="vertical" style={{ width: "100%" }} size={16}>
            <Alert
              showIcon
              type="info"
              message="这里展示历史真实运行，不把 Oracle 提前喂给诊断系统"
              description={`数据集 ${external.dataset} ${external.version}；压缩包 SHA256 ${external.archive?.sha256?.slice(0, 16)}…。案例页只含症状，点“查看”后才进入 evaluator-only 标准答案与审计轨迹。`}
            />
            <Row gutter={[12, 12]}>
              <Col xs={12} md={6}><Statistic title="案例" value={external.evaluation?.case_count || external.cases?.length} suffix="个" /></Col>
              <Col xs={12} md={6}><Statistic title="有效运行" value={external.evaluation?.run_count || external.execution?.completed_runs} suffix="次" /></Col>
              <Col xs={12} md={6}><Statistic title="严格根因准确率" value={Math.round((external.evaluation?.exact_root_accuracy || 0) * 1000) / 10} suffix="%" /></Col>
              <Col xs={12} md={6}><Statistic title="平均综合分" value={external.evaluation?.mean_score || 0} suffix="/100" /></Col>
              <Col xs={12} md={6}><Statistic title="正确拒答率" value={Math.round((external.evaluation?.correct_abstention_rate || 0) * 100)} suffix="%" /></Col>
              <Col xs={12} md={6}><Statistic title="证据引用有效率" value={Math.round((external.evaluation?.citation_valid_rate || 0) * 100)} suffix="%" /></Col>
              <Col xs={12} md={6}><Statistic title="重复一致率" value={Math.round((external.evaluation?.repeat_output_consistency || 0) * 1000) / 10} suffix="%" /></Col>
              <Col xs={12} md={6}><Statistic title="不安全动作" value={external.evaluation?.unsafe_action_count || 0} /></Col>
            </Row>
            <Table
              rowKey="case_id"
              size="small"
              scroll={{ x: 1180 }}
              dataSource={external.cases || []}
              columns={externalColumns}
              pagination={{ pageSize: 8 }}
            />
            <Card size="small" type="inner" title={externalCase ? `${externalCase.case_id}：三次运行与完整诊断轨迹` : "选择案例查看过程"}>
              <ExternalCaseDetail detail={externalCase} loading={externalCaseLoading} />
            </Card>
          </Space>
        )}
      </Card>

      <Card size="small" title="三项核心方法如何落到系统里">
        <Row gutter={[12, 12]}>
          <Col xs={24} xl={8}>
            <Card size="small" type="inner" title="1. 循证诊断">
              <Paragraph>先提出可证伪假设，再收集支持证据和反证。证据不足时输出“信息不足”，不编造根因。</Paragraph>
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card size="small" type="inner" title="2. 性能决策树">
              <Paragraph>先判定单进程、同宿主机、下游或共享资源，再进入 CPU、内存、I/O、网络、数据库等分支。</Paragraph>
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card size="small" type="inner" title="3. 统一测试集">
              <Paragraph>每个典型故障包含问题输入、标准根因、必需证据与安全要求；诊断完成后才用 Oracle 打分。</Paragraph>
            </Card>
          </Col>
        </Row>
      </Card>

      <Card size="small" title="真实诊断流程">
        <Steps
          size="small"
          responsive
          items={[
            { title: "确认范围", description: "服务 / Agent / PID / 时间窗" },
            { title: "生成假设", description: "列出支持条件与可推翻条件" },
            { title: "决策树分支", description: "CPU / 内存 / I/O / 网络 / 依赖" },
            { title: "受控取证", description: "预算、权限审批、真实采集" },
            { title: "证据裁决", description: "支持证据 + 反证 + 置信度" },
            { title: "修复后验证", description: "基线 / 故障 / 修复快照对比" },
          ]}
        />
      </Card>

      <Card size="small" title="统一评测计划">
        <Row gutter={[16, 12]}>
          <Col xs={12} md={6}><Statistic title="核心故障用例" value={plan?.case_count ?? cases.length} suffix="个" /></Col>
          <Col xs={12} md={6}><Statistic title="诊断策略" value={plan?.strategies?.length ?? 3} suffix="种" /></Col>
          <Col xs={12} md={6}><Statistic title="每场景重复" value={plan?.repetitions ?? 3} suffix="次" /></Col>
          <Col xs={12} md={6}><Statistic title="真实 Campaign 计划" value={plan?.execution_count ?? 90} suffix="次" /></Col>
        </Row>
        <Alert
          style={{ marginTop: 12 }}
          type="warning"
          showIcon
          message="三个层次不能混为一个分数"
          description="目录中的 10 个统一用例用于跨方案对比；页面可直接执行的是 7 个确定性 Golden 回归场景；90 次真实 Campaign 还需要故障注入环境。当前用例已定义根因、必需证据和修复后验证快照，具体修复代码仍按对应开源项目独立审阅。"
        />
      </Card>

      <Collapse
        items={[
          {
            key: "catalog",
            label: `测试集目录（${catalog?.dataset || "unified"} v${catalog?.version || "-"}，${cases.length} 个用例）`,
            children: <Table rowKey="case_id" size="small" scroll={{ x: 980 }} loading={loadingCatalog} dataSource={cases} columns={caseColumns} pagination={{ pageSize: 6 }} />,
          },
          {
            key: "sources",
            label: `开源项目、论文与资料来源（${sources.length} 条）`,
            children: (
              <List
                size="small"
                dataSource={sources}
                renderItem={(source) => (
                  <List.Item>
                    <Space direction="vertical" size={0}>
                      <Space wrap>
                        <Tag color="blue">{source.tier}</Tag>
                        <Text strong>{source.source_id}</Text>
                        {String(source.url || "").startsWith("http") ? (
                          <Typography.Link href={source.url} target="_blank" rel="noreferrer">查看原始来源</Typography.Link>
                        ) : <Text code>{source.url}</Text>}
                      </Space>
                      <Text type="secondary">{source.purpose} · {source.license}</Text>
                    </Space>
                  </List.Item>
                )}
              />
            ),
          },
        ]}
      />

      <Card
        size="small"
        title="Golden 逐场质量门禁"
        extra={<Button type="primary" icon={<ExperimentOutlined />} loading={running} onClick={startRun}>开始逐场评测</Button>}
      >
        {!run && <Text type="secondary">点击后会逐个展示：用例输入 → 决策树分类 → 证据与反证核验 → 场景判定 → 总体门禁。</Text>}
        {run && (
          <Space direction="vertical" style={{ width: "100%" }} size={16}>
            <div>
              <Space style={{ width: "100%", justifyContent: "space-between" }}>
                <Text strong>{run.message}</Text>
                <Text type="secondary">{run.completed}/{run.total || "-"} 场景</Text>
              </Space>
              <Progress percent={progress} status={run.status === "FAILED" ? "exception" : running ? "active" : "success"} />
            </div>
            <Steps current={stageIndex(run.stage)} size="small" responsive items={STAGES} />

            <Row gutter={[16, 16]}>
              <Col xs={24} xl={9}>
                <Card size="small" title="实时执行记录" style={{ height: "100%" }}>
                  <Timeline
                    items={latestEvents.map((event) => ({
                      color: event.stage === "SCENARIO_COMPLETED" ? (event.result?.passed ? "green" : "red") : "blue",
                      children: (
                        <div>
                          <Text strong>{event.message}</Text>
                          <div><Text type="secondary">{event.scenario_id || event.stage}</Text></div>
                        </div>
                      ),
                    }))}
                  />
                </Card>
              </Col>
              <Col xs={24} xl={15}>
                <Card size="small" title={`场景裁决（${scenarioResults.length}）`} style={{ height: "100%" }}>
                  {scenarioResults.length === 0 ? <Text type="secondary">等待第一个场景完成……</Text> : (
                    <Collapse
                      items={scenarioResults.map((item) => ({
                        key: item.scenario_id,
                        label: (
                          <Space wrap>
                            {item.passed ? <CheckCircleOutlined style={{ color: "#389e0d" }} /> : null}
                            <Text code>{item.scenario_id}</Text>
                            <Tag color={item.passed ? "success" : "error"}>{item.passed ? "通过" : "失败"}</Tag>
                            <Text type="secondary">{item.expected?.classification} → {item.actual?.classification}</Text>
                          </Space>
                        ),
                        children: <ScenarioResult result={item} />,
                      }))}
                    />
                  )}
                </Card>
              </Col>
            </Row>

            {report && (
              <Card size="small" title="最终门禁汇总">
                <Alert
                  type={report.gate_status === "PASSED" ? "success" : "error"}
                  showIcon
                  message={`Golden 回归门禁：${report.gate_status}`}
                  description={`通过 ${report.passed}/${report.total} 个场景。这个分数只对当前数据集版本 ${report.dataset_version} 和指纹 ${report.dataset_fingerprint?.slice(0, 12)}… 有效。`}
                />
                <Row gutter={[16, 12]} style={{ marginTop: 16 }}>
                  <Col xs={12} lg={4}><Statistic title="根因分类" value={percent(metrics.classification_accuracy)} /></Col>
                  <Col xs={12} lg={4}><Statistic title="场景通过" value={percent(metrics.scenario_pass_rate)} /></Col>
                  <Col xs={12} lg={4}><Statistic title="证据完整" value={percent(metrics.evidence_reference_integrity)} /></Col>
                  <Col xs={12} lg={4}><Statistic title="反证计划" value={percent(metrics.falsification_plan_rate)} /></Col>
                  <Col xs={12} lg={4}><Statistic title="越权执行" value={metrics.unsafe_auto_execute_count ?? "-"} /></Col>
                  <Col xs={12} lg={4}><Statistic title="数据集版本" value={report.dataset_version} /></Col>
                </Row>
                <Space wrap style={{ marginTop: 12 }}>
                  {Object.entries(report.gate_checks || {}).map(([key, ok]) => (
                    <Tag key={key} color={ok ? "success" : "error"}>{key}: {ok ? "PASS" : "FAIL"}</Tag>
                  ))}
                </Space>
              </Card>
            )}
          </Space>
        )}
      </Card>
    </Space>
  );
}

