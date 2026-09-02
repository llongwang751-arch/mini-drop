import { Card, Tag, Space, Typography } from "antd";
import { SafetyCertificateOutlined } from "@ant-design/icons";

const { Text } = Typography;

const ROLE_LABELS = {
  SUPPORT: ["green", "支持"],
  COUNTER: ["red", "反证"],
  NEUTRAL: ["blue", "中性"],
  UNVERIFIED_EXTERNAL: ["default", "外部未验证"],
};

const DECISION_LABELS = {
  ACCEPT_SUPPORT: ["green", "采信为支持证据"],
  ACCEPT_COUNTER: ["red", "采信为反证"],
  ACCEPT_NEUTRAL: ["blue", "采信为中性观察"],
  ACCEPT_LIMITED: ["gold", "有限采信"],
  REJECT: ["default", "门禁拒绝"],
  REJECT_LOW_QUALITY: ["default", "低质量拒绝"],
};

/**
 * 证据卡：一条已导入的真实采集证据。展示采集器、角色、Top 函数与
 * 源码位置（file:line，若分析器产出）。
 */
export default function EvidenceCard({ evidence }) {
  const [roleColor, roleLabel] = ROLE_LABELS[evidence.role] || ["default", evidence.role || "证据"];
  const envelope = evidence.envelope || {};
  const source = envelope.source || {};
  const topFunctions = envelope.observation?.metadata?.top_functions || [];
  const top = topFunctions.find((row) => row && typeof row === "object") || null;
  const summary = envelope.observation?.metadata?.summary
    || envelope.observation?.summary
    || envelope.summary
    || evidence.summary;
  const decision = evidence.classification?.decision;
  const [decisionColor, decisionLabel] = DECISION_LABELS[decision] || ["default", decision];

  return (
    <Card size="small" className={`diagnosis-evidence-card is-${String(evidence.role || "unknown").toLowerCase()}`} title={null}>
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        <Space wrap>
          <SafetyCertificateOutlined />
          <Tag color={roleColor}>{roleLabel}</Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {source.tool_name || "未知采集器"}
          </Text>
          {decision && <Tag color={decisionColor}>{decisionLabel}</Tag>}
        </Space>
        {summary && <Text>{summary}</Text>}
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
      </Space>
    </Card>
  );
}
