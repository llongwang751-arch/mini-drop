import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Empty, Input, Space, Spin, Tag, Typography, message } from "antd";
import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  SelectOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import {
  createDropInsightDiagnosis,
  getDropInsightDiagnosis,
  getDropInsightExplorationTree,
  listDiagnosticSkillActivations,
  listDropInsightEvidence,
  listDropInsightReports,
  listDropInsightToolCalls,
  runDropInsightPlanner,
} from "../api/client";
import usePolling from "../hooks/usePolling";
import SkillExperimentPanel from "./SkillExperimentPanel";
import {
  diagnosticStatusLabel,
  diagnosticToolLabel,
  skillPolicyLabel,
} from "../utils/diagnosisDisplay";
import { shortDiagnosisId } from "../utils/hypothesisSemantics";
import "./DiagnosisShowcase.css";

const { Paragraph, Text, Title } = Typography;
const TERMINAL = new Set(["COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"]);
export const SKILL_AB_HISTORY_KEY = "mini-drop:skill-ab-history:v1";
const MAX_HISTORY = 12;
const BENCHMARK_REPORT_URL = "/report-assets/evaluation/root-cause-skill-ab.json";

export function readSkillABHistory(storage = globalThis?.localStorage) {
  try {
    const value = JSON.parse(storage?.getItem(SKILL_AB_HISTORY_KEY) || "[]");
    return Array.isArray(value) ? value
      .filter((item) => item?.autoId || item?.disabledId)
      .map((item) => ({ autoId: item.autoId || "", disabledId: item.disabledId || "" }))
      .slice(0, MAX_HISTORY) : [];
  } catch {
    return [];
  }
}

function writeSkillABHistory(history, storage = globalThis?.localStorage) {
  try {
    storage?.setItem(SKILL_AB_HISTORY_KEY, JSON.stringify(history.slice(0, MAX_HISTORY)));
  } catch {
    // 浏览器禁用持久化时仍可使用当前页面，不让存储失败阻断真实诊断。
  }
}

function historyRecordToPair(record) {
  if (!record) return null;
  return {
    query: "",
    startedAt: null,
    auto: emptyArm("AUTO", record.autoId || ""),
    disabled: emptyArm("DISABLED", record.disabledId || ""),
  };
}

function emptyArm(policy, diagnosisId = "") {
  return {
    policy,
    diagnosisId,
    detail: null,
    activations: [],
    tools: [],
    evidence: [],
    reports: [],
    tree: null,
    error: "",
  };
}

const REPORT_STATUS_RANK = {
  VERIFIED: 3,
  PARTIAL_WITHOUT_COUNTER: 2,
  INSUFFICIENT_EVIDENCE: 1,
};

// Reports are per hypothesis, not successive versions of one final report.
// Prefer the strongest supported hypothesis so a later dead-end branch cannot
// hide a root cause that was already established earlier in the exploration.
function bestReport(reports = []) {
  return [...reports].sort((left, right) => {
    const leftStatus = String(left?.verification?.status || left?.verification_status || "").toUpperCase();
    const rightStatus = String(right?.verification?.status || right?.verification_status || "").toUpperCase();
    const statusDelta = (REPORT_STATUS_RANK[rightStatus] || 0) - (REPORT_STATUS_RANK[leftStatus] || 0);
    if (statusDelta) return statusDelta;
    const referenceDelta = (right?.evidence_refs?.length || 0) - (left?.evidence_refs?.length || 0);
    if (referenceDelta) return referenceDelta;
    const confidenceDelta = Number(right?.confidence || 0) - Number(left?.confidence || 0);
    if (confidenceDelta) return confidenceDelta;
    return new Date(right?.updated_at || right?.created_at || 0) - new Date(left?.updated_at || left?.created_at || 0);
  })[0] || null;
}

function scopeKey(detail) {
  const target = detail?.target || {};
  const range = detail?.time_range || detail?.requested_time_range || {};
  const authority = target.binding_id || target.agent_id || target.host_id || "";
  const process = target.pid || target.container_id || target.instance_id || "";
  if (!authority || !process || !range.start || !range.end) return "";
  return JSON.stringify({
    authority,
    process,
    service: target.service || "",
    environment: target.environment || "",
    start: range.start,
    end: range.end,
  });
}

