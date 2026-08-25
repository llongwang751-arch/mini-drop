import { Alert, Card, Empty, Space, Tag, Typography } from "antd";
import { BranchesOutlined } from "@ant-design/icons";
import ChatMessage from "./ChatMessage";
import DiagnosisPathPanel from "./DiagnosisPathPanel";
import PlannerBlock from "./PlannerBlock";
import ToolCallCard from "./ToolCallCard";
import EvidenceCard from "./EvidenceCard";
import ConclusionCard from "./ConclusionCard";
import ScopeCard from "./ScopeCard";
import FixVerificationPanel from "./FixVerificationPanel";
import DiagnosisFeedbackCard from "./DiagnosisFeedbackCard";
import ActualExplorationTree from "./ActualExplorationTree";

const { Text } = Typography;

const TOOL_LABELS = {
  collect_sys_metrics: "系统指标采集",
  collect_database_diagnostics: "数据库状态采集",
  start_perf_profile: "CPU 火焰图采集",
  start_pyspy_profile: "Python 调用栈采集",
  start_ebpf_io_profile: "I/O 延迟采集",
  get_agent_status: "采集节点检查",
};

function readableToolName(tool) {
  const key = tool?.tool_name || tool?.name || tool?.tool || "";
  return TOOL_LABELS[key] || key || "待选择采集器";
}

/**
 * 对话线程：把一次诊断会话渲染成 Codex 式对话。
 * 顺序：用户问题 → （范围确认卡，如需）→ AI 计划（分类+假设）
 *      → 工具调用（审批/结果）→ 证据 → 结论。
 * 事件以细时间线垫底，体现"持续交互"。
 */
