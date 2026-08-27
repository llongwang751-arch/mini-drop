import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
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
  ProfileOutlined,
  RobotOutlined,
  SendOutlined,
  SyncOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import ChatThread from "../components/ChatThread";
import ActualExplorationTree from "../components/ActualExplorationTree";
import DiagnosisCaseList from "../components/DiagnosisCaseList";
import DiagnosisSkillOutcomeCard from "../components/DiagnosisSkillOutcomeCard";
import EvalPanel from "../components/EvalPanel";
import MentorComplexShowcase from "../components/MentorComplexShowcase";
import TechnicalDetailDrawer from "../components/TechnicalDetailDrawer";
import usePolling from "../hooks/usePolling";
import useSSE from "../hooks/useSSE";
import {
  advanceDropInsightOrchestrator,
  clarifyDropInsightDiagnosis,
  createDropInsightDiagnosis,
  createDiagnosticSkillCandidate,
  decideDropInsightToolCall,
  deleteDropInsightDiagnosis,
  evaluateDiagnosticSkill,
  getMentorComplexShowcase,
  getDiagnosticCase,
  getDropInsightBudget,
  getDropInsightDiagnosis,
  getDropInsightExplorationTree,
  listDiagnosticCasesPage,
  listDiagnosticSkillActivations,
  listDiagnosticSkills,
  listDropInsightDiagnoses,
  listDropInsightEvidence,
  listDropInsightFeedback,
  listDropInsightEvents,
  listDropInsightHypotheses,
  listDropInsightReports,
  listDropInsightToolCalls,
  runDropInsightPlanner,
  submitDropInsightFeedback,
  updateDropInsightToolCall,
} from "../api/client";
import "./AIDiagnosis.css";

const { Paragraph, Text, Title } = Typography;

const TERMINAL = new Set(["COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"]);
const TERMINAL_CANONICAL = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);
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
  budget: null,
  skillActivations: [],
  explorationTree: null,
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

function normalizeCase(item, active = false) {
  const source = item.source || "drop_insight_v2";
  const id = nativeId(item);
  return {
    ...item,
    source,
    case_id: item.case_id || id,
    diagnosis_id: item.diagnosis_id || id,
    canonical_status: item.canonical_status || canonicalStatus(item.status),
    selection_key: selectionKey(source, id),
    active,
  };
}

function readHiddenKeys() {
  try {
    return new Set(JSON.parse(window.localStorage.getItem("mini-drop-hidden-history") || "[]"));
  } catch {
    return new Set();
  }
}

