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
    { title: "证据角色", dataIndex: "role", render: (value) => <Tag color={value === "incident" ? "error" : value === "verification" ? "success" : "blue"}>{value}</Tag> },
    { title: "GC 后存活对象", dataIndex: "alive_after_gc" },
    { title: "仍存活回调", dataIndex: "registry_entries" },
    { title: "进程 RSS", dataIndex: "rss_kib", render: (value) => value ? `${value} KiB` : "-" },
    { title: "引用机制", dataIndex: "mechanism", render: (value) => value || "无注册回调" },
    { title: "时间", dataIndex: "recorded_at", render: (value) => value ? new Date(value).toLocaleTimeString() : "-" },
  ];
}

export default function RealWorldBenchmarkPanel() {
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(true);
  const [run, setRun] = useState(null);
  const [comparisons, setComparisons] = useState(null);
  const [comparisonTarget, setComparisonTarget] = useState(null);
  const [comparisonJson, setComparisonJson] = useState("");
  const [submittingComparison, setSubmittingComparison] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    Promise.all([getRealWorldBenchmarkCatalog(), getRealWorldComparisons()])
      .then(([nextCatalog, nextComparisons]) => {
        setCatalog(nextCatalog);
        setComparisons(nextComparisons);
      })
      .catch((error) => message.error(error.message))
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
      message.success("同条件对照输入已导出；该文件不含 Mini-Drop 结论和隐藏 Oracle");
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
    <Card size="small" title={<Space><CloudServerOutlined />真实开源缺陷：页面化云端复现与成熟产品对照</Space>}>
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
                description="该实验由云端 Server 执行白名单适配器，过程和快照实时回传到本页面；不是浏览器预填答案。执行完成只表示流程终止，不自动代表 Oracle 评分通过。"
              />
              <Descriptions bordered size="small" column={{ xs: 1, md: 3 }}>
                <Descriptions.Item label="执行状态"><Tag color={statusMeta.color}>{statusMeta.label}</Tag></Descriptions.Item>
                <Descriptions.Item label="执行保真度"><Tag>{run.execution_fidelity || "未提供"}</Tag></Descriptions.Item>
                <Descriptions.Item label="评分状态"><Tag color={scoreStatus === "SCORED" ? "blue" : "warning"}>{scoreStatus}</Tag></Descriptions.Item>
              </Descriptions>
              {run.status === "COMPLETED" && (
                <Alert
                  showIcon
                  type="info"
                  message="可将本次冻结证据交给成熟产品做同条件诊断"
                  description={(
                    <Space wrap>
                      <Text>导出内容只有公开故障契约、基线/故障/修复快照和统一预算，不包含 Mini-Drop 的预测或隐藏 Oracle。</Text>
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
                <Card size="small" title={isMechanismRun ? "机制验证结果（非 Oracle 正式评分）" : "诊断完成后才揭示 Oracle"}>
                  <Alert
                    showIcon
                    type={scoreStatus === "UNSCORED" ? "warning" : result.passed === true ? "info" : result.passed === false ? "error" : "warning"}
                    message={scoreStatus === "UNSCORED" ? "本次结果未进入正式评分" : `评分结果：${passedLabel}`}
                    description={result.summary || "未提供结果摘要"}
                  />
                  <Descriptions bordered size="small" column={{ xs: 1, md: 2 }} style={{ marginTop: 12 }}>
                    <Descriptions.Item label="评分结论">{passedLabel}</Descriptions.Item>
                    <Descriptions.Item label="评分状态">{scoreStatus}</Descriptions.Item>
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

        <Card size="small" type="inner" title="成熟产品 / 公开基准同条件对照">
          <Alert showIcon type="info" message="公平比较要求同一故障窗口、同一遥测快照、同一模型和工具预算" description={catalog?.fair_comparison_rule} style={{ marginBottom: 12 }} />
          <Space wrap style={{ marginBottom: 12 }}>
            <Button icon={<DownloadOutlined />} onClick={downloadComparisonTemplate}>下载统一结果模板</Button>
            <Tag color={comparisons?.evaluator_ready ? "success" : "warning"}>
              {comparisons?.evaluator_ready ? "隐藏 Oracle 评测器已就绪" : "评测器密钥未配置：只冻结结果，不出分"}
            </Tag>
            <Text>实际提交 {comparisons?.actual_submission_count || 0} 次；正式评分 {comparisons?.scored_submission_count || 0} 次</Text>
          </Space>
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={catalog?.comparators || []}
            columns={[
              { title: "项目", dataIndex: "id", render: (value, item) => <Link href={item.url} target="_blank" rel="noreferrer">{value}</Link> },
              { title: "适合比较", dataIndex: "best_for", render: (value = []) => value.map((item) => <Tag key={item}>{item}</Tag>) },
              {
                title: "对照赛道",
                dataIndex: "comparison_track",
                width: 190,
                render: (value) => value ? <Tag color="blue">{value}</Tag> : "-",
              },
              { title: "不能等价比较", dataIndex: "not_equivalent_to", render: (value = []) => value.join("、") },
              {
                title: "实际对照状态",
                render: (_, item) => {
                  const latest = comparisons?.latest_by_comparator?.[item.id];
                  return latest
                    ? <Space direction="vertical" size={0}><Tag color={latest.status === "SCORED" ? "success" : "processing"}>{latest.status}</Tag><Text type="secondary">{latest.submitted_cases} 个案例 · {latest.input_hash?.slice(0, 18)}…</Text></Space>
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
                    <Text>Top1 {(100 * report.top1_exact_rate).toFixed(1)}% · 定位 {(100 * report.source_location_rate).toFixed(1)}%</Text>
                    <Text>证据 {(100 * report.evidence_citation_rate).toFixed(1)}% · 三阶段 {(100 * report.three_phase_snapshot_rate).toFixed(1)}%</Text>
                  </Space>;
                },
              },
              { title: "边界 / 下一步", dataIndex: "reason" },
              {
                title: "结果导入",
                fixed: "right",
                width: 130,
                render: (_, item) => <Button icon={<UploadOutlined />} onClick={() => { setComparisonTarget(item); setComparisonJson(""); }}>导入实测 JSON</Button>,
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
        title={`导入成熟产品实测结果：${comparisonTarget?.id || ""}`}
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
          description="product 必须与所选产品一致；每条结果还必须填写导出包中的 source_run_id 和 comparison_input_hash。服务端会绑定原始三阶段证据、校验工具/时间预算并拒绝 Oracle 字段。未配置评测器密钥时只冻结结果，不显示虚假分数。"
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