export default function ChatThread({
  detail,
  hypotheses,
  toolCalls,
  evidence,
  reports,
  events,
  onApproveTool,
  onRejectTool,
  onUpdateToolArgs,
  onClarify,
  clarifying,
  feedback = [],
  onSubmitFeedback,
  feedbackSubmitting,
  skillActivations = [],
  mode = "expert",
  readOnly = false,
  unavailableSections = [],
}) {
  const isExpert = mode === "expert";
  if (!detail) {
    return <Empty description="描述一个问题，AI 会一步步给出结论" style={{ marginTop: 60 }} />;
  }

  const classification = detail.classification || detail.status || "分析中";
  const reportRows = [...(reports || [])];
  const reportsHaveOrdering = reportRows.some(
    (item) => item?.version != null || item?.created_at || item?.updated_at,
  );
  if (reportsHaveOrdering) {
    reportRows.sort((a, b) => {
      if (a?.version != null || b?.version != null) return Number(b?.version || 0) - Number(a?.version || 0);
      return new Date(b?.updated_at || b?.created_at || 0) - new Date(a?.updated_at || a?.created_at || 0);
    });
  }
  const latestReport = reportRows[0] || null;
  const feedbackRows = [...(feedback || [])];
  if (feedbackRows.some((item) => item?.created_at || item?.updated_at)) {
    feedbackRows.sort(
      (a, b) => new Date(b?.updated_at || b?.created_at || 0) - new Date(a?.updated_at || a?.created_at || 0),
    );
  }
  const latestFeedback = feedbackRows[0] || null;
  const reportVerificationStatus = latestReport?.verification?.status;
  const hasVerifiedRootCause = reportVerificationStatus === "VERIFIED";
  const sortedTools = [...(toolCalls || [])].sort(
    (a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0),
  );
  const acceptedEvidence = (evidence || []).filter(
    (item) => item.classification?.decision === "ACCEPT_SUPPORT",
  );
  const skillActivation = [...skillActivations].sort(
    (a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0),
  )[0] || null;
  const skillReason = skillActivation?.match_reason || {};
  const route = skillReason.route || [];
  const dynamicRoute = [...new Set(sortedTools.map(readableToolName).filter(Boolean))];

  return (
    <div>
      <ChatMessage role="user">
        <div style={{ padding: "10px 14px", borderRadius: 10, background: "rgba(22,119,255,0.1)" }}>
          {detail.query || detail.id}
        </div>
      </ChatMessage>

      <ChatMessage role="assistant">
        {detail.status === "NEEDS_CLARIFICATION" && !readOnly && (
          <ScopeCard
            key={detail.diagnosis_id || detail.id}
            diagnosisId={detail.diagnosis_id || detail.id}
            diagnosisVersion={detail.diagnosis_version ?? detail.version}
            questions={detail.clarification_questions || []}
            onClarify={onClarify}
            submitting={clarifying}
            initialTarget={detail.target || {}}
            initialTimeRange={detail.time_range || {}}
            draftKey={detail.diagnosis_id || detail.id}
          />
        )}
        {latestReport && <ConclusionCard report={latestReport} />}
        <ActualExplorationTree hypotheses={hypotheses} toolCalls={sortedTools} report={latestReport} />
        <PlannerBlock
          classification={classification}
          hypotheses={hypotheses}
        />
        <Card
          className={`diagnosis-skill-trace ${skillActivation ? "is-published-skill" : "is-dynamic-route"}`}
          size="small"
          title={<Space><BranchesOutlined /><span>本轮诊断能力</span></Space>}
          extra={skillActivation
            ? <Tag color="green">已命中发布 Skill</Tag>
            : <Tag color="blue">动态取证路线</Tag>}
        >
          {skillActivation ? (
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              <Space wrap>
                <Text strong>复用了经过门禁验证的诊断经验</Text>
                <Tag color="green">版本 {skillReason.skill_version || "-"}</Tag>
                <Tag color="blue">匹配度 {Math.round(Number(skillActivation.match_score || 0) * 100)}%</Tag>
              </Space>
              <div>
                取证路线：{(route.length ? route : [skillActivation.selected_tool]).map((tool) => (
                  <Tag key={tool}>{TOOL_LABELS[tool] || tool}</Tag>
                ))}
              </div>
              <Text type="secondary">
                命中依据来自故障类别、服务和运行环境；若人工反馈判错，系统会记录负迁移并隔离该 Skill。
              </Text>
            </Space>
          ) : (
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              <Text>
                本轮由性能决策树按当前证据动态选择工具，尚未命中可复用的已发布 Skill。
              </Text>
              <div>
                当前路线：{dynamicRoute.length
                  ? dynamicRoute.map((name) => <Tag key={name}>{name}</Tag>)
                  : <Tag>等待范围确认后生成</Tag>}
              </div>
              <Text type="secondary">
                动态路线不冒充 Skill；只有结论经证据验证、人工确认并通过正例、反例和环境漂移门禁后，才会进入 Skill 广场。
              </Text>
            </Space>
          )}
        </Card>
        {sortedTools.map((tool) => (
          <ToolCallCard
            key={tool.tool_call_id}
            tool={tool}
            mode={mode}
            onApprove={onApproveTool}
            onReject={onRejectTool}
            onUpdateArgs={onUpdateToolArgs}
            readOnly={readOnly}
          />
        ))}
        {acceptedEvidence.length > 0 && (
          <div style={{ margin: "12px 0 4px" }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              已确认证据
            </Text>
          </div>
        )}
        {acceptedEvidence.map((item) => (
          <EvidenceCard key={item.evidence_id} evidence={item} />
        ))}
        {/* 诊断结束后仍允许评价结论；readOnly 只约束继续取证和工具调用。 */}
        {latestReport && onSubmitFeedback && (
          <DiagnosisFeedbackCard
            report={latestReport}
            latestFeedback={latestFeedback}
            onSubmit={onSubmitFeedback}
            submitting={feedbackSubmitting}
          />
        )}
        {/* 证据不足只是待验证假设；只有根因通过反证门禁后才能验证修复。 */}
        {hasVerifiedRootCause && detail?.diagnosis_id && !readOnly && (
          <FixVerificationPanel diagnosisId={detail.diagnosis_id} />
        )}
        {readOnly && unavailableSections.length > 0 && (
          <Card size="small" style={{ marginBottom: 10, background: "#fafafa" }}>
            <Text type="secondary">
              该版本未记录此类数据：{unavailableSections.join("、")}。
            </Text>
          </Card>
        )}
      </ChatMessage>

      {isExpert && (events || []).length > 0 && (
        <Card size="small" title="诊断路径（可回放）" style={{ marginLeft: 40, marginTop: 8 }}>
          <DiagnosisPathPanel events={events} />
        </Card>
      )}
    </div>
  );
}
