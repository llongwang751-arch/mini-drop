/** Mini-Drop HTTP API 客户端。

所有 Web 请求通过此模块调用 Server REST API。
axios 拦截器统一处理错误码和响应格式。

认证方式（按优先级）:
  1. HttpOnly cookie (mini_drop_api_key) — 首选，XSS 无法窃取
  2. localStorage Bearer token — 兼容旧版
  3. X-API-Key header — 兼容直接调用
*/

import axios from "axios";

const API_KEY_STORAGE_KEY = "mini-drop-api-key";

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
  withCredentials: true,  // 发送 HttpOnly cookie
});

api.interceptors.request.use((config) => {
  // cookie 会自动携带，不再需要手动设置 Authorization header
  // 但保留兼容：如果 cookie 不可用，fallback 到 localStorage
  const token = getStoredApiKey();
  if (token) {
    config.headers["X-API-Key"] = token;
  }
  return config;
});

/** 后端英文/内部错误文案 → 中文提示（方案 §4.3：错误提示改为"发生了什么 + 下一步"）。 */
const ERROR_TRANSLATIONS = [
  [/Drop Insight diagnosis not found/i, "诊断会话不存在，可能已被删除或尚未创建"],
  [/Drop Insight tool call not found/i, "工具调用不存在"],
  [/hypothesis does not belong to diagnosis/i, "该假设不属于当前诊断会话"],
  [/tool call is not awaiting approval/i, "该工具调用不在待审批状态，无法操作"],
  [/tool call not found/i, "工具调用不存在"],
  [/tool call is not executable/i, "该工具调用当前不可执行"],
  [/planner requires target\.agent_id and target\.pid/i, "缺少目标 Agent 或 PID，无法规划诊断路径"],
  [/task not found/i, "采集任务不存在"],
  [/only DONE tasks can be imported as evidence/i, "只有成功完成的采集任务才能导入为证据，请等待任务完成"],
  [/task has no artifacts/i, "该采集任务没有任何产物"],
  [/diagnosis version conflict/i, "诊断状态已变化，请刷新后重试"],
  [/diagnosis session CAS conflict/i, "诊断状态已变化，请刷新后重试"],
  [/end must be later than start/i, "结束时间必须晚于开始时间"],
  [/JSON Pointer must start with/i, "证据引用格式不正确"],
  [/diagnostic case not found/i, "诊断案例不存在"],
  [/principal role is not permitted/i, "当前账号角色无权执行此操作"],
  [/timeout of \d+ms exceeded/i, "请求超时，请稍后重试"],
  [/network error/i, "网络连接失败，请检查服务是否可达"],
];

function translateError(detail) {
  for (const [pattern, message] of ERROR_TRANSLATIONS) {
    if (pattern.test(detail)) return message;
  }
  return detail;
}

/** 响应拦截：统一提取 data 字段，简化调用方代码 */
api.interceptors.response.use(
  (resp) => {
    const body = resp.data;
    if (body.code === 0) return body.data;
    throw new Error(body.message || "未知错误");
  },
  (err) => {
    if (err.response?.status === 401) {
      throw new Error("访问认证失败：请在右上角填写 Mini-Drop API Key 并点击保存");
    }
    // Pydantic validation errors come back as `detail: [...]` (a list of
    // objects); stringify any non-string detail so callers never see
    // "[object Object]".
    let detail = err.response?.data?.detail ?? err.message;
    if (typeof detail !== "string") {
      try {
        detail = JSON.stringify(detail);
      } catch {
        detail = String(detail);
      }
    }
    throw new Error(translateError(detail));
  },
);

// ── 通用 ────────────────────────────────────────────────────────

