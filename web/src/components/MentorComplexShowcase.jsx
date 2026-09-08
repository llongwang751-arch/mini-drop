import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Modal,
  Row,
  Space,
  Statistic,
  Tag,
  Typography,
} from "antd";
import {
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExperimentOutlined,
  NodeIndexOutlined,
} from "@ant-design/icons";
import ActualExplorationTree from "./ActualExplorationTree";
import DiagnosisSkillOutcomeCard from "./DiagnosisSkillOutcomeCard";
import "./MentorComplexShowcase.css";

const { Paragraph, Text } = Typography;

const TERMINAL = new Set(["COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"]);

const STATUS_META = {
  COMPLETED: { color: "green", label: "诊断完成" },
  INSUFFICIENT_EVIDENCE: { color: "gold", label: "证据不足" },
  FAILED: { color: "red", label: "诊断失败" },
  CANCELLED: { color: "default", label: "已取消" },
};

function reportConfidence(report) {
  const value = Number(report?.confidence ?? report?.final_confidence ?? 0);
  return Number.isFinite(value) ? Math.round(value * 100) : 0;
}

function targetText(detail) {
  const target = detail?.target || {};
  const agent = target.agent_id || detail?.agent_id || "未绑定";
  const pid = target.pid || detail?.pid || "未绑定";
  return `${agent} / PID ${pid}`;
}

function SkillDecision({
  skill,
  verifiedReport,
  terminal,
  generating,
  onGenerate,
  onEvaluate,
  onOpenPlaza,
}) {
  if (skill) {
    return (
      <DiagnosisSkillOutcomeCard
        skill={skill}
        evaluating={generating}
        onEvaluate={onEvaluate}
        onOpenPlaza={onOpenPlaza}
      />
    );
  }

  if (generating) {
    return (
      <Alert
        showIcon
        type="info"
        message="正在判断是否沉淀候选 Skill"
        description="系统正在从当前案例提取真实取证顺序、反证剪枝和停止条件。"
      />
    );
  }

  if (verifiedReport) {
    return (
      <Alert
        showIcon
        type="success"
        message="当前案例具备候选 Skill 提取条件"
        description="报告已通过验证并引用可信 Evidence。可以从本案例生成候选 Skill，再执行正例、反例和环境迁移门禁。"
        action={<Button type="primary" size="small" onClick={onGenerate}>生成候选 Skill</Button>}
      />
    );
  }

  if (terminal) {
    return (
      <Alert
        showIcon
        type="warning"
        message="本案例暂不沉淀 Skill"
        description="当前案例没有带可信 Evidence 引用的 VERIFIED 报告。保留探索轨迹用于审计，但不把不完整结论固化成可复用路线。"
      />
    );
  }

  return (
    <Alert
      showIcon
      type="info"
      message="诊断结束后判断是否沉淀"
      description="候选 Skill 必须来自当前案例的完整探索轨迹和已验证报告，诊断进行中不会提前固化路线。"
    />
  );
}