function mergeCases(activeRows, historyRows) {
  const hidden = readHiddenKeys();
  const merged = new Map();
  for (const row of historyRows || []) {
    const normalized = normalizeCase(row, false);
    if (!hidden.has(normalized.case_id) && !hidden.has(normalized.selection_key)) {
      merged.set(normalized.selection_key, normalized);
    }
  }
  for (const row of activeRows || []) {
    const normalized = normalizeCase(row, true);
    const previous = merged.get(normalized.selection_key) || {};
    merged.set(normalized.selection_key, { ...previous, ...normalized, active: true });
  }
  return [...merged.values()].sort((a, b) =>
    String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")),
  );
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function normalizeReport(report) {
  if (!report) return null;
  return {
    ...report,
    conclusion: report.conclusion || report.root_cause || report.summary || report.content || "该版本未记录结论正文。",
    confidence: typeof report.confidence === "number" ? report.confidence : 0,
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

function adaptHistoricalDetail(caseItem, payload) {
  const native = payload?.native_payload || {};
  if (caseItem.source === "controlled_showcase") {
    return {
      detail: native,
      resources: {
        ...EMPTY_RESOURCES,
        hypotheses: asArray(native.hypotheses),
        toolCalls: asArray(native.tool_calls),
        evidence: asArray(native.evidence),
        reports: asArray(native.reports).map(normalizeReport).filter(Boolean),
        events: asArray(native.events),
      },
      unavailableSections: [],
    };
  }
  if (caseItem.source === "cluster_diagnosis_v1") {
    const graph = native.hypothesis_graph || {};
    const hypotheses = asArray(graph.hypotheses).length ? graph.hypotheses : asArray(graph.nodes);
    const reports = asArray(native.conclusion_versions).map(normalizeReport).filter(Boolean);
    return {
      detail: {
        ...native,
        diagnosis_id: caseItem.diagnosis_id,
        query: native.raw_query || caseItem.query,
        status: native.status || caseItem.status,
        target: native.target_scope || caseItem.target,
      },
      resources: { ...EMPTY_RESOURCES, hypotheses, evidence: asArray(native.evidence), reports },
      unavailableSections: [
        ...(hypotheses.length ? [] : ["候选假设"]),
        ...(native.evidence?.length ? [] : ["证据"]),
        ...(reports.length ? [] : ["结论版本"]),
        "v2 工具调用时间线",
      ],
    };
  }
  if (caseItem.source === "legacy_rca") {
    const run = native.run || native;
    const reports = asArray(native.reports).length
      ? asArray(native.reports).map(normalizeReport).filter(Boolean)
      : [normalizeReport(native.report)].filter(Boolean);
    return {
      detail: {
        ...run,
        diagnosis_id: caseItem.diagnosis_id,
        query: run.summary || caseItem.query,
        status: run.status || caseItem.status,
        target: { task_id: run.task_id },
      },
      resources: { ...EMPTY_RESOURCES, reports },
      unavailableSections: ["候选假设", "结构化证据裁决", "v2 工具调用时间线"],
    };
  }
  return {
    detail: { ...native, query: native.query || caseItem.query, status: native.status || caseItem.status },
    resources: { ...EMPTY_RESOURCES },
    unavailableSections: [],
  };
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
  const [unavailableSections, setUnavailableSections] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listLoaded, setListLoaded] = useState(false);
  const [listError, setListError] = useState("");
  const [loading, setLoading] = useState(false);
  const [clarifying, setClarifying] = useState(false);
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [sourceSkill, setSourceSkill] = useState(null);
  const [skillEvaluating, setSkillEvaluating] = useState(false);
  const [skillGenerating, setSkillGenerating] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [showcaseOpen, setShowcaseOpen] = useState(false);
  const [showcaseLoading, setShowcaseLoading] = useState(false);
  const [showcaseData, setShowcaseData] = useState(null);
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
  const selectedId = selectedCase?.source === "drop_insight_v2" ? selectedCase.diagnosis_id : "";
  selectedIdRef.current = selectedId;
  const readOnly = !selectedCase?.active || TERMINAL_CANONICAL.has(selectedCase?.canonical_status);

  const openMentorShowcase = useCallback(async () => {
    setShowcaseOpen(true);
    if (showcaseData) return;
    setShowcaseLoading(true);
    try {
      setShowcaseData(await getMentorComplexShowcase());
    } catch (error) {
      message.error(error?.message || "复杂案例加载失败，请稍后重试");
    } finally {
      setShowcaseLoading(false);
    }
  }, [showcaseData]);

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
      const [activeResult, historyResult] = await Promise.allSettled([
        listDropInsightDiagnoses(),
        listDiagnosticCasesPage(),
      ]);
      if (activeResult.status === "rejected" && historyResult.status === "rejected") throw activeResult.reason;
      const activeRows = activeResult.status === "fulfilled" ? activeResult.value : [];
      const historyPage = historyResult.status === "fulfilled" ? historyResult.value : {};
      const historyRows = Array.isArray(historyPage) ? historyPage : asArray(historyPage?.items);
      const nextCases = mergeCases(activeRows, historyRows);
      setCases(nextCases);
      setSelectedCase((current) => {
        const requested = current?.selection_key || initialCaseKey.current;
        if (!requested) return current;
        const match = nextCases.find((item) => item.selection_key === requested);
        if (match) initialCaseKey.current = "";
        return match || current;
      });
      if (activeResult.status === "rejected" || historyResult.status === "rejected") {
        setListError("部分案例来源暂时不可用，已展示其余记录。");
      }
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
    ];
    const settled = await Promise.allSettled(requests.map(([, request]) => request));
    if (version !== requestVersion.current) return;
    const nativeDetail = settled[0].status === "fulfilled" ? settled[0].value : null;
    const coreDetail = nativeDetail || projectedDetail;
    if (!coreDetail) throw settled[0].reason;
    const value = (index, fallback) => settled[index].status === "fulfilled" ? settled[index].value : fallback;
    setDetail(coreDetail);
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
    });
    setResourceErrors([
      ...settled.flatMap((result, index) => index > 0 && result.status === "rejected" ? [requests[index][0]] : []),
    ]);
    setUnavailableSections([]);
  }, []);

  const loadSelectedDetail = useCallback(async (caseItem) => {
    if (!caseItem) {
      requestVersion.current += 1;
      setDetail(null);
      setResources(EMPTY_RESOURCES);
      setResourceErrors([]);
      setUnavailableSections([]);
      return;
    }
    const version = ++requestVersion.current;
    setLoading(true);
    setResourceErrors([]);
    try {
      if (caseItem.source === "drop_insight_v2") {
        let projectedDetail = null;
        if (!caseItem.active) {
          const projected = await getDiagnosticCase(caseItem.case_id);
          projectedDetail = projected?.native_payload || null;
        }
        await loadV2Detail(caseItem, version, projectedDetail);
      } else {
        const payload = await getDiagnosticCase(caseItem.case_id);
        if (version !== requestVersion.current) return;
        const adapted = adaptHistoricalDetail(caseItem, payload);
        setDetail(adapted.detail);
        setResources(adapted.resources);
        setUnavailableSections(adapted.unavailableSections);
      }
    } catch (error) {
      if (version === requestVersion.current) {
        setDetail(null);
        setResources(EMPTY_RESOURCES);
        setResourceErrors(["详情"]);
        message.error(error?.message || "诊断详情加载失败");
      }
    } finally {
      if (version === requestVersion.current) setLoading(false);
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
    () => [...(resources.reports || [])].reverse().find(isVerifiedReport) || null,
    [resources.reports],
  );

  useEffect(() => {
    if (!selectedId || sourceSkill || !latestVerifiedReport) return;
    if (!TERMINAL.has(String(detail?.status || "").toUpperCase())) return;
    if (automaticSkillAttempts.current.has(selectedId)) return;
    automaticSkillAttempts.current.add(selectedId);
    materializeDiagnosticSkill(selectedId, { notify: true }).catch((error) => {
      if (selectedId === selectedIdRef.current) {
        message.info(error?.message || "本次可信诊断暂未形成可复用 Skill");
      }
    });
  }, [detail?.status, latestVerifiedReport, materializeDiagnosticSkill, selectedId, sourceSkill]);

  const pollSelectedDetail = useCallback(async () => {
    if (!selectedId || readOnly) return;
    const diagnosisId = selectedId;
    const session = await getDropInsightDiagnosis(diagnosisId);
    if (diagnosisId !== selectedIdRef.current) return;
    if (TERMINAL.has(session.status)) {
      await loadCases();
      return;
    }
    await loadSelectedDetail(selectedCase);
  }, [loadCases, loadSelectedDetail, readOnly, selectedCase, selectedId]);

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
        loadSelectedDetail(selectedCase).catch(() => undefined);
      }
    }, 220);
  }, [loadSelectedDetail, selectedCase]);

  const { connected: sseConnected } = useSSE({
    onDiagnosisProgress: handleDiagnosisProgress,
    channel: "diagnosis",
  });

  useEffect(() => () => {
    if (liveRefreshTimer.current) window.clearTimeout(liveRefreshTimer.current);
  }, []);

  usePolling(pollSelectedDetail, {
    interval: sseConnected ? 10000 : 2500,
    enabled: Boolean(selectedId) && !readOnly,
  });

  function selectCase(item) {
    setSelectedCase(item);
    syncCaseQuery(item.selection_key);
  }

  function startBlankDiagnosis() {
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
          if (item.source === "drop_insight_v2" && item.active) {
            await deleteDropInsightDiagnosis(item.diagnosis_id);
          } else {
            const hidden = readHiddenKeys();
            hidden.add(item.selection_key);
            window.localStorage.setItem("mini-drop-hidden-history", JSON.stringify([...hidden]));
          }
          if (selectedCase?.selection_key === item.selection_key) startBlankDiagnosis();
          await loadCases();
          message.success("诊断已归档");
        } catch (error) {
          message.error(error?.message || "归档失败");
        }
      },
    });
  }

  async function startNew() {
    const text = query.trim();
    if (!text) {
      message.info("请描述遇到的问题，例如：订单服务 CPU 飙高");
      return;
    }
    setSending(true);
    try {
      const created = await createDropInsightDiagnosis({ query: text, mode: "ASSISTED", auto_scope: true });
      const item = normalizeCase({ ...created, query: text, status: created.status || "CREATED" }, true);
      setQuery("");
      setSelectedCase(item);
      syncCaseQuery(item.selection_key);
      await runDropInsightPlanner(created.diagnosis_id).catch(() => undefined);
      await loadCases();
    } catch (error) {
      message.error(error?.message || "创建诊断失败");
    } finally {
      setSending(false);
    }
  }

  async function decideTool(toolCallId, approved) {
    if (!selectedId || readOnly) return;
    try {
      await decideDropInsightToolCall(selectedId, toolCallId, {
        approved,
        reason: approved ? "用户在 AI 诊断对话中审批通过" : "用户在 AI 诊断对话中拒绝",
      });
      await loadSelectedDetail(selectedCase);
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

  return (
    <div className="ai-diagnosis-page">
      <header className="diagnosis-command-header">
        <div>
          <div className="diagnosis-eyebrow"><RobotOutlined /> MINI-DROP · AI DIAGNOSIS</div>
          <Title level={2}>从异常现象走到证据，再把经验沉淀成 Skill</Title>
          <Paragraph>
            树不是诊断结束后的插图。每个假设、工具调用、反证和转向都会在执行过程中写入并实时更新。
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

      <div className="ai-diagnosis-workspace">
        <aside className="ai-diagnosis-sidebar">
          <div className="diagnosis-sidebar-heading">
            <Text strong>诊断中心</Text>
            <Text type="secondary">案例、验证与 Skill</Text>
          </div>
          <Segmented
            block
            options={[{ label: "工作台", value: "workspace" }, { label: "验证中心", value: "evaluation" }]}
            value={workspaceView}
            onChange={setWorkspaceView}
          />
          {workspaceView === "workspace" ? (
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
          ) : (
            <div className="diagnosis-sidebar-note">
              验证中心统一展示测试集、Skill 门禁、反例和版本回滚。
            </div>
          )}
        </aside>

        <main className="ai-diagnosis-main">
          <div className="diagnosis-workbench-toolbar">
            <div className="diagnosis-case-title">
              <Text type="secondary">{workspaceView === "evaluation" ? "EVALUATION CENTER" : "ACTIVE DIAGNOSIS"}</Text>
              <Title level={4}>{workspaceView === "evaluation" ? "诊断与 Skill 验证中心" : (detail?.query || selectedCase?.query || "开始一次新诊断")}</Title>
            </div>
            {workspaceView === "workspace" && (
              <Space wrap>
                <Button icon={<ExperimentOutlined />} onClick={openMentorShowcase}>复杂案例回放</Button>
                {selectedCase && <>
                  <span className={`diagnosis-status is-${canonical.toLowerCase()}`}>{STATUS_LABELS[canonical]}</span>
                  <Segmented
                    value={mode}
                    onChange={(value) => {
                      setMode(value);
                      try { window.localStorage.setItem("mini-drop-diagnosis-mode", value); } catch { /* ignore */ }
                    }}
                    options={[{ label: "简洁", value: "simple" }, { label: "专家", value: "expert" }]}
                  />
                  {readOnly && <span className="diagnosis-readonly-badge">只读记录</span>}
                  {isExpert && <Button icon={<ProfileOutlined />} onClick={() => setDetailOpen(true)}>审计细节</Button>}
                  {!readOnly && <Button type="primary" icon={<SyncOutlined />} onClick={advanceNow}>继续推进</Button>}
                </>}
              </Space>
            )}
          </div>

          {workspaceView === "evaluation" ? (
            <div className="diagnosis-evaluation-view"><EvalPanel /></div>
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

              <Spin spinning={loading}>
                <div className={`diagnosis-workbench-grid ${detail ? "has-diagnosis" : "is-empty"}`}>
                  <section className="diagnosis-narrative-panel">
                    <ChatThread
                      detail={detail}
                      hypotheses={resources.hypotheses}
                      toolCalls={resources.toolCalls}
                      evidence={resources.evidence}
                      reports={resources.reports}
                      events={resources.events}
                      mode={mode}
                      readOnly={readOnly}
                      unavailableSections={unavailableSections}
                      onApproveTool={(id) => decideTool(id, true)}
                      onRejectTool={(id) => decideTool(id, false)}
                      onUpdateToolArgs={handleUpdateToolArgs}
                      onClarify={handleClarify}
                      clarifying={clarifying}
                      feedback={resources.feedback}
                      onSubmitFeedback={handleSubmitFeedback}
                      feedbackSubmitting={feedbackSubmitting}
                      skillActivations={resources.skillActivations}
                    />
                    <DiagnosisSkillOutcomeCard
                      skill={sourceSkill}
                      evaluating={skillEvaluating || skillGenerating}
                      onEvaluate={handleEvaluateSkill}
                      onOpenPlaza={() => setWorkspaceView("evaluation")}
                    />
                  </section>

                  <aside className="diagnosis-tree-panel">
                    {detail ? (
                      <>
                        <ActualExplorationTree
                          tree={resources.explorationTree}
                          hypotheses={resources.hypotheses}
                          toolCalls={resources.toolCalls}
                          report={[...(resources.reports || [])].reverse()[0] || null}
                        />
                        <div className="diagnosis-last-event">
                          <span className="live-tree-pulse" />
                          <div>
                            <b>最近一次树更新</b>
                            <small>{resources.explorationTree?.last_event?.event_type || "等待诊断事件"}</small>
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="diagnosis-tree-empty">
                        <BranchesOutlined />
                        <b>探索画布已就绪</b>
                        <span>提交问题后，候选假设会先出现，随后工具、证据、剪枝和根因路径逐步长出来。</span>
                      </div>
                    )}
                  </aside>
                </div>
              </Spin>

              <div className="diagnosis-composer">
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onPressEnter={startNew}
                  placeholder="描述问题，例如：订单服务最近 5 分钟 CPU 与 P99 同时升高，请定位根因"
                  disabled={sending}
                  size="large"
                  aria-label="描述诊断问题"
                />
                <Button type="primary" icon={<SendOutlined />} onClick={startNew} loading={sending} size="large">开始诊断</Button>
              </div>
            </>
          )}
        </main>
      </div>

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
        open={showcaseOpen}
        loading={showcaseLoading}
        data={showcaseData}
        onClose={() => setShowcaseOpen(false)}
      />
    </div>
  );
}
