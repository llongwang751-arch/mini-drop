import { Collapse, Empty, Space, Tag, Timeline, Typography } from "antd";
import {
  chineseDiagnosticText,
  diagnosisActorLabel,
  diagnosisEventLabel,
  diagnosticStatusLabel,
  diagnosticToolLabel,
  semanticDiagnosticKey,
} from "../utils/diagnosisDisplay";

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
  "diagnostic_capability_gap": "发现诊断能力缺口",
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
  "diagnostic_capability_gap": "red",
  "diagnosis_stop_condition_met": "gray",
  "planner.insufficient_replanned": "purple",
  "diagnosis.route_learned": "green",
};

function describe(event) {
  const label = EVENT_LABELS[event.event_type] || diagnosisEventLabel(event.event_type);
  const payload = event.payload_json || event.payload || {};
  let extra = "";
  if (event.event_type === "tool_call.task_created" && payload.task_id) {
    extra = ` · 任务 ${payload.task_id}`;
  } else if (event.event_type === "tool_call.approval_decided") {
    extra = payload.approved ? " · 已通过" : " · 已拒绝";
  } else if (event.event_type === "tool_call.task_terminal" && payload.task_status) {
    extra = ` · ${diagnosticStatusLabel(payload.task_status)}`;
  } else if (event.event_type === "tool_call.requested" && payload.tool_name) {
    extra = ` · ${diagnosticToolLabel(payload.tool_name)}`;
  } else if (event.event_type === "adaptive_probe_planned") {
    const planner = payload.planner_source === "ai_tool_call" ? "AI 受约束规划" : "确定性回退";
    extra = ` · ${payload.probe_id || "-"} → ${payload.target || "-"} · ${planner}`;
  } else if (event.event_type === "falsification_round_planned") {
    extra = ` · 第 ${payload.round_index || "-"} 轮 · ${payload.probe_id || "-"}`;
  } else if (event.event_type === "diagnosis_stop_condition_met") {
    extra = ` · ${chineseDiagnosticText(payload.reason || "预算或证据边界已到达")}`;
  } else if (event.event_type === "planner.insufficient_replanned") {
    extra = ` · 第 ${payload.round_index || "-"} 轮 · ${payload.tool_name || "-"}`;
  } else if (event.event_type === "diagnosis.route_learned") {
    extra = ` · ${(payload.tool_route || []).join(" → ")}`;
  } else if (event.event_type === "diagnostic_capability_gap") {
    extra = ` · ${payload.gap_reason || "缺少可用取证能力"}`;
  }
  return `${label}${extra}`;
}

function eventIteration(event, currentIteration) {
  const payload = event.payload_json || event.payload || {};
  const explicit = Number(payload.iteration ?? payload.search_iteration ?? payload.iteration_index);
  if (Number.isFinite(explicit) && explicit >= 0) return explicit;
  if (event.event_type === "lats.search_started") return 0;
  return currentIteration;
}

function eventSemanticKey(event, iteration) {
  const payload = event.payload_json || event.payload || {};
  return [
    iteration,
    event.event_type,
    event.actor,
    payload.node_id,
    semanticDiagnosticKey(payload.reason || payload.summary || payload.detail || ""),
    JSON.stringify(payload.path_node_ids || []),
  ].join("|");
}

export function groupDiagnosisEvents(events = []) {
  const ordered = [...(events || [])].sort(
    (a, b) => new Date(a.occurred_at || 0) - new Date(b.occurred_at || 0),
  );
  const seen = new Set();
  const seenIds = new Set();
  const groups = [];
  let currentIteration = 0;
  ordered.forEach((event) => {
    const isLats = String(event.event_type || "").startsWith("lats.");
    if (isLats) currentIteration = eventIteration(event, currentIteration);
    const iteration = isLats ? currentIteration : null;
    const semanticKey = eventSemanticKey(event, iteration);
    if ((event.event_id && seenIds.has(event.event_id)) || seen.has(semanticKey)) return;
    if (event.event_id) seenIds.add(event.event_id);
    seen.add(semanticKey);
    const groupKey = isLats ? `lats:${iteration}` : "diagnosis";
    let group = groups[groups.length - 1];
    if (!group || group.key !== groupKey) {
      group = {
        key: groups.some((item) => item.key === groupKey) ? `${groupKey}:${groups.length}` : groupKey,
        iteration,
        isLats,
        events: [],
      };
      groups.push(group);
    }
    group.events.push(event);
  });
  return groups;
}