const ACCEPTED_GATE_DECISIONS = new Set([
  "ACCEPT",
  "ACCEPTED",
  "SUPPORT",
  "SUPPORTED",
  "ACCEPT_SUPPORT",
  "ACCEPT_COUNTER",
  "ACCEPT_NEUTRAL",
  "ACCEPT_LIMITED",
]);

function evidenceDecision(item) {
  const classification = item?.classification;
  return String(
    (classification && typeof classification === "object"
      ? classification.decision || classification.status
      : classification)
    || item?.decision
    || item?.gate_decision
    || "",
  ).toUpperCase();
}

function acceptedEvidenceCount(evidence = []) {
  return evidence.filter((item) => ACCEPTED_GATE_DECISIONS.has(evidenceDecision(item))).length;
}

function supportingEvidenceCount(evidence = []) {
  return evidence.filter((item) => {
    const role = String(item?.role || item?.envelope?.role || "").toUpperCase();
    const canSupport = item?.can_support_conclusion ?? item?.envelope?.quality?.can_support_conclusion;
    return evidenceDecision(item) === "ACCEPT_SUPPORT" || (role === "SUPPORT" && canSupport !== false);
  }).length;
}

function rejectedEvidenceCount(evidence = []) {
  return evidence.filter((item) => !ACCEPTED_GATE_DECISIONS.has(evidenceDecision(item))).length;
}

function acceptedEvidenceBreakdown(evidence = []) {
  const labels = {
    ACCEPT_SUPPORT: "支持",
    ACCEPT_COUNTER: "反证",
    ACCEPT_NEUTRAL: "中性",
    ACCEPT_LIMITED: "受限",
  };
  const counts = new Map();
  evidence.forEach((item) => {
    const decision = evidenceDecision(item);
    if (labels[decision]) counts.set(decision, (counts.get(decision) || 0) + 1);
  });
  return [...counts].map(([decision, count]) => ({ decision, label: labels[decision], count }));
}

function firstEvidenceSeconds(arm) {
  const created = new Date(arm.detail?.created_at || 0).getTime();
  const timestamps = arm.evidence
    .map((item) => new Date(item.created_at || item.observed_at || 0).getTime())
    .filter((value) => Number.isFinite(value) && value > 0);
  if (!created || timestamps.length === 0) return null;
  return Math.max(0, Math.round((Math.min(...timestamps) - created) / 1000));
}

function firstSupportingEvidenceSeconds(arm) {
  const created = new Date(arm.detail?.created_at || 0).getTime();
  const timestamps = arm.evidence
    .filter((item) => supportingEvidenceCount([item]) > 0)
    .map((item) => new Date(item.created_at || item.observed_at || 0).getTime())
    .filter((value) => Number.isFinite(value) && value > 0);
  if (!created || timestamps.length === 0) return null;
  return Math.max(0, Math.round((Math.min(...timestamps) - created) / 1000));
}

function elapsedSeconds(arm) {
  const created = new Date(arm.detail?.created_at || 0).getTime();
  const updated = new Date(arm.detail?.updated_at || 0).getTime();
  if (!created || !updated || updated < created) return null;
  return Math.max(0, Math.round((updated - created) / 1000));
}

function evidenceLimitation(evidence = []) {
  const reasons = [];
  evidence.forEach((item) => {
    if (supportingEvidenceCount([item]) > 0) return;
    const classification = item?.classification;
    const candidates = [
      ...(Array.isArray(classification?.reasons) ? classification.reasons : []),
      classification?.reason,
      item?.envelope?.metadata?.hypothesis_predicate?.reason,
    ];
    candidates.filter(Boolean).forEach((reason) => {
      const text = String(reason).trim();
      if (text && !reasons.includes(text)) reasons.push(text);
    });
  });
  return reasons.slice(0, 2).join("；");
}

function reportLabel(report) {
  const status = String(report?.verification?.status || report?.verification_status || "").toUpperCase();
  if (status === "VERIFIED") return "已验证";
  if (status === "PARTIAL_WITHOUT_COUNTER") return "阶段性结论";
  if (status === "INSUFFICIENT_EVIDENCE") return "证据不足";
  return report ? "待校验" : "尚未生成";
}

function uniqueToolRoute(arm) {
  return [...new Set((arm?.tools || []).map((tool) => tool.tool_name).filter(Boolean))];
}

function routePosition(route, toolName) {
  const index = route.indexOf(toolName);
  return index < 0 ? null : index + 1;
}