export function getStoredApiKey() {
  try {
    return window.localStorage.getItem(API_KEY_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function setStoredApiKey(token) {
  try {
    const normalized = (token || "").trim();
    if (normalized) {
      window.localStorage.setItem(API_KEY_STORAGE_KEY, normalized);
    } else {
      window.localStorage.removeItem(API_KEY_STORAGE_KEY);
    }
  } catch {
    // Ignore unavailable localStorage in restricted browser contexts.
  }
}

/** 通过 HttpOnly cookie 设置 API Key（比 localStorage 更安全，XSS 无法读取）。*/
export async function setCookieApiKey(token) {
  await axios.post("/api/auth/set-cookie", { api_key: token });
}

/** 清除 HttpOnly cookie。*/
export async function clearCookieApiKey() {
  await axios.post("/api/auth/clear-cookie");
}

/** 统一设置 API Key：优先 HttpOnly cookie，同时更新 localStorage 作为降级。*/
export async function saveApiKey(token) {
  const trimmed = (token || "").trim();
  setStoredApiKey(trimmed);  // 降级方案
  if (trimmed) {
    try {
      await setCookieApiKey(trimmed);
    } catch {
      // cookie 设置失败时不影响 localStorage 降级
      console.warn("HttpOnly cookie 设置失败，使用 localStorage 降级方案");
    }
  } else {
    try {
      await clearCookieApiKey();
    } catch {
      // ignore
    }
  }
}

export function healthz() {
  return api.get("/healthz");
}

function itemsOf(value) {
  if (Array.isArray(value)) return value;
  return value?.items || [];
}

// ── Agent ────────────────────────────────────────────────────────

export function listAgents() {
  return api.get("/agents").then(itemsOf);
}

export function listAuditLogs() {
  return api.get("/audit-logs").then(itemsOf);
}

// ── 任务 ────────────────────────────────────────────────────────

export function createTask(payload) {
  return api.post("/tasks", payload);
}

export function listTasks(params = {}) {
  return api.get("/tasks", { params }).then(itemsOf);
}

export function getTask(taskId) {
  return api.get(`/tasks/${taskId}`);
}

export function deleteTask(taskId) {
  return api.delete(`/tasks/${taskId}`);
}

export function cancelTask(taskId, reason = "用户在控制台主动停止任务") {
  return api.post(`/tasks/${taskId}/cancel`, { reason });
}

export function getTaskEvents(taskId) {
  return api.get(`/tasks/${taskId}/events`);
}

export function getTaskAttempts(taskId) {
  return api.get(`/tasks/${taskId}/attempts`);
}

export function getTaskArtifacts(taskId) {
  return api.get(`/tasks/${taskId}/artifacts`);
}

export function getTaskArtifactContent(taskId, artifactType, params = {}) {
  return api.get(`/tasks/${taskId}/artifacts/${artifactType}/content`, { params });
}

export async function downloadTaskArtifact(taskId, artifactType, params = {}) {
  const token = getStoredApiKey();
  const response = await axios.get(
    `/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactType)}/download`,
    {
      params,
      responseType: "blob",
      withCredentials: true,
      headers: token ? { "X-API-Key": token } : {},
    },
  );
  const disposition = response.headers["content-disposition"] || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  let filename = `${artifactType}.bin`;
  if (encoded) {
    try { filename = decodeURIComponent(encoded); } catch { filename = encoded; }
  }
  return { blob: response.data, filename };
}

export function triggerDiagnose(taskId) {
  return api.post(`/tasks/${taskId}/diagnose`);
}

export function listTaskDiagnoses(taskId) {
  return api.get(`/tasks/${taskId}/diagnoses`);
}

export function getDiagnosis(diagnosisId) {
  return api.get(`/diagnoses/${diagnosisId}`);
}

export function submitDiagnosisFeedback(diagnosisId, payload) {
  return api.post(`/diagnoses/${diagnosisId}/feedback`, payload);
}

// ── AI 集群诊断会话 ──────────────────────────────────────────────

export function createDiagnosisSession(payload) {
  return api.post("/v1/diagnoses", payload);
}

export function listDiagnosisSessions(params = {}) {
  return api.get("/v1/diagnoses", { params }).then(itemsOf);
}

export function listContinuousDiagnosisTriggers(params = {}) {
  return api
    .get("/v1/continuous-diagnosis-triggers", { params })
    .then(itemsOf);
}

export function getDiagnosisSession(diagnosisId) {
  return api.get(`/v1/diagnoses/${diagnosisId}`);
}

export function approveDiagnosisProbe(diagnosisId, payload) {
  return api.post(`/v1/diagnoses/${diagnosisId}/approvals`, payload);
}

export function listProbeDefinitions() {
  return api.get("/v1/probes");
}

// ── NLP 自然语言采集 ────────────────────────────────────────────

export function nlpParse(query) {
  return api.post("/nlp/parse", { query });
}

export function nlpSummarize(taskId) {
  return api.post("/nlp/summarize", { task_id: taskId });
}

// ── 配置 ──────────────────────────────────────────────────────────

export function getAIConfig() {
  return api.get("/ai-config");
}

export function runAIValidation() {
  return api.post("/ai-validation/runs", {}, { timeout: 180000 });
}

export function getCurrentUser() {
  return api.get("/me");
}

// ── SSE 事件 ──────────────────────────────────────────────────────

/**
 * 创建 SSE EventSource 连接。
 * @param {string} [since] - ISO 时间戳，只获取该时间之后的事件
 * @returns {EventSource}
 */
export function createEventSource(since = "") {
  const params = since ? `?since=${encodeURIComponent(since)}` : "";
  return new EventSource(`/api/events/stream${params}`);
}

// ── Prometheus 指标 ───────────────────────────────────────────────

export function getMetrics() {
  return api.get("/metrics");
}

// ── Drop Insight v2 ──────────────────────────────────────────────

export function createDropInsightDiagnosis(payload) {
  return api.post("/v2/diagnoses", payload);
}

export function deleteDropInsightDiagnosis(diagnosisId) {
  return api.delete(`/v2/diagnoses/${diagnosisId}`);
}

export function listDropInsightDiagnoses() {
  return api.get("/v2/diagnoses").then(itemsOf);
}

export function getDropInsightDiagnosis(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}`);
}

export function listDropInsightEvents(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/events`);
}

export function createDropInsightHypothesis(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/hypotheses`, payload);
}

export function listDropInsightHypotheses(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/hypotheses`).then(itemsOf);
}

