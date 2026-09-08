import { Alert, Card, Collapse, Empty, Space, Tag, Typography } from "antd";
import { BranchesOutlined } from "@ant-design/icons";
import ChatMessage from "./ChatMessage";
import DiagnosisPathPanel from "./DiagnosisPathPanel";
import PlannerBlock from "./PlannerBlock";
import ToolCallCard from "./ToolCallCard";
import EvidenceCard from "./EvidenceCard";
import ConclusionCard from "./ConclusionCard";
import ScopeCard from "./ScopeCard";
import FixVerificationPanel from "./FixVerificationPanel";
import DiagnosisFeedbackCard from "./DiagnosisFeedbackCard";
import { chineseDiagnosticText } from "../utils/diagnosisDisplay";
import { mergeSemanticHypotheses } from "../utils/hypothesisSemantics";

const { Text } = Typography;

const TOOL_LABELS = {
  collect_sys_metrics: "系统指标采集",
  collect_database_diagnostics: "数据库状态采集",
  start_perf_profile: "CPU 火焰图采集",
  start_pyspy_profile: "Python 调用栈采集",
  start_ebpf_io_profile: "I/O 延迟采集",
  get_agent_status: "采集节点检查",
};

function readableToolName(tool) {
  const key = tool?.tool_name || tool?.name || tool?.tool || "";
  return TOOL_LABELS[key] || key || "待选择采集器";
}

function buildConversationRounds(
  hypotheses = [],
  toolCalls = [],
  evidence = [],
  reports = [],
  interventions = [],
  semanticHypotheses = mergeSemanticHypotheses(hypotheses),
) {
  const rounds = new Map();
  const hypothesisRound = new Map();
  const ensure = (index) => {
    const round = Number(index || 1);
    if (!rounds.has(round)) rounds.set(round, {
      round,
      hypotheses: [],
      continuedHypotheses: [],
      tools: [],
      evidence: [],
      reports: [],
      intervention: null,
    });
    return rounds.get(round);
  };
  ensure(1);
  hypotheses.forEach((item) => {
    const round = Number(item.round_index || 1);
    hypothesisRound.set(item.hypothesis_id || item.id, round);
  });
  semanticHypotheses.forEach((item) => {
    ensure(item.first_round).hypotheses.push(item);
    (item.round_indices || []).filter((round) => round !== item.first_round).forEach((round) => {
      ensure(round).continuedHypotheses.push(item);
    });
  });
  interventions.forEach((item) => {
    ensure(item.round_index || 1).intervention = item;
  });
  toolCalls.forEach((item) => ensure(hypothesisRound.get(item.hypothesis_id) || 1).tools.push(item));
  evidence.forEach((item) => ensure(hypothesisRound.get(item.hypothesis_id) || 1).evidence.push(item));
  reports.forEach((item) => ensure(hypothesisRound.get(item.hypothesis_id) || 1).reports.push(item));
  return [...rounds.values()].sort((left, right) => left.round - right.round);
}