function reportResult(arm) {
  const report = bestReport(arm?.reports || []);
  const verification = String(report?.verification?.status || report?.verification_status || "").toUpperCase();
  const verified = verification === "VERIFIED";
  return {
    report,
    verified,
    confidence: report ? Math.round(Number(report.confidence || 0) * 100) : null,
  };
}

function expectedRouteTool(pair, evaluationContext) {
  if (evaluationContext?.expectedTool) return evaluationContext.expectedTool;
  const activation = pair?.auto?.activations?.[0];
  return activation?.selected_tool || activation?.baseline_tool || uniqueToolRoute(pair?.auto)[0] || "";
}

export function buildLiveComparison(pair, evaluationContext = null) {
  if (!pair) return null;
  const autoRoute = uniqueToolRoute(pair.auto);
  const disabledRoute = uniqueToolRoute(pair.disabled);
  const expectedTool = expectedRouteTool(pair, evaluationContext);
  const autoPosition = routePosition(autoRoute, expectedTool);
  const disabledPosition = routePosition(disabledRoute, expectedTool);
  const autoReport = reportResult(pair.auto);
  const disabledReport = reportResult(pair.disabled);
  const autoSupport = supportingEvidenceCount(pair.auto?.evidence);
  const disabledSupport = supportingEvidenceCount(pair.disabled?.evidence);
  let routeTone = "neutral";
  let routeTitle = "还没有形成可比较的专项路线";
  let routeDetail = "等待 Skill 激活和真实工具调用后再比较。";
  if (expectedTool && autoPosition != null && disabledPosition == null) {
    routeTone = "positive";
    routeTitle = `Skill 在第 ${autoPosition} 轮命中关键采集器`;
    routeDetail = `关闭组在当前预算内没有执行“${diagnosticToolLabel(expectedTool)}”。`;
  } else if (expectedTool && autoPosition != null && disabledPosition != null && autoPosition < disabledPosition) {
    routeTone = "positive";
    routeTitle = `关键采集器提前 ${disabledPosition - autoPosition} 轮`;
    routeDetail = `“${diagnosticToolLabel(expectedTool)}”由关闭组第 ${disabledPosition} 轮提前到 Skill 组第 ${autoPosition} 轮。`;
  } else if (expectedTool && autoPosition != null && disabledPosition != null && autoPosition === disabledPosition) {
    routeTitle = "两组关键采集器命中轮次相同";
    routeDetail = `本次没有观察到“${diagnosticToolLabel(expectedTool)}”的路线提前量。`;
  } else if (expectedTool && disabledPosition != null && (autoPosition == null || autoPosition > disabledPosition)) {
    routeTone = "negative";
    routeTitle = "本次 Skill 路线没有更快命中关键采集器";
    routeDetail = autoPosition == null
      ? `Skill 组尚未执行“${diagnosticToolLabel(expectedTool)}”。`
      : `Skill 组第 ${autoPosition} 轮命中，关闭组第 ${disabledPosition} 轮命中。`;
  }
  let rootTitle = "当前不能判断哪组根因更准";
  let rootDetail = "双方尚未同时形成通过证据门禁的根因结论；路线提前不等于根因已经成立。";
  if (autoReport.verified || disabledReport.verified) {
    if (autoReport.verified && !disabledReport.verified) {
      rootTitle = "Skill 组形成了可信结论，关闭组尚未形成";
      rootDetail = `Skill 组有 ${autoSupport} 条可支撑结论的证据，关闭组有 ${disabledSupport} 条。`;
    } else if (!autoReport.verified && disabledReport.verified) {
      rootTitle = "关闭组形成了可信结论，Skill 组尚未形成";
      rootDetail = "本次属于 Skill 退化样本，需要保留并复盘，不能用聚合成绩覆盖。";
    } else {
      rootTitle = "两组都形成了可信结论";
      rootDetail = `Skill 组置信度 ${autoReport.confidence}% / 关闭组 ${disabledReport.confidence}%；仍需结合根因真值判断准确性。`;
    }
  }
  const autoFirst = firstEvidenceSeconds(pair.auto);
  const disabledFirst = firstEvidenceSeconds(pair.disabled);
  const autoFirstSupport = firstSupportingEvidenceSeconds(pair.auto);
  const disabledFirstSupport = firstSupportingEvidenceSeconds(pair.disabled);
  const supportTiming = autoFirstSupport == null && disabledFirstSupport == null
    ? "两组均未形成可支撑根因的证据。"
    : `首条支持证据：Skill ${autoFirstSupport == null ? "未形成" : `${autoFirstSupport} 秒`} / 关闭 ${disabledFirstSupport == null ? "未形成" : `${disabledFirstSupport} 秒`}。`;
  return {
    expectedTool,
    autoRoute,
    disabledRoute,
    autoPosition,
    disabledPosition,
    autoSupport,
    disabledSupport,
    routeTone,
    routeTitle,
    routeDetail,
    rootTitle,
    rootDetail,
    costTitle: `${pair.auto?.tools?.length || 0} 次 / ${pair.disabled?.tools?.length || 0} 次工具调用`,
    costDetail: autoFirst == null || disabledFirst == null
      ? "首条证据耗时尚未完整返回。"
      : `首份门禁结果：Skill ${autoFirst} 秒 / 关闭 ${disabledFirst} 秒。${supportTiming}`,
  };
}

