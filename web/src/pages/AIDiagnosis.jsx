import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Segmented,
  Space,
  Spin,
  Steps,
  Typography,
  message,
} from "antd";
import { ProfileOutlined, RobotOutlined, SendOutlined, SyncOutlined } from "@ant-design/icons";
import ChatThread from "../components/ChatThread";
import DiagnosisCaseList from "../components/DiagnosisCaseList";
import EvalPanel from "../components/EvalPanel";
import TechnicalDetailDrawer from "../components/TechnicalDetailDrawer";
import {
  advanceDropInsightOrchestrator,
  clarifyDropInsightDiagnosis,
  createDropInsightDiagnosis,
  decideDropInsightToolCall,
  deleteDropInsightDiagnosis,
  getDiagnosticCase,
  getDropInsightBudget,
  getDropInsightDiagnosis,
  listDiagnosticCasesPage,
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
const EMPTY_RESOURCES = {
  hypotheses: [],
  toolCalls: [],
  evidence: [],
  reports: [],
  events: [],
  feedback: [],
  budget: null,
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

function adaptHistoricalDetail(caseItem, payload) {
  const native = payload?.native_payload || {};
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
  const [detailOpen, setDetailOpen] = useState(false);
  const [mode, setMode] = useState(() => {
    try {
      return window.localStorage.getItem("mini-drop-diagnosis-mode") === "expert" ? "expert" : "simple";
    } catch {
      return "simple";
    }
  });
  const requestVersion = useRef(0);
  const advancing = useRef(false);
  const initialCaseKey = useRef(new URLSearchParams(window.location.search).get("case") || "");

  const isExpert = mode === "expert";
  const selectedId = selectedCase?.source === "drop_insight_v2" ? selectedCase.diagnosis_id : "";
  const readOnly = !selectedCase?.active || TERMINAL_CANONICAL.has(selectedCase?.canonical_status);

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
      setListError(error?.message || "案例列表加载失败");
    } finally {
      setListLoading(false);
      setListLoaded(true);
    }
  }, []);

  const loadV2Detail = useCallback(async (caseItem, version, projectedDetail = null) => {
    const id = caseItem.diagnosis_id;
    const requests = [
      ["核心详情", getDropInsightDiagnosis(id)],
      ["事件", listDropInsightEvents(id)],
      ["候选假设", listDropInsightHypotheses(id)],
      ["证据", listDropInsightEvidence(id)],
      ["报告", listDropInsightReports(id)],
      ["工具调用", listDropInsightToolCalls(id)],
      ["预算", getDropInsightBudget(id)],
      ["反馈", listDropInsightFeedback(id)],
    ];
    const settled = await Promise.allSettled(requests.map(([, request]) => request));
    if (version !== requestVersion.current) return;
    const nativeDetail = settled[0].status === "fulfilled" ? settled[0].value : null;
    const coreDetail = nativeDetail || projectedDetail;
    if (!coreDetail) throw settled[0].reason;
    const value = (index, fallback) => settled[index].status === "fulfilled" ? settled[index].value : fallback;
    setDetail(coreDetail);
    setResources({
      events: value(1, []),
      hypotheses: value(2, []),
      evidence: value(3, []),
      reports: value(4, []),
      toolCalls: value(5, []),
      budget: value(6, null),
      feedback: value(7, []),
    });
    setResourceErrors(settled.flatMap((result, index) =>
      index > 0 && result.status === "rejected" ? [requests[index][0]] : [],
    ));
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
    if (!selectedId || readOnly) return undefined;
    const timer = setInterval(async () => {
      if (advancing.current) return;
      try {
        const [tools, reportRows, session] = await Promise.all([
          listDropInsightToolCalls(selectedId),
          listDropInsightReports(selectedId),
          getDropInsightDiagnosis(selectedId),
        ]);
        if (TERMINAL.has(session.status)) {
          await loadCases();
          return;
        }
        const hasDoneTask = tools.some((tool) =>
          tool.status === "COMPLETED" || (tool.task_id && ["TASK_CREATED", "RUNNING"].includes(tool.status)),
        );
        if (hasDoneTask && reportRows.length === 0) {
          advancing.current = true;
          try { await advanceDropInsightOrchestrator(selectedId); } finally { advancing.current = false; }
        }
      } catch {
        // Polling is best effort; the next interval retries.
      }
      loadSelectedDetail(selectedCase);
    }, 2500);
    return () => clearInterval(timer);
  }, [loadCases, loadSelectedDetail, readOnly, selectedCase, selectedId]);

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
      const created = await createDropInsightDiagnosis({ query: text, mode: "ASSISTED" });
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
    if (!selectedId || readOnly) return;
    setFeedbackSubmitting(true);
    try {
      const saved = await submitDropInsightFeedback(selectedId, payload);
      message.success(saved.revision_hypothesis_id ? "已保存纠正并开启下一轮诊断" : "反馈已保存");
      await loadSelectedDetail(selectedCase);
    } catch (error) {
      message.error(error?.message || "反馈提交失败");
    } finally { setFeedbackSubmitting(false); }
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

  return (
    <div className="ai-diagnosis-workspace">
      <Card size="small" className="ai-diagnosis-sidebar">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space align="center">
            <RobotOutlined style={{ fontSize: 18, color: "#722ed1" }} />
            <Title level={5} style={{ margin: 0 }}>AI 诊断</Title>
          </Space>
          <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
            在一个工作台中追踪问题、假设、证据与结论
          </Paragraph>
          <Segmented
            block
            options={[{ label: "诊断工作台", value: "workspace" }, { label: "方法与测试集", value: "evaluation" }]}
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
            <Card size="small" style={{ background: "#f7f9fc" }}>
              <Text strong>方法、测试集与质量门禁在主工作区展示</Text>
            </Card>
          )}
        </Space>
      </Card>

      <Card
        size="small"
        className="ai-diagnosis-main"
        style={{ display: "flex", flexDirection: "column" }}
        title={
          <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
            <Text strong ellipsis>
              {workspaceView === "evaluation" ? "AI 诊断方法与统一测试集" : (detail?.query || selectedCase?.query || "新诊断")}
            </Text>
            {workspaceView === "workspace" && selectedCase && (
              <Space wrap>
                {readOnly && <Text type="secondary">只读记录</Text>}
                <Segmented
                  size="small"
                  value={mode}
                  onChange={(value) => {
                    setMode(value);
                    try { window.localStorage.setItem("mini-drop-diagnosis-mode", value); } catch { /* ignore */ }
                  }}
                  options={[{ label: "简单", value: "simple" }, { label: "专家", value: "expert" }]}
                />
                {isExpert && <Button size="small" icon={<ProfileOutlined />} onClick={() => setDetailOpen(true)}>技术细节</Button>}
                {!readOnly && <Button size="small" icon={<SyncOutlined />} onClick={advanceNow}>继续推进</Button>}
              </Space>
            )}
          </Space>
        }
      >
        {workspaceView === "evaluation" ? (
          <div className="ai-diagnosis-thread"><EvalPanel /></div>
        ) : (
          <>
            <div className="ai-diagnosis-thread">
              {detail && (
                <Card size="small" title="调查进度" style={{ marginBottom: 12, background: "#fafcff" }}>
                  <Steps size="small" responsive current={diagnosisProcess.current} status={detail.status === "FAILED" ? "error" : "process"} items={diagnosisProcess.items} />
                </Card>
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
                />
              </Spin>
            </div>
            <Space.Compact style={{ marginTop: 12, width: "100%" }}>
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onPressEnter={startNew}
                placeholder="描述问题，例如：订单服务最近 5 分钟 CPU 飙高，请定位原因…"
                disabled={sending}
                size="large"
              />
              <Button type="primary" icon={<SendOutlined />} onClick={startNew} loading={sending} size="large">发送</Button>
            </Space.Compact>
          </>
        )}
      </Card>

      <TechnicalDetailDrawer
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        detail={detail}
        toolCalls={resources.toolCalls}
        evidence={resources.evidence}
        reports={resources.reports}
        events={resources.events}
      />
    </div>
  );
}
