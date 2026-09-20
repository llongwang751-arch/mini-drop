import { Card, Tag, Space, Typography, Progress } from "antd";
import {
  TrophyOutlined,
  InfoCircleOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import SafeMarkdown from "./SafeMarkdown";
import {
  chineseDiagnosticText,
  isKnownVerificationStatus,
  verificationStatusLabel,
} from "../utils/diagnosisDisplay";
import {
  reportConclusionText,
  reportConclusionTitle,
  reportLimitations,
  reportNextActions,
  reportRemediation,
  hasUnattributedHostIO,
} from "../utils/reportPresentation";

const { Text } = Typography;

/**
 * 结论卡：诊断报告的可信结论。SafeMarkdown 渲染结论正文，
 * 展示置信度、证据引用与反证门禁状态，以及 SRE 止血与根因治理行动建议。
 */
export default function ConclusionCard({ report }) {
  const confidence = report.confidence ?? 0;
  const verification = report.verification || {};
  const verificationStatus = verification.status;
  const conclusionTitle = reportConclusionTitle(report);
  const limitations = reportLimitations(report);
  const nextActions = reportNextActions(report);
  const remediation = reportRemediation(report);

  const verColor =
    verificationStatus === "VERIFIED"
      ? "green"
      : verificationStatus === "PARTIAL_WITHOUT_COUNTER"
        ? "gold"
        : "orange";

  return (
    <Card
      size="small"
      className={`diagnosis-conclusion-card ${verificationStatus === "VERIFIED" && !hasUnattributedHostIO(report) ? "is-verified" : "is-limited"}`}
      title={
        <Space>
          {verificationStatus === "VERIFIED" && !hasUnattributedHostIO(report) ? <TrophyOutlined /> : <InfoCircleOutlined />}
          {conclusionTitle}
        </Space>
      }
    >
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Space wrap>
          <Tag color={verificationStatus === "VERIFIED" && !hasUnattributedHostIO(report) ? "green" : "orange"}>
            {hasUnattributedHostIO(report) ? "历史评分不可用于进程归因" : `证据评分 ${(confidence * 100).toFixed(0)} / 100`}
          </Tag>
          {verificationStatus && <Tag color={hasUnattributedHostIO(report) ? "orange" : verColor}>证据门禁：{hasUnattributedHostIO(report) ? "主机观察，目标归因未通过" : verificationStatusLabel(verificationStatus)}</Tag>}
          {(verification.trace_id || report.trace_id) && (
            <Tag color="geekblue" title={verification.span_id || report.span_id ? `Span ID: ${verification.span_id || report.span_id}` : undefined}>
              Trace: {verification.trace_id || report.trace_id}
            </Tag>
          )}
        </Space>
        {!hasUnattributedHostIO(report) && <Progress
          percent={Math.round(confidence * 100)}
          showInfo={false}
          strokeColor={verificationStatus === "VERIFIED" ? "#52c41a" : "#faad14"}
        />}
        <SafeMarkdown>{reportConclusionText(report)}</SafeMarkdown>
        <Text type="secondary">证据评分是内部规则分，不是诊断正确概率。报告生成与故障修复是两个独立状态；本报告本身不证明故障已解决。</Text>
        {remediation && !hasUnattributedHostIO(report) && (
          <div className="diagnosis-remediation-section">
            <div className="diagnosis-remediation-header">
              <Space>
                <ThunderboltOutlined style={{ color: "#fa541c", fontSize: 16 }} />
                <Text strong style={{ fontSize: 14 }}>SRE 处置预案与治理建议</Text>
              </Space>
            </div>
            {remediation.mitigations?.length > 0 && (
              <div className="diagnosis-remediation-block">
                <div className="diagnosis-remediation-subtitle">
                  <span className="dot dot-mitigation" />
                  <Text strong>取证与处置计划</Text>
                </div>
                <div className="diagnosis-remediation-list">
                  {remediation.mitigations.map((item, idx) => (
                    <div key={idx} className="diagnosis-remediation-card is-mitigation">
                      <div className="diagnosis-remediation-card-title">
                        <Tag color={item.urgency === "HIGH" ? "error" : item.urgency === "MEDIUM" ? "warning" : "processing"}>
                          {item.urgency === "HIGH" ? "P0 紧急压制" : item.urgency === "MEDIUM" ? "P1 优先处置" : "P2 观测调优"}
                        </Tag>
                        <Text strong>{item.title}</Text>
                      </div>
                      <div className="diagnosis-remediation-action">{item.action}</div>
                      {item.preconditions && <div><Text type="secondary">执行前提：{Array.isArray(item.preconditions) ? item.preconditions.join("；") : item.preconditions}</Text></div>}
                      {item.validation && <div><Text type="secondary">验证方式：{Array.isArray(item.validation) ? item.validation.join("；") : item.validation}</Text></div>}

                    </div>
                  ))}
                </div>
              </div>
            )}
            {remediation.root_cause_fixes?.length > 0 && (
              <div className="diagnosis-remediation-block">
                <div className="diagnosis-remediation-subtitle">
                  <ToolOutlined style={{ color: "#1890ff", marginRight: 6 }} />
                  <Text strong>治理验证计划</Text>
                </div>
                <div className="diagnosis-remediation-list">
                  {remediation.root_cause_fixes.map((item, idx) => (
                    <div key={idx} className="diagnosis-remediation-card is-fix">
                      <div className="diagnosis-remediation-card-title">
                        <Tag color={item.scope === "CODE" ? "blue" : item.scope === "ARCH" ? "purple" : "cyan"}>
                          {item.scope === "CODE" ? "代码重构" : item.scope === "ARCH" ? "架构演进" : item.scope === "REVIEW_REQUIRED" ? "需评审验证" : "环境配置"}
                        </Tag>
                        <Text strong>{item.title}</Text>
                      </div>
                      <div className="diagnosis-remediation-action">{item.action}</div>
                      {item.preconditions && <div><Text type="secondary">执行前提：{Array.isArray(item.preconditions) ? item.preconditions.join("；") : item.preconditions}</Text></div>}
                      {item.validation && <div><Text type="secondary">验证方式：{Array.isArray(item.validation) ? item.validation.join("；") : item.validation}</Text></div>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        {nextActions.length > 0 && (
          <div className="diagnosis-conclusion-actions">
            <Text strong>建议下一步</Text>
            <ul>
              {nextActions.map((action, index) => (
                <li key={`${index}:${action}`}>{chineseDiagnosticText(action)}</li>
              ))}
            </ul>
          </div>
        )}
        {limitations.length > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            结论边界：{limitations.map((item) => chineseDiagnosticText(item)).join("；")}
          </Text>
        )}
        {(report.evidence_refs?.length > 0 || report.counter_evidence_refs?.length > 0) && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {hasUnattributedHostIO(report) ? "历史引用（仅主机观察）：" : "引用支持证据："}{report.evidence_refs?.join("、") || "无"}
            {report.counter_evidence_refs?.length > 0 &&
              `；反证：${report.counter_evidence_refs.join("、")}`}
          </Text>
        )}
        {verificationStatus && !isKnownVerificationStatus(verificationStatus) && (
          <details className="diagnosis-protocol-details">
            <summary>查看技术详情</summary>
            <Text type="secondary">原始验证状态：<Text code>{verificationStatus}</Text></Text>
          </details>
        )}
      </Space>
    </Card>
  );
}