async function hydrateArm(arm) {
  if (!arm.diagnosisId) return arm;
  const requests = await Promise.allSettled([
    getDropInsightDiagnosis(arm.diagnosisId),
    listDiagnosticSkillActivations(arm.diagnosisId),
    listDropInsightToolCalls(arm.diagnosisId),
    listDropInsightEvidence(arm.diagnosisId),
    listDropInsightReports(arm.diagnosisId),
    getDropInsightExplorationTree(arm.diagnosisId),
  ]);
  const value = (index, fallback) => requests[index].status === "fulfilled" ? requests[index].value : fallback;
  return {
    ...arm,
    detail: value(0, arm.detail),
    activations: value(1, arm.activations),
    tools: value(2, arm.tools),
    evidence: value(3, arm.evidence),
    reports: value(4, arm.reports),
    tree: value(5, arm.tree),
    error: requests[0].status === "rejected" ? (requests[0].reason?.message || "诊断状态加载失败") : "",
  };
}

function ArmView({ arm, onOpen }) {
  if (!arm.diagnosisId) return <Empty description={`${skillPolicyLabel(arm.policy)}组创建失败`} />;
  const report = bestReport(arm.reports);
  const activation = arm.activations[0] || null;
  const route = uniqueToolRoute(arm);
  const firstEvidence = firstEvidenceSeconds(arm);
  const firstSupport = firstSupportingEvidenceSeconds(arm);
  const elapsed = elapsedSeconds(arm);
  const limitation = supportingEvidenceCount(arm.evidence) ? "" : evidenceLimitation(arm.evidence);
  const status = arm.detail?.status || "CREATED";
  const gateBreakdown = acceptedEvidenceBreakdown(arm.evidence);
  const rounds = arm.tree?.stats?.rounds;
  return (
    <article className={`skill-ab-arm ${arm.policy === "AUTO" ? "is-auto" : "is-disabled"}`}>
      <div className="skill-ab-arm-header">
        <div>
          <Title level={5}>{arm.policy === "AUTO" ? "Skill 启用组" : "不使用 Skill 组"}</Title>
          <Text code copyable={{ text: arm.diagnosisId }} title={arm.diagnosisId}>
            会话 {shortDiagnosisId(arm.diagnosisId)}
          </Text>
        </div>
        <Space wrap>
          <Tag color={arm.policy === "AUTO" ? "green" : "blue"}>{skillPolicyLabel(arm.policy)}</Tag>
          <Tag>{diagnosticStatusLabel(status)}</Tag>
          {rounds != null && <Tag color="purple">第 {rounds} 轮</Tag>}
        </Space>
      </div>
      <div className="skill-ab-arm-body">
        {arm.error && <Alert type="error" showIcon message={arm.error} />}
        <div className="skill-ab-metrics" aria-label={`${arm.policy} 组真实诊断指标`}>
          <div className="skill-ab-metric"><b>{arm.activations.length}</b><span>Skill 激活</span></div>
          <div className="skill-ab-metric"><b>{arm.tools.length}</b><span>工具调用</span></div>
          <div className="skill-ab-metric is-primary"><b>{supportingEvidenceCount(arm.evidence)}</b><span>可支撑根因</span></div>
          <div className="skill-ab-metric"><b>{acceptedEvidenceCount(arm.evidence)}</b><span>门禁接受</span></div>
          <div className="skill-ab-metric"><b>{arm.tree?.stats?.rounds ?? "-"}</b><span>诊断轮次</span></div>
          <div className="skill-ab-metric"><b>{rejectedEvidenceCount(arm.evidence)}</b><span>拒绝/不可用</span></div>
        </div>
        {gateBreakdown.length > 0 && (
          <Space wrap size={[4, 4]} aria-label={`${arm.policy} 组门禁接受分类`}>
            <Text type="secondary">门禁接受构成</Text>
            {gateBreakdown.map((item) => (
              <Tag key={item.decision} color={item.decision === "ACCEPT_COUNTER" ? "red" : item.decision === "ACCEPT_SUPPORT" ? "green" : "blue"}>
                {item.label} {item.count}
              </Tag>
            ))}
          </Space>
        )}
        <div className="skill-ab-details">
          <div><Text type="secondary">复用结果</Text>{activation
            ? <><Tag color="green">{activation.skill_id || "已激活 Skill"}</Tag><Tag>匹配 {Math.round(Number(activation.match_score || 0) * 100)}%</Tag></>
            : <Tag>{arm.policy === "DISABLED" ? "策略已关闭" : "尚未命中"}</Tag>}</div>
          <div><Text type="secondary">真实路线</Text>{route.length ? route.map((item) => <Tag key={item}>{diagnosticToolLabel(item)}</Tag>) : <Tag>等待规划</Tag>}</div>
          <div><Text type="secondary">首份门禁结果</Text><Tag>{firstEvidence == null ? "等待材料" : `${firstEvidence} 秒`}</Tag></div>
          <div><Text type="secondary">首条支持证据</Text><Tag color={firstSupport == null ? "default" : "green"}>{firstSupport == null ? "本轮未形成" : `${firstSupport} 秒`}</Tag></div>
          <div><Text type="secondary">总探索耗时</Text><Tag>{elapsed == null ? "进行中" : `${elapsed} 秒`}</Tag></div>
          <div><Text type="secondary">报告</Text><Tag color={report?.verification?.status === "VERIFIED" ? "green" : report?.verification?.status === "PARTIAL_WITHOUT_COUNTER" ? "blue" : "default"}>{report ? `${Math.round(Number(report.confidence || 0) * 100)}% · ${reportLabel(report)}` : "尚未生成"}</Tag></div>
          {arm.detail?.created_at && <div><Text type="secondary">会话开始</Text><Tag>{String(arm.detail.created_at).replace("T", " ").slice(0, 19)}</Tag></div>}
          {limitation && <div><Text type="secondary">未形成支持证据</Text><Text>{limitation}</Text></div>}
        </div>
        <Button type="link" icon={<SelectOutlined />} onClick={() => onOpen?.(arm.diagnosisId)}>打开该诊断</Button>
      </div>
    </article>
  );
}

