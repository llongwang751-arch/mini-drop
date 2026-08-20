import { Card, Tag, Space, Typography } from "antd";
import { SafetyCertificateOutlined } from "@ant-design/icons";

const { Text } = Typography;

const ROLE_LABELS = {
  SUPPORT: ["green", "支持"],
  COUNTER: ["red", "反证"],
  NEUTRAL: ["blue", "中性"],
  UNVERIFIED_EXTERNAL: ["default", "外部未验证"],
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
  const decision = evidence.classification?.decision;

  return (
    <Card size="small" style={{ marginBottom: 10 }} title={null}>
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        <Space wrap>
          <SafetyCertificateOutlined />
          <Tag color={roleColor}>{roleLabel}</Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {source.tool_name || "未知采集器"}
          </Text>
          {decision && (
            <Tag color={decision === "ACCEPT_SUPPORT" ? "green" : "gold"}>{decision}</Tag>
          )}
        </Space>
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
        <Text type="secondary" style={{ fontSize: 12 }}>
          证据 {evidence.evidence_id}
        </Text>
      </Space>
    </Card>
  );
}
