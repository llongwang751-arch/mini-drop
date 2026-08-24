import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Card,
  Col,
  Collapse,
  List,
  Modal,
  Row,
  Segmented,
  Space,
  Statistic,
  Steps,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  BranchesOutlined,
  ExperimentOutlined,
  LineChartOutlined,
  ReadOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { getDiagnosisEvalCatalog, getDiagnosisEvalPlan } from "../api/client";
import CampaignPanel from "./CampaignPanel";
import RealWorldBenchmarkPanel from "./RealWorldBenchmarkPanel";
import SkillEvolutionPanel from "./SkillEvolutionPanel";
import "./EvalPanel.css";

const { Paragraph, Text, Title } = Typography;

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

const NAV_ITEMS = [
  { label: "评测总览", value: "overview", icon: <ReadOutlined /> },
  { label: "真实故障", value: "campaign", icon: <ExperimentOutlined /> },
  { label: "Skill 广场", value: "skills", icon: <BranchesOutlined /> },
  { label: "产品对照", value: "comparison", icon: <LineChartOutlined /> },
];

const DIAGNOSIS_STEPS = [
  { title: "确认范围", description: "服务、Agent、PID、时间窗" },
  { title: "生成假设", description: "明确支持条件与反证条件" },
  { title: "决策树分支", description: "CPU、内存、I/O、网络、依赖" },
  { title: "受控取证", description: "审批后调用真实采集器" },
  { title: "证据裁决", description: "引用证据并计算置信度" },
  { title: "修复验证", description: "比较基线、故障与恢复快照" },
];

function OverviewPanel({ catalog, plan, loading }) {
  const cases = catalog?.core_cases || [];
  const sources = catalog?.sources || [];
  const caseColumns = useMemo(() => [
    { title: "用例", dataIndex: "case_id", width: 180, render: (value) => <Text code>{value}</Text> },
    { title: "故障类型", dataIndex: "fault_type", width: 140, render: (value) => <Tag>{FAULT_LABELS[value] || value}</Tag> },
    { title: "标准根因（Oracle）", dataIndex: "expected_root_cause", width: 280, ellipsis: true },
    {
      title: "必需证据",
      dataIndex: "required_evidence",
      width: 280,
      render: (values = []) => values.map((value) => <Tag key={value}>{value}</Tag>),
    },
    {
      title: "修复验证",
      dataIndex: "snapshot_roles",
      width: 140,
      render: (values = []) => values.includes("verification")
        ? <Tag color="success">要求恢复快照</Tag>
        : <Tag>未定义</Tag>,
    },
  ], []);

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <section className="eval-method-grid" aria-label="AI 诊断核心方法">
        <article className="eval-method eval-method-evidence">
          <span className="eval-method-index">01</span>
          <SafetyCertificateOutlined />
          <Title level={5}>循证诊断</Title>
          <Paragraph>先提出可证伪假设，再采集支持证据和反证。证据不足时明确停下，不编造根因。</Paragraph>
        </article>
        <article className="eval-method eval-method-tree">
          <span className="eval-method-index">02</span>
          <BranchesOutlined />
          <Title level={5}>性能决策树</Title>
          <Paragraph>先判断问题发生在哪个层级，再选择 CPU、内存、I/O、网络或依赖方向取证。</Paragraph>
        </article>
        <article className="eval-method eval-method-benchmark">
          <span className="eval-method-index">03</span>
          <ExperimentOutlined />
          <Title level={5}>统一测试集</Title>
          <Paragraph>诊断前隐藏标准答案，结束后再按根因、证据、反证和安全门禁逐项评分。</Paragraph>
        </article>
      </section>

      <Card className="eval-flow-card" title="一次诊断如何得到可信结论">
        <Steps responsive items={DIAGNOSIS_STEPS} />
      </Card>

      <Card className="eval-plan-card" title="当前有效评测基线">
        <Row gutter={[24, 18]}>
          <Col xs={12} lg={6}><Statistic title="核心故障用例" value={plan?.case_count ?? cases.length} suffix="个" /></Col>
          <Col xs={12} lg={6}><Statistic title="诊断策略" value={plan?.strategies?.length ?? 3} suffix="种" /></Col>
          <Col xs={12} lg={6}><Statistic title="每场景重复" value={plan?.repetitions ?? 3} suffix="次" /></Col>
          <Col xs={12} lg={6}><Statistic title="计划执行" value={plan?.execution_count ?? 90} suffix="次" /></Col>
        </Row>
        <Alert
          className="eval-plan-note"
          type="warning"
          showIcon
          message="回归分数不等于线上正确率"
          description="统一用例用于跨版本和跨方案比较；真实故障还要在受控环境中注入，并比较基线、故障和恢复三段快照。"
        />
      </Card>

      <Collapse
        className="eval-reference-collapse"
        items={[
          {
            key: "catalog",
            label: `基础测试集目录（${catalog?.dataset || "unified"} v${catalog?.version || "-"}，${cases.length} 个）`,
            children: <Table rowKey="case_id" size="small" scroll={{ x: 1040 }} loading={loading} dataSource={cases} columns={caseColumns} pagination={{ pageSize: 6 }} />,
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
                    <Space direction="vertical" size={2}>
                      <Space wrap>
                        <Tag color="blue">{source.tier}</Tag>
                        <Text strong>{source.source_id}</Text>
                        {String(source.url || "").startsWith("http")
                          ? <Typography.Link href={source.url} target="_blank" rel="noreferrer">查看原始来源</Typography.Link>
                          : <Text code>{source.url}</Text>}
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
    </Space>
  );
}

export default function EvalPanel() {
  const [section, setSection] = useState("overview");
  const [skillPlazaOpen, setSkillPlazaOpen] = useState(false);
  const [catalog, setCatalog] = useState(null);
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadEvaluationContext = useCallback(async () => {
    setLoading(true);
    try {
      const [catalogResult, planResult] = await Promise.all([
        getDiagnosisEvalCatalog(),
        getDiagnosisEvalPlan(),
      ]);
      setCatalog(catalogResult);
      setPlan(planResult);
    } catch (error) {
      message.error(error?.message || "评测资料加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadEvaluationContext();
  }, [loadEvaluationContext]);

  return (
    <div className="eval-center">
      <header className="eval-center-header">
        <div>
          <Text className="eval-eyebrow">DIAGNOSIS VALIDATION</Text>
          <Title level={3}>诊断验证中心</Title>
          <Paragraph>从方法、真实故障、策略演化到成熟产品对照，所有结论都有过程和证据可追溯。</Paragraph>
        </div>
        <div className="eval-header-status">
          <span className="eval-status-dot" />
          <Text strong>当前评测体系</Text>
          <Text type="secondary">旧测试入口已下线</Text>
        </div>
      </header>

      <Segmented
        className="eval-center-nav"
        block
        value={section}
        options={NAV_ITEMS}
        onChange={(value) => {
          if (value === "skills") {
            setSkillPlazaOpen(true);
            return;
          }
          setSection(value);
        }}
      />

      <main className="eval-center-content">
        {section === "overview" && <OverviewPanel catalog={catalog} plan={plan} loading={loading} />}
        {section === "campaign" && <CampaignPanel />}
        {section === "comparison" && <RealWorldBenchmarkPanel />}
      </main>
      <Modal
        className="skill-plaza-modal"
        title="诊断 Skill 广场"
        open={skillPlazaOpen}
        onCancel={() => setSkillPlazaOpen(false)}
        footer={null}
        width="min(94vw, 1440px)"
        destroyOnHidden
      >
        <SkillEvolutionPanel />
      </Modal>
    </div>
  );
}
