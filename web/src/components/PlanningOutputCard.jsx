import { Alert, Space, Tag, Typography } from "antd";

const LABELS = {
  NORMAL: "描述范围内未提出异常",
  INSUFFICIENT_EVIDENCE: "缺少必要观测",
  REFUSED: "拒绝此请求",
};

export function boundedPlanningOutput(payload) {
  const output = payload?.planning_output;
  if (output?.schema_version !== "mini-drop.planning-output.v2"
    || !Object.hasOwn(LABELS, output.disposition)
    || output.tool_name !== null || !Array.isArray(output.hypotheses)
    || output.hypotheses.length !== 0 || output.causal_root_cause_verified !== false
    || payload.claim_scope !== "PLANNING_ONLY_NOT_HEALTH_OR_CAUSATION"
    || payload.is_evidence !== false || payload.health_check_performed !== false
    || payload.new_tool_requested !== false) return null;
  return output;
}

export default function PlanningOutputCard({ payload }) {
  const output = boundedPlanningOutput(payload);
  if (!output) return null;
  return (
    <Alert
      showIcon
      type={output.disposition === "NORMAL" ? "info" : "warning"}
      message={LABELS[output.disposition]}
      description={(
        <Space direction="vertical" size={6}>
          <Typography.Text>{payload.reason || output.reasoning_summary}</Typography.Text>
          <Typography.Text type="secondary">
            本次是规划判断，尚未完成当前状态检查；不代表业务健康，也不证明故障根因。未新增采集任务。
          </Typography.Text>
          {output.missing_evidence?.length > 0 && (
            <Typography.Text>待补充观测：{output.missing_evidence.join("；")}</Typography.Text>
          )}
          {output.limitations?.length > 0 && (
            <Typography.Text type="secondary">判断范围：{output.limitations.join("；")}</Typography.Text>
          )}
          <Tag>规划结果已记录</Tag>
        </Space>
      )}
    />
  );
}
