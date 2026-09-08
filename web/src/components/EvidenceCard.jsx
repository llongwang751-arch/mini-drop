import { Card, Tag, Space, Typography } from "antd";
import { SafetyCertificateOutlined } from "@ant-design/icons";
import {
  chineseDiagnosticText,
  diagnosticToolLabel,
  evidenceDecisionLabel,
  evidenceRoleLabel,
  isKnownDiagnosticTool,
  isKnownEvidenceDecision,
  isKnownEvidenceRole,
} from "../utils/diagnosisDisplay";

const { Text } = Typography;

const ROLE_LABELS = {
  SUPPORT: "green",
  SUPPORTS: "green",
  SUPPORTED: "green",
  COUNTER: "red",
  COUNTERS: "red",
  REFUTES: "red",
  CONTROL: "cyan",
  NEUTRAL: "blue",
  UNVERIFIED_EXTERNAL: "default",
};

const DECISION_COLORS = {
  ACCEPT: "green",
  ACCEPTED: "green",
  ACCEPT_SUPPORT: "green",
  ACCEPT_COUNTER: "red",
  ACCEPT_NEUTRAL: "blue",
  ACCEPT_LIMITED: "gold",
  USABLE: "green",
  REJECT: "default",
  REJECT_LOW_QUALITY: "default",
};

/**
 * 证据卡：一条已导入的真实采集证据。展示采集器、角色、Top 函数与
 * 源码位置（file:line，若分析器产出）。
 */
export default function EvidenceCard({ evidence }) {
  const roleCode = String(evidence.role || "").toUpperCase();
  const roleColor = ROLE_LABELS[roleCode] || "default";
  const roleLabel = evidenceRoleLabel(evidence.role);
  const envelope = evidence.envelope || {};
  const source = envelope.source || {};
  const topFunctions = envelope.observation?.metadata?.top_functions || [];
  const top = topFunctions.find((row) => row && typeof row === "object") || null;
  const summary = envelope.observation?.metadata?.summary
    || envelope.observation?.summary
    || envelope.summary
    || evidence.summary;
  const decision = evidence.classification?.decision;
  const decisionCode = String(decision || "").toUpperCase();
  const decisionColor = DECISION_COLORS[decisionCode] || "default";
  const hasUnknownProtocolCode = (
    !isKnownEvidenceRole(evidence.role)
    || (decision && !isKnownEvidenceDecision(decision))
    || (source.tool_name && !isKnownDiagnosticTool(source.tool_name))
  );

  return (
    <Card size="small" className={`diagnosis-evidence-card is-${String(evidence.role || "unknown").toLowerCase()}`} title={null}>
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        <Space wrap>
          <SafetyCertificateOutlined />
          <Tag color={roleColor}>{roleLabel}</Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {source.tool_name ? diagnosticToolLabel(source.tool_name) : "未知采集器"}
          </Text>
          {decision && <Tag color={decisionColor}>{evidenceDecisionLabel(decision)}</Tag>}
        </Space>
        {summary && <Text>{chineseDiagnosticText(summary)}</Text>}
        {top && (
          <div>
            <Text strong>{top.name}</Text>
            {typeof top.percent === "number" && (
              <Tag color="volcano" style={{ marginLeft: 8 }}>{top.percent}%</Tag>
            )}
            {(top.file || top.line) && (
              <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                {top.file}:{top.line}
              </Text>
            )}
          </div>
        )}
        <Text type="secondary" className="diagnosis-evidence-id">证据 {evidence.evidence_id}</Text>
        {hasUnknownProtocolCode && (
          <details className="diagnosis-protocol-details">
            <summary>查看技术详情</summary>
            <Space direction="vertical" size={2} style={{ width: "100%", marginTop: 6 }}>
              <Text type="secondary">原始证据角色：<Text code>{evidence.role || "未返回"}</Text></Text>
              {decision && <Text type="secondary">原始门禁码：<Text code>{decision}</Text></Text>}
              {source.tool_name && <Text type="secondary">原始工具标识：<Text code>{source.tool_name}</Text></Text>}
            </Space>
          </details>
        )}
      </Space>
    </Card>
  );
}