function BenchmarkSummary({ report, loading }) {
  if (loading) return <Spin size="small" tip="正在读取量化评测"><div className="skill-ab-benchmark-loading" /></Spin>;
  const root = report?.root_cause_evaluation_540;
  const paired = report?.paired_skill_ab_500;
  if (!root || !paired) {
    return <Alert type="warning" showIcon message="量化评测报告尚未生成" description="先运行根因真值集与 Skill A/B 评测脚本；页面不会填充演示数字。" />;
  }
  const percentage = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;
  return (
    <section className="skill-ab-benchmark" aria-label="Skill 量化评测结果">
      <div className="skill-ab-benchmark-heading">
        <div>
          <Text className="skill-ab-kicker">已运行的受控真值评测</Text>
          <Title level={5}>540 条根因评测 + 500 组同题 Skill A/B</Title>
        </div>
        <Button href={BENCHMARK_REPORT_URL} target="_blank" icon={<FileSearchOutlined />}>查看机器报告</Button>
      </div>
      <div className="skill-ab-score-grid">
        <article className="skill-ab-score-card is-featured">
          <span>500 组根因 Top-1</span>
          <strong>{percentage(paired.skill_enabled?.root_cause_top1_accuracy)}</strong>
          <small>Skill 开启；关闭组 {percentage(paired.no_skill?.root_cause_top1_accuracy)}</small>
        </article>
        <article className="skill-ab-score-card">
          <span>准确率差值</span>
          <strong>+{Number(paired.delta_percentage_points || 0).toFixed(1)} 个百分点</strong>
          <small>同一 Case、同一两次工具预算</small>
        </article>
        <article className="skill-ab-score-card">
          <span>关键采集器两轮内命中</span>
          <strong>{percentage(paired.skill_enabled?.decisive_collector_within_two_rate)}</strong>
          <small>关闭组 {percentage(paired.no_skill?.decisive_collector_within_two_rate)}</small>
        </article>
        <article className="skill-ab-score-card">
          <span>改善 / 退化</span>
          <strong>{paired.improved_cases} / {paired.regressed_cases}</strong>
          <small>p={paired.paired_significance?.p_value == null ? "-" : Number(paired.paired_significance.p_value).toExponential(2)}；不变 {paired.unchanged_cases}</small>
        </article>
      </div>
      <div className="skill-ab-benchmark-foot">
        <Tag color="purple">540 条：Skill {percentage(root.skill_enabled?.root_cause_top1_accuracy)}</Tag>
        <Tag>540 条：关闭 {percentage(root.no_skill?.root_cause_top1_accuracy)}</Tag>
        <Tag color="blue">关键路线提前 {paired.decisive_route_improved_cases} 组</Tag>
        <Tag color="cyan">
          差值 95% CI [{Number(paired.delta_bootstrap_95?.lower_percentage_points || 0).toFixed(1)}, {Number(paired.delta_bootstrap_95?.upper_percentage_points || 0).toFixed(1)}]pp
        </Tag>
        <Text type="secondary">来自 21 个可执行白名单故障合同的受控回放；不是 540 次公网真机注入，真机链路由独立验收报告证明。</Text>
      </div>
    </section>
  );
}

