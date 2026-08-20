import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
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
import { SendOutlined, RobotOutlined, SyncOutlined, ProfileOutlined } from "@ant-design/icons";
import ChatThread from "../components/ChatThread";
import SessionList from "../components/SessionList";
import HistoryList from "../components/HistoryList";
import EvalPanel from "../components/EvalPanel";
import TechnicalDetailDrawer from "../components/TechnicalDetailDrawer";
import {
  advanceDropInsightOrchestrator,
  clarifyDropInsightDiagnosis,
  createDropInsightDiagnosis,
  decideDropInsightToolCall,
  deleteDropInsightDiagnosis,
  getDropInsightBudget,
  getDropInsightDiagnosis,
  listDiagnosticCases,
  listDropInsightDiagnoses,
  listDropInsightEvidence,
  listDropInsightFeedback,
  listDropInsightEvents,
  listDropInsightHypotheses,
  listDropInsightReports,
  listDropInsightToolCalls,
  runDropInsightPlanner,
  updateDropInsightToolCall,
  submitDropInsightFeedback,
} from "../api/client";

const { Text, Title, Paragraph } = Typography;

const TERMINAL = new Set([
  "COMPLETED",
  "INSUFFICIENT_EVIDENCE",
  "FAILED",
  "CANCELLED",
]);

/**
 * 统一 AI 诊断（Codex 式对话）：
 * 左侧会话列表（+只读历史），中间对话线程，底部一个输入框。
 * 用户描述问题 → AI 自动规划/采集/推进 → 结论；高危采集需人工审批。
 */