export function listDropInsightEvidence(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/evidence`).then(itemsOf);
}

export function importDropInsightTaskEvidence(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/evidence/import-task`, payload);
}

export function generateDropInsightReport(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/reports`, payload);
}

export function listDropInsightReports(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/reports`).then(itemsOf);
}

export function previewDropInsightToolCall(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/tool-calls/preview`, payload);
}

export function requestDropInsightToolCall(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/tool-calls`, payload);
}

export function listDropInsightToolCalls(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/tool-calls`).then(itemsOf);
}

export function decideDropInsightToolCall(diagnosisId, toolCallId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/tool-calls/${toolCallId}/decision`, payload);
}

export function updateDropInsightToolCall(diagnosisId, toolCallId, argumentsObj) {
  return api.put(`/v2/diagnoses/${diagnosisId}/tool-calls/${toolCallId}`, {
    arguments: argumentsObj,
  });
}

export function runDropInsightPlanner(diagnosisId) {
  return api.post(`/v2/diagnoses/${diagnosisId}/planner/run`, {});
}

export function getDropInsightBudget(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/budget`);
}

export function advanceDropInsightOrchestrator(diagnosisId) {
  return api.post(`/v2/diagnoses/${diagnosisId}/orchestrator/advance`);
}

// ── Schedule / Cron ────────────────────────────────────────────

export function listSchedules() {
  return api.get("/schedules").then(itemsOf);
}

export function createSchedule(payload) {
  return api.post("/schedules", payload);
}

export function updateSchedule(id, payload) {
  return api.put(`/schedules/${id}`, payload);
}

export function deleteSchedule(id) {
  return api.delete(`/schedules/${id}`);
}

export function triggerSchedule(id) {
  return api.post(`/schedules/${id}/trigger`);
}

export function listScheduleRecords(id) {
  return api.get(`/schedules/${id}/records`).then(itemsOf);
}

// ── Composite Task / DAG ───────────────────────────────────────

export function listCompositeTasks() {
  return api.get("/composite-tasks").then(itemsOf);
}

export function createCompositeTask(payload) {
  return api.post("/composite-tasks", payload);
}

export function getCompositeTask(id) {
  return api.get(`/composite-tasks/${id}`);
}

export function aggregateCompositeTask(id) {
  return api.post(`/composite-tasks/${id}/aggregate`);
}

export function cancelCompositeTask(id) {
  return api.post(`/composite-tasks/${id}/cancel`);
}

// ── Fix-verification (before/after) ────────────────────────────

export function verifyDiagnosisFix(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/fix/verify`, payload);
}