function LiveComparisonSummary({ comparison }) {
  if (!comparison) return null;
  const routeClass = comparison.routeTone === "positive" ? "is-positive" : comparison.routeTone === "negative" ? "is-negative" : "";
  return (
    <section className="skill-ab-live-summary" aria-label="当前真实 A/B 对比结论">
      <div className="skill-ab-live-title">
        <div>
          <Text className="skill-ab-kicker">当前这一次真实诊断</Text>
          <Title level={5}>先看结论，再看原始计数</Title>
        </div>
        <Tag color={comparison.routeTone === "positive" ? "green" : comparison.routeTone === "negative" ? "red" : "default"}>
          {comparison.routeTone === "positive" ? "观察到路线收益" : comparison.routeTone === "negative" ? "本次出现退化" : "暂不判胜"}
        </Tag>
      </div>
      <div className="skill-ab-verdict-grid">
        <article className={`skill-ab-verdict ${routeClass}`}>
          {comparison.routeTone === "positive" ? <CheckCircleOutlined /> : comparison.routeTone === "negative" ? <WarningOutlined /> : <ArrowRightOutlined />}
          <div><strong>{comparison.routeTitle}</strong><p>{comparison.routeDetail}</p></div>
        </article>
        <article className="skill-ab-verdict">
          <FileSearchOutlined />
          <div><strong>{comparison.rootTitle}</strong><p>{comparison.rootDetail}</p></div>
        </article>
        <article className="skill-ab-verdict">
          <ExperimentOutlined />
          <div><strong>{comparison.costTitle}</strong><p>{comparison.costDetail}</p></div>
        </article>
      </div>
      {comparison.expectedTool && (
        <div className="skill-ab-route-diff" aria-label="关键采集器命中轮次对比">
          <span>关键采集器</span>
          <b>{diagnosticToolLabel(comparison.expectedTool)}</b>
          <Tag color="green">Skill：{comparison.autoPosition == null ? "未命中" : `第 ${comparison.autoPosition} 轮`}</Tag>
          <ArrowRightOutlined aria-hidden />
          <Tag>关闭：{comparison.disabledPosition == null ? "未命中" : `第 ${comparison.disabledPosition} 轮`}</Tag>
        </div>
      )}
    </section>
  );
}

