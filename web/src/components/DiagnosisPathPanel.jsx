import { Empty, Space, Tag, Timeline, Typography } from "antd";

const { Text } = Typography;

// 将诊断事件转换为用户能理解的决策步骤，便于回放和审计。
const EVENT_LABELS = {
  "diagnosis.created": "创建诊断会话",
  "diagnosis.clarified": "补充诊断范围",
  "planner.needs_clarification": "等待补充信息",
  "hypothesis.created": "建立候选假设",
  "tool_call.policy_evaluated": "评估工具调用风险",
  "tool_call.requested": "申请采集工具",
  "tool_call.arguments_updated": "修改工具参数",
  "tool_call.approval_decided": "人工审批工具",
  "tool_call.task_created": "创建采集任务",
  "tool_call.task_terminal": "采集任务结束",
  "tool_call.completed": "工具执行完成",
  "evidence.added": "加入诊断证据",
  "task_evidence.imported": "导入采集证据",
  "report.generated": "生成诊断报告",
  "adaptive_probe_planned": "AI 选择下一步取证",
  "falsification_round_planned": "规划反证取证",
  "falsification_route_replanned": "切换下一条取证路线",
  "adaptive_probe_unavailable": "检查可用探针",
  "diagnosis_stop_condition_met": "达到诊断停止条件",
  "planner.insufficient_replanned": "证据不足，AI 自动切换取证方向",
  "diagnosis.route_learned": "验证成功，沉淀诊断路线",
};

const EVENT_COLORS = {
  "diagnosis.created": "blue",
  "diagnosis.clarified": "blue",
  "planner.needs_clarification": "orange",
  "hypothesis.created": "purple",
  "tool_call.policy_evaluated": "gray",
  "tool_call.requested": "geekblue",
  "tool_call.arguments_updated": "geekblue",
  "tool_call.approval_decided": "volcano",
  "tool_call.task_created": "blue",
  "tool_call.task_terminal": "cyan",
  "tool_call.completed": "green",
  "evidence.added": "green",
  "task_evidence.imported": "green",
  "report.generated": "green",
  "adaptive_probe_planned": "purple",
  "falsification_round_planned": "purple",
  "falsification_route_replanned": "geekblue",
  "adaptive_probe_unavailable": "orange",
  "diagnosis_stop_condition_met": "gray",
  "planner.insufficient_replanned": "purple",
  "diagnosis.route_learned": "green",
};

function describe(event) {
  const label = EVENT_LABELS[event.event_type] || event.event_type;
  const payload = event.payload_json || event.payload || {};
  let extra = "";
  if (event.event_type === "tool_call.task_created" && payload.task_id) {
    extra = ` · 任务 ${payload.task_id}`;
  } else if (event.event_type === "tool_call.approval_decided") {
    extra = payload.approved ? " · 已通过" : " · 已拒绝";
  } else if (event.event_type === "tool_call.task_terminal" && payload.task_status) {
    extra = ` · ${payload.task_status}`;
  } else if (event.event_type === "tool_call.requested" && payload.tool_name) {
    extra = ` · ${payload.tool_name}`;
  } else if (event.event_type === "adaptive_probe_planned") {
    const planner = payload.planner_source === "ai_tool_call" ? "AI 受约束规划" : "确定性回退";
    extra = ` · ${payload.probe_id || "-"} → ${payload.target || "-"} · ${planner}`;
  } else if (event.event_type === "falsification_round_planned") {
    extra = ` · 第 ${payload.round_index || "-"} 轮 · ${payload.probe_id || "-"}`;
  } else if (event.event_type === "diagnosis_stop_condition_met") {
    extra = ` · ${payload.reason || "预算或证据边界已到达"}`;
  } else if (event.event_type === "planner.insufficient_replanned") {
    extra = ` · 第 ${payload.round_index || "-"} 轮 · ${payload.tool_name || "-"}`;
  } else if (event.event_type === "diagnosis.route_learned") {
    extra = ` · ${(payload.tool_route || []).join(" → ")}`;
  }
  return `${label}${extra}`;
}

export default function DiagnosisPathPanel({ events = [] }) {
  const ordered = [...(events || [])].sort(
    (a, b) => new Date(a.occurred_at || 0) - new Date(b.occurred_at || 0),
  );

  if (ordered.length === 0) {
    return <Empty description="暂无诊断路径记录" style={{ margin: "24px 0" }} />;
  }

  return (
    <Timeline
      items={ordered.map((event, index) => ({
        color: EVENT_COLORS[event.event_type] || "gray",
        children: (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Space wrap>
              <Tag style={{ fontSize: 11 }}>#{index + 1}</Tag>
              <Text strong style={{ fontSize: 13 }}>{describe(event)}</Text>
            </Space>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {event.event_type} · {event.actor} ·{" "}
              {event.occurred_at ? new Date(event.occurred_at).toLocaleTimeString() : ""}
            </Text>
            {(event.payload_json || event.payload || {}).reason && (
              <Text style={{ fontSize: 12 }}>选择依据：{(event.payload_json || event.payload || {}).reason}</Text>
            )}
            {(event.payload_json || event.payload || {}).expected_observation && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                预期观察：{(event.payload_json || event.payload || {}).expected_observation}
              </Text>
            )}
            {(event.payload_json || event.payload || {}).falsification_criterion && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                推翻条件：{(event.payload_json || event.payload || {}).falsification_criterion}
              </Text>
            )}
          </Space>
        ),
      }))}
    />
  );
}