function ConversationRound({ item, initialQuery, interventionLabels, isLatest }) {
  const userText = item.round === 1 ? initialQuery : item.intervention?.message;
  const primaryHypothesis = item.hypotheses.find((row) => row.source !== "SYSTEM_FALLBACK") || item.hypotheses[0];
  const toolNames = [...new Set(item.tools.map(readableToolName))];
  const waitingApproval = item.tools.some((tool) => String(tool.status || "").toUpperCase().includes("APPROVAL"));
  const running = item.tools.some((tool) => ["PENDING", "RUNNING", "TASK_CREATED", "UPLOADING", "ANALYZING"].includes(String(tool.status || "").toUpperCase()));
  const roundState = waitingApproval ? "等待人工审批" : running ? "正在执行" : item.reports.length ? "本轮已裁决" : item.tools.length ? "等待证据" : "正在规划";
  return (
    <div className={`diagnosis-conversation-round ${isLatest ? "is-latest" : ""}`}>
      {userText && (
        <ChatMessage role="user">
          <div className="diagnosis-user-query">
            {item.round > 1 && <Tag color="cyan">{interventionLabels[item.intervention?.action] || "继续对话"}</Tag>}
            {userText}
          </div>
        </ChatMessage>
      )}
      <ChatMessage role="assistant">
        <Card size="small" className="diagnosis-round-response">
          <div className="diagnosis-round-response-head">
            <Space wrap>
              <Tag color={isLatest ? "processing" : "default"}>第 {item.round} 轮</Tag>
              <Text strong>{item.round === 1 ? "已理解问题并建立首轮计划" : "已根据新输入修订调查计划"}</Text>
            </Space>
            <Tag color={waitingApproval ? "gold" : running ? "blue" : item.reports.length ? "green" : "default"}>{roundState}</Tag>
          </div>
          {primaryHypothesis ? (
            <div className="diagnosis-round-plan"><Text type="secondary">本轮主假设</Text><Text>{chineseDiagnosticText(primaryHypothesis.statement)}</Text></div>
          ) : item.continuedHypotheses.length > 0 ? (
            <div className="diagnosis-round-plan">
              <Text type="secondary">本轮调查方向</Text>
              <Text>继续验证已在前序轮次提出的同一因果假设；本轮工具、证据和评分更新仍单独保留。</Text>
            </div>
          ) : (
            <Text type="secondary">Agent 正在解析意图、发现安全目标并准备可验证假设。</Text>
          )}
          <Space wrap size={[4, 4]}>
            {item.continuedHypotheses.length > 0 && (
              <Tag color="cyan">合并 {item.continuedHypotheses.length} 个跨轮重述</Tag>
            )}
            {toolNames.map((name) => <Tag key={name}>{name}</Tag>)}
            {item.evidence.length > 0 && <Tag color="green">{item.evidence.length} 条准入证据</Tag>}
            {item.reports.length > 0 && <Tag color="purple">{item.reports.length} 个报告版本</Tag>}
          </Space>
          {item.round > 1 && item.intervention?.revision_hypothesis_id && (
            <div className="diagnosis-round-audit-note">
              <Text strong>已进入第 {item.round} 轮诊断</Text>
              <Text type="secondary">旧报告与旧证据保持可回放，本轮输入只改变探索方向，不会被当作事实证据。</Text>
            </div>
          )}
        </Card>
      </ChatMessage>
    </div>
  );
}

/**
 * 对话线程：把一次诊断会话渲染成 Codex 式对话。
 * 顺序：用户问题 → （范围确认卡，如需）→ AI 计划（分类+假设）
 *      → 工具调用（审批/结果）→ 证据 → 结论。
 * 事件以细时间线垫底，体现"持续交互"。
 */
