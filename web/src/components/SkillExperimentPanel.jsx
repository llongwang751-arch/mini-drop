import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  ExperimentOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import {
  approveDiagnosticExperiment,
  assignDiagnosticExperiment,
  createDiagnosticExperiment,
  evaluateDiagnosticExperiment,
  getDiagnosticExperiment,
  listDiagnosticExperiments,
  recordDiagnosticExperimentOutcome,
  runDropInsightPlanner,
} from "../api/client";
import { diagnosticStatusLabel, skillPolicyLabel } from "../utils/diagnosisDisplay";
import { shortDiagnosisId } from "../utils/hypothesisSemantics";

const { Paragraph, Text, Title } = Typography;

function percent(value) {
  return value == null ? "-" : `${(Number(value) * 100).toFixed(1)}%`;
}

function pValue(value) {
  if (value == null) return "样本不足";
  if (Number(value) < 0.0001) return "< 0.0001";
  return Number(value).toFixed(4);
}

function newUnitKey() {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `manual-traffic-${suffix}`;
}

export default function SkillExperimentPanel({
  seedRequest = null,
  onOpenDiagnosis,
  onCasesChanged,
}) {
  const [experiments, setExperiments] = useState([]);
  const [experimentId, setExperimentId] = useState("");
  const [summary, setSummary] = useState(null);
  const [name, setName] = useState("诊断 Skill 随机实验");
  const [query, setQuery] = useState(seedRequest?.query || "");
  const [unitKey, setUnitKey] = useState(newUnitKey);
  const [busy, setBusy] = useState("");

  useEffect(() => {
    if (seedRequest?.query) setQuery(seedRequest.query);
  }, [seedRequest]);

  async function refreshExperiments(preferredId = "") {
    setBusy((current) => current || "refresh");
    try {
      const rows = await listDiagnosticExperiments();
      setExperiments(rows || []);
      const nextId = preferredId || experimentId || rows?.[0]?.experiment_id || "";
      setExperimentId(nextId);
      setSummary(nextId ? await getDiagnosticExperiment(nextId) : null);
    } catch (error) {
      message.error(error?.message || "随机实验读取失败");
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    void refreshExperiments();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function createExperiment() {
    const trimmed = name.trim();
    if (trimmed.length < 3) {
      message.info("请输入至少 3 个字的实验名称");
      return;
    }
    setBusy("create");
    try {
      const created = await createDiagnosticExperiment({
        name: trimmed,
        treatment_ratio: 0.5,
        minimum_labeled_per_arm: 30,
        minimum_effect_percentage_points: 5,
        alpha: 0.05,
        guardrails: {
          maximum_safety_violations: 0,
          maximum_verified_rate_regression_percentage_points: 5,
        },
      });
      await refreshExperiments(created?.experiment_id || "");
      message.success("已创建服务端 50/50 随机实验");
    } catch (error) {
      message.error(error?.message || "创建实验失败");
    } finally {
      setBusy("");
    }
  }

  async function assignTraffic() {
    if (!experimentId) {
      message.info("请先创建或选择实验");
      return;
    }
    if (!query.trim() || unitKey.trim().length < 3) {
      message.info("请填写诊断问题和流量单元标识");
      return;
    }
    setBusy("assign");
    try {
      const diagnosis = {
        query: query.trim(),
        mode: seedRequest?.mode || "AUTONOMOUS",
        auto_scope: seedRequest?.auto_scope ?? true,
        ...(seedRequest?.target ? { target: seedRequest.target } : {}),
        ...(seedRequest?.time_range ? { time_range: seedRequest.time_range } : {}),
        ...(seedRequest?.budget ? { budget: seedRequest.budget } : {}),
      };
      const assigned = await assignDiagnosticExperiment(experimentId, {
        unit_key: unitKey.trim(),
        stratum: seedRequest?.evaluation_context?.scenarioId || "manual",
        diagnosis,
      });
      const diagnosisId = assigned?.diagnosis?.diagnosis_id;
      if (diagnosisId) {
        await Promise.allSettled([
          runDropInsightPlanner(diagnosisId),
          onCasesChanged?.(),
        ]);
      }
      setUnitKey(newUnitKey());
      setSummary(await getDiagnosticExperiment(experimentId));
      const arm = assigned?.assignment?.arm;
      message.success(`服务端已分配 ${skillPolicyLabel(arm)}`);
    } catch (error) {
      message.error(error?.message || "实验流量分配失败");
    } finally {
      setBusy("");
    }
  }

  async function labelOutcome(row, correct) {
    setBusy(`label:${row.diagnosis_id}`);
    try {
      await recordDiagnosticExperimentOutcome(
        experimentId,
        row.diagnosis_id,
        {
          root_cause_correct: correct,
          outcome_source: "HUMAN",
          safety_violation_count: 0,
          notes: "验收人在页面对照根因后标注",
          metadata: {},
        },
      );
      setSummary(await getDiagnosticExperiment(experimentId));
      message.success("人工 Oracle 标签已留痕");
    } catch (error) {
      message.error(error?.message || "标注失败");
    } finally {
      setBusy("");
    }
  }

  async function evaluate() {
    if (!experimentId) return;
    setBusy("evaluate");
    try {
      setSummary(await evaluateDiagnosticExperiment(experimentId));
      message.success("已保存一份长期指标快照");
    } catch (error) {
      message.error(error?.message || "评估失败");
    } finally {
      setBusy("");
    }
  }

  async function approve() {
    setBusy("approve");
    try {
      await approveDiagnosticExperiment(
        experimentId,
        "样本量、效果量、显著性和安全护栏均已人工复核",
      );
      setSummary(await getDiagnosticExperiment(experimentId));
      message.success("发布建议已人工批准并留痕");
    } catch (error) {
      message.error(error?.message || "人工批准失败");
    } finally {
      setBusy("");
    }
  }

  const experiment = summary?.experiment || null;
  const significance = summary?.significance || {};
  const recommendation = summary?.recommendation || experiment?.recommendation || {};
  const rows = summary?.assignments || [];
  const ci = significance.confidence_interval_95_percentage_points || [];
  const columns = useMemo(() => [
    {
      title: "诊断",
      dataIndex: "diagnosis_id",
      render: (value) => (
        <Button type="link" onClick={() => onOpenDiagnosis?.(value)}>
          {shortDiagnosisId(value)}
        </Button>
      ),
    },
    {
      title: "随机臂",
      dataIndex: "arm",
      render: (value) => <Tag color={value === "AUTO" ? "green" : "default"}>{skillPolicyLabel(value)}</Tag>,
    },
    {
      title: "诊断状态",
      dataIndex: "diagnosis_status",
      render: (value) => diagnosticStatusLabel(value),
    },
    {
      title: "Oracle 标签",
      key: "oracle",
      render: (_, row) => row.labeled ? (
        <Tag color={row.root_cause_correct ? "success" : "error"}>
          {row.root_cause_correct ? "根因正确" : "根因错误"}
        </Tag>
      ) : (
        <Space size={4}>
          <Button
            size="small"
            loading={busy === `label:${row.diagnosis_id}`}
            onClick={() => labelOutcome(row, true)}
          >正确</Button>
          <Button
            size="small"
            danger
            loading={busy === `label:${row.diagnosis_id}`}
            onClick={() => labelOutcome(row, false)}
          >错误</Button>
        </Space>
      ),
    },
  ], [busy, onOpenDiagnosis]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Card className="skill-experiment-card" bordered={false}>
      <div className="showcase-section-header">
        <div>
          <Title level={4}>服务端随机实验</Title>
          <Paragraph>
            用加盐 SHA-256 粘性分流把真实诊断分到 Skill AUTO 或 DISABLED。
            只有人工/受控 Oracle 标注才进入根因准确率，路线提前不会被当成正确率。
          </Paragraph>
        </div>
        <Button
          icon={<ReloadOutlined />}
          loading={busy === "refresh"}
          onClick={() => refreshExperiments()}
        >刷新</Button>
      </div>

      <Alert
        type="info"
        showIcon
        icon={<SafetyCertificateOutlined />}
        message="只自动生成发布建议，永不自动修改 Prompt、代码、权限或生产流量"
        description="默认每臂至少 30 个已标注样本，p ≤ 0.05，提升至少 5 个百分点，且安全与验证率护栏通过，才可由有权限的人批准。"
      />

      <div className="skill-experiment-create">
        <Input value={name} onChange={(event) => setName(event.target.value)} aria-label="随机实验名称" />
        <Button loading={busy === "create"} onClick={createExperiment}>新建 50/50 实验</Button>
        <Select
          value={experimentId || undefined}
          placeholder="选择已持久化实验"
          options={experiments.map((item) => ({
            value: item.experiment_id,
            label: `${item.name} · ${item.status}`,
          }))}
          onChange={async (value) => {
            setExperimentId(value);
            setSummary(await getDiagnosticExperiment(value));
          }}
        />
      </div>

      {experiment ? (
        <>
          <Row className="skill-experiment-metrics" gutter={[12, 12]}>
            <Col xs={12} md={6}><Statistic title="AUTO 正确率" value={percent(summary?.arms?.AUTO?.root_cause_accuracy?.rate)} /></Col>
            <Col xs={12} md={6}><Statistic title="DISABLED 正确率" value={percent(summary?.arms?.DISABLED?.root_cause_accuracy?.rate)} /></Col>
            <Col xs={12} md={6}><Statistic title="效果量" value={significance.delta_percentage_points ?? "-"} suffix={significance.delta_percentage_points == null ? "" : " pp"} /></Col>
            <Col xs={12} md={6}><Statistic title="p 值" value={pValue(significance.p_value)} /></Col>
          </Row>
          <Space wrap className="skill-experiment-contract">
            <Tag color="blue">{experiment.status}</Tag>
            <Tag>已分流 {summary.assignment_count || 0}</Tag>
            <Tag>已标注 {summary.labeled_count || 0}</Tag>
            <Tag>95% CI {ci[0] == null ? "-" : `[${ci[0]}, ${ci[1]}] pp`}</Tag>
            <Tag>长期快照 {summary.metric_history?.length || 0}</Tag>
          </Space>

          <div className="skill-experiment-traffic">
            <label>
              <Text strong>真实诊断问题</Text>
              <Input.TextArea
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                autoSize={{ minRows: 2, maxRows: 4 }}
                placeholder="从故障广场带入，或输入一条需要真实取证的问题"
              />
            </label>
            <label>
              <Text strong>流量单元标识</Text>
              <Input value={unitKey} onChange={(event) => setUnitKey(event.target.value)} />
              <Text type="secondary">只保存哈希；同一标识在同一实验中稳定落到同一臂。</Text>
            </label>
            <Button
              type="primary"
              icon={<ExperimentOutlined />}
              loading={busy === "assign"}
              onClick={assignTraffic}
            >由服务端随机分流并诊断</Button>
          </div>

          <Table
            className="skill-experiment-table"
            rowKey="assignment_id"
            size="small"
            pagination={{ pageSize: 5, hideOnSinglePage: true }}
            columns={columns}
            dataSource={rows}
            locale={{ emptyText: <Empty description="还没有真实分流样本" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          />

          <div className="skill-experiment-actions">
            <Button loading={busy === "evaluate"} onClick={evaluate}>计算显著性并保存快照</Button>
            {recommendation.eligible && experiment.status === "ROLLOUT_RECOMMENDED" && (
              <Button
                type="primary"
                icon={<CheckCircleOutlined />}
                loading={busy === "approve"}
                onClick={approve}
              >人工批准发布建议</Button>
            )}
            <Text type={recommendation.eligible ? "success" : "secondary"}>
              {recommendation.eligible
                ? "护栏通过，等待人工批准"
                : `当前不可发布：${(recommendation.blockers || ["尚未评估"]).join(" / ")}`}
            </Text>
          </div>
        </>
      ) : (
        <Empty description="创建第一个服务端随机实验" />
      )}
    </Card>
  );
}
