import { useState } from "react";
import {
  Alert,
  Card,
  Col,
  Modal,
  Row,
  Segmented,
  Space,
  Statistic,
  Steps,
  Typography,
} from "antd";
import {
  BranchesOutlined,
  ExperimentOutlined,
  LineChartOutlined,
  ReadOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import CampaignPanel from "./CampaignPanel";
import RealWorldBenchmarkPanel from "./RealWorldBenchmarkPanel";
import SkillEvolutionPanel from "./SkillEvolutionPanel";
import "./EvalPanel.css";

const { Paragraph, Text, Title } = Typography;

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

function OverviewPanel() {
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

      <Card className="eval-plan-card" title="当前验证体系">
        <Row gutter={[24, 18]}>
          <Col xs={12} lg={6}><Statistic title="真实故障类型" value={4} suffix="类" /></Col>
          <Col xs={12} lg={6}><Statistic title="Skill 独立难例" value={15} suffix="个" /></Col>
          <Col xs={12} lg={6}><Statistic title="诊断路径" value={3} suffix="种" /></Col>
          <Col xs={12} lg={6}><Statistic title="证据阶段" value={3} suffix="段" /></Col>
        </Row>
        <Alert
          className="eval-plan-note"
          type="warning"
          showIcon
          message="回归分数不等于线上正确率"
          description="页面只保留当前真实故障、Skill 独立难例和同条件产品对照。旧版 10 条静态目录已下线，不再参与展示或请求。"
        />
      </Card>
    </Space>
  );
}

export default function EvalPanel() {
  const [section, setSection] = useState("overview");
  const [skillPlazaOpen, setSkillPlazaOpen] = useState(false);

  return (
    <div className="eval-center">
      <header className="eval-center-header">
        <div>
          <Text className="eval-eyebrow">诊断验证</Text>
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
        {section === "overview" && <OverviewPanel />}
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
