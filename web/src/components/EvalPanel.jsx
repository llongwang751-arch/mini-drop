import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Modal,
  Row,
  Segmented,
  Space,
  Statistic,
  Steps,
  Tag,
  Typography,
} from "antd";
import {
  BranchesOutlined,
  BugOutlined,
  ExperimentOutlined,
  ReadOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { listDiagnosticSkills } from "../api/client";
import FaultPlazaPanel from "./FaultPlazaPanel";
import LatsReplayPanel from "./LatsReplayPanel";
import SkillABPanel from "./SkillABPanel";
import SkillEvolutionPanel from "./SkillEvolutionPanel";
import "./EvalPanel.css";

const { Paragraph, Text, Title } = Typography;

const NAV_ITEMS = [
  { label: "评测总览", value: "overview", icon: <ReadOutlined /> },
  { label: "故障广场", value: "faults", icon: <BugOutlined /> },
  { label: "Skill A/B", value: "skill-ab", icon: <ExperimentOutlined /> },
  { label: "Skill 示例与沉淀", value: "skills", icon: <BranchesOutlined /> },
];

const DIAGNOSIS_STEPS = [
  { title: "确认范围", description: "服务、Agent、PID、时间窗" },
  { title: "生成假设", description: "明确支持条件与反证条件" },
  { title: "决策树分支", description: "CPU、内存、I/O、网络、依赖" },
  { title: "受控取证", description: "审批后调用真实采集器" },
  { title: "证据裁决", description: "引用证据并计算置信度" },
  { title: "修复验证", description: "比较基线、故障与恢复快照" },
];

const COLLECTOR_TO_TOOL = {
  sys_metrics: "collect_sys_metrics",
  perf_cpu: "start_perf_profile",
  continuous_perf: "start_continuous_profile",
  pyspy: "start_pyspy_profile",
  go_pprof: "collect_go_profile",
  java_async: "start_jvm_profile",
  memory_smaps: "collect_memory_profile",
  ebpf_io: "start_ebpf_io_profile",
};

function skillABEvaluationContext(started) {
  const scenario = started?.scenario || {};
  const collectors = Array.isArray(scenario.recommended_collectors) ? scenario.recommended_collectors : [];
  const decisiveCollector = collectors[1] || collectors[0] || "";
  return {
    scenarioId: scenario.scenario_id || "",
    scenarioTitle: scenario.title || "",
    expectedCollector: decisiveCollector,
    expectedTool: COLLECTOR_TO_TOOL[decisiveCollector] || "",
    expectedSignals: Array.isArray(scenario.expected_signals) ? scenario.expected_signals : [],
  };
}

function OverviewPanel({ onOpenSkills }) {
  const [stats, setStats] = useState({ skills: null, cases: null, gates: null });

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      listDiagnosticSkills(),
      fetch("/report-assets/skill-evolution/benchmark-report.json", { cache: "no-store" })
        .then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return response.json();
        }),
    ]).then(([skillsResult, benchmarkResult]) => {
      if (!active) return;
      const report = benchmarkResult.status === "fulfilled" ? benchmarkResult.value : null;
      const gates = report?.gate_summary || report?.gates || report?.family_results || null;
      setStats({
        skills: skillsResult.status === "fulfilled" ? (skillsResult.value || []).length : null,
        cases: report?.dataset?.case_count ?? report?.summary?.case_count ?? null,
        gates: gates && typeof gates === "object" ? Object.keys(gates).length : null,
      });
    });
    return () => { active = false; };
  }, []);

  const visible = (value) => value == null ? "-" : value;
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
          <Col xs={12} lg={8}><Statistic title="当前诊断 Skill" value={visible(stats.skills)} suffix={stats.skills == null ? "" : "个"} /></Col>
          <Col xs={12} lg={8}><Statistic title="当前测试集案例" value={visible(stats.cases)} suffix={stats.cases == null ? "" : "个"} /></Col>
          <Col xs={12} lg={8}><Statistic title="报告门禁分组" value={visible(stats.gates)} suffix={stats.gates == null ? "" : "类"} /></Col>
        </Row>
        <Alert
          className="eval-plan-note"
          type="warning"
          showIcon
          message="回归分数不等于线上正确率"
          description="页面只展示 Drop Insight V2 的真实诊断过程和 Skill 独立难例。旧版 Campaign、产品对照与静态目录均已下线。"
        />
      </Card>

      <Card className="eval-flow-card" title="示例 Skill 如何沉淀">
        <Paragraph>
          仓库提供 CPU 热点、内存增长、I/O 延迟、Java GC、锁竞争等参考路线，用来展示“候选 → 门禁评测 → 人工发布 → 负反馈隔离/回滚”。参考路线不冒充当前数据库中已发布的真实 Skill。
        </Paragraph>
        <Space wrap>
          <Tag color="blue">CPU 热点</Tag>
          <Tag color="purple">内存增长</Tag>
          <Tag color="cyan">I/O 延迟</Tag>
          <Tag color="gold">Java GC</Tag>
          <Button type="primary" onClick={onOpenSkills}>查看示例与运行实例</Button>
        </Space>
      </Card>
    </Space>
  );
}

export default function EvalPanel({ onStartDiagnosis, onOpenDiagnosis, onCasesChanged }) {
  const [section, setSection] = useState("overview");
  const [skillPlazaOpen, setSkillPlazaOpen] = useState(false);
  const [skillABSeed, setSkillABSeed] = useState(null);

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
        {section === "overview" && <OverviewPanel onOpenSkills={() => setSkillPlazaOpen(true)} />}
        {section === "faults" && (
          <Space direction="vertical" size={18} style={{ width: "100%" }}>
            <FaultPlazaPanel
              onStartDiagnosis={onStartDiagnosis}
              onPrepareSkillAB={(diagnosisRequest, started) => {
                setSkillABSeed({
                  ...diagnosisRequest,
                  evaluation_context: skillABEvaluationContext(started),
                });
                setSection("skill-ab");
              }}
            />
            <LatsReplayPanel
              onOpenDiagnosis={onOpenDiagnosis}
              onCasesChanged={onCasesChanged}
            />
          </Space>
        )}
        {section === "skill-ab" && (
          <SkillABPanel
            seedRequest={skillABSeed}
            onOpenDiagnosis={onOpenDiagnosis}
            onCasesChanged={onCasesChanged}
          />
        )}
      </main>
      <Modal
        className="skill-plaza-modal"
        title="诊断 Skill 广场"
        open={skillPlazaOpen}
        onCancel={() => setSkillPlazaOpen(false)}
        footer={null}
        width="min(94vw, 1440px)"
        style={{ top: 24 }}
        destroyOnHidden
      >
        <SkillEvolutionPanel />
      </Modal>
    </div>
  );
}
