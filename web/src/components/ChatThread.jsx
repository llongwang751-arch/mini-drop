import { Card, Empty, Typography } from "antd";
import ChatMessage from "./ChatMessage";
import DiagnosisPathPanel from "./DiagnosisPathPanel";
import PlannerBlock from "./PlannerBlock";
import ToolCallCard from "./ToolCallCard";
import EvidenceCard from "./EvidenceCard";
import ConclusionCard from "./ConclusionCard";
import ScopeCard from "./ScopeCard";
import FixVerificationPanel from "./FixVerificationPanel";
import DiagnosisFeedbackCard from "./DiagnosisFeedbackCard";

const { Text } = Typography;

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
            questions={detail.clarification_questions || []}
            onClarify={onClarify}
            submitting={clarifying}
            initialTarget={detail.target || {}}
            initialTimeRange={detail.time_range || {}}
            draftKey={detail.diagnosis_id || detail.id}
          />
        )}
        <PlannerBlock
          classification={classification}
          hypotheses={hypotheses}
        />
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
        {latestReport && <ConclusionCard report={latestReport} />}
        {latestReport && !readOnly && onSubmitFeedback && (
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
