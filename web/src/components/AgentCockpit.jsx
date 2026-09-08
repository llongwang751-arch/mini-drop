import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Empty,
  List,
  Modal,
  Progress,
  Select,
  Space,
  Steps,
  Table,
  Tabs,
  Tag,
  Timeline,
  Typography,
  message,
} from "antd";
import {
  ApartmentOutlined,
  ApiOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import {
  formatLatsScore,
  latsEnvironmentLabel,
  latsExecutionModeLabel,
  latsPhaseGroup,
  latsPhaseLabel,
  latsRolloutLabel,
  latsTerminationLabel,
  normalizeLatsSearch,
} from "../utils/latsSearch";
import {
  checkpointLabel,
  chineseDiagnosticText,
  dedupeDiagnosticRows,
  diagnosisEventLabel,
  diagnosticStatusLabel,
  diagnosticToolLabel,
  evidenceDecisionLabel,
  evidenceRoleLabel,
  isKnownEvidenceDecision,
  isKnownEvidenceRole,
  runtimeStatusLabel,
} from "../utils/diagnosisDisplay";
import { listOperatorMemories, putOperatorMemory } from "../api/client";
import "./AgentCockpit.css";

const { Paragraph, Text } = Typography;

const STAGE_LABELS = {
  CREATED: "理解问题",
  NEEDS_CLARIFICATION: "确认范围",
  HYPOTHESIZING: "生成假设",
  PLANNING: "任务规划",
  WAITING_APPROVAL: "等待人工审批",
  COLLECTING: "工具取证",
  ANALYZING: "分析采集物",
  EVALUATING: "证据裁决",
  REPORTING: "生成报告",
  COMPLETED: "结论验证",
  INSUFFICIENT_EVIDENCE: "证据不足",
  FAILED: "执行失败",
  CANCELLED: "已取消",
};

const SUCCESS_TOOL_STATES = new Set(["DONE", "COMPLETED", "SUCCEEDED", "SUCCESS"]);
const FAILED_TOOL_STATES = new Set(["FAILED", "REJECTED", "DENIED", "CANCELLED", "CANCELED"]);
const ACCEPTED_EVIDENCE_STATES = new Set([
  "ACCEPT",
  "ACCEPTED",
  "ACCEPT_SUPPORT",
  "ACCEPT_COUNTER",
  "ACCEPT_NEUTRAL",
  "SUPPORT",
  "SUPPORTED",
  "USABLE",
]);

const PLAN_EVENT_TYPES = new Set([
  "hypothesis.created",
  "adaptive_probe_planned",
  "falsification_round_planned",
  "falsification_route_replanned",
  "planner.insufficient_replanned",
  "diagnosis_stop_condition_met",
]);

function rows(value) {
  return Array.isArray(value) ? value : [];
}

function eventPayload(event) {
  return event?.payload_json || event?.payload || {};
}

function printable(value, fallback = "暂无") {
  if (value == null || value === "") return fallback;
  if (typeof value === "string" || typeof value === "number") return String(value);
  return fallback;
}

function percent(numerator, denominator) {
  if (!denominator) return null;
  return Math.round((numerator / denominator) * 100);
}

function evidenceDecision(item) {
  return String(
    item?.classification?.decision || item?.decision || item?.gate_decision || "",
  ).toUpperCase();
}

function isAcceptedEvidence(item) {
  if (item?.classification?.can_support_conclusion === true) return true;
  return ACCEPTED_EVIDENCE_STATES.has(evidenceDecision(item));
}

function sourceOfEvidence(item) {
  return item?.envelope?.source || item?.source || {};
}

function currentRound(detail, resources) {
  const direct = [
    detail?.current_round,
    detail?.round_index,
    resources?.explorationTree?.stats?.current_round,
    resources?.explorationTree?.stats?.rounds,
    resources?.budget?.diagnosis_rounds_used,
  ].map(Number).filter((value) => Number.isFinite(value) && value >= 0);
  const derived = [
    ...rows(resources?.hypotheses).map((item) => Number(item?.round_index || 0)),
    ...rows(resources?.interventions).map((item) => Number(item?.round_index || 0)),
  ].filter((value) => Number.isFinite(value));
  return Math.max(0, ...direct, ...derived);
}

function findRuntime(detail, resources) {
  const direct = detail?.agent_runtime || detail?.runtime;
  const eventRuntime = rows(resources?.events)
    .map(eventPayload)
    .map((payload) => payload?.agent_runtime || payload?.runtime || payload)
    .find((candidate) => candidate?.framework || candidate?.agent_framework || candidate?.checkpoint_backend);
  const runtime = resources?.runtimeStatus || direct || eventRuntime;
  if (!runtime) return null;
  return {
    framework: runtime.framework || runtime.agent_framework,
    version: runtime.version || runtime.agent_version,
    checkpoint: runtime.actual_backend || runtime.checkpoint_backend || runtime.checkpointer,
    requestedCheckpoint: runtime.requested_backend,
    status: runtime.status,
    degraded: runtime.degraded === true,
    fallbackReason: runtime.fallback_reason,
    persistenceGuarantee: runtime.persistence_guarantee,
    survivesRestart: runtime.survives_process_restart,
    thread: runtime.thread_id || runtime.threadId || direct?.thread_id || direct?.threadId,
  };
}

function describePlanEvent(event) {
  const payload = eventPayload(event);
  const detail = payload.reason
    || payload.generation_reason
    || payload.expected_observation
    || payload.tool_name
    || payload.probe_id;
  return detail ? chineseDiagnosticText(printable(detail)) : "该步骤已写入诊断事件流";
}

function hypothesisSourceLabel(value) {
  const labels = {
    MODEL: "AI 生成",
    MODEL_REPLAN: "AI 重新规划",
    DETERMINISTIC_RULE: "确定性规则",
    USER_GUIDED_FALLBACK: "用户纠正",
    COUNTER_EVIDENCE_RULE: "反证触发",
    SYSTEM_FALLBACK: "开放探索兜底",
    USER: "人工假设",
  };
  return labels[String(value || "").toUpperCase()] || chineseDiagnosticText(value, "来源未知");
}

function skillIdentity(item, index) {
  return item?.skill_id || item?.id || item?.activation_id || `skill-record-${index}`;
}

function globalMemoryRows(activations, sourceSkill) {
  const output = [];
  const seen = new Set();
  [...rows(activations), ...(sourceSkill ? [sourceSkill] : [])].forEach((item, index) => {
    const id = skillIdentity(item, index);
    if (seen.has(id)) return;
    seen.add(id);
    output.push(item);
  });
  return output;
}

function linkedEvidenceRate(toolCalls, acceptedEvidence) {
  const successful = toolCalls.filter((item) => SUCCESS_TOOL_STATES.has(String(item?.status || "").toUpperCase()));
  if (!successful.length) return { value: null, note: "暂无成功工具样本" };
  const evidenceLinks = acceptedEvidence.map(sourceOfEvidence);
  const hasAnyLink = evidenceLinks.some((source) => source?.tool_call_id || source?.task_id);
  if (!hasAnyLink) return { value: null, note: "可信证据未返回工具/任务关联，无法计算" };
  const linked = successful.filter((tool) => evidenceLinks.some((source) => (
    (source?.tool_call_id && source.tool_call_id === tool?.tool_call_id)
    || (source?.task_id && source.task_id === tool?.task_id)
  ))).length;
  return {
    value: percent(linked, successful.length),
    note: `${linked}/${successful.length} 个成功工具产出可关联证据`,
  };
}

function gateValue(value) {
  if (value == null) return "暂无";
  if (typeof value === "boolean") return value ? "通过" : "未通过";
  if (typeof value === "object" && value.passed != null) return value.passed ? "通过" : "未通过";
  return printable(value);
}

function RuntimeBar({ runtime, connected }) {
  return (
    <div className="agent-cockpit-runtime">
      <Space size={8} wrap>
        <span className={`agent-cockpit-connection ${connected ? "is-connected" : "is-fallback"}`}>
          <span className="agent-cockpit-connection-dot" />
          {connected ? "事件流实时" : "轮询兜底"}
        </span>
        {runtime ? (
          <>
            <Tag color="blue">运行框架 {printable(runtime.framework)}</Tag>
            {runtime.version && <Tag>版本 {runtime.version}</Tag>}
            {runtime.checkpoint && <Tag color="cyan">会话保存 {checkpointLabel(runtime.checkpoint)}</Tag>}
            {runtime.status && (
              <Tag color={runtime.degraded ? "red" : "green"}>{runtimeStatusLabel(runtime.status, runtime.degraded)}</Tag>
            )}
            {runtime.thread && <Text code copyable>{runtime.thread}</Text>}
            {runtime.degraded && runtime.fallbackReason && (
              <Text type="danger">{chineseDiagnosticText(runtime.fallbackReason)}</Text>
            )}
          </>
        ) : (
          <Text type="secondary">运行框架信息尚未随本次诊断返回</Text>
        )}
      </Space>
    </div>
  );
}

function StagePanel({ detail, resources, stage, round, runtime }) {
  const classification = detail?.classification || detail?.intent || {};
  const category = typeof classification === "object"
    ? classification.category || classification.intent || classification.label
    : classification;
  const target = detail?.target || {};
  const intentEvents = rows(resources.events).filter((event) => (
    event?.event_type === "diagnosis.created"
    || event?.event_type === "diagnosis.clarified"
    || event?.event_type === "planner.needs_clarification"
  ));
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message={`当前处于“${stage}” · 第 ${round} 轮`}
        description="阶段来自持久化诊断状态；用户补充信息会开启新轮次，但不会被当作事实证据。"
      />
      <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="原始问题" span={2}>{printable(detail?.query || detail?.request?.query)}</Descriptions.Item>
        <Descriptions.Item label="意图分类">{chineseDiagnosticText(printable(category))}</Descriptions.Item>
        <Descriptions.Item label="诊断状态"><Tag>{diagnosticStatusLabel(detail?.status)}</Tag></Descriptions.Item>
        <Descriptions.Item label="目标服务">{printable(target.service || detail?.service)}</Descriptions.Item>
        <Descriptions.Item label="运行环境">{printable(target.environment || detail?.environment)}</Descriptions.Item>
        {runtime && (
          <>
            <Descriptions.Item label="Agent 运行框架">{printable(runtime.framework)}</Descriptions.Item>
            <Descriptions.Item label="实际会话保存方式">
              <Space wrap size={4}>
                <Tag color={runtime.degraded ? "red" : "cyan"}>{checkpointLabel(runtime.checkpoint)}</Tag>
                {runtime.requestedCheckpoint && runtime.requestedCheckpoint !== runtime.checkpoint && (
                  <Text type="secondary">请求 {checkpointLabel(runtime.requestedCheckpoint)}</Text>
                )}
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="跨进程重启恢复">
              {runtime.survivesRestart ? "支持" : "不支持"}
            </Descriptions.Item>
            <Descriptions.Item label="持久化保证">{chineseDiagnosticText(printable(runtime.persistenceGuarantee))}</Descriptions.Item>
          </>
        )}
      </Descriptions>
      {intentEvents.length ? (
        <Timeline items={intentEvents.map((event) => ({
          color: "blue",
          children: <><Text strong>{diagnosisEventLabel(event.event_type)}</Text><br /><Text type="secondary">{describePlanEvent(event)}</Text></>,
        }))} />
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无意图理解事件" />}
    </Space>
  );
}

function PlanPanel({ hypotheses, events }) {
  const planEvents = events.filter((event) => PLAN_EVENT_TYPES.has(event?.event_type));
  const uniqueHypotheses = dedupeDiagnosticRows(hypotheses, (item) => (
    item?.source === "SYSTEM_FALLBACK" || /OTHER\s*\/\s*UNKNOWN/i.test(item?.statement || "")
      ? `unknown:${item?.round_index || 1}`
      : `${item?.round_index || 1}:${item?.statement || ""}`
  ));
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="计划是可证伪假设，不是预先写死的流程"
        description="每轮证据、反证或人工转向都可以改变后续路线；下表只展示服务端实际保存的假设。"
      />
      <Table
        rowKey={(item, index) => item?.hypothesis_id || `hypothesis-${index}`}
        size="small"
        pagination={false}
        locale={{ emptyText: "尚未生成假设" }}
        dataSource={uniqueHypotheses}
        columns={[
          { title: "轮次", width: 76, render: (_, item) => item?.round_index ?? "-" },
          { title: "可验证假设", dataIndex: "statement", ellipsis: true, render: (value) => chineseDiagnosticText(printable(value)) },
          { title: "状态", width: 120, render: (_, item) => <Tag>{diagnosticStatusLabel(item?.status)}</Tag> },
          { title: "来源", width: 150, render: (_, item) => hypothesisSourceLabel(item?.source) },
        ]}
      />
      {planEvents.length > 0 && (
        <div>
          <Text strong>真实规划轨迹</Text>
          <Timeline className="agent-cockpit-timeline" items={planEvents.map((event) => ({
            color: event.event_type?.includes("stop") ? "gray" : "purple",
            children: <><Text>{diagnosisEventLabel(event.event_type)}</Text><br /><Text type="secondary">{describePlanEvent(event)}</Text></>,
          }))} />
        </div>
      )}
    </Space>
  );
}

function ToolPanel({ toolCalls }) {
  return (
    <>
      <Alert
        type="info"
        showIcon
        message="工具调用受白名单、目标绑定、权限、风险和预算共同约束"
        description="这里展示真实工具调用与关联采集任务；Agent 不直接获得 Shell。"
        style={{ marginBottom: 16 }}
      />
      <Table
        rowKey={(item, index) => item?.tool_call_id || `tool-${index}`}
        size="small"
        pagination={false}
        locale={{ emptyText: "尚未调用工具" }}
        dataSource={toolCalls}
        columns={[
          { title: "工具", dataIndex: "tool_name", render: (value) => diagnosticToolLabel(value) },
          { title: "状态", width: 120, render: (_, item) => <Tag>{diagnosticStatusLabel(item?.status)}</Tag> },
          { title: "策略判定", width: 130, render: (_, item) => chineseDiagnosticText(printable(item?.policy_decision)) },
          { title: "任务 ID", width: 210, ellipsis: true, render: (_, item) => printable(item?.task_id) },
        ]}
      />
    </>
  );
}

function EvidencePanel({ evidence, acceptedEvidence }) {
  const protocolCell = (value, label, known) => (
    <Space direction="vertical" size={2}>
      <span>{label(value)}</span>
      {value && !known(value) && (
        <details className="diagnosis-protocol-details">
          <summary>技术详情</summary>
          <Text code>{value}</Text>
        </details>
      )}
    </Space>
  );
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="success"
        showIcon
        message="只有可信证据（Evidence）可以支撑事实结论"
        description="用户输入、会话记忆、Skill 路线和模型假设都只是调查上下文；必须经过目标、时间窗、完整性和质量门禁后才能成为可信证据。"
      />
      <Space wrap>
        <Tag color="green">可用于结论 {acceptedEvidence.length}</Tag>
        <Tag>证据总数 {evidence.length}</Tag>
      </Space>
      <Table
        rowKey={(item, index) => item?.evidence_id || `evidence-${index}`}
        size="small"
        pagination={false}
        locale={{ emptyText: "尚无通过门禁的结构化证据" }}
        dataSource={evidence}
        columns={[
          { title: "证据 ID", dataIndex: "evidence_id", ellipsis: true, render: (value) => printable(value) },
          {
            title: "角色",
            width: 130,
            render: (_, item) => protocolCell(item?.role, evidenceRoleLabel, isKnownEvidenceRole),
          },
          {
            title: "门禁判定",
            width: 190,
            render: (_, item) => {
              const decision = evidenceDecision(item);
              return (
                <Space direction="vertical" size={2}>
                  <Tag color={isAcceptedEvidence(item) ? "green" : "default"}>
                    {evidenceDecisionLabel(decision)}
                  </Tag>
                  {decision && !isKnownEvidenceDecision(decision) && (
                    <details className="diagnosis-protocol-details">
                      <summary>技术详情</summary>
                      <Text code>{decision}</Text>
                    </details>
                  )}
                </Space>
              );
            },
          },
          { title: "任务 ID", width: 190, ellipsis: true, render: (_, item) => printable(sourceOfEvidence(item)?.task_id) },
        ]}
      />
    </Space>
  );
}

