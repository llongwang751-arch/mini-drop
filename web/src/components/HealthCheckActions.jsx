import { useState } from "react";
import { Alert, Button, Space } from "antd";
import { startManagedServiceDiagnosis } from "../api/client";

const CODES = new Set(["NORMAL_OBSERVED", "ANOMALY_OBSERVED", "INSUFFICIENT_OBSERVABILITY"]);

export function healthCheckResult(detail, resources = {}) {
  if (!["COMPLETED", "INSUFFICIENT_EVIDENCE"].includes(detail?.status)) return null;
  const events = Array.isArray(resources.events) ? resources.events : resources.events?.items || [];
  const event = events.find(e => e.event_type === "health_check.completed");
  const result = event?.payload_json || event?.payload;
  return result?.schema === "mini-drop.health-check.v1" && result.diagnosis_id === detail.diagnosis_id
    && result.causal_root_cause_verified === false && CODES.has(result.code)
    && Array.isArray(result.checked) && Array.isArray(result.anomalies) && Array.isArray(result.unmeasured)
    && Array.isArray(result.evidence_refs) ? result : null;
}

export default function HealthCheckActions({ detail, result, onOpenDiagnosis, onSelectService }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!result) return null;
  const anomalous = result.code === "ANOMALY_OBSERVED";
  const missing = result.code === "INSUFFICIENT_OBSERVABILITY";
  async function followUp() {
    if (!result.service_id) { onSelectService?.(); return; }
    setBusy(true); setError("");
    try {
      const created = await startManagedServiceDiagnosis(result.service_id, {
        query: anomalous ? `本次体检发现 ${result.anomalies.join("；")}，请定位具体性能路径与原因。` : "检查当前状态",
        mode: "AUTONOMOUS", health_check: !anomalous,
        follow_up_diagnosis_id: detail.diagnosis_id,
      });
      await onOpenDiagnosis?.(created.diagnosis_id || created.id);
    } catch (err) { setError(err?.message || "后续检查创建失败，请重试"); }
    finally { setBusy(false); }
  }
  return <Space direction="vertical" style={{ width: "100%", marginBottom: 16 }}>
    <Alert type={anomalous || missing ? "warning" : "success"} showIcon
      message={anomalous ? "体检已完成，可以继续调查异常原因" : missing ? "本次无法判断，需要补充采集数据" : "体检已完成，无需继续寻找已检查项的异常根因"}
      description={`未检查：${result.unmeasured.join("、")}。后续检查会重新确认当前进程并保存独立记录。`}
      action={<Button aria-label={anomalous ? "深入诊断" : missing ? "重新采集" : "再次检查"} loading={busy} onClick={followUp}>{anomalous ? "深入诊断" : missing ? "重新采集" : "再次检查"}</Button>} />
    {error && <Alert type="error" message={error} />}
  </Space>;
}