export default function MentorComplexShowcase({
  open,
  loading = false,
  detail,
  explorationTree,
  hypotheses = [],
  toolCalls = [],
  evidence = [],
  reports = [],
  sourceSkill,
  verifiedReport,
  skillBusy = false,
  onGenerateSkill,
  onEvaluateSkill,
  onOpenPlaza,
  onClose,
}) {
  const caseId = detail?.diagnosis_id || detail?.case_id || detail?.id || "未选择";
  const latestReport = reports[0] || null;
  const status = String(detail?.status || "").toUpperCase();
  const statusMeta = STATUS_META[status] || { color: "blue", label: detail?.status || "诊断进行中" };
  const treeStats = explorationTree?.stats || {};
  const hasExploration = Boolean(
    explorationTree?.nodes?.length
      || hypotheses.length
      || toolCalls.length
      || latestReport?.exploration_nodes?.length,
  );

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭回放</Button>}
      width="min(1380px, 96vw)"
      centered
      destroyOnHidden
      className="mentor-showcase-modal current-case-replay-modal"
      title={(
        <div className="case-replay-title">
          <Space wrap>
            <ExperimentOutlined />
            <span>复杂案例回放</span>
            <Tag color="blue">当前案例</Tag>
          </Space>
          <Text type="secondary" code>{caseId}</Text>
        </div>
      )}
    >
      <div aria-label="当前诊断案例回放">
        {loading ? (
          <div className="case-replay-loading" aria-label="正在加载当前案例">
            <ClockCircleOutlined spin />
            <Text>正在读取当前案例的探索轨迹</Text>
          </div>
        ) : !detail ? (
          <Empty description="请先从左侧选择一个诊断案例" />
        ) : (
          <div className="mentor-showcase-content current-case-replay-content">
            <Alert
              type="info"
              showIcon
              message={detail.query || "当前诊断案例"}
              description={`本弹窗只读取诊断 ${caseId} 的详情、探索树、Evidence、报告和候选 Skill，不切换到示例库中的其他案例。`}
            />

            <Card size="small" className="case-replay-overview">
              <div className="case-replay-overview-head">
                <Space wrap>
                  <Tag color={statusMeta.color}>{statusMeta.label}</Tag>
                  <Text strong>{targetText(detail)}</Text>
                </Space>
                <Text type="secondary">树版本 {explorationTree?.revision || 0}</Text>
              </div>
              <Row gutter={[12, 12]}>
                <Col xs={12} md={6}><Statistic title="诊断轮次" value={treeStats.rounds || treeStats.current_round || 0} suffix="轮" /></Col>
                <Col xs={12} md={6}><Statistic title="候选假设" value={hypotheses.length} suffix="个" /></Col>
                <Col xs={12} md={6}><Statistic title="工具调用" value={toolCalls.length} suffix="次" /></Col>
                <Col xs={12} md={6}><Statistic title="可信证据" value={evidence.length} suffix="条" /></Col>
              </Row>
              <Descriptions size="small" column={{ xs: 1, md: 3 }} className="case-replay-metadata">
                <Descriptions.Item label="当前案例 ID"><Text code copyable>{caseId}</Text></Descriptions.Item>
                <Descriptions.Item label="报告版本">{reports.length || 0}</Descriptions.Item>
                <Descriptions.Item label="最新置信度">{reportConfidence(latestReport)}%</Descriptions.Item>
              </Descriptions>
            </Card>

            <section className="case-replay-section" aria-labelledby="current-case-skill-title">
              <div className="case-replay-section-heading">
                <div>
                  <Space><NodeIndexOutlined /><Text strong id="current-case-skill-title">候选 Skill 沉淀判断</Text></Space>
                  <Paragraph type="secondary">只根据当前案例的报告和实际探索轨迹做判断。</Paragraph>
                </div>
                {sourceSkill
                  ? <Tag icon={<CheckCircleOutlined />} color={sourceSkill.status === "ACTIVE" ? "green" : "gold"}>{sourceSkill.status === "ACTIVE" ? "已发布" : "已形成候选"}</Tag>
                  : <Tag>尚未形成候选</Tag>}
              </div>
              <SkillDecision
                skill={sourceSkill}
                verifiedReport={verifiedReport}
                terminal={TERMINAL.has(status)}
                generating={skillBusy}
                onGenerate={onGenerateSkill}
                onEvaluate={onEvaluateSkill}
                onOpenPlaza={onOpenPlaza}
              />
            </section>

            <section className="case-replay-section" aria-labelledby="current-case-tree-title">
              <div className="case-replay-section-heading">
                <div>
                  <Space><BranchesOutlined /><Text strong id="current-case-tree-title">当前案例探索树</Text></Space>
                  <Paragraph type="secondary">节点、剪枝和方向切换全部来自当前诊断记录。</Paragraph>
                </div>
                <Tag>{treeStats.nodes || explorationTree?.nodes?.length || 0} 个节点</Tag>
              </div>
              <div className="diagnosis-tree-panel case-replay-tree-panel">
                {hasExploration ? (
                  <ActualExplorationTree
                    tree={explorationTree}
                    hypotheses={hypotheses}
                    toolCalls={toolCalls}
                    report={latestReport}
                  />
                ) : (
                  <Empty description="当前案例还没有探索节点" />
                )}
              </div>
              <div className="diagnosis-last-event case-replay-last-event">
                <span className="live-tree-pulse" />
                <div>
                  <b>最近一次树更新</b>
                  <small>{explorationTree?.last_event?.event_type || "等待当前案例产生诊断事件"}</small>
                </div>
              </div>
            </section>

          </div>
        )}
      </div>
    </Modal>
  );
}
