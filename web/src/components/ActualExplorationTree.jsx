import {
  AimOutlined,
  ApartmentOutlined,
  BranchesOutlined,
  BookOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  InfoCircleOutlined,
  OrderedListOutlined,
  MinusCircleOutlined,
  SearchOutlined,
  SwapOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Descriptions, Modal, Segmented, Space, Tag, Tooltip, Typography } from "antd";
import {
  formatLatsScore,
  latsEnvironmentLabel,
  latsExecutionModeLabel,
  latsPhaseLabel,
  latsRolloutLabel,
  latsTerminationLabel,
  normalizeLatsNodeMetrics,
  normalizeLatsSearch,
} from "../utils/latsSearch";
import {
  chineseDiagnosticText,
  diagnosticStatusLabel,
  diagnosticToolLabel,
} from "../utils/diagnosisDisplay";
import { hypothesisSemanticKey } from "../utils/hypothesisSemantics";
import "./MentorComplexShowcase.css";
import "./ActualExplorationTree.css";

const { Text } = Typography;

const TOOL_LABELS = {
  collect_sys_metrics: "系统指标",
  collect_database_diagnostics: "数据库状态",
  start_perf_profile: "CPU 火焰图",
  start_pyspy_profile: "Python 调用栈",
  start_ebpf_io_profile: "I/O 延迟",
  get_agent_status: "采集节点检查",
};

const CATEGORY_LABELS = {
  CPU_HOTSPOT: "CPU",
  PYTHON_RUNTIME: "Python",
  GO_RUNTIME: "Go",
  JVM_RUNTIME: "Java / JVM",
  CPP_NATIVE: "C++ / 原生程序",
  IO_LATENCY: "I/O",
  MEMORY_PRESSURE: "内存",
  SYSTEM_RESOURCE: "系统资源",
  DATABASE_LOCK: "数据库",
  NETWORK_DEGRADATION: "网络",
  QUEUE_BACKLOG: "队列积压",
  SAME_HOST_CONTENTION: "同机资源争用",
  GENERAL: "通用",
};

const PRUNED = new Set([
  "REFUTED",
  "FALSIFIED",
  "DISPROVED",
  "RULED_OUT",
  "REJECTED",
  "FAILED",
  "CANCELLED",
  "DENIED",
]);

function inferCategory(name = "") {
  if (name.includes("perf")) return "CPU_HOTSPOT";
  if (name.includes("pyspy")) return "PYTHON_RUNTIME";
  if (name.includes("ebpf") || name.includes("io")) return "IO_LATENCY";
  if (name.includes("database")) return "DATABASE_LOCK";
  if (name.includes("network")) return "NETWORK_DEGRADATION";
  if (name.includes("sys_metrics")) return "SYSTEM_RESOURCE";
  return "GENERAL";
}

function buildFallbackTree(hypotheses = [], toolCalls = [], report) {
  const orderedTools = [...toolCalls].sort(
    (a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0),
  );
  const hypothesisIds = new Set(hypotheses.map((item) => item.hypothesis_id));
  const nodes = hypotheses.map((hypothesis) => {
    const status = String(hypothesis.status || "OPEN").toUpperCase();
    return {
      id: `hypothesis:${hypothesis.hypothesis_id}`,
      parent_id: hypothesis.parent_hypothesis_id && hypothesisIds.has(hypothesis.parent_hypothesis_id)
        ? `hypothesis:${hypothesis.parent_hypothesis_id}`
        : null,
      hypothesis_id: hypothesis.hypothesis_id,
      kind: "hypothesis",
      title: hypothesis.statement,
      status,
      state: hypothesis.hypothesis_id === report?.hypothesis_id
        ? "confirmed"
        : PRUNED.has(status) ? "refuted" : "visited",
      round_index: hypothesis.round_index || 1,
      domain: `第 ${hypothesis.round_index || 1} 轮假设`,
      evidence: hypothesis.generation_reason || "",
      changed_at: hypothesis.updated_at || hypothesis.created_at,
    };
  });
  orderedTools.forEach((tool, index) => {
    const status = String(tool.status || "UNKNOWN").toUpperCase();
    const hypothesisMatched = hypothesisIds.has(tool.hypothesis_id);
    nodes.push({
      id: `tool:${tool.tool_call_id || index}`,
      parent_id: hypothesisMatched ? `hypothesis:${tool.hypothesis_id}` : null,
      kind: "tool",
      title: `${index + 1}. ${TOOL_LABELS[tool.tool_name] || tool.tool_name}`,
      status,
      state: PRUNED.has(status)
        ? "refuted"
        : tool.hypothesis_id === report?.hypothesis_id ? "confirmed" : "visited",
      category: inferCategory(tool.tool_name),
      domain: CATEGORY_LABELS[inferCategory(tool.tool_name)],
      round_index: hypotheses.find((item) => item.hypothesis_id === tool.hypothesis_id)?.round_index || 1,
      tool: tool.tool_name,
      evidence: tool.policy_reason || "",
      changed_at: tool.executed_at || tool.decided_at || tool.created_at,
    });
  });
  const switches = [];
  for (let index = 1; index < orderedTools.length; index += 1) {
    const previous = inferCategory(orderedTools[index - 1].tool_name);
    const current = inferCategory(orderedTools[index].tool_name);
    if (previous !== current) switches.push({
      from_category: previous,
      to_category: current,
      reason: "上一方向证据不足，转向新的取证分支",
    });
  }
  return { nodes, switches };
}

const NODE_META = {
  visited: { label: "已调查", color: "blue", icon: <BranchesOutlined /> },
  refuted: { label: "反证剪枝", color: "red", icon: <CloseCircleOutlined /> },
  confirmed: { label: "根因路径", color: "green", icon: <CheckCircleOutlined /> },
  unvisited: { label: "满足停止条件", color: "default", icon: <MinusCircleOutlined /> },
};

const KIND_LABELS = {
  diagnosis: "诊断根节点",
  intervention: "人工干预",
  hypothesis: "候选假设",
  tool: "工具调用",
  evidence: "证据裁决",
  report: "诊断结论",
  verification: "修复复测",
};

const EDGE_KIND_LABELS = {
  BRANCH: "假设分支",
  INVESTIGATE: "发起取证",
  SUPPORT: "支持证据",
  COUNTER: "反证",
  REPORT: "形成结论",
  VERIFY: "修复复测",
  INTERVENE: "人工转向",
  REPLAN: "重新规划",
};

const COUNTER_EVIDENCE = new Set(["ACCEPT_COUNTER", "REJECT", "REJECT_LOW_QUALITY"]);

const SKILL_STATE_META = {
  ACTIVE: { label: "持续生效", tone: "active", color: "processing" },
  ACTIVATED: { label: "已激活", tone: "active", color: "processing" },
  REUSED: { label: "跨轮沿用", tone: "active", color: "cyan" },
  MATCHED: { label: "已召回", tone: "matched", color: "blue" },
  RETRIEVED: { label: "已召回", tone: "matched", color: "blue" },
  DEVIATED: { label: "已偏离", tone: "deviated", color: "orange" },
  SWITCHED: { label: "已切换", tone: "deviated", color: "orange" },
  EXHAUSTED: { label: "路线耗尽", tone: "exited", color: "default" },
  EXITED: { label: "已退出", tone: "exited", color: "default" },
  DEACTIVATED: { label: "已退出", tone: "exited", color: "default" },
  COMPLETED: { label: "路线完成", tone: "completed", color: "green" },
  QUARANTINED: { label: "已隔离", tone: "deviated", color: "red" },
};

const SKILL_EVENT_META = {
  RETRIEVED: { label: "召回候选", tone: "matched" },
  MATCHED: { label: "召回候选", tone: "matched" },
  LOADED: { label: "按需加载", tone: "loaded" },
  ACTIVATED: { label: "激活路线", tone: "active" },
  REUSED: { label: "跨轮沿用", tone: "reused" },
  CONTINUED: { label: "跨轮沿用", tone: "reused" },
  DEVIATED: { label: "偏离路线", tone: "deviated" },
  SWITCHED: { label: "切换路线", tone: "deviated" },
  EXHAUSTED: { label: "路线耗尽", tone: "exited" },
  EXITED: { label: "退出路线", tone: "exited" },
  DEACTIVATED: { label: "退出路线", tone: "exited" },
  COMPLETED: { label: "路线完成", tone: "completed" },
};

const SKILL_STEP_STATUS = {
  COMPLETED: { label: "已执行", tone: "completed" },
  SUCCEEDED: { label: "已执行", tone: "completed" },
  SUCCESS: { label: "已执行", tone: "completed" },
  RUNNING: { label: "正在执行", tone: "running" },
  ACTIVE: { label: "当前步骤", tone: "running" },
  CURRENT: { label: "当前步骤", tone: "running" },
  PENDING: { label: "待执行", tone: "pending" },
  PLANNED: { label: "待执行", tone: "pending" },
  APPLIED: { label: "已用于规划", tone: "running" },
  SKIPPED: { label: "已跳过", tone: "skipped" },
  REFUTED: { label: "被反证", tone: "deviated" },
  FAILED: { label: "执行失败", tone: "deviated" },
};

function firstPresent(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}

function firstArray(...values) {
  return values.find((value) => Array.isArray(value) && value.length > 0)
    || values.find(Array.isArray)
    || [];
}