function MemoryPanel({ detail, interventions, skillActivations, globalMemory }) {
  const query = String(detail?.query || detail?.request?.query || "").trim();
  const sessionRows = [
    ...(query ? [{ id: "initial-query", role: "用户问题", message: query, round: 0 }] : []),
    ...interventions.map((item, index) => ({
      id: item?.intervention_id || `intervention-${index}`,
      role: item?.action || "人工干预",
      message: item?.message,
      round: item?.round_index,
    })),
  ];
  const [operatorMemories, setOperatorMemories] = useState([]);
  const [memoryBusy, setMemoryBusy] = useState("");

  async function refreshOperatorMemories() {
    try {
      setOperatorMemories(await listOperatorMemories("*"));
    } catch {
      setOperatorMemories([]);
    }
  }

  useEffect(() => {
    void refreshOperatorMemories();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const preferenceValue = (key, fallback) => (
    operatorMemories.find((item) => item.memory_key === key)?.value ?? fallback
  );

  async function savePreference(memoryKey, value) {
    setMemoryBusy(memoryKey);
    try {
      await putOperatorMemory({
        project_scope: "*",
        memory_key: memoryKey,
        value,
      });
      await refreshOperatorMemories();
      message.success("跨诊断偏好已明确保存");
    } catch (error) {
      message.error(error?.message || "偏好保存失败");
    } finally {
      setMemoryBusy("");
    }
  }
  return (
    <Tabs
      items={[
        {
          key: "session",
          label: `会话记忆 (${sessionRows.length})`,
          children: (
            <>
              <Alert
                type="info"
                showIcon
                message="会话记忆用于续写当前调查"
                description="它记录用户问题和人工干预，帮助下一轮保持上下文；记忆本身不是可信证据。"
                style={{ marginBottom: 12 }}
              />
              <List
                size="small"
                locale={{ emptyText: "暂无会话记忆" }}
                dataSource={sessionRows}
                renderItem={(item) => (
                  <List.Item>
                    <List.Item.Meta
                      title={<Space wrap><Tag color="blue">{item.role}</Tag>{item.round ? <Text type="secondary">第 {item.round} 轮</Text> : null}</Space>}
                      description={printable(item.message)}
                    />
                  </List.Item>
                )}
              />
            </>
          ),
        },
        {
          key: "operator",
          label: `跨诊断偏好 (${operatorMemories.length})`,
          children: (
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              <Alert
                type="info"
                showIcon
                message="只保存用户明确选择的稳定偏好"
                description="这些偏好跨诊断加载，但只影响表达和保守排序；不保存历史根因、PID、任务目标或权限，也不是 Evidence。"
              />
              <div className="agent-memory-preferences">
                <label>
                  <Text strong>回答语言</Text>
                  <Select
                    value={preferenceValue("response_language", "zh-CN")}
                    loading={memoryBusy === "response_language"}
                    options={[
                      { value: "zh-CN", label: "中文" },
                      { value: "en-US", label: "English" },
                    ]}
                    onChange={(value) => savePreference("response_language", value)}
                  />
                </label>
                <label>
                  <Text strong>解释详细度</Text>
                  <Select
                    value={preferenceValue("explanation_depth", "standard")}
                    loading={memoryBusy === "explanation_depth"}
                    options={[
                      { value: "brief", label: "简短" },
                      { value: "standard", label: "标准" },
                      { value: "detailed", label: "详细" },
                    ]}
                    onChange={(value) => savePreference("explanation_depth", value)}
                  />
                </label>
                <label>
                  <Text strong>探针排序</Text>
                  <Select
                    value={preferenceValue("preferred_low_risk_first", true)}
                    loading={memoryBusy === "preferred_low_risk_first"}
                    options={[
                      { value: true, label: "低风险优先" },
                      { value: false, label: "使用默认排序" },
                    ]}
                    onChange={(value) => savePreference("preferred_low_risk_first", value)}
                  />
                </label>
              </div>
              <Text type="secondary">
                来源固定为 EXPLICIT_USER；服务端白名单会拒绝“允许 shell”之类越权记忆。
              </Text>
            </Space>
          ),
        },
        {
          key: "global",
          label: `长期路线记忆 (${globalMemory.length})`,
          children: (
            <>
              <Alert
                type="warning"
                showIcon
                message="长期记忆保存的是已发布 Skill 路线，不缓存旧根因"
                description="Skill 只能影响下一步取证排序，仍须重新调用工具并形成当前诊断的可信证据。"
                style={{ marginBottom: 12 }}
              />
              <List
                size="small"
                locale={{ emptyText: "本轮没有 Skill 激活或来源 Skill" }}
                dataSource={globalMemory}
                renderItem={(item, index) => (
                  <List.Item>
                    <List.Item.Meta
                      title={<Space wrap><Text strong>{skillIdentity(item, index)}</Text><Tag>{printable(item?.status || item?.outcome, "路线记录")}</Tag></Space>}
                      description={item?.match_score == null ? "未记录匹配分" : `本轮匹配分 ${Math.round(Number(item.match_score) * 100)}%`}
                    />
                  </List.Item>
                )}
              />
              {skillActivations.length > 0 && <Text type="secondary">本轮实际激活 {skillActivations.length} 次；这里只展示服务端返回记录。</Text>}
            </>
          ),
        },
      ]}
    />
  );
}

function EvolutionPanel({ sourceSkill }) {
  if (!sourceSkill) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前诊断尚未形成候选 Skill" />;
  }
  const gate = sourceSkill.gate_metrics || {};
  const state = String(sourceSkill.status || sourceSkill.state || "CANDIDATE").toUpperCase();
  const evaluated = Number(gate.total || 0) > 0;
  const published = ["ACTIVE", "QUARANTINED", "RETIRED"].includes(state);
  const current = ["QUARANTINED", "RETIRED"].includes(state) ? 3 : published ? 2 : evaluated ? 1 : 0;
  return (
    <Space direction="vertical" size={14} style={{ width: "100%" }}>
      <Alert
        type="warning"
        showIcon
        message="自进化不等于自动修改生产"
        description="系统可以从可信轨迹提出候选并运行门禁评测；投入复用仍需人工发布，负反馈可触发隔离或回滚。"
      />
      <Space wrap>
        <Text strong>{sourceSkill.skill_id || sourceSkill.id || "候选 Skill"}</Text>
        <Tag color={state === "ACTIVE" ? "green" : state === "QUARANTINED" ? "red" : "gold"}>{diagnosticStatusLabel(state)}</Tag>
        {sourceSkill.version != null && <Tag>v{sourceSkill.version}</Tag>}
      </Space>
      <Steps
        size="small"
        responsive={false}
        current={current}
        items={[
          { title: "候选", description: "从可信报告提取路线" },
          { title: "门禁评测", description: evaluated ? `${gate.passed || 0}/${gate.total} 通过` : "尚未评测" },
          { title: "人工发布", description: published ? "服务端已有发布状态" : "等待人工确认" },
          { title: "隔离 / 回滚", description: ["QUARANTINED", "RETIRED"].includes(state) ? diagnosticStatusLabel(state) : "持续观察反馈" },
        ]}
      />
      <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="门禁资格">{gate.eligible == null ? "暂无" : gate.eligible ? "通过" : "未通过"}</Descriptions.Item>
        <Descriptions.Item label="评测模式">{printable(gate.evaluation_mode)}</Descriptions.Item>
        <Descriptions.Item label="负迁移保护">{gateValue(gate.negative_transfer_guard)}</Descriptions.Item>
        <Descriptions.Item label="环境漂移保护">{gateValue(gate.environment_drift_guard)}</Descriptions.Item>
        <Descriptions.Item label="Linux 实机诊断验证（Campaign）">{gateValue(gate.campaign_validation)}</Descriptions.Item>
        <Descriptions.Item label="回滚目标">{printable(sourceSkill.rollback_to_version || sourceSkill.rolled_back_to)}</Descriptions.Item>
      </Descriptions>
    </Space>
  );
}

function retrievalTrace(row) {
  return row?.retrieval_trace || row?.trace || {};
}

function RetrievalMatch({ match }) {
  const score = Number(match?.score);
  return (
    <List.Item className="agent-cockpit-retrieval-match">
      <List.Item.Meta
        title={(
          <Space wrap>
            <Text strong>{printable(match?.title || match?.knowledge_id)}</Text>
            {Number.isFinite(score) && <Tag color="blue">相关分 {score.toFixed(3)}</Tag>}
          </Space>
        )}
        description={(
          <Space direction="vertical" size={6} style={{ width: "100%" }}>
            <Space wrap size={[4, 4]}>
              <Text code>{printable(match?.document)}</Text>
              {match?.content_hash && <Text type="secondary">SHA-256 {String(match.content_hash).slice(0, 12)}…</Text>}
            </Space>
            {match?.excerpt && <Paragraph className="agent-cockpit-retrieval-excerpt">{chineseDiagnosticText(match.excerpt)}</Paragraph>}
            {rows(match?.matched_terms).length > 0 && (
              <Space wrap size={[4, 4]}>{rows(match.matched_terms).map((term) => <Tag key={term}>{term}</Tag>)}</Space>
            )}
            {rows(match?.required_evidence).length > 0 && (
              <Text type="secondary">仍需现场证据：{match.required_evidence.map((item) => chineseDiagnosticText(item)).join("；")}</Text>
            )}
            {rows(match?.caveats).length > 0 && (
              <Text type="warning">适用限制：{match.caveats.map((item) => chineseDiagnosticText(item)).join("；")}</Text>
            )}
          </Space>
        )}
      />
    </List.Item>
  );
}

function RAGPanel({ retrievals }) {
  const totalMatches = retrievals.reduce((total, item) => total + rows(retrievalTrace(item)?.matches).length, 0);
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="warning"
        showIcon
        message="主动知识检索（Agentic RAG）只提供调查先验，不是当前故障证据"
        description="Agent 可按当前问题主动检索仓库知识，用来源、版本和内容哈希指导下一步工具取证；检索命中不能直接写入根因结论。"
      />
      <Space wrap>
        <Tag color="blue">检索 {retrievals.length} 次</Tag>
        <Tag>命中文档 {totalMatches} 条</Tag>
      </Space>
      {retrievals.length ? (
        <Collapse
          defaultActiveKey={[String(retrievals.length - 1)]}
          items={retrievals.map((row, index) => {
            const trace = retrievalTrace(row);
            const matches = rows(trace.matches);
            return {
              key: String(index),
              label: (
                <Space wrap>
                  <Text strong>第 {row?.round_index ?? index + 1} 轮 · {printable(row?.phase, "知识检索")}</Text>
                  <Tag color={matches.length ? "blue" : "default"}>{matches.length} 条命中</Tag>
                </Space>
              ),
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
                    <Descriptions.Item label="检索问题" span={2}>{chineseDiagnosticText(printable(trace.query))}</Descriptions.Item>
                    <Descriptions.Item label="检索器">{printable(trace.retriever)}</Descriptions.Item>
                    <Descriptions.Item label="目录">{printable(trace.catalog)}</Descriptions.Item>
                    <Descriptions.Item label="检索摘要哈希" span={2}><Text code copyable>{printable(trace.query_hash)}</Text></Descriptions.Item>
                  </Descriptions>
                  <List
                    size="small"
                    locale={{ emptyText: "本轮没有达到相关性门槛的知识命中" }}
                    dataSource={matches}
                    renderItem={(match) => <RetrievalMatch match={match} />}
                  />
                </Space>
              ),
            };
          })}
        />
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本案例尚无持久化知识检索轨迹" />}
    </Space>
  );
}

const LATS_PHASES = [
  "SELECTION",
  "EXPANSION",
  "EVALUATION",
  "ACTION",
  "OBSERVATION",
  "REFLECTION",
  "BACKPROPAGATION",
];

function LatsBudgetMeter({ label, used, maximum, remaining }) {
  if (used == null && maximum == null && remaining == null) return null;
  const percentUsed = used != null && maximum > 0
    ? Math.min(100, Math.max(0, Math.round((used / maximum) * 100)))
    : null;
  return (
    <article className="agent-cockpit-lats-budget-item">
      <div>
        <Text strong>{label}</Text>
        <Text type="secondary">
          {used != null && maximum != null ? `已用 ${used}/${maximum}` : used != null ? `已用 ${used}` : maximum != null ? `上限 ${maximum}` : ""}
          {remaining != null ? `${used != null || maximum != null ? " · " : ""}剩余 ${remaining}` : ""}
        </Text>
      </div>
      {percentUsed != null && <Progress percent={percentUsed} size="small" showInfo={false} status={percentUsed >= 100 ? "exception" : "normal"} />}
    </article>
  );
}

function ExistingDiagnosisBudget({ budget }) {
  if (!budget) return null;
  const limits = budget.limits || budget.limit || {};
  const used = budget.used || {};
  const remaining = budget.remaining || {};
  const toolUsed = used.tool_calls ?? budget.tool_calls_used;
  const roundUsed = used.diagnosis_rounds ?? budget.diagnosis_rounds_used;
  const toolMaximum = limits.max_tool_calls ?? budget.max_tool_calls;
  const roundMaximum = limits.max_diagnosis_rounds ?? budget.max_diagnosis_rounds;
  const hasValues = [toolUsed, roundUsed, toolMaximum, roundMaximum, remaining.tool_calls]
    .some((value) => value != null);
  if (!hasValues) return null;
  return (
    <section className="agent-cockpit-existing-budget" aria-label="现有诊断预算">
      <Text strong>现有诊断预算（非 LATS 搜索统计）</Text>
      <div className="agent-cockpit-lats-budget-grid">
        <LatsBudgetMeter label="工具调用" used={toolUsed} maximum={toolMaximum} remaining={remaining.tool_calls} />
        <LatsBudgetMeter label="诊断轮次" used={roundUsed} maximum={roundMaximum} remaining={remaining.diagnosis_rounds} />
      </div>
    </section>
  );
}

function LatsSearchPanel({ search, nodeById, diagnosticBudget, diagnosisStatus }) {
  if (!search) {
    return (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="该案例未返回 LATS 搜索元数据"
          description="页面仍按现有假设、工具调用、可信证据和报告展示诊断；不会把缺失的访问次数、价值或 UCT/PUCT 分数补成 0。"
        />
        <Descriptions size="small" bordered column={1}>
          <Descriptions.Item label="当前诊断状态">{printable(diagnosisStatus)}</Descriptions.Item>
          <Descriptions.Item label="搜索阶段">未记录</Descriptions.Item>
          <Descriptions.Item label="停止条件">未记录</Descriptions.Item>
        </Descriptions>
        <ExistingDiagnosisBudget budget={diagnosticBudget} />
      </Space>
    );
  }

  const selectedNode = nodeById.get(search.selectedNodeId);
  const bestPath = search.bestPathNodeIds.map((id) => ({ id, node: nodeById.get(id) }));
  const components = search.latestSelection?.components || {};
  const componentRows = [
    { label: "Q / 价值", value: components.q },
    { label: "探索奖励", value: components.exploration },
    { label: "先验奖励", value: components.priorBonus },
    { label: "虚拟损失", value: components.virtualLoss },
  ].filter((item) => item.value != null);
  const budget = search.budget;
  const termination = search.termination;
  const liveProgressive = search.executionMode === "BUDGETED_LATS"
    || search.environmentSemantics === "LIVE_PROGRESSIVE"
    || search.rolloutSemantics === "REAL_TOOL_SINGLE_STEP_NO_ROLLBACK";
  const frozenReplay = search.executionMode === "FULL_LATS"
    && search.environmentSemantics === "FROZEN_REPLAY";
  const currentPhaseGroup = latsPhaseGroup(search.phase);
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type={liveProgressive ? "warning" : "info"}
        showIcon
        message={search.executionMode ? latsExecutionModeLabel(search.executionMode) : `${search.algorithm || "LATS"} 搜索轨迹`}
        description={liveProgressive
          ? "真实工具会持续推进现场时间，兄弟分支可能来自不同墙钟状态；系统保留反思与价值回传，但不会冒充可回滚到同一起点的离线分支试探（rollout）。"
          : frozenReplay
            ? "同一个冻结观察快照（snapshot）会在每次分支试探（rollout）前复位。这里是算法轨迹，不调用实时采集器，也不是当前线上状态的实时证据。"
            : "执行模式和环境语义来自本次服务端搜索记录；页面不根据终态反推或补造模式。"}
      />
      <div className="agent-cockpit-lats-loop" role="list" aria-label="LATS 搜索循环">
        {LATS_PHASES.map((phase, index) => (
          <span
            role="listitem"
            key={phase}
            className={phase === currentPhaseGroup ? "is-current" : undefined}
            aria-current={phase === currentPhaseGroup ? "step" : undefined}
          >
            <b>{index + 1}</b>{latsPhaseLabel(phase)}
          </span>
        ))}
      </div>
      <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="搜索算法">{printable(search.algorithm)}</Descriptions.Item>
        <Descriptions.Item label="算法版本">{printable(search.algorithmVersion)}</Descriptions.Item>
        <Descriptions.Item label="当前阶段"><Tag color="processing">{latsPhaseLabel(search.phase)}</Tag></Descriptions.Item>
        <Descriptions.Item label="迭代次数">{search.iteration ?? "未记录"}</Descriptions.Item>
        <Descriptions.Item label="候选节点">{search.candidateCount ?? "未记录"}</Descriptions.Item>
        <Descriptions.Item label="当前选择">
          {selectedNode?.title ? chineseDiagnosticText(selectedNode.title) : search.selectedNodeId || "未记录"}
        </Descriptions.Item>
        <Descriptions.Item label="环境语义">{search.environmentSemantics ? latsEnvironmentLabel(search.environmentSemantics) : "未记录"}</Descriptions.Item>
        <Descriptions.Item label="分支试探语义（rollout）">{search.rolloutSemantics ? latsRolloutLabel(search.rolloutSemantics) : "未记录"}</Descriptions.Item>
        {frozenReplay && <Descriptions.Item label="冻结观察快照（snapshot）">{search.snapshotId || "待同步"}</Descriptions.Item>}
        {frozenReplay && <Descriptions.Item label="快照重置">{search.resetCount == null ? "待同步" : `${search.resetCount} 次`}</Descriptions.Item>}
      </Descriptions>

      <section className="agent-cockpit-lats-section" aria-label="LATS 当前选择依据">
        <Space wrap>
          <Text strong>当前选择依据</Text>
          {search.latestSelection?.score != null && <Tag color="geekblue">总分 {formatLatsScore(search.latestSelection.score)}</Tag>}
        </Space>
        {search.latestSelection?.reason
          ? <Paragraph>{chineseDiagnosticText(search.latestSelection.reason)}</Paragraph>
          : <Text type="secondary">服务端未返回本次选择理由。</Text>}
        {componentRows.length > 0 && (
          <div className="agent-cockpit-lats-score-grid">
            {componentRows.map((item) => (
              <article key={item.label}><small>{item.label}</small><b>{formatLatsScore(item.value)}</b></article>
            ))}
          </div>
        )}
      </section>

      <section className="agent-cockpit-lats-section" aria-label="LATS 当前最佳路径">
        <Text strong>当前最佳路径</Text>
        {bestPath.length ? (
          <ol className="agent-cockpit-lats-path">
            {bestPath.map(({ id, node }, index) => (
              <li key={id}>
                <span>{index + 1}</span>
                <div><Text strong>{node?.title ? chineseDiagnosticText(node.title) : id}</Text><Text code>{id}</Text></div>
              </li>
            ))}
          </ol>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="服务端尚未返回最佳路径" />}
      </section>

      {budget ? (
        <section className="agent-cockpit-lats-section" aria-label="LATS 搜索预算">
          <Text strong>搜索预算</Text>
          <div className="agent-cockpit-lats-budget-grid">
            <LatsBudgetMeter label="搜索迭代" used={budget.usedIterations} maximum={budget.maxIterations} remaining={budget.remainingIterations} />
            {frozenReplay && <LatsBudgetMeter label="冻结回放模拟" used={budget.usedSimulations} maximum={budget.maxSimulations} remaining={budget.remainingSimulations} />}
            {!frozenReplay && <LatsBudgetMeter label="工具调用" used={budget.usedToolCalls} maximum={budget.maxToolCalls} remaining={budget.remainingToolCalls} />}
            <LatsBudgetMeter label="诊断轮次" used={budget.usedDiagnosisRounds} maximum={budget.maxDiagnosisRounds} remaining={budget.remainingDiagnosisRounds} />
          </div>
          {frozenReplay && (
            <div className="agent-cockpit-existing-budget" aria-label="冻结回放实时工具调用">
              <Text strong>实时工具调用（与模拟分开）</Text>
              <LatsBudgetMeter label="真实采集器" used={budget.usedToolCalls ?? 0} maximum={budget.maxToolCalls ?? 0} remaining={budget.remainingToolCalls} />
            </div>
          )}
        </section>
      ) : <ExistingDiagnosisBudget budget={diagnosticBudget} />}

      <Alert
        type={termination?.stopped ? (termination.reason === "VERIFIED" ? "success" : "warning") : "info"}
        showIcon
        message={`停止条件：${termination ? latsTerminationLabel(termination.reason) : "未记录"}`}
        description={chineseDiagnosticText(termination?.detail, termination?.stopped === false ? "搜索仍在推进。" : "服务端尚未返回停止条件细节。")}
      />
    </Space>
  );
}

function EvaluationPanel({ metrics, sourceSkill, onOpenEvaluation }) {
  const items = [
    { label: "工具成功率", value: metrics.toolSuccessRate == null ? "暂无" : `${metrics.toolSuccessRate}%`, note: metrics.toolSuccessNote },
    { label: "证据产出率", value: metrics.evidenceRate.value == null ? "暂无" : `${metrics.evidenceRate.value}%`, note: metrics.evidenceRate.note },
    { label: "人工干预", value: `${metrics.interventions} 次`, note: "用户输入只改变调查方向" },
    { label: "报告版本", value: metrics.reportVersions ? `${metrics.reportVersions} 个` : "暂无", note: "按服务端返回的报告记录计数" },
  ];
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <div className="agent-cockpit-eval-grid">
        {items.map((item) => (
          <article key={item.label}>
            <Text type="secondary">{item.label}</Text>
            <b>{item.value}</b>
            <small>{item.note}</small>
          </article>
        ))}
      </div>
      {metrics.toolSuccessRate != null && (
        <div>
          <Text type="secondary">已结束工具调用的成功比例</Text>
          <Progress percent={metrics.toolSuccessRate} size="small" status="active" />
        </div>
      )}
      <EvolutionPanel sourceSkill={sourceSkill} />
      {onOpenEvaluation && (
        <Button
          type="primary"
          icon={<ExperimentOutlined />}
          onClick={onOpenEvaluation}
          aria-label="打开完整评测中心"
        >
          打开完整评测中心
        </Button>
      )}
    </Space>
  );
}

