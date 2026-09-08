import { Card, Tag, Space, Typography, Progress } from "antd";
import { TrophyOutlined } from "@ant-design/icons";
import SafeMarkdown from "./SafeMarkdown";
import {
  chineseDiagnosticText,
  isKnownVerificationStatus,
  verificationStatusLabel,
} from "../utils/diagnosisDisplay";
import {
  reportConclusionText,
  reportConclusionTitle,
} from "../utils/reportPresentation";

const { Text } = Typography;

/**
 * 结论卡：诊断报告的可信结论。SafeMarkdown 渲染结论正文，
 * 展示置信度、证据引用与反证门禁状态。
 */
export default function ConclusionCard({ report }) {
  const confidence = report.confidence ?? 0;
  const verification = report.verification || {};
  const verificationStatus = verification.status;
  const conclusionTitle = reportConclusionTitle(report);
  const verColor =
    verificationStatus === "VERIFIED"
      ? "green"
      : verificationStatus === "PARTIAL_WITHOUT_COUNTER"
        ? "gold"
        : "orange";

  return (
    <Card
      size="small"
      className="diagnosis-conclusion-card"
      title={
        <Space>
          <TrophyOutlined />
          {conclusionTitle}
        </Space>
      }
    >
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Space wrap>
          <Tag color={confidence >= 0.6 ? "green" : "orange"}>
            置信度 {(confidence * 100).toFixed(0)}%
          </Tag>
          {verificationStatus && <Tag color={verColor}>证据门禁：{verificationStatusLabel(verificationStatus)}</Tag>}
        </Space>
        <Progress
          percent={Math.round(confidence * 100)}
          showInfo={false}
          strokeColor={confidence >= 0.6 ? "#52c41a" : "#faad14"}
        />
        <SafeMarkdown>{reportConclusionText(report)}</SafeMarkdown>
        {report.next_actions?.length > 0 && (
          <div className="diagnosis-conclusion-actions">
            <Text strong>建议下一步</Text>
            <ul>
              {report.next_actions.map((action, index) => (
                <li key={`${index}:${action}`}>{chineseDiagnosticText(action)}</li>
              ))}
            </ul>
          </div>
        )}
        {report.limitations?.length > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            结论边界：{report.limitations.map((item) => chineseDiagnosticText(item)).join("；")}
          </Text>
        )}
        {(report.evidence_refs?.length > 0 || report.counter_evidence_refs?.length > 0) && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            引用支持证据：{report.evidence_refs?.join("、") || "无"}
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