function skillEventKind(value = "") {
  return String(value)
    .trim()
    .toUpperCase()
    .replace(/^SKILL[.:_-]/, "")
    .replace(/[^A-Z]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function skillRetrievalMethodLabel(value = "") {
  const method = String(value || "").toUpperCase();
  if (!method) return "检索方法未上报";
  if (method.includes("BM25") && (
    method.includes("VECTOR") || method.includes("HASH") || method.includes("NGRAM") || method.includes("CONCEPT")
  )) {
    return "BM25 + 本地哈希/领域概念特征";
  }
  if (method.includes("HYBRID")) return "本地混合检索";
  if (method.includes("BM25")) return "BM25 文本检索";
  if (method.includes("RULE") || method.includes("GATE")) return "结构化门禁";
  return chineseDiagnosticText(value);
}

function skillScoreLabel(value) {
  const score = Number(value);
  if (!Number.isFinite(score)) return "未上报";
  if (Math.abs(score) <= 1) return `${Math.round(score * 100)}%`;
  return Number.isInteger(score) ? String(score) : score.toFixed(3);
}

function normalizeSkillRouteStep(step, index, applications = []) {
  const source = typeof step === "string" ? { tool: step } : (step || {});
  const tool = firstPresent(source.tool, source.tool_name, source.name, source.id, `步骤 ${index + 1}`);
  const application = [...applications].reverse().find((item) => (
    firstPresent(item.tool, item.tool_name, item.selected_tool) === tool
  ));
  const observedStatuses = Array.isArray(source.observed_statuses) ? source.observed_statuses : [];
  const selectedRounds = Array.isArray(source.selected_rounds) ? source.selected_rounds : [];
  const status = skillEventKind(firstPresent(
    source.status,
    source.state,
    observedStatuses[observedStatuses.length - 1],
    application?.status,
    source.selected_by_skill || application ? "APPLIED" : "PENDING",
  ));
  return {
    ...source,
    id: String(firstPresent(source.id, source.step_id, `${tool}:${index}`)),
    tool: String(tool),
    order: Number(firstPresent(source.order, source.route_index, source.index, index + 1)),
    status,
    roundIndex: Number(firstPresent(
      source.round_index,
      source.round,
      selectedRounds[selectedRounds.length - 1],
      application?.round_index,
      0,
    )),
    applicationIndex: Number(firstPresent(source.application_index, application?.application_index, 0)),
    reason: chineseDiagnosticText(firstPresent(source.reason, source.detail, application?.reason, ""), ""),
  };
}

function normalizeSkillTimelineItem(item, index) {
  const source = item || {};
  const kind = skillEventKind(firstPresent(source.kind, source.type, source.event, source.event_type, source.state, ""));
  const meta = SKILL_EVENT_META[kind] || {
    label: chineseDiagnosticText(firstPresent(source.label, kind, "路线更新")),
    tone: "neutral",
  };
  const roundIndex = Number(firstPresent(source.round_index, source.round, 0));
  const tool = firstPresent(source.tool, source.tool_name, source.selected_tool, "");
  return {
    ...source,
    id: String(firstPresent(source.id, source.event_id, `${kind || "event"}:${roundIndex}:${index}`)),
    kind,
    label: firstPresent(source.label, roundIndex > 0 && ["REUSED", "CONTINUED"].includes(kind)
      ? `第 ${roundIndex} 轮沿用`
      : meta.label),
    tone: meta.tone,
    roundIndex,
    tool: tool ? String(tool) : "",
    at: firstPresent(source.at, source.created_at, source.timestamp, ""),
    reason: chineseDiagnosticText(firstPresent(source.reason, source.detail, source.summary, ""), ""),
  };
}

/**
 * skill_trace 是新增的服务端投影。兼容对象、事件数组和旧版 activation 快照，
 * 这样滚动升级期间旧 API 不会让整棵 LATS 树失效。
 */
function normalizeSkillTrace(rawTrace, rawOverlay = null) {
  if (!rawTrace) return null;
  let source = rawTrace;
  let list = [];
  if (Array.isArray(rawTrace)) {
    list = rawTrace.filter(Boolean);
    const selected = [...list].reverse().find((item) => (
      item?.current_skill || item?.skill || item?.route_steps || item?.retrieval
    )) || [...list].reverse().find((item) => item?.skill_id || item?.name) || {};
    const activationArray = list.some((item) => (
      item?.activation_id || Array.isArray(item?.reuse_trace) || Array.isArray(item?.probe_order)
    ));
    if (activationArray) {
      const projectedTimeline = [];
      if (selected.retrieval || selected.match_score != null || selected.matched_terms?.length) {
        projectedTimeline.push({
          kind: "RETRIEVED",
          round_index: selected.reuse_trace?.[0]?.round_index || 1,
          reason: selected.matched_terms?.length ? `命中线索：${selected.matched_terms.join("、")}` : "",
        });
      }
      list.forEach((activation, activationIndex) => {
        const reuse = Array.isArray(activation.reuse_trace) && activation.reuse_trace.length
          ? activation.reuse_trace
          : [{ round_index: 1, selected_tool: activation.selected_tool }];
        const firstApplication = reuse[0] || {};
        projectedTimeline.push({
          ...firstApplication,
          kind: activationIndex > 0 ? "SWITCHED" : "ACTIVATED",
          tool: firstPresent(firstApplication.selected_tool, activation.selected_tool, ""),
          reason: firstPresent(firstApplication.reason, activation.category_correction?.guard, ""),
        });
        reuse.forEach((item, reuseIndex) => {
          if (reuseIndex === 0 && !["DEVIATED", "EXHAUSTED", "EXITED", "DEACTIVATED", "COMPLETED", "QUARANTINED"].includes(
            skillEventKind(item.state),
          )) return;
          const persistedKind = skillEventKind(item.state);
          projectedTimeline.push({
            ...item,
            kind: ["DEVIATED", "EXHAUSTED", "EXITED", "DEACTIVATED", "COMPLETED", "QUARANTINED", "SWITCHED"].includes(persistedKind)
              ? persistedKind
              : "REUSED",
            tool: firstPresent(item.selected_tool, activation.selected_tool, ""),
            reason: firstPresent(item.exit_reason, item.reason, activation.category_correction?.guard, ""),
          });
        });
      });
      source = { ...selected, timeline: projectedTimeline };
    } else {
      source = { ...selected, timeline: selected.timeline || list };
    }
  }

  const current = source.current_skill || source.skill || source.activation || source;
  const rawRetrieval = source.retrieval || current.retrieval || source.match || current.match || {};
  const retrieval = typeof rawRetrieval === "string" ? { method: rawRetrieval } : rawRetrieval;
  const applications = (source === current
    ? (Array.isArray(source.applications) ? source.applications : [])
    : [
      ...(Array.isArray(source.applications) ? source.applications : []),
      ...(Array.isArray(current.applications) ? current.applications : []),
    ]).filter(Boolean);
  const overlayRoutes = Array.isArray(rawOverlay?.routes) ? rawOverlay.routes : [];
  const overlayRoute = overlayRoutes.find((item) => (
    String(item.activation_id || "") === String(firstPresent(current.activation_id, source.activation_id, ""))
    || String(item.skill_id || "") === String(firstPresent(current.skill_id, source.skill_id, ""))
  ));
  const rawSteps = firstArray(
    overlayRoute?.steps,
    source.route_steps,
    current.route_steps,
    source.route,
    current.route,
    source.probe_order,
    current.probe_order,
  );
  const routeSteps = rawSteps.map((step, index) => normalizeSkillRouteStep(step, index, applications));

  const skillId = String(firstPresent(current.skill_id, source.skill_id, current.id, source.id, ""));
  const skillName = String(firstPresent(
    current.skill_name,
    current.display_name,
    current.name,
    source.skill_name,
    source.display_name,
    source.name,
    current.summary,
    source.summary,
    skillId,
  ));
  const state = skillEventKind(firstPresent(
    current.state,
    current.status,
    source.state,
    source.status,
    current.kind,
    source.kind,
    source.reuse_trace?.length > 1 || current.reuse_trace?.length > 1 ? "REUSED" : "ACTIVE",
  ));
  const repositoryInstructionLoaded = Boolean(
    firstPresent(current.instruction_trust, source.instruction_trust, current.trust, source.trust) === "REPOSITORY_REVIEWED"
    && firstPresent(current.source_path, source.source_path)
    && firstPresent(
      current.instruction_sha256,
      source.instruction_sha256,
      current.source_sha256,
      source.source_sha256,
      current.content_sha256,
      source.content_sha256,
    ),
  );
  const loadMode = skillEventKind(firstPresent(
    current.load_mode,
    source.load_mode,
    repositoryInstructionLoaded ? "FULL_SKILL_MD" : "",
  ));
  const explicitLoaded = firstPresent(
    current.full_skill_loaded,
    source.full_skill_loaded,
    current.instructions_loaded,
    source.instructions_loaded,
  );
  const fullSkillLoaded = explicitLoaded === undefined
    ? (loadMode ? loadMode.includes("FULL_SKILL") : null)
    : Boolean(explicitLoaded);
  const loadedSections = firstArray(current.loaded_sections, source.loaded_sections);

  let timelineSource = firstArray(source.timeline, current.timeline, source.events);
  if (!timelineSource.length) {
    timelineSource = [];
    if (Object.keys(retrieval).length) {
      timelineSource.push({ kind: "RETRIEVED", reason: retrieval.reason });
    }
    if (skillId && !["MATCHED", "RETRIEVED"].includes(state)) {
      timelineSource.push({ kind: "ACTIVATED", round_index: firstPresent(source.activated_round, 1) });
    }
    applications.slice(1).forEach((application, index) => timelineSource.push({
      ...application,
      kind: "REUSED",
      round_index: firstPresent(application.round_index, application.round, index + 2),
    }));
    if (["DEVIATED", "SWITCHED", "EXHAUSTED", "EXITED", "DEACTIVATED", "COMPLETED", "QUARANTINED"].includes(state)) {
      timelineSource.push({ kind: state, reason: firstPresent(source.exit_reason, source.state_reason, "") });
    }
  }
  const timeline = timelineSource.map(normalizeSkillTimelineItem);
  const hasVisibleData = skillId || skillName || timeline.length || routeSteps.length || Object.keys(retrieval).length;
  if (!hasVisibleData) return null;

  return {
    skillId,
    skillName: skillName || "未命名 Skill",
    version: firstPresent(current.version, current.skill_version, source.version, source.skill_version, ""),
    category: firstPresent(current.category, source.category, current.skill_category, source.skill_category, retrieval.selected_category, ""),
    state,
    stateMeta: SKILL_STATE_META[state] || { label: chineseDiagnosticText(state || "状态未知"), tone: "neutral", color: "default" },
    sourcePath: firstPresent(current.source_path, source.source_path, ""),
    contentSha256: firstPresent(
      current.content_sha256,
      source.content_sha256,
      current.instruction_sha256,
      source.instruction_sha256,
      current.source_sha256,
      source.source_sha256,
      "",
    ),
    loadMode,
    fullSkillLoaded,
    loadedSections: loadedSections.map((section, index) => String(firstPresent(
      typeof section === "string" ? section : null,
      section?.title,
      section?.name,
      section?.id,
      `章节 ${index + 1}`,
    ))),
    retrieval: {
      method: firstPresent(retrieval.method, retrieval.retriever, retrieval.strategy, source.retrieval_method, ""),
      score: firstPresent(retrieval.score, retrieval.match_score, source.match_score),
      margin: firstPresent(retrieval.margin, retrieval.score_margin, source.score_margin),
      reason: chineseDiagnosticText(firstPresent(
        retrieval.reason,
        retrieval.match_reason,
        source.match_reason,
        source.matched_terms?.length ? `命中线索：${source.matched_terms.join("、")}` : "",
      ), ""),
      categoryCorrected: Boolean(firstPresent(
        retrieval.category_corrected,
        source.category_corrected,
        source.category_correction,
        false,
      )),
      baselineCategory: firstPresent(
        retrieval.baseline_category,
        source.baseline_category,
        source.category_correction?.from,
        "",
      ),
      selectedCategory: firstPresent(
        retrieval.selected_category,
        source.selected_category,
        source.category_correction?.to,
        current.category,
        source.category,
        current.skill_category,
        source.skill_category,
        "",
      ),
    },
    routeSteps,
    timeline,
    currentStep: firstPresent(source.current_step, current.current_step) == null
      ? null
      : Number(firstPresent(source.current_step, current.current_step)),
    currentStepIndex: firstPresent(source.current_step_index, current.current_step_index) == null
      ? null
      : Number(firstPresent(source.current_step_index, current.current_step_index)),
    currentTool: firstPresent(
      source.current_selected_tool,
      current.current_selected_tool,
      source.selected_tool,
      current.selected_tool,
      "",
    ),
  };
}

function normalizeTreeNode(node, verifiedHypothesisId = "") {
  const kind = String(node.kind || "").toLowerCase();
  const status = String(node.status || "UNKNOWN").toUpperCase();
  let state = String(node.state || "").toLowerCase();
  if (!NODE_META[state]) {
    if (node.id === verifiedHypothesisId || node.verified) state = "confirmed";
    else state = PRUNED.has(status) ? "refuted" : "visited";
  }
  const rawTitle = node.title || node.label || node.id;
  return {
    ...node,
    id: String(node.id),
    parent_id: node.parent_id == null ? null : String(node.parent_id),
    kind,
    title: kind === "tool"
      ? TOOL_LABELS[rawTitle] || diagnosticToolLabel(rawTitle)
      : chineseDiagnosticText(rawTitle),
    state,
    status,
    round_index: Number(node.round_index ?? node.round ?? 0),
    evidence: chineseDiagnosticText(node.evidence || node.reason || "", ""),
    tool: node.tool || (kind === "tool" ? node.label : ""),
    search_metrics: normalizeLatsNodeMetrics(node.search_metrics || node.searchMetrics),
  };
}

function nodeRecency(node, index) {
  const round = Number(node.round_index || 0);
  const timestamp = new Date(node.changed_at || 0).getTime();
  return {
    round: Number.isFinite(round) ? round : 0,
    timestamp: Number.isFinite(timestamp) ? timestamp : 0,
    index,
  };
}

function compareNodeRecency(left, right) {
  const a = nodeRecency(left.node, left.index);
  const b = nodeRecency(right.node, right.index);
  return a.round - b.round || a.timestamp - b.timestamp || a.index - b.index;
}

function metricHistoryEntry(node) {
  if (!node.search_metrics) return null;
  return {
    nodeId: node.id,
    roundIndex: node.round_index || 0,
    changedAt: node.changed_at || "",
    ...node.search_metrics,
  };
}

/**
 * 旧版本的服务端树可能在每次重规划时为同一因果假设创建新节点。页面只合并
 * hypothesis 节点；工具、证据、报告、人工干预和轮次仍逐条保留。合并后的节点
 * 使用最近一次状态，同时保存每轮 Q/Visits/Reward 快照供评分弹窗回放。
 */
function mergeLegacyHypothesisNodes(nodes, edges = []) {
  const hypothesisGroups = new Map();
  nodes.forEach((node, index) => {
    if (node.kind !== "hypothesis") return;
    const key = hypothesisSemanticKey(node.title);
    if (!hypothesisGroups.has(key)) hypothesisGroups.set(key, []);
    hypothesisGroups.get(key).push({ node, index });
  });

  const aliasById = new Map();
  const mergedById = new Map();
  let mergedCount = 0;
  hypothesisGroups.forEach((entries) => {
    const ordered = [...entries].sort(compareNodeRecency);
    const representative = ordered[ordered.length - 1].node;
    ordered.forEach(({ node }) => aliasById.set(node.id, representative.id));
    if (ordered.length === 1) {
      mergedById.set(representative.id, representative);
      return;
    }
    mergedCount += ordered.length - 1;
    const metricHistory = ordered
      .flatMap(({ node }) => node.search_metric_history?.length
        ? node.search_metric_history
        : [metricHistoryEntry(node)])
      .filter(Boolean);
    const latestMetrics = [...ordered].reverse().find(({ node }) => node.search_metrics)?.node.search_metrics || null;
    mergedById.set(representative.id, {
      ...representative,
      search_metrics: latestMetrics,
      search_metric_history: metricHistory,
      semantic_merged_count: ordered.length,
      merged_node_ids: ordered.map(({ node }) => node.id),
      round_indices: [...new Set(ordered.map(({ node }) => Number(node.round_index || 0)).filter((round) => round > 0))],
      state_history: ordered.map(({ node }) => ({
        nodeId: node.id,
        roundIndex: node.round_index || 0,
        state: node.state,
        status: node.status,
      })),
    });
  });

  const remap = (value) => aliasById.get(String(value)) || String(value);
  const projectedNodes = [];
  const emittedHypotheses = new Set();
  nodes.forEach((node) => {
    if (node.kind === "hypothesis") {
      const canonicalId = remap(node.id);
      if (emittedHypotheses.has(canonicalId)) return;
      emittedHypotheses.add(canonicalId);
      const merged = mergedById.get(canonicalId) || node;
      const groupIds = new Set(merged.merged_node_ids || [canonicalId]);
      const parentCandidate = [...nodes]
        .filter((candidate) => groupIds.has(candidate.id))
        .reverse()
        .map((candidate) => candidate.parent_id)
        .find((parentId) => parentId && remap(parentId) !== canonicalId);
      projectedNodes.push({
        ...merged,
        parent_id: parentCandidate ? remap(parentCandidate) : null,
      });
      return;
    }
    projectedNodes.push({
      ...node,
      parent_id: node.parent_id ? remap(node.parent_id) : null,
    });
  });

  const edgeKeys = new Set();
  const projectedEdges = edges.reduce((result, edge) => {
    const fromRaw = edge?.from ?? edge?.source ?? edge?.parent_id;
    const toRaw = edge?.to ?? edge?.target ?? edge?.child_id;
    if (fromRaw == null || toRaw == null) return result;
    const from = remap(fromRaw);
    const to = remap(toRaw);
    if (from === to) return result;
    const kind = String(edge?.kind || edge?.type || "").toUpperCase();
    const key = `${from}\u0000${to}\u0000${kind}`;
    if (edgeKeys.has(key)) return result;
    edgeKeys.add(key);
    result.push({ ...edge, from, to });
    return result;
  }, []);

  return { nodes: projectedNodes, edges: projectedEdges, aliasById, mergedCount };
}

function scoreLabel(search) {
  const algorithm = String(search?.algorithm || "").toUpperCase();
  return algorithm.includes("PUCT") ? "PUCT" : algorithm.includes("UCT") ? "UCT" : "UCT / PUCT";
}

function nodeMetricAria(metrics, label) {
  if (!metrics) return "";
  const values = [
    metrics.visits != null ? `访问 ${metrics.visits} 次` : "",
    metrics.meanValue != null ? `平均价值 ${formatLatsScore(metrics.meanValue)}` : "",
    metrics.prior != null ? `先验 ${formatLatsScore(metrics.prior)}` : "",
    metrics.uctScore != null ? `${label} ${formatLatsScore(metrics.uctScore)}` : "",
  ].filter(Boolean);
  return values.length ? `，LATS ${values.join("，")}` : "";
}

function LatsNodeMetrics({ node, scoreName, onInspect }) {
  const metrics = node.search_metrics;
  if (!metrics) return null;
  const values = [
    { key: "visits", label: "访问次数", value: metrics.visits == null ? null : String(metrics.visits) },
    { key: "mean", label: "平均回报 Q", value: metrics.meanValue == null ? null : formatLatsScore(metrics.meanValue) },
    { key: "prior", label: "先验概率", value: metrics.prior == null ? null : formatLatsScore(metrics.prior) },
    { key: "uct", label: scoreName, value: metrics.uctScore == null ? null : formatLatsScore(metrics.uctScore) },
  ].filter((item) => item.value != null);
  if (!values.length && !node.lats_selected && !node.lats_best_path) return null;
  return (
    <div className="tree-lats-metrics" aria-label={`LATS 节点指标${nodeMetricAria(metrics, scoreName)}`}>
      {(node.lats_selected || node.lats_best_path) && (
        <Space wrap size={[4, 4]} className="tree-lats-flags">
          {node.lats_selected && <Tag color="processing">当前选择</Tag>}
          {node.lats_best_path && <Tag color="gold">当前最佳路径</Tag>}
        </Space>
      )}
      {values.length > 0 && (
        <div className="tree-lats-metric-grid">
          {values.map((item) => (
            <span key={item.key}><small>{item.label}</small><b>{item.value}</b></span>
          ))}
        </div>
      )}
      <Button
        type="link"
        size="small"
        icon={<InfoCircleOutlined />}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => onInspect(node)}
        aria-label={`查看 LATS 节点评分：${node.title}`}
      >
        查看评分
      </Button>
    </div>
  );
}

function LatsNodeDetail({ node, search }) {
  const metrics = node?.search_metrics;
  const selected = Boolean(node?.lats_selected);
  const scoreName = scoreLabel(search);
  if (!node || !metrics) return null;
  const selection = selected ? search?.latestSelection : null;
  return (
    <Space direction="vertical" size={14} style={{ width: "100%" }}>
      <Space wrap>
        <Tag color="blue">{KIND_LABELS[node.kind] || node.kind || "探索节点"}</Tag>
        {selected && <Tag color="processing">本次被选择</Tag>}
        {node.lats_best_path && <Tag color="gold">当前最佳路径</Tag>}
        {metrics.pruned && <Tag color="red">已剪枝</Tag>}
      </Space>
      <Text strong>{node.title}</Text>
      <Descriptions size="small" bordered column={{ xs: 1, sm: 2 }}>
        <Descriptions.Item label="节点 ID" span={2}><Text code copyable>{node.id}</Text></Descriptions.Item>
        <Descriptions.Item label="访问次数">{metrics.visits ?? "未记录"}</Descriptions.Item>
        <Descriptions.Item label="搜索深度">{metrics.depth ?? "未记录"}</Descriptions.Item>
        <Descriptions.Item label="累计价值">{formatLatsScore(metrics.valueSum)}</Descriptions.Item>
        <Descriptions.Item label="平均价值 Q">{formatLatsScore(metrics.meanValue)}</Descriptions.Item>
        <Descriptions.Item label="先验概率 Prior">{formatLatsScore(metrics.prior)}</Descriptions.Item>
        <Descriptions.Item label={`${scoreName} 分数`}>{formatLatsScore(metrics.uctScore)}</Descriptions.Item>
        <Descriptions.Item label="本轮奖励">{formatLatsScore(metrics.reward)}</Descriptions.Item>
        <Descriptions.Item label="搜索阶段">{search ? latsPhaseLabel(search.phase) : "未记录"}</Descriptions.Item>
      </Descriptions>
      {selection?.reason && (
        <div className="tree-lats-detail-note"><Text strong>本次选择理由</Text><Text>{chineseDiagnosticText(selection.reason)}</Text></div>
      )}
      {metrics.lastObservationSummary && (
        <div className="tree-lats-detail-note"><Text strong>最近观察</Text><Text>{chineseDiagnosticText(metrics.lastObservationSummary)}</Text></div>
      )}
      {metrics.lastReflection && (
        <div className="tree-lats-detail-note"><Text strong>最近反思</Text><Text>{chineseDiagnosticText(metrics.lastReflection)}</Text></div>
      )}
      {node.search_metric_history?.length > 1 && (
        <div className="tree-lats-detail-note" aria-label="跨轮评分更新历史">
          <Text strong>跨轮评分更新</Text>
          <Text type="secondary">同一因果假设的节点已合并；下面保留每次服务端记录的搜索值。</Text>
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            {node.search_metric_history.map((entry, index) => (
              <Text key={`${entry.nodeId || "metric"}:${entry.roundIndex || 0}:${index}`}>
                第 {entry.roundIndex || "?"} 轮 · 访问 {entry.visits ?? "未记录"} · Q {formatLatsScore(entry.meanValue)} · 奖励 {formatLatsScore(entry.reward)}
              </Text>
            ))}
          </Space>
        </div>
      )}
      <Alert
        type="info"
        showIcon
        message="评分字段怎么读"
        description="访问次数表示该分支被搜索的次数；平均回报 Q 表示历史评估均值；先验概率来自规划阶段；UCT/PUCT 用于在已知高价值分支和较少探索的分支之间取舍。"
      />
      <Text type="secondary">这里只显示服务端搜索事件返回的值；未记录字段不会补成 0。</Text>
    </Space>
  );
}

function LatsSearchStrip({ search, nodeById }) {
  if (!search) return null;
  const budget = search.budget;
  const termination = search.termination;
  const bestPath = search.bestPathNodeIds.map((id) => ({ id, node: nodeById.get(id) }));
  const liveProgressive = search.executionMode === "BUDGETED_LATS"
    || search.environmentSemantics === "LIVE_PROGRESSIVE"
    || search.rolloutSemantics === "REAL_TOOL_SINGLE_STEP_NO_ROLLBACK";
  const frozenReplay = search.executionMode === "FULL_LATS"
    && search.environmentSemantics === "FROZEN_REPLAY";
  return (
    <section className="lats-search-strip" aria-label="LATS 搜索状态">
      <div className="lats-search-strip-head">
        <Space wrap size={[6, 6]}>
          <Tag color="geekblue">{search.algorithm || "LATS"}</Tag>
          {search.algorithmVersion && <Tag>{search.algorithmVersion}</Tag>}
          {search.executionMode && (
            <Tooltip title={liveProgressive ? "真实工具会推进现场时间；兄弟分支的观察可能来自不同墙钟状态，不能冒充同一起点的可逆 rollout。" : "执行语义由服务端本次搜索记录确定。"}>
              <Tag color={liveProgressive ? "orange" : "cyan"}>{latsExecutionModeLabel(search.executionMode)}</Tag>
            </Tooltip>
          )}
          <Tag color={search.phase === "TERMINATED" ? "green" : "processing"}>{latsPhaseLabel(search.phase)}</Tag>
          {search.iteration != null && <Tag>第 {search.iteration} 次迭代</Tag>}
          {search.candidateCount != null && <Tag>候选 {search.candidateCount} 个</Tag>}
        </Space>
        {termination && (
          <Text type={termination.stopped ? "secondary" : undefined}>
            停止条件：{latsTerminationLabel(termination.reason)}
          </Text>
        )}
      </div>
      {search.latestSelection?.reason && (
        <Text className="lats-selection-reason">选择理由：{chineseDiagnosticText(search.latestSelection.reason)}</Text>
      )}
      {(search.environmentSemantics || search.rolloutSemantics) && (
        <Text type="secondary" className="lats-environment-semantics">
          {search.environmentSemantics && latsEnvironmentLabel(search.environmentSemantics)}
          {search.environmentSemantics && search.rolloutSemantics ? " · " : ""}
          {search.rolloutSemantics && latsRolloutLabel(search.rolloutSemantics)}
        </Text>
      )}
      {liveProgressive && (
        <Text type="warning" className="lats-live-warning">
          真实时间持续推进：兄弟分支不可回滚到完全相同的环境状态。
        </Text>
      )}
      {frozenReplay && (
        <div className="lats-frozen-replay-stats" aria-label="冻结回放运行统计">
          <div>
            <Text strong>冻结快照（snapshot）</Text>
            <Space wrap size={[6, 6]}>
              <Tag color="cyan">{search.snapshotId || "标识待同步"}</Tag>
              {search.snapshotDigest && (
                <Tooltip title={search.snapshotDigest}>
                  <Tag>摘要 {search.snapshotDigest.slice(0, 12)}…</Tag>
                </Tooltip>
              )}
              {search.resetCount != null && <Tag>重置 {search.resetCount} 次</Tag>}
            </Space>
          </div>
          <div>
            <Text strong>回放模拟</Text>
            <Space wrap size={[6, 6]}>
              {budget?.usedSimulations != null && budget?.maxSimulations != null
                ? <Tag color="geekblue">已用 {budget.usedSimulations}/{budget.maxSimulations}</Tag>
                : budget?.usedSimulations != null ? <Tag color="geekblue">已用 {budget.usedSimulations}</Tag> : <Tag>等待首轮模拟</Tag>}
              {budget?.remainingSimulations != null && <Tag>剩余 {budget.remainingSimulations}</Tag>}
            </Space>
          </div>
          <div className="is-live-tool-count">
            <Text strong>实时工具调用</Text>
            <Space wrap size={[6, 6]}>
              <Tag>{budget?.usedToolCalls ?? 0}/{budget?.maxToolCalls ?? 0}</Tag>
              <Text type="secondary">冻结回放不调用真实采集器</Text>
            </Space>
          </div>
        </div>
      )}
      {budget && (
        <Space wrap size={[6, 6]} className="lats-search-budget">
          {budget.usedIterations != null && budget.maxIterations != null && (
            <Tag>搜索预算 {budget.usedIterations}/{budget.maxIterations}</Tag>
          )}
          {budget.remainingIterations != null && <Tag color="blue">剩余迭代 {budget.remainingIterations}</Tag>}
          {!frozenReplay && budget.usedToolCalls != null && budget.maxToolCalls != null && (
            <Tag>工具预算 {budget.usedToolCalls}/{budget.maxToolCalls}</Tag>
          )}
          {!frozenReplay && budget.remainingToolCalls != null && <Tag color="blue">剩余工具 {budget.remainingToolCalls}</Tag>}
        </Space>
      )}
      <div className="lats-best-path" aria-label="LATS 当前最佳路径">
        <Text strong>当前最佳路径</Text>
        {bestPath.length ? bestPath.map(({ id, node }, index) => (
          <span key={id}>
            {index > 0 && <i aria-hidden="true">→</i>}
            <Tag color="gold">{node?.title || id}</Tag>
          </span>
        )) : <Text type="secondary">服务端尚未返回最佳路径</Text>}
      </div>
    </section>
  );
}

function skillCategoryLabel(category = "") {
  return CATEGORY_LABELS[category] || chineseDiagnosticText(category, category || "未分类");
}

function SkillTraceLane({ trace, onInspect }) {
  if (!trace) return null;
  const loadLabel = trace.fullSkillLoaded === true
    ? "完整 SKILL.md 已加载"
    : trace.fullSkillLoaded === false ? "仅加载目录元数据" : "加载状态待同步";
  const loadColor = trace.fullSkillLoaded === true ? "green" : trace.fullSkillLoaded === false ? "orange" : "default";
  return (
    <section className={`skill-trace-lane is-${trace.stateMeta.tone}`} aria-label="动态探索树中的 Skill 复用轨迹">
      <header className="skill-trace-head">
        <div className="skill-trace-title">
          <span className="skill-trace-title-icon" aria-hidden="true"><BookOutlined /></span>
          <div>
            <Text strong>Skill 调查路线</Text>
            <Text type="secondary">作为搜索先验参与规划，不属于诊断证据</Text>
          </div>
        </div>
        <Space wrap size={[6, 6]}>
          <Tag color="purple">非证据先验</Tag>
          <Tag color={trace.stateMeta.color}>{trace.stateMeta.label}</Tag>
          <Button type="link" size="small" icon={<InfoCircleOutlined />} onClick={onInspect}>
            查看 Skill 详情
          </Button>
        </Space>
      </header>

      <div className="skill-trace-summary">
        <div className="skill-trace-name">
          <Text strong>{trace.skillName}</Text>
          {trace.version && <Tag>版本 {trace.version}</Tag>}
          {trace.category && <Tag>{skillCategoryLabel(trace.category)}</Tag>}
        </div>
        <Space wrap size={[6, 6]}>
          {trace.retrieval.method && <Tag icon={<SearchOutlined />}>{skillRetrievalMethodLabel(trace.retrieval.method)}</Tag>}
          {trace.retrieval.score != null && <Tag color="blue">匹配分 {skillScoreLabel(trace.retrieval.score)}</Tag>}
          <Tag color={loadColor}>{loadLabel}</Tag>
          {trace.retrieval.categoryCorrected && (
            <Tag color="gold">
              路由已纠偏{trace.retrieval.baselineCategory || trace.retrieval.selectedCategory
                ? `：${skillCategoryLabel(trace.retrieval.baselineCategory)} → ${skillCategoryLabel(trace.retrieval.selectedCategory)}`
                : ""}
            </Tag>
          )}
        </Space>
      </div>

      {trace.timeline.length > 0 && (
        <div className="skill-trace-flow" role="list" aria-label="Skill 召回与沿用过程">
          {trace.timeline.map((event, index) => (
            <div className={`skill-trace-event is-${event.tone}`} role="listitem" key={event.id}>
              {index > 0 && <span className="skill-trace-link" aria-hidden="true" />}
              <button type="button" onClick={onInspect} aria-label={`查看 Skill 阶段：${event.label}`}>
                <i aria-hidden="true">{index + 1}</i>
                <span>
                  <b>{event.label}</b>
                  <small>{event.tool ? diagnosticToolLabel(event.tool) : event.roundIndex > 0 ? `第 ${event.roundIndex} 轮` : ""}</small>
                </span>
              </button>
            </div>
          ))}
        </div>
      )}

      {trace.routeSteps.length > 0 && (
        <div className="skill-route-steps" aria-label="Skill 推荐工具路线">
          <Text strong>推荐工具路线</Text>
          <div>
            {trace.routeSteps.map((step, index) => {
              const state = SKILL_STEP_STATUS[step.status] || { label: diagnosticStatusLabel(step.status), tone: "pending" };
              const current = trace.currentStep === step.order
                || trace.currentStepIndex === index
                || (trace.currentTool && trace.currentTool === step.tool);
              return (
                <button
                  type="button"
                  className={`skill-route-step is-${state.tone} ${current ? "is-current" : ""}`}
                  key={step.id}
                  onClick={onInspect}
                  aria-label={`${diagnosticToolLabel(step.tool)}，${current ? "当前步骤" : state.label}`}
                >
                  {index > 0 && <span aria-hidden="true">→</span>}
                  <b>{diagnosticToolLabel(step.tool)}</b>
                  <small>{current ? "当前步骤" : state.label}{step.roundIndex > 0 ? ` · 第 ${step.roundIndex} 轮` : ""}</small>
                </button>
              );
            })}
          </div>
        </div>
      )}
      <span className="skill-trace-to-tree" aria-hidden="true" />
    </section>
  );
}

function SkillTraceDetail({ trace }) {
  if (!trace) return null;
  const loadDescription = trace.fullSkillLoaded === true
    ? "已在命中后按需读取完整 SKILL.md，并把正文放入本轮 Agent 上下文。"
    : trace.fullSkillLoaded === false
      ? "本次只读取了目录元数据，完整 SKILL.md 尚未加载。"
      : "服务端尚未上报完整 SKILL.md 的加载状态。";
  return (
    <Space direction="vertical" size={14} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="Skill 是调查先验，不是根因证据"
        description="它负责推荐排查顺序和工具路线；最终结论仍必须由真实工具调用和 Evidence 支撑。虚线用于把 Skill 路线与 LATS 证据树区分开。"
      />
      <Descriptions size="small" bordered column={{ xs: 1, sm: 2 }}>
        <Descriptions.Item label="Skill 名称">{trace.skillName}</Descriptions.Item>
        <Descriptions.Item label="当前状态"><Tag color={trace.stateMeta.color}>{trace.stateMeta.label}</Tag></Descriptions.Item>
        <Descriptions.Item label="Skill ID" span={2}><Text code copyable>{trace.skillId || "未上报"}</Text></Descriptions.Item>
        <Descriptions.Item label="诊断方向">{skillCategoryLabel(trace.category)}</Descriptions.Item>
        <Descriptions.Item label="版本">{trace.version || "未上报"}</Descriptions.Item>
        <Descriptions.Item label="检索方式">{skillRetrievalMethodLabel(trace.retrieval.method)}</Descriptions.Item>
        <Descriptions.Item label="匹配分">{skillScoreLabel(trace.retrieval.score)}</Descriptions.Item>
        <Descriptions.Item label="候选分差">{skillScoreLabel(trace.retrieval.margin)}</Descriptions.Item>
        <Descriptions.Item label="分类纠偏">
          {trace.retrieval.categoryCorrected
            ? `${skillCategoryLabel(trace.retrieval.baselineCategory)} → ${skillCategoryLabel(trace.retrieval.selectedCategory)}`
            : "未发生"}
        </Descriptions.Item>
        <Descriptions.Item label="正文加载" span={2}>{loadDescription}</Descriptions.Item>
        {trace.sourcePath && (
          <Descriptions.Item label="来源文件" span={2}><Text code copyable>{trace.sourcePath}</Text></Descriptions.Item>
        )}
        {trace.contentSha256 && (
          <Descriptions.Item label="内容摘要" span={2}><Text code copyable>{trace.contentSha256}</Text></Descriptions.Item>
        )}
      </Descriptions>

      {trace.loadedSections.length > 0 && (
        <div className="skill-detail-block">
          <Text strong>已加载章节</Text>
          <Space wrap size={[6, 6]}>{trace.loadedSections.map((section) => <Tag key={section}>{chineseDiagnosticText(section)}</Tag>)}</Space>
        </div>
      )}
      {trace.retrieval.reason && (
        <div className="skill-detail-block">
          <Text strong>为什么命中这个 Skill</Text>
          <Text>{trace.retrieval.reason}</Text>
        </div>
      )}
      {trace.routeSteps.length > 0 && (
        <div className="skill-detail-block">
          <Text strong>工具路线与执行位置</Text>
          <ol className="skill-detail-route">
            {trace.routeSteps.map((step, index) => {
              const state = SKILL_STEP_STATUS[step.status] || { label: diagnosticStatusLabel(step.status), tone: "pending" };
              const current = trace.currentStep === step.order
                || trace.currentStepIndex === index
                || (trace.currentTool && trace.currentTool === step.tool);
              return (
                <li className={`is-${state.tone} ${current ? "is-current" : ""}`} key={step.id}>
                  <span>{step.order || index + 1}</span>
                  <div><Text strong>{diagnosticToolLabel(step.tool)}</Text><Text type="secondary">{current ? "当前步骤" : state.label}{step.roundIndex > 0 ? ` · 第 ${step.roundIndex} 轮` : ""}</Text></div>
                </li>
              );
            })}
          </ol>
        </div>
      )}
      {trace.timeline.length > 0 && (
        <div className="skill-detail-block">
          <Text strong>跨轮复用记录</Text>
          <ol className="skill-detail-timeline">
            {trace.timeline.map((event) => (
              <li key={event.id}>
                <Tag color={event.tone === "deviated" ? "orange" : event.tone === "completed" ? "green" : "purple"}>{event.label}</Tag>
                <Text>{event.roundIndex > 0 ? `第 ${event.roundIndex} 轮` : "当前会话"}</Text>
                {event.tool && <Text code>{event.tool}</Text>}
                {event.reason && <Text type="secondary">{event.reason}</Text>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </Space>
  );
}

function normalizeEdge(edge, index) {
  const from = edge?.from ?? edge?.source ?? edge?.parent_id;
  const to = edge?.to ?? edge?.target ?? edge?.child_id;
  if (from == null || to == null) return null;
  return {
    ...edge,
    id: edge.id || `edge:${from}:${to}:${index}`,
    from: String(from),
    to: String(to),
    kind: String(edge.kind || edge.type || "").toUpperCase(),
  };
}

function relationshipLabel(parent, child, edge) {
  if (edge?.label) return edge.label;
  if (edge?.kind && EDGE_KIND_LABELS[edge.kind]) return EDGE_KIND_LABELS[edge.kind];
  if (child.kind === "intervention") return "人工转向";
  if (child.kind === "hypothesis") return parent?.kind === "intervention" ? "重新规划" : "假设分支";
  if (child.kind === "tool") return "发起取证";
  if (child.kind === "evidence") {
    if (child.status === "ACCEPT_SUPPORT") return "支持证据";
    if (COUNTER_EVIDENCE.has(child.status)) return "反证";
    return "证据结果";
  }
  if (child.kind === "report") return "形成结论";
  if (child.kind === "verification") return "修复复测";
  return "父子关系";
}

function buildTreeGraph(nodes, edges = []) {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const childrenByParent = new Map();
  const edgeByChild = new Map();
  const attached = new Set();
  const normalizedEdges = edges
    .map(normalizeEdge)
    .filter((edge) => edge && edge.from !== edge.to && nodeById.has(edge.from) && nodeById.has(edge.to));

  const attach = (edge) => {
    if (attached.has(edge.to)) return;
    childrenByParent.set(edge.from, [...(childrenByParent.get(edge.from) || []), nodeById.get(edge.to)]);
    edgeByChild.set(edge.to, edge);
    attached.add(edge.to);
  };

  normalizedEdges.forEach(attach);
  nodes.forEach((node, index) => {
    if (attached.has(node.id) || !node.parent_id || !nodeById.has(node.parent_id) || node.parent_id === node.id) return;
    attach({ id: `parent:${node.parent_id}:${node.id}:${index}`, from: node.parent_id, to: node.id, kind: "" });
  });

  return {
    childrenByParent,
    edgeByChild,
    roots: nodes.filter((node) => !attached.has(node.id)),
    edgeCount: attached.size,
  };
}

function hypothesisIdOf(node) {
  if (node?.hypothesis_id) return node.hypothesis_id;
  if (node?.kind === "hypothesis" && String(node.id || "").startsWith("hypothesis:")) {
    return String(node.id).slice("hypothesis:".length);
  }
  return "";
}

function SkillRouteReferenceTags({ node }) {
  const references = Array.isArray(node?.skill_route_refs) ? node.skill_route_refs : [];
  if (!references.length) return null;
  return references.map((reference, index) => {
    const routeIndex = Number(firstPresent(reference.route_index, reference.step_index, 0));
    const selected = Boolean(reference.selected_by_skill);
    return (
      <Tooltip
        key={`${reference.activation_id || reference.skill_id || "skill"}:${routeIndex}:${index}`}
        title="该工具名出现在 Skill 推荐路线中；这是路线关联，不会被当作支持根因的证据。"
      >
        <Tag className="tree-skill-route-tag" color="purple">
          Skill 路线{routeIndex > 0 ? `第 ${routeIndex} 步` : ""}{selected ? " · 已采用" : ""}
        </Tag>
      </Tooltip>
    );
  });
}

function ExplorationNode({
  node,
  parent = null,
  childrenByParent,
  edgeByChild,
  activeNodeIds,
  scoreName,
  onInspectSearch,
  onIntervene,
  intervening,
  depth = 1,
  ancestors = new Set(),
}) {
  const meta = NODE_META[node.state] || NODE_META.unvisited;
  const nextAncestors = new Set(ancestors).add(node.id);
  const children = (childrenByParent.get(node.id) || []).filter((child) => !nextAncestors.has(child.id));
  const active = activeNodeIds.has(node.id);
  const edge = edgeByChild.get(node.id);
  const hasSkillRoute = Array.isArray(node.skill_route_refs) && node.skill_route_refs.length > 0;
  const kindLabel = KIND_LABELS[node.kind] || node.kind || "探索节点";
  const relationship = parent ? relationshipLabel(parent, node, edge) : "";
  return (
    <li
      role="treeitem"
      aria-level={depth}
      aria-expanded={children.length ? true : undefined}
      aria-label={`${kindLabel}：${node.title}，${active ? "刚刚更新" : meta.label}${node.lats_selected ? "，当前选择" : ""}${node.lats_best_path ? "，当前最佳路径" : ""}${nodeMetricAria(node.search_metrics, scoreName)}`}
      className={`tree-node-branch tree-branch-${node.state || "unvisited"} ${parent ? "has-parent" : "is-root"} ${node.lats_selected ? "is-lats-selected" : ""} ${node.lats_best_path ? "is-lats-best-path" : ""} ${hasSkillRoute ? "is-skill-routed" : ""}`}
      data-node-id={node.id}
      data-parent-id={parent?.id || ""}
    >
      {parent && <span className="tree-edge-label">{relationship}</span>}
      <div className={`tree-card tree-card-${node.state || "unvisited"} ${active ? "is-live-node" : ""} ${node.lats_selected ? "is-lats-selected" : ""} ${node.lats_best_path ? "is-lats-best-path" : ""} ${hasSkillRoute ? "is-skill-route-node" : ""}`}>
        <div className="tree-card-context">
          <span>{kindLabel}</span>
          {node.round_index > 0 && <span>第 {node.round_index} 轮</span>}
          {node.round_indices?.length > 1 && <span>跨轮 {node.round_indices.join("、")}</span>}
        </div>
        <div className="tree-card-heading">
          <span className="tree-icon">{meta.icon}</span>
          <Text strong>{node.title}</Text>
        </div>
        <Space wrap size={[4, 4]}>
          <Tag color={meta.color}>{active ? "刚刚更新" : meta.label}</Tag>
          {node.domain && <Tag>{node.domain}</Tag>}
          <SkillRouteReferenceTags node={node} />
        </Space>
        {node.tool && <div className="tree-card-tool"><Text code>{node.tool}</Text></div>}
        {node.evidence && (
          <div className="tree-card-evidence">
            <span>{node.kind === "evidence" ? "证据内容" : "判断依据"}</span>
            <Text type="secondary">{node.evidence}</Text>
          </div>
        )}
        <LatsNodeMetrics node={node} scoreName={scoreName} onInspect={onInspectSearch} />
        {node.kind === "hypothesis" && hypothesisIdOf(node) && onIntervene && (
          <div className="tree-node-actions" onPointerDown={(event) => event.stopPropagation()}>
            <Button
              size="small"
              disabled={intervening}
              aria-label={`优先调查：${node.title}`}
              onClick={() => onIntervene({
                action: "CHANGE_DIRECTION",
                hypothesis_id: hypothesisIdOf(node),
                message: `优先沿「${node.title}」方向继续调查，并说明新增证据需求`,
              })}
            >优先调查</Button>
            <Button
              size="small"
              danger
              disabled={intervening}
              aria-label={`寻找反证：${node.title}`}
              onClick={() => onIntervene({
                action: "CHALLENGE_HYPOTHESIS",
                hypothesis_id: hypothesisIdOf(node),
                message: `请质疑「${node.title}」，优先寻找能够推翻它的反证`,
              })}
            >寻找反证</Button>
          </div>
        )}
      </div>
      {children.length > 0 && (
        <ul role="group">{children.map((child) => (
          <ExplorationNode
            key={child.id}
            node={child}
            parent={node}
            childrenByParent={childrenByParent}
            edgeByChild={edgeByChild}
            activeNodeIds={activeNodeIds}
            scoreName={scoreName}
            onInspectSearch={onInspectSearch}
            onIntervene={onIntervene}
            intervening={intervening}
            depth={depth + 1}
            ancestors={nextAncestors}
          />
        ))}</ul>
      )}
    </li>
  );
}

function ReadableRouteCard({ node, active, scoreName, onInspectSearch, onIntervene, intervening }) {
  const meta = NODE_META[node.state] || NODE_META.unvisited;
  return (
    <article className={`diagnosis-route-card is-${node.state || "unvisited"} ${active ? "is-active" : ""}`}>
      <div className="diagnosis-route-card-head">
        <Space wrap size={[4, 4]}>
          <Tag color={meta.color}>{KIND_LABELS[node.kind] || node.kind || "节点"}</Tag>
          {node.domain && <Tag>{node.domain}</Tag>}
          {active && <Tag color="processing">刚刚更新</Tag>}
          <SkillRouteReferenceTags node={node} />
        </Space>
        <span className="diagnosis-route-state">{meta.icon} {meta.label}</span>
      </div>
      <Text strong className="diagnosis-route-title">{node.title}</Text>
      {node.round_indices?.length > 1 && <Tag color="cyan">跨轮合并：第 {node.round_indices.join("、")} 轮</Tag>}
      {node.tool && <Text code>{node.tool}</Text>}
      {node.evidence && <Text type="secondary" className="diagnosis-route-evidence">{node.evidence}</Text>}
      <LatsNodeMetrics node={node} scoreName={scoreName} onInspect={onInspectSearch} />
      {node.kind === "hypothesis" && hypothesisIdOf(node) && onIntervene && (
        <div className="tree-node-actions">
          <Button
            size="small"
            disabled={intervening}
            aria-label={`优先调查：${node.title}`}
            onClick={() => onIntervene({
              action: "CHANGE_DIRECTION",
              hypothesis_id: hypothesisIdOf(node),
              message: `优先沿「${node.title}」方向继续调查，并说明新增证据需求`,
            })}
          >优先调查</Button>
          <Button
            size="small"
            danger
            disabled={intervening}
            aria-label={`寻找反证：${node.title}`}
            onClick={() => onIntervene({
              action: "CHALLENGE_HYPOTHESIS",
              hypothesis_id: hypothesisIdOf(node),
              message: `请质疑「${node.title}」，优先寻找能够推翻它的反证`,
            })}
          >寻找反证</Button>
        </div>
      )}
    </article>
  );
}

/**
 * 将同一批持久化节点投影为按轮次阅读的辅助视图。默认视图仍是父子拓扑，
 * 这里不重排或补造诊断事实，只按节点自身的 round_index 分组。
 */
function ReadableRouteBoard({ nodes, rounds = [], activeNodeIds, scoreName, onInspectSearch, onIntervene, intervening }) {
  const entryNodes = nodes.filter((node) => Number(node.round_index || 0) === 0);
  const roundIndexes = [...new Set([
    ...rounds.map((round) => Number(round.round_index || 0)),
    ...nodes.map((node) => Number(node.round_index || 0)),
  ].filter((value) => value > 0))].sort((a, b) => a - b);
  const roundMeta = new Map(rounds.map((round) => [Number(round.round_index || 0), round]));

  return (
    <div className="diagnosis-route-board" aria-label="按轮次阅读诊断路径">
      {entryNodes.map((node) => (
        <ReadableRouteCard
          key={node.id}
          node={node}
          active={activeNodeIds.has(node.id)}
          scoreName={scoreName}
          onInspectSearch={onInspectSearch}
          onIntervene={onIntervene}
          intervening={intervening}
        />
      ))}
      {roundIndexes.map((roundIndex) => {
        const meta = roundMeta.get(roundIndex) || {};
        const items = nodes
          .filter((node) => Number(node.round_index || 0) === roundIndex)
          .sort((left, right) => String(left.changed_at || "").localeCompare(String(right.changed_at || "")));
        if (!items.length) return null;
        return (
          <section className="diagnosis-route-round" key={roundIndex}>
            <header>
              <div>
                <span>轮次 {String(roundIndex).padStart(2, "0")}</span>
                <b>第 {roundIndex} 轮调查</b>
                {meta.title && <Text type="secondary" ellipsis={{ tooltip: meta.title }}>{meta.title}</Text>}
              </div>
              <Space wrap size={[4, 4]}>
                <Tag color={meta.status === "CONFIRMED" ? "green" : meta.status === "REFUTED" ? "red" : "blue"}>
                  {meta.status === "CONFIRMED" ? "已确认" : meta.status === "REFUTED" ? "已剪枝" : "调查中"}
                </Tag>
                <Tag>{items.length} 个节点</Tag>
              </Space>
            </header>
            <div className="diagnosis-route-grid">
              {items.map((node) => (
                <ReadableRouteCard
                  key={node.id}
                  node={node}
                  active={activeNodeIds.has(node.id)}
                  scoreName={scoreName}
                  onInspectSearch={onInspectSearch}
                  onIntervene={onIntervene}
                  intervening={intervening}
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

export function FitExplorationTree({ children }) {
  const viewportRef = useRef(null);
  const contentRef = useRef(null);
  const dragRef = useRef(null);
  const panRef = useRef({ x: 0, y: 0 });
  const hintId = useId();
  const [layout, setLayout] = useState({ scale: 0.72, height: 520 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);

  const measure = useCallback(() => {
    const viewport = viewportRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;
    const naturalWidth = content.scrollWidth;
    const naturalHeight = content.scrollHeight;
    if (!naturalWidth || !naturalHeight) return;
    const availableWidth = Math.max(320, viewport.clientWidth - 16);
    // Keep text readable on large trees. Very wide branches remain pannable instead
    // of being compressed into an illegible thumbnail.
    const scale = Math.min(1, Math.max(0.42, availableWidth / naturalWidth));
    setLayout({ scale, height: Math.ceil(naturalHeight * scale) + 8 });
  }, []);

  useLayoutEffect(() => {
    measure();
    const frame = window.requestAnimationFrame(measure);
    if (typeof ResizeObserver === "undefined") return () => window.cancelAnimationFrame(frame);
    const observer = new ResizeObserver(measure);
    if (viewportRef.current) observer.observe(viewportRef.current);
    if (contentRef.current) observer.observe(contentRef.current);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [measure]);

  const changeZoom = useCallback((factor) => {
    setZoom((current) => Math.min(2.8, Math.max(0.65, current * factor)));
  }, []);

  const resetView = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  const handleKeyDown = useCallback((event) => {
    const step = event.shiftKey ? 72 : 32;
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "+", "=", "-", "_"].includes(event.key)) {
      event.preventDefault();
    }
    if (event.key === "ArrowUp") setPan((current) => ({ ...current, y: current.y + step }));
    if (event.key === "ArrowDown") setPan((current) => ({ ...current, y: current.y - step }));
    if (event.key === "ArrowLeft") setPan((current) => ({ ...current, x: current.x + step }));
    if (event.key === "ArrowRight") setPan((current) => ({ ...current, x: current.x - step }));
    if (event.key === "+" || event.key === "=") changeZoom(1.12);
    if (event.key === "-" || event.key === "_") changeZoom(0.89);
    if (event.key === "Home") resetView();
    if (event.key === "Escape") setFullscreen(false);
  }, [changeZoom, resetView]);

  useEffect(() => { panRef.current = pan; }, [pan]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    const handleWheel = (event) => {
      event.preventDefault();
      changeZoom(event.deltaY < 0 ? 1.12 : 0.89);
    };
    const handlePointerDown = (event) => {
      const target = event.target;
      if (event.button !== 0 || (target instanceof Element && target.closest(
        ".diagnosis-tree-controls, .tree-node-actions, .tree-lats-metrics, button, a, input, textarea, select, [role='button']",
      ))) return;
      dragRef.current = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        pan: panRef.current,
      };
      viewport.setPointerCapture?.(event.pointerId);
      setDragging(true);
    };
    const handlePointerMove = (event) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      setPan({
        x: drag.pan.x + event.clientX - drag.x,
        y: drag.pan.y + event.clientY - drag.y,
      });
    };
    const stopDragging = (event) => {
      if (!dragRef.current || dragRef.current.pointerId !== event.pointerId) return;
      viewport.releasePointerCapture?.(dragRef.current.pointerId);
      dragRef.current = null;
      setDragging(false);
    };
    viewport.addEventListener("wheel", handleWheel, { passive: false });
    viewport.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopDragging);
    window.addEventListener("pointercancel", stopDragging);
    return () => {
      viewport.removeEventListener("wheel", handleWheel);
      viewport.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopDragging);
      window.removeEventListener("pointercancel", stopDragging);
    };
  }, [changeZoom]);

  const actualScale = layout.scale * zoom;

  return (
    <div
      ref={viewportRef}
      className={`diagnosis-tree-fit-viewport ${dragging ? "is-dragging" : ""} ${fullscreen ? "is-fullscreen" : ""}`}
      style={{ height: fullscreen ? "calc(100vh - 32px)" : layout.height }}
      onDoubleClick={resetView}
      onKeyDown={handleKeyDown}
      role="region"
      tabIndex={0}
      aria-label="可缩放拖动的诊断探索树"
      aria-describedby={hintId}
    >
      <div className="diagnosis-tree-controls" onPointerDown={(event) => event.stopPropagation()}>
        <Tooltip title="缩小">
          <Button size="small" icon={<ZoomOutOutlined />} aria-label="缩小探索树" onClick={() => changeZoom(0.86)} />
        </Tooltip>
        <span className="diagnosis-tree-scale">{Math.round(actualScale * 100)}%</span>
        <Tooltip title="放大">
          <Button size="small" icon={<ZoomInOutlined />} aria-label="放大探索树" onClick={() => changeZoom(1.16)} />
        </Tooltip>
        <Tooltip title="恢复一屏适配">
          <Button size="small" icon={<AimOutlined />} aria-label="复位探索树" onClick={resetView} />
        </Tooltip>
        <Tooltip title={fullscreen ? "退出全屏" : "全屏查看"}>
          <Button
            size="small"
            icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
            aria-label={fullscreen ? "退出探索树全屏" : "打开探索树全屏弹窗"}
            aria-pressed={fullscreen}
            onClick={() => setFullscreen((current) => !current)}
          />
        </Tooltip>
      </div>
      <div className="diagnosis-tree-gesture-hint" id={hintId}>滚轮缩放 · 按住拖动 · 方向键平移 · 双击复位 · Esc 退出全屏</div>
      <div
        ref={contentRef}
        className="diagnosis-tree-fit-content"
        style={{ transform: `translateX(-50%) translate(${pan.x}px, ${pan.y}px) scale(${actualScale})` }}
      >
        {children}
      </div>
    </div>
  );
}

function StructuredExplorationTree({
  nodes,
  edges = [],
  switches,
  snapshot = null,
  skillTrace: rawSkillTrace = null,
  verifiedHypothesisId = "",
  onIntervene,
  intervening,
}) {
  const [viewMode, setViewMode] = useState("topology");
  const [inspectedSearchNode, setInspectedSearchNode] = useState(null);
  const [skillDetailOpen, setSkillDetailOpen] = useState(false);
  const search = useMemo(() => normalizeLatsSearch(snapshot?.search, snapshot), [snapshot]);
  const skillTrace = useMemo(
    () => normalizeSkillTrace(
      rawSkillTrace || snapshot?.skill_trace || snapshot?.skillTrace,
      snapshot?.skill_route_overlay || snapshot?.skillRouteOverlay,
    ),
    [rawSkillTrace, snapshot],
  );
  const frozenReplay = search?.executionMode === "FULL_LATS"
    && search?.environmentSemantics === "FROZEN_REPLAY";
  const normalizedNodes = useMemo(
    () => nodes.map((node) => normalizeTreeNode(node, verifiedHypothesisId)),
    [nodes, verifiedHypothesisId],
  );
  const semanticProjection = useMemo(
    () => mergeLegacyHypothesisNodes(normalizedNodes, edges),
    [edges, normalizedNodes],
  );
  const projectedSearch = useMemo(() => {
    if (!search) return null;
    const remap = (id) => semanticProjection.aliasById.get(id) || id;
    const bestPathNodeIds = (search.bestPathNodeIds || []).map(remap).filter((id, index, path) => id && path.indexOf(id) === index);
    return {
      ...search,
      selectedNodeId: remap(search.selectedNodeId),
      bestPathNodeIds,
      latestSelection: search.latestSelection ? {
        ...search.latestSelection,
        nodeId: remap(search.latestSelection.nodeId),
      } : null,
    };
  }, [search, semanticProjection.aliasById]);
  const bestPathNodeIds = useMemo(() => new Set(projectedSearch?.bestPathNodeIds || []), [projectedSearch?.bestPathNodeIds]);
  const decoratedNodes = useMemo(() => semanticProjection.nodes.map((node) => ({
    ...node,
    lats_selected: node.search_metrics?.selected === true || projectedSearch?.selectedNodeId === node.id,
    lats_best_path: node.search_metrics?.bestPath === true || bestPathNodeIds.has(node.id),
  })), [bestPathNodeIds, projectedSearch?.selectedNodeId, semanticProjection.nodes]);
  const graph = useMemo(() => buildTreeGraph(decoratedNodes, semanticProjection.edges), [decoratedNodes, semanticProjection.edges]);
  const nodeById = useMemo(() => new Map(decoratedNodes.map((node) => [node.id, node])), [decoratedNodes]);
  const prunedCount = decoratedNodes.filter((node) => node.state === "refuted").length;
  const activeNodeIds = new Set((snapshot?.active_node_ids || []).map(
    (id) => semanticProjection.aliasById.get(id) || id,
  ));
  const stats = snapshot?.stats || {};
  const searchScoreName = scoreLabel(search);
  return (
    <Card className="actual-exploration-tree" size="small"
      title={(
        <Space>
          <BranchesOutlined />
          <span>{frozenReplay ? "冻结回放探索树" : snapshot ? "实时探索树" : "实际探索树"}</span>
          {snapshot && <span className="live-tree-pulse" aria-label={frozenReplay ? "回放事件更新中" : "实时更新中"} />}
        </Space>
      )}
      extra={(
        <Space wrap>
          <Text type="secondary">{snapshot ? `版本 ${snapshot.revision || 0}` : "由真实父子关系生成"}</Text>
          <Segmented
            size="small"
            value={viewMode}
            onChange={setViewMode}
            options={[
              { label: "树状结构", value: "topology", icon: <ApartmentOutlined /> },
              { label: "轮次路径", value: "route", icon: <OrderedListOutlined /> },
            ]}
            aria-label="探索树显示方式"
          />
        </Space>
      )}
    >
      <Space wrap className="actual-tree-summary">
        <Tag color="blue">{stats.rounds || 0} 轮</Tag>
        <Tag color="blue">探索 {decoratedNodes.length} 个可见节点</Tag>
        {semanticProjection.mergedCount > 0 && <Tag color="cyan">语义合并 {semanticProjection.mergedCount} 个重复假设</Tag>}
        <Tag>父子关系 {graph.edgeCount} 条</Tag>
        <Tag color="red">剪枝 {stats.pruned ?? prunedCount} 条</Tag>
        <Tag color="purple">方向切换 {(switches || []).length} 次</Tag>
        {Number(stats.human_interventions || 0) > 0 && <Tag color="cyan">人工干预 {stats.human_interventions} 次</Tag>}
        {skillTrace && <Tag color="purple">Skill {skillTrace.stateMeta.label}</Tag>}
        {snapshot?.status && <Tag>{diagnosticStatusLabel(snapshot.status)}</Tag>}
      </Space>
      <LatsSearchStrip search={projectedSearch} nodeById={nodeById} />
      {snapshot?.rounds?.length > 0 && (
        <div className="live-tree-rounds" aria-label="诊断轮次">
          {snapshot.rounds.map((round) => (
            <div className={`live-tree-round is-${String(round.status || "").toLowerCase()}`} key={round.round_index}>
              <b>{round.round_index}</b>
              <span>第 {round.round_index} 轮</span>
              <small>{frozenReplay
                ? `${round.simulation_count ?? round.tool_call_count ?? 0} 模拟 · ${round.evidence_count ?? 0} 冻结观察`
                : `${round.tool_call_count ?? 0} 工具 · ${round.evidence_count ?? 0} 证据`}</small>
            </div>
          ))}
        </div>
      )}
      {(switches || []).map((item, index) => (
        <div className="actual-tree-switch" key={`${item.from || item.from_category}-${item.to || item.to_category}-${index}`}>
          <SwapOutlined />
          <Tag color={item.source === "USER_INTERVENTION" ? "cyan" : "purple"}>
            {item.source === "USER_INTERVENTION" ? "人工转向" : "取证转向"}
          </Tag>
          第 {index + 1} 次转向：{CATEGORY_LABELS[item.from || item.from_category] || item.from || item.from_category}
          {" → "}{CATEGORY_LABELS[item.to || item.to_category] || item.to || item.to_category}
          <Text type="secondary">，{chineseDiagnosticText(item.reason)}</Text>
        </div>
      ))}
      {viewMode === "topology" ? (
        <>
          <div className="diagnosis-tree-legend" aria-label="探索树图例">
            <span className="is-visited"><i />已调查分支</span>
            <span className="is-refuted"><i />反证剪枝</span>
            <span className="is-confirmed"><i />根因路径</span>
            <span className="is-unvisited"><i />停止后未继续</span>
            {skillTrace && <span className="is-skill-prior"><i />Skill 路线（非证据）</span>}
          </div>
          <SkillTraceLane trace={skillTrace} onInspect={() => setSkillDetailOpen(true)} />
          <FitExplorationTree>
            <div className="exploration-tree exploration-tree-dynamic diagnosis-record-tree">
              <ul className="dynamic-tree-root" role="tree" aria-label="真实父子探索树">
                {graph.roots.map((root) => (
                  <ExplorationNode
                    key={root.id}
                    node={root}
                    childrenByParent={graph.childrenByParent}
                    edgeByChild={graph.edgeByChild}
                    activeNodeIds={activeNodeIds}
                    scoreName={searchScoreName}
                    onInspectSearch={setInspectedSearchNode}
                    onIntervene={onIntervene}
                    intervening={intervening}
                  />
                ))}
              </ul>
            </div>
          </FitExplorationTree>
        </>
      ) : (
        <ReadableRouteBoard
          nodes={decoratedNodes}
          rounds={snapshot?.rounds || []}
          activeNodeIds={activeNodeIds}
          scoreName={searchScoreName}
          onInspectSearch={setInspectedSearchNode}
          onIntervene={onIntervene}
          intervening={intervening}
        />
      )}
      <Modal
        title="LATS 节点评分详情"
        open={Boolean(inspectedSearchNode)}
        onCancel={() => setInspectedSearchNode(null)}
        footer={<Button onClick={() => setInspectedSearchNode(null)}>关闭</Button>}
        width={720}
        destroyOnHidden
      >
        <LatsNodeDetail node={inspectedSearchNode} search={projectedSearch} />
      </Modal>
      <Modal
        title="Skill 复用详情"
        open={skillDetailOpen}
        onCancel={() => setSkillDetailOpen(false)}
        footer={<Button onClick={() => setSkillDetailOpen(false)}>关闭</Button>}
        width={780}
        destroyOnHidden
      >
        <SkillTraceDetail trace={skillTrace} />
      </Modal>
    </Card>
  );
}

export default function ActualExplorationTree({ tree, hypotheses, toolCalls, report, onIntervene, intervening = false }) {
  if (tree?.nodes?.length) {
    return (
      <StructuredExplorationTree
        nodes={tree.nodes}
        edges={tree.edges || []}
        switches={tree.switches || []}
        snapshot={tree}
        skillTrace={tree.skill_trace || tree.skillTrace}
        onIntervene={onIntervene}
        intervening={intervening}
      />
    );
  }
  if (report?.exploration_nodes?.length) {
    return (
      <StructuredExplorationTree
        nodes={report.exploration_nodes}
        edges={report.exploration_edges || []}
        switches={report.exploration_switches || []}
        skillTrace={report.skill_trace || report.skillTrace}
        onIntervene={onIntervene}
        intervening={intervening}
      />
    );
  }
  const fallback = buildFallbackTree(hypotheses, toolCalls, report);
  if (!fallback.nodes.length) return null;
  return (
    <StructuredExplorationTree
      nodes={fallback.nodes}
      switches={fallback.switches}
      skillTrace={report?.skill_trace || report?.skillTrace}
      verifiedHypothesisId={report?.hypothesis_id || ""}
      onIntervene={onIntervene}
      intervening={intervening}
    />
  );
}