export default function SkillABPanel({ seedRequest = null, onOpenDiagnosis, onCasesChanged }) {
  const initialHistoryRef = useRef(null);
  if (initialHistoryRef.current == null) initialHistoryRef.current = readSkillABHistory();
  const [query, setQuery] = useState(seedRequest?.query || "");
  const [history, setHistory] = useState(initialHistoryRef.current);
  const [pair, setPair] = useState(() => historyRecordToPair(initialHistoryRef.current[0]));
  const [creating, setCreating] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [benchmarkReport, setBenchmarkReport] = useState(null);
  const [benchmarkLoading, setBenchmarkLoading] = useState(true);
  const requestRef = useRef(0);

  useEffect(() => {
    if (seedRequest?.query) setQuery(seedRequest.query);
  }, [seedRequest]);

  useEffect(() => {
    let active = true;
    if (typeof globalThis.fetch !== "function") {
      setBenchmarkLoading(false);
      return () => { active = false; };
    }
    globalThis.fetch(BENCHMARK_REPORT_URL, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((value) => { if (active) setBenchmarkReport(value); })
      .catch(() => { if (active) setBenchmarkReport(null); })
      .finally(() => { if (active) setBenchmarkLoading(false); });
    return () => { active = false; };
  }, []);

  const autoId = pair?.auto?.diagnosisId || "";
  const disabledId = pair?.disabled?.diagnosisId || "";
  const refreshPair = useCallback(async () => {
    if (!autoId && !disabledId) return;
    const requestId = ++requestRef.current;
    setRefreshing(true);
    try {
      const [auto, disabled] = await Promise.all([
        hydrateArm(emptyArm("AUTO", autoId)),
        hydrateArm(emptyArm("DISABLED", disabledId)),
      ]);
      if (requestId !== requestRef.current) return;
      const hydratedQuery = auto.detail?.query || disabled.detail?.query || "";
      const hydratedStartedAt = auto.detail?.created_at || disabled.detail?.created_at || null;
      setPair((current) => current ? {
        ...current,
        query: current.query || hydratedQuery,
        startedAt: current.startedAt || hydratedStartedAt,
        auto,
        disabled,
      } : current);
      if (hydratedQuery) setQuery((current) => current || hydratedQuery);
    } finally {
      if (requestId === requestRef.current) setRefreshing(false);
    }
  }, [autoId, disabledId]);

  useEffect(() => {
    if (pair && !pair.auto.detail && !pair.disabled.detail) void refreshPair();
  }, [autoId, disabledId]); // eslint-disable-line react-hooks/exhaustive-deps

  const active = Boolean(pair) && [pair.auto, pair.disabled].some(
    (arm) => arm.diagnosisId && !TERMINAL.has(String(arm.detail?.status || "CREATED").toUpperCase()),
  );
  usePolling(refreshPair, { interval: 2500, enabled: active });

  const scopeComparison = useMemo(() => {
    if (!pair) return { exact: false, known: false };
    const left = scopeKey(pair.auto.detail);
    const right = scopeKey(pair.disabled.detail);
    return { known: Boolean(left && right), exact: Boolean(left && right && left === right) };
  }, [pair]);
  const liveComparison = useMemo(
    () => buildLiveComparison(pair, seedRequest?.evaluation_context || null),
    [pair, seedRequest],
  );

  async function startComparison() {
    const text = query.trim();
    if (!text) {
      message.info("请先填写同一条诊断问题");
      return;
    }
    setCreating(true);
    try {
      const shared = {
        query: text,
        mode: seedRequest?.mode || "AUTONOMOUS",
        auto_scope: seedRequest?.auto_scope ?? true,
        ...(seedRequest?.target ? { target: seedRequest.target } : {}),
        ...(seedRequest?.time_range ? { time_range: seedRequest.time_range } : {}),
        ...(seedRequest?.budget ? { budget: seedRequest.budget } : {}),
      };
      const results = await Promise.allSettled([
        createDropInsightDiagnosis({ ...shared, skill_policy: "AUTO" }),
        createDropInsightDiagnosis({ ...shared, skill_policy: "DISABLED" }),
      ]);
      const autoIdNext = results[0].status === "fulfilled" ? results[0].value?.diagnosis_id : "";
      const disabledIdNext = results[1].status === "fulfilled" ? results[1].value?.diagnosis_id : "";
      if (!autoIdNext && !disabledIdNext) throw (results[0].reason || results[1].reason || new Error("两组诊断创建失败"));
      const nextPair = {
        query: text,
        startedAt: new Date().toISOString(),
        auto: emptyArm("AUTO", autoIdNext),
        disabled: emptyArm("DISABLED", disabledIdNext),
      };
      setPair(nextPair);
      const nextRecord = {
        autoId: autoIdNext,
        disabledId: disabledIdNext,
      };
      setHistory((current) => {
        const next = [nextRecord, ...current.filter((item) => item.autoId !== autoIdNext || item.disabledId !== disabledIdNext)].slice(0, MAX_HISTORY);
        writeSkillABHistory(next);
        return next;
      });
      await Promise.allSettled([
        autoIdNext ? runDropInsightPlanner(autoIdNext) : Promise.resolve(),
        disabledIdNext ? runDropInsightPlanner(disabledIdNext) : Promise.resolve(),
      ]);
      const [auto, disabled] = await Promise.all([
        hydrateArm(emptyArm("AUTO", autoIdNext)),
        hydrateArm(emptyArm("DISABLED", disabledIdNext)),
      ]);
      setPair((current) => current ? { ...current, auto, disabled } : current);
      await onCasesChanged?.();
      if (!autoIdNext || !disabledIdNext) message.warning("只有一组诊断创建成功，请查看错误后重试");
      else message.success("真实 Skill A/B 已启动");
    } catch (createError) {
      message.error(createError?.message || "Skill A/B 创建失败");
    } finally {
      setCreating(false);
    }
  }

  return (
    <section className="skill-ab-panel" aria-label="真实 Skill A/B 对比">
      <SkillExperimentPanel
        seedRequest={seedRequest}
        onOpenDiagnosis={onOpenDiagnosis}
        onCasesChanged={onCasesChanged}
      />
      <div className="showcase-section-header">
        <div>
          <Title level={4}>真实 Skill：启用与关闭对比</Title>
          <Paragraph>两组都创建真实诊断、真实调用工具和证据门禁。支持、反证、中性和受限四类准入结果都计入“门禁接受”；历史只保存诊断 ID，指标会从服务端重新读取。</Paragraph>
        </div>
        {pair && <Button icon={<ReloadOutlined />} loading={refreshing} onClick={refreshPair}>刷新两组</Button>}
      </div>
      <BenchmarkSummary report={benchmarkReport} loading={benchmarkLoading} />
      <div className="skill-ab-controls">
        <div className="skill-ab-control-row">
          <label>
            <Text strong>两组共用问题</Text>
            <Input.TextArea
              aria-label="Skill A/B 诊断问题"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              autoSize={{ minRows: 2, maxRows: 4 }}
              placeholder="例如：诊断 demo 服务 CPU 持续升高，找出热点函数并排除 I/O 等待"
            />
          </label>
          <Button type="primary" icon={<ExperimentOutlined />} loading={creating} onClick={startComparison}>启动真实对比</Button>
        </div>
        <Alert
          className="skill-ab-scope-note"
          type={scopeComparison.exact ? "success" : "warning"}
          showIcon
          message={scopeComparison.exact ? "两组已确认严格同一诊断范围" : "当前不是严格同范围实验"}
          description={scopeComparison.exact
            ? "两组目标绑定、进程与时间窗完全一致，可直接比较路径差异。"
            : "当前公共 API 会让两组独立自动发现目标和时间窗。结果可演示真实路线，但不能据此声称 Skill 提升了准确率或速度。"}
        />
      </div>
      {creating && !pair ? <Spin tip="正在创建两组真实诊断"><div style={{ minHeight: 120 }} /></Spin> : null}
      {pair && (
        <>
          <LiveComparisonSummary comparison={liveComparison} />
          <div className="skill-ab-grid">
            <ArmView arm={pair.auto} onOpen={onOpenDiagnosis} />
            <ArmView arm={pair.disabled} onOpen={onOpenDiagnosis} />
          </div>
        </>
      )}
      {history.length > 0 && (
        <section className="skill-ab-history" aria-label="Skill A/B 对比历史">
          <div>
            <Text strong>本浏览器的 A/B 对比历史</Text>
            <Paragraph type="secondary">返回验证中心或刷新页面后仍可打开；状态和指标均从真实诊断重新读取。</Paragraph>
          </div>
          <div className="skill-ab-history-list">
            {history.map((item) => (
              <Button
                key={`${item.autoId}:${item.disabledId}`}
                className="skill-ab-history-item"
                type={autoId === item.autoId && disabledId === item.disabledId ? "primary" : "default"}
                onClick={() => {
                  setQuery("");
                  setPair(historyRecordToPair(item));
                }}
              >
                <span>启用 {shortDiagnosisId(item.autoId)} / 关闭 {shortDiagnosisId(item.disabledId)}</span>
                <small>点击后从服务端重新读取</small>
              </Button>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
