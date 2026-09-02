/** Mini-Drop HTTP API 客户端。

所有 Web 请求通过此模块调用 Server REST API。
axios 拦截器统一处理错误码和响应格式。

认证方式：控制台把本地保存的访问凭据放入 ``X-API-Key``。Go API 是唯一
公开 HTTP 入口，不依赖 Python Worker 提供网页路由。
*/

import axios from "axios";

const API_KEY_STORAGE_KEY = "mini-drop-api-key";

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
  withCredentials: false,
});

api.interceptors.request.use((config) => {
  const token = getStoredApiKey();
  if (token) {
    config.headers["X-API-Key"] = token;
  }
  return config;
});

/** 后端英文/内部错误文案 → 中文提示（方案 §4.3：错误提示改为"发生了什么 + 下一步"）。 */
const ERROR_TRANSLATIONS = [
  [
    /Drop Insight diagnosis not found/i,
    "诊断会话不存在，可能已被删除或尚未创建",
  ],
  [/Drop Insight tool call not found/i, "工具调用不存在"],
  [/hypothesis does not belong to diagnosis/i, "该假设不属于当前诊断会话"],
  [/tool call is not awaiting approval/i, "该工具调用不在待审批状态，无法操作"],
  [/tool call not found/i, "工具调用不存在"],
  [/tool call is not executable/i, "该工具调用当前不可执行"],
  [
    /planner requires target\.agent_id and target\.pid/i,
    "缺少目标 Agent 或 PID，无法规划诊断路径",
  ],
  [/task not found/i, "采集任务不存在"],
  [
    /only DONE tasks can be imported as evidence/i,
    "只有成功完成的采集任务才能导入为证据，请等待任务完成",
  ],
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
      throw new Error(
        "访问认证失败：请在右上角填写 Mini-Drop API Key 并点击保存",
      );
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
    if (
      err.response?.status >= 500 &&
      /^Request failed with status code/i.test(detail)
    ) {
      throw new Error(
        "服务暂时不可用，请稍后重试；若持续失败，请检查 Server 与数据库状态",
      );
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

export async function saveApiKey(token) {
  setStoredApiKey((token || "").trim());
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

export function listTaskKinds() {
  return api.get("/task-kinds").then(itemsOf);
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
  return api.get(`/tasks/${taskId}/artifacts/${artifactType}/content`, {
    params,
  });
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
    try {
      filename = decodeURIComponent(encoded);
    } catch {
      filename = encoded;
    }
  }
  return { blob: response.data, filename };
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

/**
 * 连接 Python 诊断引擎的实时事件流。该路径经 Go 网关的 v2 白名单代理，
 * 与控制面任务/Agent 事件流分开，避免两个事件所有权边界互相覆盖。
 */
export function createDiagnosisEventSource(
  diagnosisId = "",
  afterSequence = 0,
) {
  if (!diagnosisId) return new EventSource("/api/v2/events/stream");
  const after = Math.max(0, Number(afterSequence) || 0);
  return new EventSource(
    `/api/v2/diagnoses/${encodeURIComponent(diagnosisId)}/events/stream?after=${after}`,
  );
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

export function getDropInsightExplorationTree(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/exploration-tree`);
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
  return api.post(
    `/v2/diagnoses/${diagnosisId}/tool-calls/${toolCallId}/decision`,
    payload,
  );
}

export function updateDropInsightToolCall(
  diagnosisId,
  toolCallId,
  argumentsObj,
) {
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

// ── Fix-verification (before/after) ────────────────────────────

export function verifyDiagnosisFix(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/fix/verify`, payload);
}

export function listFixVerifications(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/fix`).then(itemsOf);
}

export function listDropInsightFeedback(diagnosisId) {
  return api.get(`/v2/diagnoses/${diagnosisId}/feedback`).then(itemsOf);
}

export function submitDropInsightFeedback(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/feedback`, payload);
}

export function listDiagnosticSkills() {
  return api.get("/v2/diagnostic-skills").then(itemsOf);
}

export function getMentorComplexShowcase() {
  return api.get("/v2/showcases/mentor-complex");
}

export function getDiagnosticSkill(skillId) {
  return api.get(`/v2/diagnostic-skills/${encodeURIComponent(skillId)}`);
}

export function createDiagnosticSkillCandidate(diagnosisId) {
  return api.post(
    `/v2/diagnoses/${encodeURIComponent(diagnosisId)}/diagnostic-skills/candidate`,
  );
}

export function listDiagnosticSkillActivations(diagnosisId) {
  return api
    .get(
      `/v2/diagnoses/${encodeURIComponent(diagnosisId)}/diagnostic-skill-activations`,
    )
    .then(itemsOf);
}

export function evaluateDiagnosticSkill(skillId) {
  return api.post(
    `/v2/diagnostic-skills/${encodeURIComponent(skillId)}/evaluate`,
  );
}

export function publishDiagnosticSkill(skillId) {
  return api.post(
    `/v2/diagnostic-skills/${encodeURIComponent(skillId)}/publish`,
  );
}

export function quarantineDiagnosticSkill(skillId, reason) {
  return api.post(
    `/v2/diagnostic-skills/${encodeURIComponent(skillId)}/quarantine`,
    { reason },
  );
}

export function rollbackDiagnosticSkill(skillId) {
  return api.post(
    `/v2/diagnostic-skills/${encodeURIComponent(skillId)}/rollback`,
  );
}

export function getDropInsightTargetCandidates(diagnosisId) {
  return api.get(
    `/v2/diagnoses/${encodeURIComponent(diagnosisId)}/target-candidates`,
  );
}

export function clarifyDropInsightDiagnosis(diagnosisId, payload) {
  return api.post(`/v2/diagnoses/${diagnosisId}/clarify`, payload);
}

export function listTopProcesses(agentId, limit = 20) {
  return api
    .get("/top-processes", { params: { agent_id: agentId, limit } })
    .then(itemsOf);
}