function EventTimeline({ events, startIndex = 0 }) {
  return (
    <Timeline
      items={events.map((event, index) => {
        const payload = event.payload_json || event.payload || {};
        return {
          color: EVENT_COLORS[event.event_type] || (String(event.event_type || "").startsWith("lats.") ? "purple" : "gray"),
          children: (
            <Space direction="vertical" size={2} style={{ width: "100%" }}>
              <Space wrap>
                <Tag style={{ fontSize: 11 }}>#{startIndex + index + 1}</Tag>
                <Text strong style={{ fontSize: 13 }}>{describe(event)}</Text>
                <Tag>{diagnosisActorLabel(event.actor)}</Tag>
              </Space>
              {event.occurred_at && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {new Date(event.occurred_at).toLocaleTimeString()}
                </Text>
              )}
              {payload.reason && (
                <Text style={{ fontSize: 12 }}>选择依据：{chineseDiagnosticText(payload.reason)}</Text>
              )}
              {payload.summary && (
                <Text style={{ fontSize: 12 }}>步骤摘要：{chineseDiagnosticText(payload.summary)}</Text>
              )}
              {payload.expected_observation && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  预期观察：{chineseDiagnosticText(payload.expected_observation)}
                </Text>
              )}
              {payload.falsification_criterion && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  推翻条件：{chineseDiagnosticText(payload.falsification_criterion)}
                </Text>
              )}
              {payload.route_memory?.selected_probe_prior != null && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  历史路线先验：{Math.round(payload.route_memory.selected_probe_prior * 100)}%
                  （只参与排序，不绕过审批）
                </Text>
              )}
              {payload.evolution_candidate?.proposal && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  能力演进建议：{chineseDiagnosticText(payload.evolution_candidate.proposal)}
                </Text>
              )}
              <details className="diagnosis-event-raw">
                <summary>查看原始事件信息</summary>
                <Text code>{event.event_type || "未记录事件码"}</Text>
                {event.actor && <Text type="secondary"> · {event.actor}</Text>}
              </details>
            </Space>
          ),
        };
      })}
    />
  );
}

export default function DiagnosisPathPanel({ events = [] }) {
  const groups = groupDiagnosisEvents(events);

  if (groups.length === 0) {
    return <Empty description="暂无诊断路径记录" style={{ margin: "24px 0" }} />;
  }

  let offset = 0;
  const items = groups.map((group) => {
    const uniqueSteps = [...new Set(group.events.map((event) => diagnosisEventLabel(event.event_type)))];
    const item = {
      key: group.key,
      label: (
        <Space wrap>
          <Text strong>{group.isLats ? (group.iteration > 0 ? `LATS 第 ${group.iteration} 次搜索迭代` : "LATS 搜索准备") : "诊断事件"}</Text>
          <Tag>{group.events.length} 个步骤</Tag>
          <Text type="secondary">{uniqueSteps.slice(0, 3).join("、")}{uniqueSteps.length > 3 ? "…" : ""}</Text>
        </Space>
      ),
      children: <EventTimeline events={group.events} startIndex={offset} />,
    };
    offset += group.events.length;
    return item;
  });
  return (
    <Collapse
      className="diagnosis-event-groups"
      defaultActiveKey={[groups[groups.length - 1].key]}
      items={items}
    />
  );
}
