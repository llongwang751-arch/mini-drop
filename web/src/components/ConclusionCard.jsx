import { Card, Tag, Space, Typography, Progress } from "antd";
import { TrophyOutlined } from "@ant-design/icons";
import SafeMarkdown from "./SafeMarkdown";

const { Text } = Typography;

/**
 * 结论卡：诊断报告的可信结论。SafeMarkdown 渲染结论正文，
 * 展示置信度、证据引用与反证门禁状态。
 */
export default function ConclusionCard({ report }) {
  const confidence = report.confidence ?? 0;
  const verification = report.verification || {};
  const verificationStatus = verification.status;
  const verColor =
    verificationStatus === "VERIFIED"
      ? "green"
      : verificationStatus === "PARTIAL_WITHOUT_COUNTER"
        ? "gold"
        : "orange";

  return (
    <Card
      size="small"
      style={{ marginBottom: 10, background: "rgba(82,196,26,0.06)" }}
      title={
        <Space>
          <TrophyOutlined />
          诊断结论
        </Space>
      }
    >
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Space wrap>
          <Tag color={confidence >= 0.6 ? "green" : "orange"}>
            置信度 {(confidence * 100).toFixed(0)}%
          </Tag>
          {verificationStatus && (
            <Tag color={verColor}>反证门禁:{verificationStatus}</Tag>
          )}
        </Space>
        <Progress
          percent={Math.round(confidence * 100)}
          showInfo={false}
          strokeColor={confidence >= 0.6 ? "#52c41a" : "#faad14"}
        />
        <SafeMarkdown>{report.conclusion}</SafeMarkdown>
        {(report.evidence_refs?.length > 0 || report.counter_evidence_refs?.length > 0) && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            支持证据：{report.evidence_refs?.join("、") || "无"}
            {report.counter_evidence_refs?.length > 0 &&
              `；反证：${report.counter_evidence_refs.join("、")}`}
          </Text>
        )}
      </Space>
    </Card>
  );
}