export default function ChatThread({
  detail,
  hypotheses,
  toolCalls,
  evidence,
  reports,
  events,
  onApproveTool,
  onRejectTool,
  onUpdateToolArgs,
  onClarify,
  clarifying,
  feedback = [],
  onSubmitFeedback,
  feedbackSubmitting,
  skillActivations = [],
  interventions = [],
  mode = "expert",
  readOnly = false,
  unavailableSections = [],
}) {
  const isExpert = mode === "expert";
  if (!detail) {
    return <Empty description="描述一个问题，AI 会一步步给出结论" style={{ marginTop: 60 }} />;
  }

  const classification = detail.classification || detail.status || "分析中";
  const reportRows = [...(reports || [])];
  const reportsHaveOrdering = reportRows.some(
    (item) => item?.version != null || item?.created_at || item?.updated_at,
  );
  if (reportsHaveOrdering) {
    reportRows.sort((a, b) => {
      if (a?.version != null || b?.version != null) return Number(b?.version || 0) - Number(a?.version || 0);
      return new Date(b?.updated_at || b?.created_at || 0) - new Date(a?.updated_at || a?.created_at || 0);
    });
  }
  const latestReport = reportRows[0] || null;
  const feedbackRows = [...(feedback || [])];
  if (feedbackRows.some((item) => item?.created_at || item?.updated_at)) {
    feedbackRows.sort(
      (a, b) => new Date(b?.updated_at || b?.created_at || 0) - new Date(a?.updated_at || a?.created_at || 0),
    );
  }
  const latestFeedback = feedbackRows[0] || null;
  const reportVerificationStatus = latestReport?.verification?.status;
  const hasVerifiedRootCause = reportVerificationStatus === "VERIFIED";
  const sortedTools = [...(toolCalls || [])].sort(
    (a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0),
  );
  const evidenceRows = [...(evidence || [])].sort(
    (a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0),
  );
  const sortedSkillActivations = [...skillActivations].sort(
    (a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0),
  );
  const skillActivation = sortedSkillActivations[0] || null;
  const skillPolicy = String(detail.skill_policy || detail.request?.skill_policy || "AUTO").toUpperCase();
  const skillDisabled = skillPolicy === "DISABLED";
  const dynamicRoute = [...new Set(sortedTools.map(readableToolName).filter(Boolean))];
  const interventionRows = [...interventions].sort(
    (a, b) => Number(a.sequence || 0) - Number(b.sequence || 0),
  );
  const interventionLabels = {
    ADD_CONTEXT: "补充上下文",
    CHALLENGE_HYPOTHESIS: "寻找反证",
    CHANGE_DIRECTION: "调整方向",
    CONTINUE_INVESTIGATION: "继续调查",
  };
  const semanticHypotheses = mergeSemanticHypotheses(hypotheses);
  const conversationRounds = buildConversationRounds(
    hypotheses,
    sortedTools,
    evidenceRows,
    reportRows,
    interventionRows,
    semanticHypotheses,
  );
  const displayedHypothesisCount = semanticHypotheses.length;

  const investigationDetails = (
    <div className="diagnosis-investigation-details">
      <div className="diagnosis-phase-heading">
        <span>01</span>
        <div><b>形成可验证假设</b><small>规则打底，模型结合当前范围排序并补全证伪条件</small></div>
      </div>
      <PlannerBlock
        classification={classification}
        hypotheses={hypotheses}
      />
      <Card
        className={`diagnosis-skill-trace ${skillActivation ? "is-published-skill" : "is-dynamic-route"}`}
        size="small"
        title={<Space><BranchesOutlined /><span>本轮诊断能力</span></Space>}
        extra={skillActivation
          ? <Tag color="green">{sortedSkillActivations.length === 1 ? "已命中发布 Skill" : `分轮引用 ${sortedSkillActivations.length} 个 Skill`}</Tag>
          : <Tag color={skillDisabled ? "orange" : "blue"}>{skillDisabled ? "Skill 已关闭" : "动态取证路线"}</Tag>}
      >
        {skillActivation ? (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Text strong>复用了经过门禁验证的诊断经验</Text>
            <div className="diagnosis-skill-composition">
              {sortedSkillActivations.map((activation, index) => {
                const reason = activation.match_reason || {};
                const activationRoute = reason.route?.length ? reason.route : [activation.selected_tool];
                return (
                  <div className="diagnosis-skill-composition-item" key={activation.activation_id || activation.skill_id}>
                    <Space wrap>
                      <Tag color="green">Skill {index + 1}</Tag>
                      <Tag>版本 {reason.skill_version || "-"}</Tag>
                      <Tag color="blue">匹配度 {Math.round(Number(activation.match_score || 0) * 100)}%</Tag>
                      {reason.retrieval === "HYBRID_BM25_VECTOR" && (
                        <>
                          <Tag color="purple">BM25 {Math.round(Number(reason.bm25 || 0) * 100)}%</Tag>
                          <Tag color="geekblue">向量 {Math.round(Number(reason.vector || 0) * 100)}%</Tag>
                          <Tag>上下文 {Math.round(Number(reason.structured || 0) * 100)}%</Tag>
                        </>
                      )}
                      {Number(reason.observed_outcomes || 0) > 0 && (
                        <Tag color="cyan">
                          复用可信度 {Math.round(Number(reason.posterior_reliability || 0) * 100)}%
                          （{reason.observed_outcomes} 次反馈）
                        </Tag>
                      )}
                    </Space>
                    <div>
                      取证路线：{activationRoute.map((tool) => (
                        <Tag key={`${activation.skill_id}:${tool}`}>{TOOL_LABELS[tool] || tool}</Tag>
                      ))}
                    </div>
                    {reason.matched_terms?.length > 0 && (
                      <Text type="secondary">命中词：{reason.matched_terms.join("、")}</Text>
                    )}
                  </div>
                );
              })}
            </div>
            <Text type="secondary">
              Skill 只替换下一步取证建议，不缓存旧根因。多个命中表示不同轮次分别引用，并非通用 DAG 编排；所有建议仍需重新取证并经过策略与证据门禁。
            </Text>
          </Space>
        ) : skillDisabled ? (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Text strong>本组诊断明确设置为“不使用 Skill”</Text>
            <Text>服务端不会检索或激活已发布 Skill，所有路线都由基线规划器重新生成。</Text>
            <div>当前路线：{dynamicRoute.length
              ? dynamicRoute.map((name) => <Tag key={name}>{name}</Tag>)
              : <Tag>等待规划</Tag>}</div>
          </Space>
        ) : (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Text>
              本轮由性能决策树按当前证据动态选择工具，尚未命中可复用的已发布 Skill。
            </Text>
            <div>
              当前路线：{dynamicRoute.length
                ? dynamicRoute.map((name) => <Tag key={name}>{name}</Tag>)
                : <Tag>等待范围确认后生成</Tag>}
            </div>
            <Text type="secondary">
              动态路线不冒充 Skill；只有结论经证据验证、人工确认并通过正例、反例和环境漂移门禁后，才会进入 Skill 广场。
            </Text>
          </Space>
        )}
      </Card>
      <div className="diagnosis-phase-heading">
        <span>02</span>
        <div><b>执行受控取证</b><small>计划器决定查什么，策略层决定是否允许执行</small></div>
      </div>
      {sortedTools.length > 0
        ? sortedTools.map((tool) => (
            <ToolCallCard
              key={tool.tool_call_id}
              tool={tool}
              mode={mode}
              onApprove={onApproveTool}
              onReject={onRejectTool}
              onUpdateArgs={onUpdateToolArgs}
              readOnly={readOnly}
            />
          ))
        : <Alert type="info" showIcon message="等待范围确认后创建第一项取证任务" />}

      <div className="diagnosis-phase-heading">
        <span>03</span>
        <div><b>裁决证据与反证</b><small>材料通过目标、时间窗、完整性和分析内容校验后才进入证据层</small></div>
      </div>
      {evidenceRows.length > 0
        ? evidenceRows.map((item) => <EvidenceCard key={item.evidence_id} evidence={item} />)
        : <Alert type="warning" showIcon message="尚无通过门禁的结构化证据，系统不会提前下结论" />}
    </div>
  );

  return (
    <div className="diagnosis-conversation">
      <div className="diagnosis-round-history" aria-label="多轮诊断对话">
        {conversationRounds.map((item, index) => (
          <ConversationRound
            key={item.round}
            item={item}
            initialQuery={detail.query || detail.id}
            interventionLabels={interventionLabels}
            isLatest={index === conversationRounds.length - 1}
          />
        ))}
      </div>

      <ChatMessage role="assistant">
        <div className="diagnosis-trace-summary" aria-label="诊断过程摘要">
          <span><b>{displayedHypothesisCount}</b> 个去重后假设</span>
          <span><b>{sortedTools.length}</b> 次工具调用</span>
          <span><b>{evidenceRows.length}</b> 条证据</span>
          <span><b>{reportRows.length}</b> 个报告版本</span>
          {interventionRows.length > 0 && <span><b>{interventionRows.length}</b> 次人工干预</span>}
        </div>
        {!isExpert && latestReport && (
          <>
            <div className="diagnosis-phase-heading diagnosis-conclusion-first">
              <span>00</span>
              <div><b>结论先行</b><small>先回答根因、可信度和下一步；完整探索过程保留在下方</small></div>
            </div>
            <ConclusionCard report={latestReport} />
            {onSubmitFeedback && (
              <DiagnosisFeedbackCard
                report={latestReport}
                latestFeedback={latestFeedback}
                onSubmit={onSubmitFeedback}
                submitting={feedbackSubmitting}
              />
            )}
            {hasVerifiedRootCause && detail?.diagnosis_id && !readOnly && (
              <FixVerificationPanel diagnosisId={detail.diagnosis_id} />
            )}
          </>
        )}
        {detail.status === "NEEDS_CLARIFICATION" && !readOnly && (
          detail.mode === "AUTONOMOUS" ? (
            <Alert
              type="info"
              showIcon
              message="AI 正在自主确定诊断范围"
              description="系统会持续发现在线 Agent 和可采集进程，从服务端签发的安全候选中选择目标并自动开始取证；无需填写 Agent、PID、服务或时间窗。"
            />
          ) : (
            <ScopeCard
              key={detail.diagnosis_id || detail.id}
              diagnosisId={detail.diagnosis_id || detail.id}
              diagnosisVersion={detail.diagnosis_version ?? detail.version}
              questions={detail.clarification_questions || []}
              onClarify={onClarify}
              submitting={clarifying}
              initialTarget={detail.target || {}}
              initialTimeRange={detail.time_range || detail.requested_time_range || {}}
              draftKey={detail.diagnosis_id || detail.id}
            />
          )
        )}
        {isExpert ? investigationDetails : (
          <Collapse
            className="diagnosis-investigation-collapse"
            defaultActiveKey={latestReport ? [] : ["investigation"]}
            items={[{
              key: "investigation",
              label: (
                <div className="diagnosis-investigation-label">
                  <b>完整调查过程</b>
                  <small>{latestReport ? "展开查看假设、Skill 路线、工具调用与证据" : "诊断进行中，实时展示取证进度"}</small>
                </div>
              ),
              children: investigationDetails,
            }]}
          />
        )}

        {latestReport && isExpert && (
          <>
            <div className="diagnosis-phase-heading">
              <span>04</span>
              <div><b>生成可引用结论</b><small>结论必须显式引用本次证据；证据不足时保持拒答</small></div>
            </div>
            <ConclusionCard report={latestReport} />
          </>
        )}
        {/* 诊断结束后仍允许评价结论；readOnly 只约束继续取证和工具调用。 */}
        {latestReport && isExpert && onSubmitFeedback && (
          <DiagnosisFeedbackCard
            report={latestReport}
            latestFeedback={latestFeedback}
            onSubmit={onSubmitFeedback}
            submitting={feedbackSubmitting}
          />
        )}
        {/* 证据不足只是待验证假设；只有根因通过反证门禁后才能验证修复。 */}
        {hasVerifiedRootCause && isExpert && detail?.diagnosis_id && !readOnly && (
          <FixVerificationPanel diagnosisId={detail.diagnosis_id} />
        )}
        {["INSUFFICIENT_EVIDENCE", "PARTIAL", "PARTIAL_COMPLETED"].includes(String(detail.status || "").toUpperCase()) && !readOnly && (
          <Alert
            className="diagnosis-evidence-gap"
            type="warning"
            showIcon
            message="本轮证据不足，不等于没有故障"
            description={`系统目前展示 ${displayedHypothesisCount} 个去重后假设、${sortedTools.length} 次工具调用、${evidenceRows.length} 条准入证据。请在页面底部补充现象，或在右侧探索树选择“优先调查 / 寻找反证”开启下一轮。`}
          />
        )}
        {readOnly && unavailableSections.length > 0 && (
          <Card size="small" style={{ marginBottom: 10, background: "#fafafa" }}>
            <Text type="secondary">
              该版本未记录此类数据：{unavailableSections.join("、")}。
            </Text>
          </Card>
        )}
      </ChatMessage>

      {isExpert && (events || []).length > 0 && (
        <Card size="small" className="diagnosis-event-replay" title="事件日志（可回放）">
          <DiagnosisPathPanel events={events} />
        </Card>
      )}
    </div>
  );
}