export default function AIDiagnosis() {
  const [sessions, setSessions] = useState([]);
  const [cases, setCases] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [view, setView] = useState("诊断会话");
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);

  const [detail, setDetail] = useState(null);
  const [hypotheses, setHypotheses] = useState([]);
  const [toolCalls, setToolCalls] = useState([]);
  const [evidence, setEvidence] = useState([]);
  const [reports, setReports] = useState([]);
  const [events, setEvents] = useState([]);
  const [feedback, setFeedback] = useState([]);
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [loading, setLoading] = useState(false);
  const [clarifying, setClarifying] = useState(false);
  // 简单/专家模式（方案 §4.3）：简单模式隐藏技术细节与事件时间线，持久化。
  const [mode, setMode] = useState(() => {
    try {
      return window.localStorage.getItem("mini-drop-diagnosis-mode") === "expert" ? "expert" : "simple";
    } catch {
      return "simple";
    }
  });
  const isExpert = mode === "expert";
  const advancing = useRef(false);
  const [historyCase, setHistoryCase] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const refreshList = useCallback(async () => {
    try {
      setSessions(await listDropInsightDiagnoses());
    } catch {
      setSessions([]);
    }
  }, []);

  const refreshDetail = useCallback(async (id) => {
    if (!id) return;
    setLoading(true);
    try {
      const values = await Promise.all([
        getDropInsightDiagnosis(id),
        listDropInsightEvents(id),
        listDropInsightHypotheses(id),
        listDropInsightEvidence(id),
        listDropInsightReports(id),
        listDropInsightToolCalls(id),
        getDropInsightBudget(id),
        listDropInsightFeedback(id),
      ]);
      setDetail(values[0]);
      setEvents(values[1]);
      setHypotheses(values[2]);
      setEvidence(values[3]);
      setReports(values[4]);
      setToolCalls(values[5]);
      setFeedback(values[7]);
    } catch (err) {
      message.error(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const rows = await listDiagnosticCases();
      let hiddenIds = new Set();
      try {
        hiddenIds = new Set(JSON.parse(window.localStorage.getItem("mini-drop-hidden-history") || "[]"));
      } catch {
        hiddenIds = new Set();
      }
      setCases(rows.filter((item) => !hiddenIds.has(item.case_id || item.diagnosis_id || item.id)));
    } catch {
      setCases([]);
    }
  }, []);

  useEffect(() => {
    refreshList();
    loadHistory();
  }, [refreshList, loadHistory]);

  // 自动推进循环：有完成但未生成报告的采集任务时，自动 advance 会话。
  useEffect(() => {
    if (!selectedId) return undefined;
    const timer = setInterval(async () => {
      if (advancing.current) return;
      try {
        const tools = await listDropInsightToolCalls(selectedId);
        const reportRows = await listDropInsightReports(selectedId);
        const session = await getDropInsightDiagnosis(selectedId);
        if (TERMINAL.has(session.status)) {
          clearInterval(timer);
          return;
        }
        // 只要还有挂着任务的工具调用就持续推进：TASK_CREATED/RUNNING 都表示
        // 采集任务在途，COMPLETED 表示任务已 DONE 但可能尚未生成报告。漏掉
        // RUNNING 会让诊断在任务完成后永远停在本轮状态（界面一直显示采集执行中）。
        const hasDoneTask = tools.some(
          (t) =>
            t.status === "COMPLETED" ||
            (t.task_id && (t.status === "TASK_CREATED" || t.status === "RUNNING")),
        );
        if (hasDoneTask && reportRows.length === 0) {
          advancing.current = true;
          try {
            await advanceDropInsightOrchestrator(selectedId);
          } finally {
            advancing.current = false;
          }
        }
      } catch {
        /* 轮询失败忽略 */
      }
      refreshDetail(selectedId);
    }, 2500);
    return () => clearInterval(timer);
  }, [selectedId, refreshDetail]);

  function selectSession(id) {
    setSelectedId(id);
    setHistoryCase(null);
    refreshDetail(id);
  }

  async function handleDeleteSession(id, name) {
    Modal.confirm({
      title: `删除会话「${name || id}」？`,
      content: "删除后会从会话列表隐藏，但证据与审计仍保留可追溯。",
      okButtonProps: { danger: true },
      okText: "删除",
      cancelText: "取消",
      onOk: async () => {
        try {
          await deleteDropInsightDiagnosis(id);
          message.success("会话已删除");
          if (selectedId === id) {
            setSelectedId("");
            setHistoryCase(null);
            setDetail(null);
          }
          await refreshList();
        } catch (err) {
          message.error(err.message);
        }
      },
    });
  }

  async function handleDeleteHistory(item) {
    const id = item.case_id || item.diagnosis_id || item.id;
    const name = item.query || id;
    Modal.confirm({
      title: `删除历史「${name}」？`,
      content: "该记录会从诊断历史中移除；原始采集证据和审计记录继续保留。",
      okButtonProps: { danger: true },
      okText: "删除",
      cancelText: "取消",
      onOk: async () => {
        try {
          if (item.source === "drop_insight_v2" && item.diagnosis_id) {
            await deleteDropInsightDiagnosis(item.diagnosis_id);
            await refreshList();
          } else {
            let hiddenIds = [];
            try {
              hiddenIds = JSON.parse(window.localStorage.getItem("mini-drop-hidden-history") || "[]");
            } catch {
              hiddenIds = [];
            }
            window.localStorage.setItem(
              "mini-drop-hidden-history",
              JSON.stringify([...new Set([...hiddenIds, id])]),
            );
          }
          if (historyCase && (historyCase.case_id || historyCase.diagnosis_id || historyCase.id) === id) {
            setHistoryCase(null);
          }
          await loadHistory();
          message.success("历史诊断已删除");
        } catch (err) {
          message.error(err.message);
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
      const created = await createDropInsightDiagnosis({
        query: text,
        mode: "ASSISTED",
      });
      setQuery("");
      setSelectedId(created.diagnosis_id);
      setHistoryCase(null);
      await runDropInsightPlanner(created.diagnosis_id).catch(() => undefined);
      await Promise.all([refreshList(), refreshDetail(created.diagnosis_id)]);
    } catch (err) {
      message.error(err.message);
    } finally {
      setSending(false);
    }
  }

  async function decideTool(toolCallId, approved) {
    if (!selectedId) return;
    try {
      await decideDropInsightToolCall(selectedId, toolCallId, {
        approved,
        reason: approved ? "用户在 AI 诊断对话中审批通过" : "用户在 AI 诊断对话中拒绝",
      });
      await refreshDetail(selectedId);
    } catch (err) {
      message.error(err?.message || String(err));
    }
  }

  async function handleUpdateToolArgs(toolCallId, argumentsObj) {
    if (!selectedId) return;
    try {
      await updateDropInsightToolCall(selectedId, toolCallId, argumentsObj);
      await refreshDetail(selectedId);
    } catch (err) {
      message.error(err?.message || String(err));
      throw err;
    }
  }

  async function handleClarify(payload) {
    if (!selectedId) return;
    setClarifying(true);
    try {
      await clarifyDropInsightDiagnosis(selectedId, payload);
      await runDropInsightPlanner(selectedId).catch(() => undefined);
      await refreshDetail(selectedId);
    } catch (err) {
      message.error(err.message);
    } finally {
      setClarifying(false);
    }
  }

  async function advanceNow() {
    if (!selectedId || advancing.current) return;
    advancing.current = true;
    try {
      await advanceDropInsightOrchestrator(selectedId);
      await refreshDetail(selectedId);
    } catch (err) {
      message.error(err.message);
    } finally {
      advancing.current = false;
    }
  }

  async function handleSubmitFeedback(payload) {
    if (!selectedId) return;
    setFeedbackSubmitting(true);
    try {
      const saved = await submitDropInsightFeedback(selectedId, payload);
      message.success(
        saved.revision_hypothesis_id ? "已保存纠正并开启下一轮诊断" : "反馈已保存",
      );
      await refreshDetail(selectedId);
    } catch (err) {
      message.error(err.message);
      throw err;
    } finally {
      setFeedbackSubmitting(false);
    }
  }

  function openHistory(item) {
    setHistoryCase(item);
    setSelectedId("");
  }

  const activeQuery = useMemo(() => detail?.query || historyCase?.query || "", [detail, historyCase]);
  const diagnosisProcess = useMemo(() => {
    const hasScope = Boolean(detail?.target?.agent_id || detail?.agent_id || detail?.target?.pid || detail?.pid);
    const hasHypotheses = hypotheses.length > 0;
    const hasPlannedTools = toolCalls.length > 0;
    const hasEvidence = evidence.length > 0;
    const hasReport = reports.length > 0;
    let current = 0;
    if (hasScope) current = 1;
    if (hasHypotheses) current = 2;
    if (hasPlannedTools) current = 3;
    if (hasEvidence) current = 4;
    if (hasReport || TERMINAL.has(detail?.status)) current = 5;
    return {
      current,
      items: [
        { title: "理解问题", description: "识别服务、现象和时间窗" },
        { title: "确认范围", description: "Agent / PID / 上下游" },
        { title: "生成假设", description: "支持条件与可推翻条件" },
        { title: "决策树取证", description: "选采集器并经过权限门禁" },
        { title: "证据裁决", description: "引用证据、反证与置信度" },
        { title: "结论验证", description: "输出限制与修复后复测" },
      ],
    };
  }, [detail, hypotheses, toolCalls, evidence, reports]);

  return (
    <div style={{ display: "flex", gap: 20, minHeight: "calc(100vh - 140px)" }}>
      {/* 左栏：会话 / 历史 */}
      <Card size="small" style={{ width: 300, flexShrink: 0 }}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space align="center" style={{ width: "100%" }}>
            <RobotOutlined style={{ fontSize: 18, color: "#722ed1" }} />
            <Title level={5} style={{ margin: 0 }}>AI 诊断</Title>
          </Space>
          <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
            描述问题，AI 一步步定位异常并给出结论
          </Paragraph>
          <Segmented
            block
            options={["诊断会话", "诊断历史", "方法与测试集"]}
            value={view}
            onChange={(value) => {
              setView(value);
              if (value === "诊断历史") loadHistory();
            }}
          />
          {view === "诊断会话" ? (
            <SessionList
              sessions={sessions}
              selectedId={selectedId}
              onSelect={selectSession}
              onDelete={handleDeleteSession}
              onNew={() => {
                setSelectedId("");
                setHistoryCase(null);
                setDetail(null);
              }}
            />
          ) : view === "诊断历史" ? (
            <HistoryList cases={cases} onOpen={openHistory} onDelete={handleDeleteHistory} />
          ) : (
            <Card size="small" style={{ background: "#f7f9fc" }}>
              <Text strong>方法与测试集已在右侧展开</Text>
              <Paragraph type="secondary" style={{ fontSize: 12, margin: "6px 0 0" }}>
                左侧宽度保持不变；完整决策树、用例目录、来源和质量门禁在主工作区查看。
              </Paragraph>
            </Card>
          )}
        </Space>
      </Card>

      {/* 主区：对话线程 */}
      <Card
        size="small"
        style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}
        title={
          <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
            <Text strong ellipsis>
              {view === "方法与测试集" ? "AI 诊断方法与统一测试集" : (activeQuery || "新对话")}
            </Text>
            {view !== "方法与测试集" && <Space wrap>
              <Segmented
                size="small"
                value={mode}
                onChange={(value) => {
                  setMode(value);
                  try {
                    window.localStorage.setItem("mini-drop-diagnosis-mode", value);
                  } catch {
                    // ignore
                  }
                }}
                options={[
                  { label: "简单", value: "simple" },
                  { label: "专家", value: "expert" },
                ]}
              />
              {selectedId && (
                <>
                  {isExpert && (
                    <Button size="small" icon={<ProfileOutlined />} onClick={() => setDetailOpen(true)}>
                      技术细节
                    </Button>
                  )}
                  <Button size="small" icon={<SyncOutlined />} onClick={advanceNow}>
                    继续推进
                  </Button>
                </>
              )}
            </Space>}
          </Space>
        }
      >
        {view === "方法与测试集" ? (
          <div style={{ overflowY: "auto", maxHeight: "calc(100vh - 220px)", paddingRight: 8 }}>
            <EvalPanel />
          </div>
        ) : <>
          <div
            style={{
              flex: 1,
              overflowY: "auto",
              maxHeight: "calc(100vh - 300px)",
              paddingRight: 8,
            }}
          >
            {selectedId && detail && (
              <Card
                size="small"
                title="AI 正在怎样诊断"
                style={{ marginBottom: 12, background: "#fafcff" }}
              >
                <Steps
                  size="small"
                  responsive
                  current={diagnosisProcess.current}
                  status={detail.status === "FAILED" ? "error" : "process"}
                  items={diagnosisProcess.items}
                />
              </Card>
            )}
            <Spin spinning={loading && Boolean(selectedId)}>
              {historyCase ? (
                <ChatThread
                  detail={{ query: historyCase.query || historyCase.case_id, status: historyCase.canonical_status }}
                  hypotheses={[]}
                  toolCalls={[]}
                  evidence={[]}
                  reports={[]}
                  events={[]}
                  onApproveTool={() => undefined}
                  onRejectTool={() => undefined}
                />
              ) : (
                <ChatThread
                  detail={detail}
                  hypotheses={hypotheses}
                  toolCalls={toolCalls}
                  evidence={evidence}
                  reports={reports}
                  events={events}
                  mode={mode}
                  onApproveTool={(id) => decideTool(id, true)}
                  onRejectTool={(id) => decideTool(id, false)}
                  onUpdateToolArgs={handleUpdateToolArgs}
                  onClarify={handleClarify}
                  clarifying={clarifying}
                  feedback={feedback}
                  onSubmitFeedback={handleSubmitFeedback}
                  feedbackSubmitting={feedbackSubmitting}
                />
              )}
            </Spin>
          </div>

          <Space.Compact style={{ marginTop: 12, width: "100%" }}>
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onPressEnter={startNew}
            placeholder="描述问题，例如：订单服务最近 5 分钟 CPU 飙高，请定位原因…"
            disabled={sending}
            size="large"
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={startNew}
            loading={sending}
            size="large"
          >
            发送
          </Button>
          </Space.Compact>
        </>}
      </Card>

      <TechnicalDetailDrawer
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        detail={detail}
        toolCalls={toolCalls}
        evidence={evidence}
        reports={reports}
        events={events}
      />
    </div>
  );
}
