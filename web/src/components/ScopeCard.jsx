import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Col, Form, Input, Row, Select, Space, Typography, message } from "antd";
import { QuestionCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { getDropInsightTargetCandidates } from "../api/client";

const { Text } = Typography;
const AUTHORITATIVE_STATUSES = new Set(["READY", "AMBIGUOUS"]);
const SERVER_RESOLVED_QUESTIONS = new Set(["target.agent_id", "target.pid"]);

const STATUS_MESSAGES = {
  EMPTY: { type: "warning", message: "未发现可用诊断目标，请确认采集 Agent 和目标进程正在运行。" },
  STALE: { type: "warning", message: "目标候选信息已过期，请重新发现后再提交。" },
  UNAVAILABLE: { type: "error", message: "目标发现服务暂不可用，请稍后重试。" },
  TRUNCATED: { type: "warning", message: "目标候选结果不完整，无法安全确认目标，请缩小范围后重试。" },
  INVALIDATED: { type: "error", message: "先前选择的目标已失效，请从最新候选中重新选择。" },
};

function localDateTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function defaultWindow() {
  const end = new Date();
  return { start: localDateTime(new Date(end.getTime() - 5 * 60_000)), end: localDateTime(end) };
}

function candidateLabel(candidate) {
  const process = candidate.process || candidate.service || "未知进程";
  const instance = candidate.instance ? ` · ${candidate.instance}` : "";
  return `${process}${instance} · 安全目标 ${candidate.binding_id}`;
}

export default function ScopeCard({
  diagnosisId,
  diagnosisVersion,
  questions,
  onClarify,
  submitting,
  initialTarget = {},
  initialTimeRange = {},
  draftKey = diagnosisId || "new",
}) {
  const [form] = Form.useForm();
  const [candidates, setCandidates] = useState([]);
  const [discoveryId, setDiscoveryId] = useState(null);
  const [discoveryStatus, setDiscoveryStatus] = useState("LOADING");
  const [reason, setReason] = useState(null);
  const [selectedBindingId, setSelectedBindingId] = useState(undefined);
  const requestRef = useRef(0);
  const editingRef = useRef(false);
  const pendingDraftRef = useRef(null);
  const visibleQuestions = useMemo(
    () => (questions || []).filter((item) => !SERVER_RESOLVED_QUESTIONS.has(item.question_id)),
    [questions],
  );
  const required = useMemo(() => new Set(visibleQuestions.map((item) => item.question_id)), [visibleQuestions]);
  const draftStorageKey = `mini-drop-scope-draft:${diagnosisId || draftKey}`;
  const eligibleCandidates = useMemo(() => candidates.filter((item) => item.eligible), [candidates]);
  const selectedCandidate = useMemo(
    () => candidates.find((item) => item.binding_id === selectedBindingId && item.eligible),
    [candidates, selectedBindingId],
  );
  const authoritative = AUTHORITATIVE_STATUSES.has(discoveryStatus);
  const canSubmit = authoritative && Boolean(selectedCandidate) && diagnosisVersion != null;

  function removeDraft() {
    try {
      window.sessionStorage.removeItem(draftStorageKey);
    } catch {
      // Storage availability must not weaken target validation.
    }
  }

  function persistDraft(values, bindingId = selectedBindingId) {
    if (!discoveryId) return;
    const draft = {
      discovery_id: discoveryId,
      binding_id: bindingId || null,
      service: values.service || "",
      environment: values.environment || "",
      start: values.start || "",
      end: values.end || "",
    };
    try {
      window.sessionStorage.setItem(draftStorageKey, JSON.stringify(draft));
    } catch {
      // Keep the current in-memory values when storage is unavailable.
    }
  }

  useEffect(() => {
    editingRef.current = false;
    pendingDraftRef.current = null;
    setCandidates([]);
    setDiscoveryId(null);
    setDiscoveryStatus("LOADING");
    setReason(null);
    setSelectedBindingId(undefined);
    const range = defaultWindow();
    form.setFieldsValue({
      service: initialTarget.service || "",
      environment: initialTarget.environment || "",
      binding_id: undefined,
      start: initialTimeRange.start ? localDateTime(initialTimeRange.start) : range.start,
      end: initialTimeRange.end ? localDateTime(initialTimeRange.end) : range.end,
    });

    try {
      const draft = JSON.parse(window.sessionStorage.getItem(draftStorageKey) || "null");
      if (draft?.discovery_id && draft.binding_id) {
        pendingDraftRef.current = draft;
      } else if (draft) {
        removeDraft();
      }
    } catch {
      removeDraft();
    }

    const requestId = ++requestRef.current;
    getDropInsightTargetCandidates(diagnosisId)
      .then((result) => applyDiscovery(result, requestId, diagnosisId, false))
      .catch((error) => {
        if (requestId !== requestRef.current) return;
        setDiscoveryStatus("UNAVAILABLE");
        setReason(error?.message || null);
      });
    return () => {
      requestRef.current += 1;
    };
  }, [diagnosisId, draftStorageKey]);

  function applyDiscovery(result, requestId, requestedDiagnosisId, isRefresh) {
    if (requestId !== requestRef.current || requestedDiagnosisId !== diagnosisId) return;
    if (!result || result.diagnosis_id !== diagnosisId) {
      setCandidates([]);
      setDiscoveryId(null);
      setDiscoveryStatus("UNAVAILABLE");
      setReason("服务返回了不属于当前诊断的候选结果");
      return;
    }

    const nextCandidates = Array.isArray(result.candidates) ? result.candidates : [];
    const nextStatus = STATUS_MESSAGES[result.status] || AUTHORITATIVE_STATUSES.has(result.status)
      ? result.status
      : "UNAVAILABLE";
    const currentBinding = form.getFieldValue("binding_id");
    const currentStillEligible = nextCandidates.some(
      (item) => item.binding_id === currentBinding && item.eligible,
    );

    setCandidates(nextCandidates);
    setDiscoveryId(result.discovery_id || null);
    setReason(result.reason || null);

    if (isRefresh && currentBinding && !currentStillEligible) {
      form.setFieldValue("binding_id", undefined);
      setSelectedBindingId(undefined);
      setDiscoveryStatus("INVALIDATED");
      removeDraft();
      return;
    }

    let nextBinding = currentStillEligible ? currentBinding : undefined;
    const draft = pendingDraftRef.current;
    if (!editingRef.current && draft) {
      const draftCandidate = nextCandidates.find(
        (item) => item.binding_id === draft.binding_id && item.eligible,
      );
      if (draft.discovery_id === result.discovery_id && draftCandidate) {
        nextBinding = draft.binding_id;
        form.setFieldsValue({
          service: draft.service || "",
          environment: draft.environment || "",
          binding_id: nextBinding,
          start: draft.start || form.getFieldValue("start"),
          end: draft.end || form.getFieldValue("end"),
        });
      } else {
        removeDraft();
      }
      pendingDraftRef.current = null;
    }

    const nextEligible = nextCandidates.filter((item) => item.eligible);
    if (!nextBinding && AUTHORITATIVE_STATUSES.has(nextStatus) && nextEligible.length === 1) {
      nextBinding = nextEligible[0].binding_id;
      form.setFieldValue("binding_id", nextBinding);
    }
    if (!AUTHORITATIVE_STATUSES.has(nextStatus)) {
      nextBinding = undefined;
      form.setFieldValue("binding_id", undefined);
    }
    setSelectedBindingId(nextBinding);
    setDiscoveryStatus(nextStatus);
  }

  function retryDiscovery() {
    const requestId = ++requestRef.current;
    setDiscoveryStatus("LOADING");
    setReason(null);
    getDropInsightTargetCandidates(diagnosisId)
      .then((result) => applyDiscovery(result, requestId, diagnosisId, true))
      .catch((error) => {
        if (requestId !== requestRef.current) return;
        setDiscoveryStatus("UNAVAILABLE");
        setReason(error?.message || null);
      });
  }

  async function handleSubmit(values) {
    if (!canSubmit || !selectedCandidate) {
      message.error("请先从当前诊断的有效候选中确认目标");
      return;
    }
    const start = new Date(values.start);
    const end = new Date(values.end);
    if (end <= start) {
      message.error("结束时间必须晚于开始时间");
      return;
    }
    await onClarify({
      expected_version: diagnosisVersion,
      target: {
        service: values.service.trim(),
        environment: values.environment,
        binding_id: selectedCandidate.binding_id,
        discovery_id: discoveryId,
      },
      time_range: { start: start.toISOString(), end: end.toISOString(), timezone: "Asia/Shanghai" },
    });
    removeDraft();
    message.success("范围已确认，AI 开始生成可证伪假设和取证计划");
  }

  const rule = (id, label) => ({
    required: required.has(id) || ["target.service", "target.environment"].includes(id),
    message: `请选择或填写${label}`,
  });
  const statusMessage = STATUS_MESSAGES[discoveryStatus];
  const selectionMessage = authoritative && eligibleCandidates.length > 1
    ? "发现多个有效目标，请明确选择一个服务端签发的安全绑定。"
    : authoritative && eligibleCandidates.length === 1
      ? "已安全绑定唯一有效目标。"
      : authoritative
        ? "当前候选中没有可用目标，无法提交。"
        : null;

  return (
    <Card size="small" style={{ marginBottom: 12, background: "#fffaf0", borderColor: "#ffe7ba" }}>
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
          <Space><QuestionCircleOutlined style={{ color: "#fa8c16" }} /><Text strong>AI 需要确认诊断范围</Text></Space>
          <Button icon={<ReloadOutlined />} onClick={retryDiscovery} loading={discoveryStatus === "LOADING"}>重新发现目标</Button>
        </Space>
        <Alert type="info" showIcon message="目标由当前诊断的服务端发现结果确定" description="服务和环境用于理解业务；目标候选只暴露服务端签发的不可编辑绑定，不公开底层 Agent/PID 权限信息；时间窗用于保证证据与故障发生时间一致。" />
        {discoveryStatus === "LOADING" && <Alert type="info" showIcon message="正在发现当前诊断可用的安全目标…" />}
        {statusMessage && <Alert type={statusMessage.type} showIcon message={statusMessage.message} description={reason || undefined} />}
        {selectionMessage && <Alert type={eligibleCandidates.length > 0 ? "success" : "warning"} showIcon message={selectionMessage} description={reason || undefined} />}
        {diagnosisVersion == null && <Alert type="error" showIcon message="诊断版本不可用，请刷新诊断详情后重试。" />}
        {visibleQuestions.length > 0 && <ul style={{ margin: 0, paddingLeft: 20 }}>{visibleQuestions.map((q, i) => <li key={q.question_id || i}>{q.prompt}</li>)}</ul>}
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          onValuesChange={(changed, allValues) => {
            editingRef.current = true;
            if (Object.prototype.hasOwnProperty.call(changed, "binding_id")) {
              setSelectedBindingId(changed.binding_id);
              if (discoveryStatus === "INVALIDATED") {
                setDiscoveryStatus(eligibleCandidates.length > 1 ? "AMBIGUOUS" : "READY");
              }
              persistDraft(allValues, changed.binding_id);
            } else {
              persistDraft(allValues);
            }
          }}
          requiredMark
        >
          <Row gutter={12}>
            <Col xs={24} md={12} xl={8}><Form.Item name="service" label="服务" rules={[rule("target.service", "服务")]}><Input placeholder="例如 order-service" /></Form.Item></Col>
            <Col xs={24} md={12} xl={8}><Form.Item name="environment" label="环境" rules={[rule("target.environment", "环境")]}><Select placeholder="选择环境" options={["development", "staging", "production", "demo"].map((value) => ({ value, label: value }))} /></Form.Item></Col>
            <Col xs={24} md={24} xl={8}>
              <Form.Item name="binding_id" label="诊断目标" rules={[{ required: true, message: "请选择诊断目标" }]}>
                <Select
                  loading={discoveryStatus === "LOADING"}
                  disabled={!authoritative || eligibleCandidates.length === 0}
                  showSearch
                  optionFilterProp="label"
                  placeholder={eligibleCandidates.length ? "选择一个服务端候选目标" : "暂无可用目标"}
                  options={candidates.map((candidate) => ({
                    value: candidate.binding_id,
                    label: candidateLabel(candidate),
                    disabled: !candidate.eligible,
                    title: candidate.ineligible_reason || undefined,
                  }))}
                />
              </Form.Item>
            </Col>
            {selectedCandidate && (
              <Col span={24}>
                <Alert
                  type="info"
                  showIcon
                  message={`已选安全目标：${selectedCandidate.binding_id}`}
                  description={[selectedCandidate.process || selectedCandidate.service || "未知进程", selectedCandidate.instance].filter(Boolean).join(" · ")}
                />
              </Col>
            )}
            <Col xs={24} md={12}><Form.Item name="start" label="开始时间" rules={[{ required: true, message: "请选择开始时间" }]}><Input type="datetime-local" /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name="end" label="结束时间" rules={[{ required: true, message: "请选择结束时间" }]}><Input type="datetime-local" /></Form.Item></Col>
          </Row>
          <Button type="primary" htmlType="submit" loading={submitting} disabled={!canSubmit}>确认范围并开始取证</Button>
        </Form>
      </Space>
    </Card>
  );
}