export function listFixVerifications(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/fix`).then(itemsOf);
}

// ── 统一诊断视图（/diagnostic-cases）──────────────────────────

export function listDiagnosticCases(params = {}) {
  return api.get("/diagnostic-cases", { params }).then(itemsOf);
}

export function listDiagnosticCasesPage(params = {}) {
  return api.get("/diagnostic-cases", { params });
}

// ── 评测闭环（方案 §9）──────────────────────────────────────

export function getDiagnosisEvalCatalog() {
  return api.get("/v1/diagnosis-evaluations/catalog");
}

export function getExternalDiagnosisBenchmark() {
  return api.get("/v1/diagnosis-evaluations/external");
}

export function getExternalDiagnosisBenchmarkCase(caseId) {
  return api.get(`/v1/diagnosis-evaluations/external/cases/${encodeURIComponent(caseId)}`);
}

export function getRealWorldBenchmarkCatalog() {
  return api.get("/v1/real-world-benchmarks/catalog");
}

export function startRealWorldBenchmark(caseId) {
  return api.post("/v1/real-world-benchmarks/runs", { case_id: caseId });
}

export function getRealWorldBenchmarkRun(runId) {
  return api.get(`/v1/real-world-benchmarks/runs/${encodeURIComponent(runId)}`);
}

export function listDropInsightFeedback(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/feedback`).then(itemsOf);
}

export function submitDropInsightFeedback(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/feedback`, payload);
}

export function getDiagnosisEvalPlan() {
  return api.get("/v1/diagnosis-evaluations/plan");
}

export function runDiagnosisEvalGolden() {
  return api.get("/v1/diagnosis-evaluations/golden", { timeout: 300000 });
}

export function startDiagnosisEvalGoldenRun() {
  return api.post("/v1/diagnosis-evaluations/golden-runs");
}

export function getDiagnosisEvalGoldenRun(runId) {
  return api.get(`/v1/diagnosis-evaluations/golden-runs/${encodeURIComponent(runId)}`);
}

export function listDiagnosisCampaignScenarios() {
  return api.get("/v1/diagnosis-campaigns/scenarios").then(itemsOf);
}

export function startDiagnosisCampaign(scenarioId = "LIVE-CPU-001") {
  return api.post("/v1/diagnosis-campaigns/runs", { scenario_id: scenarioId });
}

export function getDiagnosisCampaign(runId) {
  return api.get(`/v1/diagnosis-campaigns/runs/${encodeURIComponent(runId)}`);
}

export function getDiagnosticCase(caseId) {
  return api.get(`/diagnostic-cases/${encodeURIComponent(caseId)}`);
}

export function clarifyDropInsightDiagnosis(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/clarify`, payload);
}

export function listTopProcesses(limit = 20) {
  return api.get("/top-processes", { params: { limit } }).then(itemsOf);
}
