import { Card, Tag, Space, Typography, Progress } from "antd";
import { TrophyOutlined, InfoCircleOutlined } from "@ant-design/icons";
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
  hasUnattributedHostIO,
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
  const limitations = reportLimitations(report);
  const nextActions = reportNextActions(report);
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
        </Space>
        {!hasUnattributedHostIO(report) && <Progress
          percent={Math.round(confidence * 100)}
          showInfo={false}
          strokeColor={verificationStatus === "VERIFIED" ? "#52c41a" : "#faad14"}
        />}
        <SafeMarkdown>{reportConclusionText(report)}</SafeMarkdown>
        <Text type="secondary">证据评分是内部规则分，不是诊断正确概率。报告生成与故障修复是两个独立状态；本报告本身不证明故障已解决。</Text>
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
