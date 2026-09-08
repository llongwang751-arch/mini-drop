import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Drawer,
  Input,
  Modal,
  Segmented,
  Space,
  Spin,
  Steps,
  Typography,
  message,
} from "antd";
import {
  BranchesOutlined,
  DisconnectOutlined,
  ExperimentOutlined,
  FullscreenOutlined,
  MenuUnfoldOutlined,
  ProfileOutlined,
  RobotOutlined,
  SendOutlined,
  SyncOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import ChatThread from "../components/ChatThread";
import AgentCockpit from "../components/AgentCockpit";
import ActualExplorationTree from "../components/ActualExplorationTree";
import DiagnosisCaseList from "../components/DiagnosisCaseList";
import EvalPanel from "../components/EvalPanel";
import MentorComplexShowcase from "../components/MentorComplexShowcase";
import TechnicalDetailDrawer from "../components/TechnicalDetailDrawer";
import usePolling from "../hooks/usePolling";
import useSSE from "../hooks/useSSE";
import { getFrozenReplayMeta } from "../utils/latsReplay";
import {
  advanceDropInsightOrchestrator,
  clarifyDropInsightDiagnosis,
  createDropInsightDiagnosis,
  createDiagnosticSkillCandidate,
  decideDropInsightToolCall,
  deleteDropInsightDiagnosis,
  evaluateDiagnosticSkill,
  getAgentRuntimeStatus,
  getDropInsightBudget,
  getDropInsightDiagnosis,
  getDropInsightExplorationTree,
  listDiagnosticSkillActivations,
  listDiagnosticSkills,
  listDropInsightDiagnoses,
  listDropInsightEvidence,
  listDropInsightFeedback,
  listDropInsightEvents,
  listDropInsightHypotheses,
  listDropInsightInterventions,
  listDropInsightReports,
  listDropInsightRetrievals,
  listDropInsightToolCalls,
  runDropInsightPlanner,
  submitDropInsightFeedback,
  submitDropInsightIntervention,
  updateDropInsightToolCall,
} from "../api/client";
import "./AIDiagnosis.css";

const { Paragraph, Text, Title } = Typography;

/**
 * AI 诊断工作台是“调查流程”的投影，不直接执行 shell 或采集器。
 * 创建诊断后，服务端依次完成目标发现、范围绑定、假设、受控工具调用、证据准入
 * 与报告生成；本页通过 SSE 增量刷新，并在 SSE 断开时用低频轮询兜底。
 */

const TERMINAL = new Set(["COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"]);
const LOCKED_TERMINAL = new Set(["FAILED", "CANCELLED"]);
const STATUS_LABELS = {
  CREATED: "等待开始",
  PLANNING: "构建假设",
  COLLECTING: "正在取证",
  ANALYZING: "证据裁决",
  WAITING_APPROVAL: "等待审批",
  COMPLETED: "诊断完成",
  PARTIAL: "证据不足",
  FAILED: "诊断失败",
  CANCELLED: "已取消",
  UNKNOWN: "状态同步中",
};
const EMPTY_RESOURCES = {
  hypotheses: [],
  toolCalls: [],
  evidence: [],
  reports: [],
  events: [],
  feedback: [],
  interventions: [],
  budget: null,
  skillActivations: [],
  explorationTree: null,
  runtimeStatus: null,
  retrievals: [],
};

const COMPOSER_ACTIONS = [
  { label: "补充 / 追问", value: "ADD_CONTEXT" },
  { label: "继续取证", value: "CONTINUE_INVESTIGATION" },
  { label: "调整方向", value: "CHANGE_DIRECTION" },
  { label: "寻找反证", value: "CHALLENGE_HYPOTHESIS" },
];

const COMPOSER_PLACEHOLDERS = {
  ADD_CONTEXT: "继续追问结论，或补充新的现象、上下文和约束",
  CONTINUE_INVESTIGATION: "说明希望继续验证的问题；Agent 会保留旧证据并开启下一轮",
  CHANGE_DIRECTION: "告诉 Agent 要改查哪个方向，以及为什么要调整当前计划",
  CHALLENGE_HYPOTHESIS: "指出你质疑的假设，Agent 会优先寻找能够推翻它的反证",
};

function canonicalStatus(status) {
  const value = String(status || "").toUpperCase();
  if (["CREATED", "PENDING", "UNDERSTANDING", "NEEDS_CLARIFICATION", "NEEDS_SCOPE_CONFIRMATION"].includes(value)) return "CREATED";
  if (["PLANNING", "HYPOTHESIZING", "PLAN_READY"].includes(value)) return "PLANNING";
  if (["COLLECTING", "COLLECTING_EVIDENCE", "RUNNING", "EXECUTING", "PROBING"].includes(value)) return "COLLECTING";
  if (["ANALYZING", "REPORTING", "VERIFYING"].includes(value)) return "ANALYZING";
  if (["WAITING_APPROVAL", "WAITING_FOR_APPROVAL", "APPROVAL_REQUIRED"].includes(value)) return "WAITING_APPROVAL";
  if (["COMPLETED", "DONE", "SUCCEEDED"].includes(value)) return "COMPLETED";
  if (["PARTIAL_COMPLETED", "PARTIAL", "INSUFFICIENT_EVIDENCE"].includes(value)) return "PARTIAL";
  if (["FAILED", "ERROR", "TIMED_OUT"].includes(value)) return "FAILED";
  if (["CANCELLED", "CANCELED"].includes(value)) return "CANCELLED";
  return "UNKNOWN";
}

function nativeId(item) {
  return item.diagnosis_id || item.case_id || item.id || "";
}

function selectionKey(source, id) {
  return `${source || "unknown"}:${id}`;
}

function normalizeCase(item) {
  const source = "drop_insight_v2";
  const id = nativeId(item);
  return {
    ...item,
    source,
    case_id: item.case_id || id,
    diagnosis_id: item.diagnosis_id || id,
    canonical_status: item.canonical_status || canonicalStatus(item.status),
    selection_key: selectionKey(source, id),
  };
}

function isVerifiedReport(report) {
  const verificationStatus = report?.verification?.status
    || report?.verification_status
    || report?.status;
  const evidenceRefs = report?.evidence_refs || report?.evidence_refs_json || [];
  return String(verificationStatus || "").toUpperCase() === "VERIFIED"
    && Array.isArray(evidenceRefs)
    && evidenceRefs.length > 0;
}

function syncCaseQuery(value) {
  const url = new URL(window.location.href);
  if (value) url.searchParams.set("case", value);
  else url.searchParams.delete("case");
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

export default function AIDiagnosis() {
  const [cases, setCases] = useState([]);
  const [selectedCase, setSelectedCase] = useState(null);
  const [workspaceView, setWorkspaceView] = useState("workspace");
  const [caseFilter, setCaseFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const [detail, setDetail] = useState(null);
  const [resources, setResources] = useState(EMPTY_RESOURCES);
  const [resourceErrors, setResourceErrors] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listLoaded, setListLoaded] = useState(false);
  const [listError, setListError] = useState("");
  const [loading, setLoading] = useState(false);
  const [clarifying, setClarifying] = useState(false);
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [interventionSubmitting, setInterventionSubmitting] = useState(false);
  const [sourceSkill, setSourceSkill] = useState(null);
  const [skillEvaluating, setSkillEvaluating] = useState(false);
  const [skillGenerating, setSkillGenerating] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);
  const [caseDrawerOpen, setCaseDrawerOpen] = useState(false);
  const [treeFullscreen, setTreeFullscreen] = useState(false);
  const [pendingTreeIntervention, setPendingTreeIntervention] = useState(null);
  const [composerAction, setComposerAction] = useState("ADD_CONTEXT");
  // 每次进入诊断页都先给对话完整宽度。分屏是临时对照工具，不应因上一次
  // 浏览器偏好把用户永久困在狭窄的双栏布局中。
  const [contentView, setContentView] = useState("conversation");
  const [mode, setMode] = useState(() => {
    try {
      return window.localStorage.getItem("mini-drop-diagnosis-mode") === "expert" ? "expert" : "simple";
    } catch {
      return "simple";
    }
  });
  const requestVersion = useRef(0);
  const advancing = useRef(false);
  const liveRefreshTimer = useRef(null);
  const selectedIdRef = useRef("");
  const automaticSkillAttempts = useRef(new Set());
  const initialCaseKey = useRef(new URLSearchParams(window.location.search).get("case") || "");

  const isExpert = mode === "expert";
  const selectedId = selectedCase?.diagnosis_id || "";
  selectedIdRef.current = selectedId;
  const statusValue = String(detail?.status || selectedCase?.status || "").toUpperCase();
  const settled = TERMINAL.has(statusValue);
  const replayMeta = getFrozenReplayMeta(detail, selectedCase, resources.explorationTree);
  const frozenReplay = replayMeta.isFrozenReplay;
  const readOnly = LOCKED_TERMINAL.has(statusValue) || frozenReplay;

  const loadSourceSkill = useCallback(async (diagnosisId) => {
    if (!diagnosisId) {
      setSourceSkill(null);
      return null;
    }
    try {
      const skills = await listDiagnosticSkills();
      const matched = (skills || [])
        .filter((skill) => (skill.source_diagnosis_ids || []).includes(diagnosisId))
        .sort((left, right) => Number(right.version || 0) - Number(left.version || 0));
      const latest = matched[0] || null;
      setSourceSkill(latest);
      return latest;
    } catch {
      setSourceSkill(null);
      return null;
    }
  }, []);

  const materializeDiagnosticSkill = useCallback(async (diagnosisId, { notify = false } = {}) => {
    if (!diagnosisId) return null;
    if (diagnosisId === selectedIdRef.current) setSkillGenerating(true);
    try {
      const candidate = await createDiagnosticSkillCandidate(diagnosisId);
      const evaluated = await evaluateDiagnosticSkill(candidate.skill_id);
      if (diagnosisId === selectedIdRef.current) setSourceSkill(evaluated);
      if (notify) {
        const gate = evaluated?.gate_metrics || {};
        const actionText = candidate?.parent_skill_id
          ? `本次诊断已自动把 Skill 优化为 v${candidate.version}`
          : `本次诊断已自动生成候选 Skill v${candidate.version || 1}`;
        if (gate.eligible) {
          message.success(`${actionText}，门禁 ${gate.passed || 0}/${gate.total || 0} 通过，等待人工批准发布`);
        } else {
          message.warning(`${actionText}，门禁 ${gate.passed || 0}/${gate.total || 3} 通过，暂不投入复用`);
        }
      }
      return evaluated;
    } finally {
      if (diagnosisId === selectedIdRef.current) setSkillGenerating(false);
    }
  }, []);

  const loadCases = useCallback(async () => {
    setListLoading(true);
    setListError("");
    try {
      const rows = await listDropInsightDiagnoses();
      const nextCases = (rows || [])
        .map(normalizeCase)
        .sort((a, b) => String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")));
      setCases(nextCases);
      setSelectedCase((current) => {
        const requested = current?.selection_key || initialCaseKey.current;
        if (!requested) return current;
        const match = nextCases.find((item) => item.selection_key === requested);
        if (match) initialCaseKey.current = "";
        return match || current;
      });
    } catch (error) {
      setCases([]);
      setListError(error?.message || "诊断列表暂时不可用，请检查服务连接后重试");
    } finally {
      setListLoading(false);
      setListLoaded(true);
    }
  }, []);

  const loadV2Detail = useCallback(async (caseItem, version, projectedDetail = null) => {
    const id = caseItem.diagnosis_id;
    const requests = [
      ["核心详情", getDropInsightDiagnosis(id)],
      ["实时探索树", getDropInsightExplorationTree(id)],
      ["事件", listDropInsightEvents(id)],
      ["候选假设", listDropInsightHypotheses(id)],
      ["证据", listDropInsightEvidence(id)],
      ["报告", listDropInsightReports(id)],
      ["工具调用", listDropInsightToolCalls(id)],
      ["预算", getDropInsightBudget(id)],
      ["反馈", listDropInsightFeedback(id)],
      ["诊断策略复用", listDiagnosticSkillActivations(id)],
      ["人工干预", listDropInsightInterventions(id)],
      ["Agent Runtime", getAgentRuntimeStatus()],
      ["知识检索轨迹", listDropInsightRetrievals(id)],
    ];
    const settled = await Promise.allSettled(requests.map(([, request]) => request));
    if (version !== requestVersion.current) return;
    const nativeDetail = settled[0].status === "fulfilled" ? settled[0].value : null;
    const coreDetail = nativeDetail || projectedDetail;
    if (!coreDetail) throw settled[0].reason;
    const value = (index, fallback) => settled[index].status === "fulfilled" ? settled[index].value : fallback;
    setDetail(coreDetail);
    setSelectedCase((current) => {
      if (!current || current.diagnosis_id !== id) return current;
      const nextStatus = coreDetail.status || current.status;
      const nextUpdatedAt = coreDetail.updated_at || current.updated_at;
      if (nextStatus === current.status && nextUpdatedAt === current.updated_at) return current;
      return normalizeCase({
        ...current,
        status: nextStatus,
        updated_at: nextUpdatedAt,
      });
    });
    setResources({
      explorationTree: value(1, null),
      events: value(2, []),
      hypotheses: value(3, []),
      evidence: value(4, []),
      reports: value(5, []),
      toolCalls: value(6, []),
      budget: value(7, null),
      feedback: value(8, []),
      skillActivations: value(9, []),
      interventions: value(10, []),
      runtimeStatus: value(11, null),
      retrievals: value(12, []),
    });
    setResourceErrors([
      ...settled.flatMap((result, index) => index > 0 && result.status === "rejected" ? [requests[index][0]] : []),
    ]);
  }, []);

  const loadSelectedDetail = useCallback(async (caseItem, { background = false } = {}) => {
    if (!caseItem) {
      requestVersion.current += 1;
      setDetail(null);
      setResources(EMPTY_RESOURCES);
      setResourceErrors([]);
      return;
    }
    const version = ++requestVersion.current;
    if (!background) setLoading(true);
    setResourceErrors([]);
    try {
      await loadV2Detail(caseItem, version);
    } catch (error) {
      if (version === requestVersion.current) {
        setDetail(null);
        setResources(EMPTY_RESOURCES);
        setResourceErrors(["详情"]);
        message.error(error?.message || "诊断详情加载失败");
      }
    } finally {
      if (!background && version === requestVersion.current) setLoading(false);
    }
  }, [loadV2Detail]);

  useEffect(() => { loadCases(); }, [loadCases]);

  useEffect(() => {
    if (!selectedCase && initialCaseKey.current && listLoaded && !listLoading) {
      setListError("链接中的诊断案例不存在或已从列表隐藏。");
      initialCaseKey.current = "";
      syncCaseQuery("");
    }
  }, [listLoaded, listLoading, selectedCase]);

  useEffect(() => { loadSelectedDetail(selectedCase); }, [selectedCase, loadSelectedDetail]);
  useEffect(() => {
    setSourceSkill(null);
    loadSourceSkill(selectedId);
  }, [selectedId, loadSourceSkill]);

  const latestVerifiedReport = useMemo(
    () => (resources.reports || []).find(isVerifiedReport) || null,
    [resources.reports],
  );

  useEffect(() => {
    if (!selectedId || sourceSkill || !latestVerifiedReport || frozenReplay) return;
    if (!TERMINAL.has(String(detail?.status || "").toUpperCase())) return;
    if (automaticSkillAttempts.current.has(selectedId)) return;
    automaticSkillAttempts.current.add(selectedId);
    materializeDiagnosticSkill(selectedId, { notify: true }).catch((error) => {
      if (selectedId === selectedIdRef.current) {
        message.info(error?.message || "本次可信诊断暂未形成可复用 Skill");
      }
    });
  }, [detail?.status, frozenReplay, latestVerifiedReport, materializeDiagnosticSkill, selectedId, sourceSkill]);

  const pollSelectedDetail = useCallback(async () => {
    if (!selectedId || settled) return;
    const diagnosisId = selectedId;
    const session = await getDropInsightDiagnosis(diagnosisId);
    if (diagnosisId !== selectedIdRef.current) return;
    if (TERMINAL.has(session.status)) {
      await loadCases();
      return;
    }
    await loadSelectedDetail(selectedCase, { background: true });
  }, [loadCases, loadSelectedDetail, selectedCase, selectedId, settled]);

  const handleDiagnosisProgress = useCallback((event) => {
    const diagnosisId = event?.diagnosis_id;
    if (!diagnosisId || diagnosisId !== selectedIdRef.current) return;
    getDropInsightExplorationTree(diagnosisId)
      .then((tree) => {
        if (diagnosisId !== selectedIdRef.current) return;
        setResources((current) => {
          const previousRevision = Number(current.explorationTree?.revision || 0);
          if (Number(tree?.revision || 0) < previousRevision) return current;
          return { ...current, explorationTree: tree };
        });
      })
      .catch(() => undefined);

    if (liveRefreshTimer.current) window.clearTimeout(liveRefreshTimer.current);
    liveRefreshTimer.current = window.setTimeout(() => {
      if (diagnosisId === selectedIdRef.current && selectedCase) {
        loadSelectedDetail(selectedCase, { background: true }).catch(() => undefined);
      }
    }, 220);
  }, [loadSelectedDetail, selectedCase]);

  useEffect(() => {
    const reloadAfterCredentialChange = () => loadCases();
    window.addEventListener("mini-drop:credentials-changed", reloadAfterCredentialChange);
    return () => window.removeEventListener("mini-drop:credentials-changed", reloadAfterCredentialChange);
  }, [loadCases]);

  const { connected: sseConnected } = useSSE({
    onDiagnosisProgress: handleDiagnosisProgress,
    channel: "diagnosis",
    resourceId: selectedId,
  });

  useEffect(() => () => {
    if (liveRefreshTimer.current) window.clearTimeout(liveRefreshTimer.current);
  }, []);

  usePolling(pollSelectedDetail, {
    interval: sseConnected ? 10000 : 2500,
    enabled: Boolean(selectedId) && !settled,
  });

  function selectCase(item) {
    setReplayOpen(false);
    setCaseDrawerOpen(false);
    setSelectedCase(item);
    syncCaseQuery(item.selection_key);
  }

  function startBlankDiagnosis() {
    setReplayOpen(false);
    setCaseDrawerOpen(false);
    setSelectedCase(null);
    syncCaseQuery("");
    setWorkspaceView("workspace");
  }

  function archiveCase(item) {
    Modal.confirm({
      title: `归档诊断「${item.query || item.case_id}」？`,
      content: "归档后会从当前列表隐藏，但证据与审计记录继续保留。",
      okText: "归档",
      cancelText: "取消",
      onOk: async () => {
        try {
          await deleteDropInsightDiagnosis(item.diagnosis_id);
          if (selectedCase?.selection_key === item.selection_key) startBlankDiagnosis();
          await loadCases();
          message.success("诊断已归档");
        } catch (error) {
          message.error(error?.message || "归档失败");
        }
      },
    });
  }

  async function createAndOpenDiagnosis(payload) {
    const text = String(payload?.query || "").trim();
    if (!text) {
      message.info("请描述遇到的问题，例如：订单服务 CPU 飙高");
      return null;
    }
    setSending(true);
    try {
      const created = await createDropInsightDiagnosis({
        ...payload,
        query: text,
      });
      const item = normalizeCase({ ...created, query: text, status: created.status || "CREATED" }, true);
      setQuery("");
      setSelectedCase(item);
      syncCaseQuery(item.selection_key);
      setWorkspaceView("workspace");
      await runDropInsightPlanner(created.diagnosis_id).catch(() => undefined);
      await loadCases();
      return created;
    } catch (error) {
      message.error(error?.message || "创建诊断失败");
      return null;
    } finally {
      setSending(false);
    }
  }

  async function startNew() {
    return createAndOpenDiagnosis({
      query,
      mode: isExpert ? "ASSISTED" : "AUTONOMOUS",
      auto_scope: !isExpert,
    });
  }

  async function startDiagnosisFromShowcase(diagnosisRequest) {
    const created = await createAndOpenDiagnosis({
      ...diagnosisRequest,
      query: diagnosisRequest?.query || diagnosisRequest?.prompt || "诊断故障广场中的受控异常",
      mode: diagnosisRequest?.mode || (isExpert ? "ASSISTED" : "AUTONOMOUS"),
      auto_scope: diagnosisRequest?.auto_scope ?? !isExpert,
    });
    if (created) message.success("已切换到真实诊断工作台");
  }

  async function openDiagnosis(diagnosisId) {
    if (!diagnosisId) return;
    const existing = cases.find((item) => item.diagnosis_id === diagnosisId);
    if (existing) {
      selectCase(existing);
      setWorkspaceView("workspace");
      return;
    }
    try {
      const session = await getDropInsightDiagnosis(diagnosisId);
      selectCase(normalizeCase(session));
      setWorkspaceView("workspace");
    } catch (error) {
      message.error(error?.message || "诊断记录加载失败");
    }
  }

  async function handleIntervention({ action = "ADD_CONTEXT", message: interventionMessage, hypothesis_id: hypothesisId } = {}) {
    const text = String(interventionMessage || "").trim();
    if (!selectedId || readOnly || !text || interventionSubmitting) return;
    setInterventionSubmitting(true);
    try {
      const idempotencyKey = globalThis.crypto?.randomUUID?.()
        || `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const saved = await submitDropInsightIntervention(selectedId, {
        expected_version: detail?.diagnosis_version ?? detail?.version,
        action,
        message: text,
        ...(hypothesisId ? { hypothesis_id: hypothesisId } : {}),
        idempotency_key: idempotencyKey,
      });
      setResources((current) => ({
        ...current,
        interventions: [
          ...(current.interventions || []).filter((item) => item.intervention_id !== saved?.intervention_id),
          saved,
        ],
      }));
      setQuery("");
      setComposerAction("ADD_CONTEXT");
      message.success(action === "ADD_CONTEXT" ? "补充信息已进入下一轮诊断" : "已记录人工干预并更新探索方向");
      await Promise.all([loadSelectedDetail(selectedCase, { background: true }), loadCases()]);
      return saved;
    } catch (error) {
      message.error(error?.message || "人工干预提交失败");
      return null;
    } finally {
      setInterventionSubmitting(false);
    }
  }

  async function submitComposer() {
    if (!selectedId) return startNew();
    if (readOnly) {
      message.info("该诊断已经结束，请点击“新建诊断”开启新的调查");
      return null;
    }
    return handleIntervention({ action: composerAction, message: query });
  }

  function prepareTreeIntervention(payload) {
    if (!payload || readOnly) return;
    setPendingTreeIntervention({
      action: payload.action || "CHANGE_DIRECTION",
      hypothesis_id: payload.hypothesis_id,
      message: payload.message || "",
    });
  }

  async function confirmTreeIntervention() {
    const saved = await handleIntervention(pendingTreeIntervention || {});
    if (saved) setPendingTreeIntervention(null);
  }

  async function decideTool(toolCallId, approved) {
    if (!selectedId || readOnly) return;
    try {
      await decideDropInsightToolCall(selectedId, toolCallId, {
        approved,
        reason: approved ? "用户在 AI 诊断对话中审批通过" : "用户在 AI 诊断对话中拒绝",
      });
      await loadSelectedDetail(selectedCase);
      await loadCases();
    } catch (error) { message.error(error?.message || String(error)); }
  }

  async function handleUpdateToolArgs(toolCallId, argumentsObj) {
    if (!selectedId || readOnly) return;
    await updateDropInsightToolCall(selectedId, toolCallId, argumentsObj);
    await loadSelectedDetail(selectedCase);
  }

  async function handleClarify(payload) {
    if (!selectedId || readOnly) return;
    setClarifying(true);
    try {
      await clarifyDropInsightDiagnosis(selectedId, payload);
      await runDropInsightPlanner(selectedId).catch(() => undefined);
      await loadSelectedDetail(selectedCase);
    } catch (error) { message.error(error?.message || "提交澄清失败"); }
    finally { setClarifying(false); }
  }

  async function advanceNow() {
    if (!selectedId || readOnly || advancing.current) return;
    advancing.current = true;
    try {
      await advanceDropInsightOrchestrator(selectedId);
      await loadSelectedDetail(selectedCase);
    } catch (error) { message.error(error?.message || "推进失败"); }
    finally { advancing.current = false; }
  }

  async function handleSubmitFeedback(payload) {
    if (!selectedId) return;
    setFeedbackSubmitting(true);
    try {
      const saved = await submitDropInsightFeedback(selectedId, payload);
      message.success(saved.revision_hypothesis_id ? "已保存纠正并开启下一轮诊断" : "反馈已保存");
      if (payload.feedback_label === "correct" && !sourceSkill) {
        try {
          await materializeDiagnosticSkill(selectedId, { notify: true });
        } catch (skillError) {
          message.info(skillError?.message || "本次轨迹尚未满足技能沉淀条件");
        }
      }
      await loadSelectedDetail(selectedCase);
      await loadCases();
    } catch (error) {
      message.error(error?.message || "反馈提交失败");
    } finally { setFeedbackSubmitting(false); }
  }

  async function handleEvaluateSkill(skill) {
    if (!skill?.skill_id || skillEvaluating) return;
    setSkillEvaluating(true);
    try {
      const evaluated = await evaluateDiagnosticSkill(skill.skill_id);
      setSourceSkill(evaluated);
      const gate = evaluated?.gate_metrics || {};
      if (gate.eligible) message.success(`门禁评测通过：${gate.passed || 3}/${gate.total || 3}`);
      else message.warning(`门禁尚未通过：${gate.passed || 0}/${gate.total || 3}，请到 Skill 广场查看失败项`);
    } catch (error) {
      message.error(error?.message || "Skill 门禁评测失败");
    } finally {
      setSkillEvaluating(false);
    }
  }

  async function handleGenerateSkill() {
    if (!selectedId || skillGenerating) return;
    try {
      await materializeDiagnosticSkill(selectedId, { notify: true });
    } catch (error) {
      message.info(error?.message || "当前案例暂未满足候选 Skill 沉淀条件");
    }
  }

  const diagnosisProcess = useMemo(() => {
    const hasScope = Boolean(detail?.target?.agent_id || detail?.agent_id || detail?.target?.pid || detail?.pid);
    let current = 0;
    if (hasScope) current = 1;
    if (resources.hypotheses.length) current = 2;
    if (resources.toolCalls.length) current = 3;
    if (resources.evidence.length) current = 4;
    if (resources.reports.length || TERMINAL.has(detail?.status)) current = 5;
    return {
      current,
      items: ["理解问题", "确认范围", "生成假设", "决策树取证", "证据裁决", "结论验证"].map((title) => ({ title })),
    };
  }, [detail, resources]);

  const canonical = canonicalStatus(detail?.status || selectedCase?.status);
  const treeStats = resources.explorationTree?.stats || {};
  const treeRevision = resources.explorationTree?.revision || 0;
  const hasActiveDiagnosis = Boolean(detail || selectedCase);

  return (
    <div className="ai-diagnosis-page">
      <header className={`diagnosis-command-header ${hasActiveDiagnosis ? "is-compact" : ""}`}>
        <div>
          <div className="diagnosis-eyebrow"><RobotOutlined /> MINI-DROP · 智能诊断</div>
          <Title level={2}>
            {hasActiveDiagnosis ? `当前诊断 · 第 ${treeStats.current_round || 0} 轮` : "Drop 负责采集事实，Agent 决定下一步，Skill 只复用已验证路线"}
          </Title>
          <Paragraph>
            {hasActiveDiagnosis
              ? "先看根因、可信度与建议；需要时再展开完整调查过程。"
              : "三层通过持久化事件连接：采集成功不等于证据有效，Skill 命中也不等于根因成立。"}
          </Paragraph>
        </div>
        <div className="diagnosis-live-signals">
          <span className={sseConnected ? "is-online" : "is-offline"}>
            {sseConnected ? <WifiOutlined /> : <DisconnectOutlined />}
            {sseConnected ? "实时事件已连接" : "轮询兜底中"}
          </span>
          <span><BranchesOutlined /> 树版本 {treeRevision}</span>
          <span>第 {treeStats.current_round || 0} 轮</span>
        </div>
      </header>

      {!hasActiveDiagnosis && (
        <section className="diagnosis-layer-map" aria-label="Mini-Drop 诊断架构分层">
          <article>
            <span className="diagnosis-layer-index">01</span>
            <div>
              <Text strong>Drop · 探针与采集底座</Text>
              <Paragraph>C++ Agent 在目标机执行白名单 Collector，产出 Artifact；它负责拿材料，不负责宣布根因。</Paragraph>
            </div>
          </article>
          <article>
            <span className="diagnosis-layer-index">02</span>
            <div>
              <Text strong>诊断智能体（Agent）· 调查与受控执行</Text>
              <Paragraph>诊断工作进程基于假设和证据选择下一探针；Go API 在能力、权限、风险和预算门禁内创建采集任务。</Paragraph>
            </div>
          </article>
          <article>
            <span className="diagnosis-layer-index">03</span>
            <div>
              <Text strong>Skill 路线记忆 · 经验复用</Text>
              <Paragraph>只保存已验证的工具顺序、证据要求和退出条件；需评测、发布，冲突时退出并回到基线 Planner。</Paragraph>
            </div>
          </article>
        </section>
      )}

      <div className="ai-diagnosis-workspace">
        <main className="ai-diagnosis-main">
          <div className="diagnosis-workbench-toolbar">
            <div className="diagnosis-case-title">
              <Space wrap size={8} className="diagnosis-workspace-navigation">
                <Button
                  icon={<MenuUnfoldOutlined />}
                  aria-label="打开诊断案例列表"
                  onClick={() => setCaseDrawerOpen(true)}
                >
                  诊断案例{cases.length ? ` ${cases.length}` : ""}
                </Button>
                <Segmented
                  options={[{ label: "Agent 工作台", value: "workspace" }, { label: "验证与 A/B", value: "evaluation" }]}
                  value={workspaceView}
                  onChange={setWorkspaceView}
                />
              </Space>
              <Text type="secondary">{workspaceView === "evaluation" ? "验证中心" : "当前诊断"}</Text>
              <Title level={4}>{workspaceView === "evaluation" ? "诊断与 Skill 验证中心" : (detail?.query || selectedCase?.query || "开始一次新诊断")}</Title>
            </div>
            {workspaceView === "workspace" && (
              <Space wrap>
                {selectedCase && <>
                  <span className={`diagnosis-status is-${canonical.toLowerCase()}`}>{STATUS_LABELS[canonical]}</span>
                  {frozenReplay ? (
                    <span className="diagnosis-replay-badge">FULL_LATS · 冻结回放</span>
                  ) : (
                    <Segmented
                      value={mode}
                      onChange={(value) => {
                        setMode(value);
                        try { window.localStorage.setItem("mini-drop-diagnosis-mode", value); } catch { /* ignore */ }
                      }}
                      options={[
                        { label: "自主", value: "simple" },
                        { label: "人工审批", value: "expert" },
                      ]}
                    />
                  )}
                  <Segmented
                    value={contentView}
                    onChange={setContentView}
                    options={[
                      { label: "对话", value: "conversation" },
                      { label: "探索树", value: "tree" },
                      { label: "分屏", value: "split" },
                    ]}
                    aria-label="工作台显示方式"
                  />
                  {(contentView === "tree" || contentView === "split") && (
                    <Button
                      icon={<FullscreenOutlined />}
                      aria-label="全屏查看探索树"
                      onClick={() => setTreeFullscreen(true)}
                    >全屏树</Button>
                  )}
                  {readOnly && <span className="diagnosis-readonly-badge">只读记录</span>}
                  <Button
                    icon={<ExperimentOutlined />}
                    onClick={() => setReplayOpen(true)}
                    aria-label="打开当前复杂案例回放"
                  >
                    复杂案例回放{resources.explorationTree?.stats?.rounds ? ` · ${resources.explorationTree.stats.rounds} 轮` : ""}
                  </Button>
                  {isExpert && <Button icon={<ProfileOutlined />} onClick={() => setDetailOpen(true)}>审计细节</Button>}
                  {!settled && !frozenReplay && <Button type="primary" icon={<SyncOutlined />} onClick={advanceNow}>继续推进</Button>}
                </>}
              </Space>
            )}
          </div>

          {workspaceView === "evaluation" ? (
            <div className="diagnosis-evaluation-view">
              <EvalPanel
                onStartDiagnosis={startDiagnosisFromShowcase}
                onOpenDiagnosis={openDiagnosis}
                onCasesChanged={loadCases}
              />
            </div>
          ) : (
            <>
              {detail && (
                <div className="diagnosis-stage-rail">
                  <Steps
                    size="small"
                    responsive={false}
                    current={diagnosisProcess.current}
                    status={detail.status === "FAILED" ? "error" : "process"}
                    items={diagnosisProcess.items}
                  />
                </div>
              )}
              {resourceErrors.length > 0 && (
                <Alert
                  className="ai-diagnosis-resource-warning"
                  type="warning"
                  showIcon
                  message={`部分数据加载失败：${resourceErrors.join("、")}`}
                  action={<Button size="small" onClick={() => loadSelectedDetail(selectedCase)}>重试</Button>}
                />
              )}

              {frozenReplay && (
                <Alert
                  className="diagnosis-replay-notice"
                  type="info"
                  showIcon
                  message="FULL_LATS · 冻结算法回放"
                  description="本会话只在固定观察快照（snapshot）上执行可重复模拟。真实采集、普通规划器、工具审批和人工干预均已禁用；回放观察不是当前线上状态的实时证据。"
                />
              )}

              {detail && (
                <div className="diagnosis-cockpit-slot">
                  <AgentCockpit
                    detail={detail}
                    resources={resources}
                    sourceSkill={sourceSkill}
                    connected={sseConnected}
                    onOpenEvaluation={() => setWorkspaceView("evaluation")}
                  />
                </div>
              )}

              <Spin spinning={loading}>
                <div className={`diagnosis-workbench-grid ${detail ? `has-diagnosis view-${contentView}` : "is-empty"}`}>
                  {(!detail || contentView !== "tree") && <section className="diagnosis-narrative-panel">
                    <ChatThread
                      detail={detail}
                      hypotheses={resources.hypotheses}
                      toolCalls={resources.toolCalls}
                      evidence={resources.evidence}
                      reports={resources.reports}
                      events={resources.events}
                      mode={mode}
                      readOnly={readOnly}
                      unavailableSections={[]}
                      onApproveTool={(id) => decideTool(id, true)}
                      onRejectTool={(id) => decideTool(id, false)}
                      onUpdateToolArgs={handleUpdateToolArgs}
                      onClarify={handleClarify}
                      clarifying={clarifying}
                      feedback={resources.feedback}
                      onSubmitFeedback={handleSubmitFeedback}
                      feedbackSubmitting={feedbackSubmitting}
                      skillActivations={resources.skillActivations}
                      interventions={resources.interventions}
                    />
                  </section>}
                  {detail && contentView !== "conversation" && (
                    <aside className="diagnosis-tree-panel" aria-label="实时诊断探索树">
                      <ActualExplorationTree
                        tree={resources.explorationTree}
                        hypotheses={resources.hypotheses}
                        toolCalls={resources.toolCalls}
                        report={latestVerifiedReport || resources.reports?.[0]}
                        onIntervene={frozenReplay ? undefined : prepareTreeIntervention}
                        intervening={interventionSubmitting}
                      />
                      {!resources.explorationTree?.nodes?.length
                        && !resources.hypotheses?.length
                        && !resources.toolCalls?.length && (
                          <div className="diagnosis-tree-empty">
                            <BranchesOutlined />
                            <b>探索树将在规划后逐节点出现</b>
                            <span>每次假设、工具调用、反证剪枝和人工转向都会通过事件流增量更新。</span>
                          </div>
                      )}
                    </aside>
                  )}
                </div>
              </Spin>

              <div className="diagnosis-composer-shell">
                {frozenReplay && (
                  <div className="diagnosis-replay-composer-note" role="note">
                    冻结回放为只读算法轨迹；如需采集当前环境，请新建普通诊断。
                  </div>
                )}
                {selectedId && !readOnly && (
                  <div className="diagnosis-composer-meta">
                    <div>
                      <RobotOutlined />
                      <span>继续同一条诊断线程 · 旧轮次、证据和报告不会被覆盖</span>
                    </div>
                    <Segmented
                      size="small"
                      options={COMPOSER_ACTIONS}
                      value={composerAction}
                      onChange={setComposerAction}
                      aria-label="本轮交互意图"
                    />
                  </div>
                )}
                <div className="diagnosis-composer">
                  <Input.TextArea
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        submitComposer();
                      }
                    }}
                    autoSize={{ minRows: 1, maxRows: 5 }}
                    placeholder={frozenReplay
                      ? "冻结回放不能追加真实采集、普通规划或人工干预"
                      : selectedId && !readOnly
                      ? COMPOSER_PLACEHOLDERS[composerAction]
                      : "描述问题，例如：订单服务最近 5 分钟 CPU 与 P99 同时升高，请定位根因"}
                    disabled={sending || interventionSubmitting || readOnly}
                    size="large"
                    aria-label={selectedId ? "补充诊断上下文" : "描述诊断问题"}
                  />
                  <Button
                    type="primary"
                    icon={<SendOutlined />}
                    aria-label={frozenReplay ? "回放只读" : undefined}
                    onClick={submitComposer}
                    loading={sending || interventionSubmitting}
                    disabled={readOnly}
                    size="large"
                  >{frozenReplay
                    ? "回放只读"
                    : selectedId
                    ? (statusValue === "COMPLETED" ? "基于结论继续一轮" : "发送并继续诊断")
                    : "开始诊断"}</Button>
                </div>
              </div>
            </>
          )}
        </main>
      </div>

      <Drawer
        title="诊断案例"
        placement="left"
        width="min(380px, calc(100vw - 24px))"
        open={caseDrawerOpen}
        onClose={() => setCaseDrawerOpen(false)}
        className="diagnosis-case-drawer"
      >
        <div className="diagnosis-sidebar-heading">
          <Text strong>选择已有会话，或开始一条新诊断</Text>
          <Text type="secondary">案例列表不再占用主工作台宽度</Text>
        </div>
        <DiagnosisCaseList
          cases={cases}
          selectedKey={selectedCase?.selection_key}
          filter={caseFilter}
          onFilterChange={setCaseFilter}
          onSelect={selectCase}
          onNew={startBlankDiagnosis}
          onArchive={archiveCase}
          loading={listLoading}
          loadError={listError}
        />
      </Drawer>

      <Modal
        title="实时诊断探索树 · 全屏阅读"
        open={treeFullscreen}
        onCancel={() => setTreeFullscreen(false)}
        footer={null}
        width="calc(100vw - 48px)"
        className="diagnosis-tree-fullscreen-modal"
        destroyOnHidden
      >
        {detail && (
          <ActualExplorationTree
            tree={resources.explorationTree}
            hypotheses={resources.hypotheses}
            toolCalls={resources.toolCalls}
            report={latestVerifiedReport || resources.reports?.[0]}
            onIntervene={frozenReplay ? undefined : prepareTreeIntervention}
            intervening={interventionSubmitting}
          />
        )}
      </Modal>

      <Modal
        title="人工调整诊断路径"
        open={Boolean(pendingTreeIntervention)}
        onCancel={() => setPendingTreeIntervention(null)}
        onOk={confirmTreeIntervention}
        okText="确认并开启下一轮"
        cancelText="取消"
        confirmLoading={interventionSubmitting}
        okButtonProps={{ disabled: !String(pendingTreeIntervention?.message || "").trim() }}
        width={620}
        destroyOnHidden
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message="这次调整会创建新的诊断轮次"
            description="已有证据和报告会完整保留，Agent 将按你的新方向重排假设与工具计划。"
          />
          <div>
            <Text strong>干预方式</Text>
            <div style={{ marginTop: 8 }}>
              <Segmented
                block
                value={pendingTreeIntervention?.action || "CHANGE_DIRECTION"}
                onChange={(action) => setPendingTreeIntervention((current) => ({ ...current, action }))}
                aria-label="人工干预方式"
                options={[
                  { label: "优先调查", value: "CHANGE_DIRECTION" },
                  { label: "寻找反证", value: "CHALLENGE_HYPOTHESIS" },
                ]}
              />
            </div>
          </div>
          {pendingTreeIntervention?.hypothesis_id && (
            <Text type="secondary">关联假设：{pendingTreeIntervention.hypothesis_id}</Text>
          )}
          <div>
            <Text strong>给 Agent 的具体要求</Text>
            <Input.TextArea
              aria-label="给 Agent 的具体要求"
              autoSize={{ minRows: 4, maxRows: 8 }}
              value={pendingTreeIntervention?.message || ""}
              onChange={(event) => setPendingTreeIntervention((current) => ({
                ...current,
                message: event.target.value,
              }))}
              placeholder="说明希望优先验证什么、需要什么反证，或希望 Agent 避免哪条路径"
              style={{ marginTop: 8 }}
            />
          </div>
        </Space>
      </Modal>

      <TechnicalDetailDrawer
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        detail={detail}
        toolCalls={resources.toolCalls}
        evidence={resources.evidence}
        reports={resources.reports}
        events={resources.events}
      />
      <MentorComplexShowcase
        open={replayOpen}
        loading={loading}
        detail={detail}
        explorationTree={resources.explorationTree}
        hypotheses={resources.hypotheses}
        toolCalls={resources.toolCalls}
        evidence={resources.evidence}
        reports={resources.reports}
        sourceSkill={sourceSkill}
        verifiedReport={frozenReplay ? null : latestVerifiedReport}
        skillBusy={skillEvaluating || skillGenerating}
        onGenerateSkill={frozenReplay ? undefined : handleGenerateSkill}
        onEvaluateSkill={frozenReplay ? undefined : handleEvaluateSkill}
        onOpenPlaza={() => {
          setReplayOpen(false);
          setWorkspaceView("evaluation");
        }}
        onClose={() => setReplayOpen(false)}
      />
    </div>
  );
}