export default function AgentCockpit({
  detail,
  resources = {},
  sourceSkill = null,
  connected = false,
  onOpenEvaluation,
}) {
  const [activePanel, setActivePanel] = useState(null);
  const hypotheses = rows(resources.hypotheses);
  const toolCalls = rows(resources.toolCalls);
  const evidence = rows(resources.evidence);
  const events = rows(resources.events);
  const reports = rows(resources.reports);
  const interventions = rows(resources.interventions);
  const skillActivations = rows(resources.skillActivations);
  const retrievals = rows(resources.retrievals);
  const latsSearch = normalizeLatsSearch(resources?.explorationTree?.search, resources?.explorationTree);
  const latsNodeById = useMemo(() => new Map(
    rows(resources?.explorationTree?.nodes)
      .filter((node) => node?.id != null)
      .map((node) => [String(node.id), node]),
  ), [resources?.explorationTree?.nodes]);
  const acceptedEvidence = evidence.filter(isAcceptedEvidence);
  const globalMemory = globalMemoryRows(skillActivations, sourceSkill);
  const round = currentRound(detail, resources);
  const rawStage = String(detail?.current_stage || detail?.stage || detail?.status || "CREATED").toUpperCase();
  const stage = STAGE_LABELS[rawStage] || printable(detail?.current_stage || detail?.stage || detail?.status, "等待开始");
  const runtime = findRuntime(detail, resources);
  const latestRetrieval = retrievals.length ? retrievalTrace(retrievals[retrievals.length - 1]) : null;

  const metrics = useMemo(() => {
    const succeeded = toolCalls.filter((item) => SUCCESS_TOOL_STATES.has(String(item?.status || "").toUpperCase())).length;
    const failed = toolCalls.filter((item) => FAILED_TOOL_STATES.has(String(item?.status || "").toUpperCase())).length;
    const terminal = succeeded + failed;
    return {
      toolSuccessRate: percent(succeeded, terminal),
      toolSuccessNote: terminal ? `${succeeded}/${terminal} 次已结束调用成功` : "暂无已结束工具样本",
      evidenceRate: linkedEvidenceRate(toolCalls, acceptedEvidence),
      interventions: interventions.length,
      reportVersions: reports.length,
    };
  }, [acceptedEvidence, interventions.length, reports.length, toolCalls]);

  if (!detail) return null;

  const cards = [
    { key: "stage", label: "当前阶段 / 轮次", value: `第 ${round} 轮`, meta: stage, icon: <ClockCircleOutlined /> },
    { key: "plan", label: "规划假设", value: `${hypotheses.length} 个`, meta: "点击查看意图与可证伪计划", icon: <BranchesOutlined /> },
    { key: "rag", label: "主动知识检索", value: `${retrievals.length} 次`, meta: latestRetrieval ? `最近命中 ${rows(latestRetrieval.matches).length} 条知识` : "暂无检索轨迹", icon: <FileSearchOutlined /> },
    {
      key: "lats",
      label: "LATS 搜索",
      value: latsSearch ? latsPhaseLabel(latsSearch.phase) : "未启用",
      meta: latsSearch
        ? `${latsSearch.algorithm || "LATS"}${latsSearch.iteration == null ? "" : ` · 第 ${latsSearch.iteration} 次迭代`}`
        : "当前案例无搜索统计",
      icon: <ApartmentOutlined />,
    },
    { key: "tools", label: "工具调用", value: `${toolCalls.length} 次`, meta: metrics.toolSuccessRate == null ? "成功率暂无" : `成功率 ${metrics.toolSuccessRate}%`, icon: <ApiOutlined /> },
    { key: "evidence", label: "证据", value: `${acceptedEvidence.length}/${evidence.length}`, meta: "可支撑结论 / 总数", icon: <SafetyCertificateOutlined /> },
    { key: "memory", label: "记忆", value: `${(detail?.query || detail?.request?.query ? 1 : 0) + interventions.length} 条`, meta: `会话上下文 · ${globalMemory.length} 条路线记忆`, icon: <DatabaseOutlined /> },
    { key: "evaluation", label: "评测", value: metrics.toolSuccessRate == null ? "暂无" : `${metrics.toolSuccessRate}%`, meta: "工具成功率 · 点击看完整口径", icon: <ExperimentOutlined /> },
  ];

  const panelTitles = {
    stage: "阶段与意图",
    plan: "任务规划与动态改写",
    rag: "主动知识检索（Agentic RAG）轨迹",
    lats: "LATS 搜索、评估与价值回传",
    tools: "受控工具调用链",
    evidence: "可信证据事实层",
    memory: "会话记忆与长期路线记忆",
    evaluation: "Agent 评测与 Skill 演进",
  };

  return (
    <section className="agent-cockpit" aria-label="Agent 可观测控制台">
      <div className="agent-cockpit-heading">
        <div>
          <Space size={8}>
            <ApartmentOutlined />
            <Text strong>Agent 运行驾驶舱</Text>
            <Tag color="blue">真实会话投影</Tag>
          </Space>
          <Paragraph>点击指标查看意图、LATS 搜索、工具、证据、记忆和评测；所有数值均来自当前诊断记录。</Paragraph>
        </div>
        <Space size={6} wrap>
          <CheckCircleOutlined className="agent-cockpit-safe-icon" />
          <Text type="secondary">可信证据才是事实</Text>
        </Space>
      </div>
      <RuntimeBar runtime={runtime} connected={connected} />
      <div className="agent-cockpit-grid">
        {cards.map((card) => (
          <button
            type="button"
            className={`agent-cockpit-card is-${card.key}`}
            key={card.key}
            onClick={() => setActivePanel(card.key)}
            aria-label={`查看${card.label}`}
          >
            <span className="agent-cockpit-card-icon">{card.icon}</span>
            <span className="agent-cockpit-card-copy">
              <small>{card.label}</small>
              <b>{card.value}</b>
              <em>{card.meta}</em>
            </span>
          </button>
        ))}
      </div>

      <Modal
        className="agent-cockpit-modal"
        title={activePanel ? panelTitles[activePanel] : "Agent 详情"}
        open={Boolean(activePanel)}
        onCancel={() => setActivePanel(null)}
        footer={null}
        width={940}
        destroyOnHidden
      >
        {activePanel === "stage" && <StagePanel detail={detail} resources={resources} stage={stage} round={round} runtime={runtime} />}
        {activePanel === "plan" && <PlanPanel hypotheses={hypotheses} events={events} />}
        {activePanel === "rag" && <RAGPanel retrievals={retrievals} />}
        {activePanel === "lats" && (
          <LatsSearchPanel
            search={latsSearch}
            nodeById={latsNodeById}
            diagnosticBudget={resources?.budget}
            diagnosisStatus={detail?.status}
          />
        )}
        {activePanel === "tools" && <ToolPanel toolCalls={toolCalls} />}
        {activePanel === "evidence" && <EvidencePanel evidence={evidence} acceptedEvidence={acceptedEvidence} />}
        {activePanel === "memory" && (
          <MemoryPanel
            detail={detail}
            interventions={interventions}
            skillActivations={skillActivations}
            globalMemory={globalMemory}
          />
        )}
        {activePanel === "evaluation" && (
          <EvaluationPanel metrics={metrics} sourceSkill={sourceSkill} onOpenEvaluation={onOpenEvaluation} />
        )}
      </Modal>
    </section>
  );
}
