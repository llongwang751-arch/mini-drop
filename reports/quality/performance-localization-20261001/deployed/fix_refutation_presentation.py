from pathlib import Path
import os

stage=Path(__file__).resolve().parent

def edit(name,changes):
 p=Path(name);before=p.read_text(encoding='utf-8');after=before
 for old,new in changes:
  assert old in after,(name,old[:70])
  after=after.replace(old,new,1)
 backup=stage/('old-'+p.name)
 if not backup.exists():backup.write_text(before,encoding='utf-8')
 pending=stage/('pending-'+p.name);pending.write_text(after,encoding='utf-8')
 os.replace(pending,p)

edit('web/src/utils/reportPresentation.js',[
 ('export function isLocalizedReport(report) {', '''// A measured counterexample is useful even when the causal report is inconclusive.
export function isRefutedObservationReport(report) {
  const v = report?.verification;
  const observation = v?.observation_verification;
  const criteria = observation?.criteria;
  const refs = observation?.evidence_refs;
  return ["INSUFFICIENT_EVIDENCE", "FALSIFIED", "REJECTED"].includes(verificationStatus(report))
    && v.claim_scope === "BOUNDED_OBSERVATION" && v.causal_root_cause_verified === false
    && observation?.schema_version === "performance-observation-verification.v1"
    && observation.status === "REFUTED" && observation.checked_ratio === 1
    && observation.claim_scope === "BOUNDED_OBSERVATION" && observation.causal_root_cause_verified === false
    && Array.isArray(criteria) && criteria.length > 0
    && criteria.every(c => c.checked === true && typeof c.matches === "boolean"
      && Number.isFinite(c.measurement?.value))
    && criteria.some(c => c.kind === "falsification" && c.matches === true)
    && Array.isArray(refs) && refs.length > 0
    && refs.every(ref => report.counter_evidence_refs?.includes(ref));
}

export function observationMeasurementText(report) {
  if (!isRefutedObservationReport(report)) return "";
  const values = new Map();
  for (const c of report.verification.observation_verification.criteria) {
    const m = c.measurement;
    const label = { average_latency_ms: "平均耗时(ms)", operation_count_delta: "新增成功操作(次)" }[m.field]
      || `${m.signal}.${m.field}`;
    values.set(`${m.signal}.${m.field}`, `${label}=${m.value}`);
  }
  return [...values.values()].join("；");
}

export function isLocalizedReport(report) {'''),
 ('  if (isLocalizedReport(report)) return "性能路径已定位，根因仍待确认";',
  '  if (isLocalizedReport(report)) return "性能路径已定位，根因仍待确认";\n  if (isRefutedObservationReport(report)) return "已测量，异常假设被反驳";'),
 ('const score = item => isCausalRootReport(item) ? 4 : (rank[status(item)] || 0);',
  'const score = item => isCausalRootReport(item) ? 4 : isRefutedObservationReport(item) ? 1.5 : (rank[status(item)] || 0);')])

edit('web/src/components/ObservabilityOverview.jsx',[
 ('isCausalRootReport, isObservationReport, isLocalizedReport',
  'isCausalRootReport, isObservationReport, isLocalizedReport, isRefutedObservationReport, observationMeasurementText'),
 ('  const metricEvidence = evidence.filter(isSystemMetrics).at(-1) || null;', '''  const target = detail.target || {};
  const metricCandidates = evidence.filter(isSystemMetrics);
  const metricEvidence = metricCandidates.filter(item =>
    assessObservationWindow(item, target).code !== "INSUFFICIENT_OBSERVABILITY").at(-1)
    || metricCandidates.at(-1) || null;'''),
 ('  const target = detail.target || {};\n  const binding = target.process_binding || {};',
  '  const binding = target.process_binding || {};'),
 ('  const localized = reports.find(isLocalizedReport);',
  '  const localized = reports.find(isLocalizedReport);\n  const refuted = reports.find(isRefutedObservationReport);'),
 ('  } else if (COMPLETED_WINDOWS.has(status) && metricEvidence) {', '''  } else if (COMPLETED_WINDOWS.has(status) && refuted && windowAssessment.code !== "ANOMALY_OBSERVED") {
    assessment = {
      code: "OBSERVATION_REFUTED",
      title: "本次观测未发现该性能异常",
      detail: `${observationMeasurementText(refuted)}。完整数值计划已检查，测量反驳了该异常假设；仅限本报告目标与窗口，不代表全部业务正常。`,
    };
  } else if (COMPLETED_WINDOWS.has(status) && metricEvidence) {'''),
 ('{healthyWindow ? "检查结果：正常" : assessment.code === "BOTTLENECK_LOCALIZED"',
  '{healthyWindow ? "检查结果：正常" : assessment.code === "OBSERVATION_REFUTED" ? "异常假设已反驳" : assessment.code === "BOTTLENECK_LOCALIZED"')])

edit('web/src/components/ConclusionCard.jsx',[
 ('  isLocalizedReport,', '  isLocalizedReport,\n  isRefutedObservationReport,\n  observationMeasurementText,'),
 ('        {isLocalizedReport(report) && <Text strong>',
  '        {isRefutedObservationReport(report) && <Text strong>本次测量：{observationMeasurementText(report)}。该异常假设被反证，不代表全部业务正常。</Text>}\n        {isLocalizedReport(report) && <Text strong>')])
edit('web/src/utils/observationAssessment.js',[
 ('["ACCEPT_SUPPORT", "ACCEPT_LIMITED", "ACCEPT_NEUTRAL"]',
  '["ACCEPT_SUPPORT", "ACCEPT_LIMITED", "ACCEPT_NEUTRAL", "ACCEPT_COUNTER"]')])
print('Fixed refutation ranking, scope, trusted counter observations and complete-window selection.')
