/**
 * 调查的最终报告可能来自复测任务，不能把它的 E 编号链接到原调查的规划记录。
 * 沿服务端复测关系寻找真实任务及诊断；只有取得对应轨迹后才生成页内出处链接。
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { List } from "../types";
import DiagnosticFacts, { parseDiagnosticFacts } from "./DiagnosticFacts";
import { ErrorPanel } from "./ui";

type DiagnosticTrace = {
  id: string;
  task_id: string;
  trace: { evidence_id: string; tool: string; observation: unknown }[];
};
type Verification = {
  id: string;
  candidate_task_id: string;
  candidate_diagnosis_id?: string;
};
type Report = {
  report_version?: number;
  facts?: unknown;
  hypotheses?: unknown;
};

export default function InvestigationDiagnosticEvidence({
  baselineTaskId,
  diagnosisId,
  candidateDiagnosisId,
  verificationId,
  report,
}: {
  baselineTaskId?: string;
  diagnosisId?: string;
  candidateDiagnosisId?: string;
  verificationId?: string;
  report?: Report;
}) {
  const verified = useQuery({
    queryKey: [
      "investigation-diagnostic-verification",
      baselineTaskId,
      verificationId,
    ],
    queryFn: ({ signal }) =>
      api<List<Verification>>(`/tasks/${baselineTaskId}/verifications`, {
        signal,
      }),
    enabled:
      !!report &&
      !!candidateDiagnosisId &&
      !!verificationId &&
      !!baselineTaskId,
  });
  const verification = verified.data?.items.find(
    (item) =>
      item.id === verificationId &&
      item.candidate_diagnosis_id === candidateDiagnosisId,
  );
  const taskId = candidateDiagnosisId
    ? verification?.candidate_task_id
    : baselineTaskId;
  const reportDiagnosisId = candidateDiagnosisId || diagnosisId;
  const evidence = useQuery({
    queryKey: ["investigation-diagnostic-evidence", taskId, reportDiagnosisId],
    queryFn: ({ signal }) =>
      api<List<DiagnosticTrace>>(`/tasks/${taskId}/diagnoses`, { signal }),
    enabled: !!report && !!taskId && !!reportDiagnosisId,
  });
  const diagnosis = evidence.data?.items.find(
    (item) => item.id === reportDiagnosisId && item.task_id === taskId,
  );
  const facts = parseDiagnosticFacts(report?.facts);
  const linked =
    !!diagnosis &&
    !!facts &&
    facts.every((fact) =>
      diagnosis.trace.some(
        (step) =>
          step.evidence_id === fact.evidence_id && step.tool === fact.tool,
      ),
    );
  if (!report) return null;
  return (
    <>
      <DiagnosticFacts
        reportVersion={report.report_version}
        facts={report.facts}
        hypotheses={report.hypotheses}
        runID={linked ? reportDiagnosisId : undefined}
      />
      {((candidateDiagnosisId && verified.isPending) ||
        (taskId && evidence.isPending)) && (
        <p role="status">正在读取报告对应的真实诊断轨迹…</p>
      )}
      {verified.error && <ErrorPanel error={verified.error} />}
      {evidence.error && <ErrorPanel error={evidence.error} />}
      {diagnosis ? (
        <section aria-label="报告对应的诊断工具证据">
          <h3>诊断 #{diagnosis.id} 的工具证据</h3>
          <p className="muted">
            以下观测属于任务 #{taskId}，与上方事实使用同一诊断编号。
          </p>
          <ol className="investigation-evidence">
            {diagnosis.trace.map((step) => (
              <li
                key={step.evidence_id}
                id={`diagnosis-${diagnosis.id}-${step.evidence_id}`}
              >
                <details>
                  <summary>
                    {step.evidence_id} · {step.tool}
                  </summary>
                  <pre>{JSON.stringify(step.observation, null, 2)}</pre>
                </details>
              </li>
            ))}
          </ol>
        </section>
      ) : evidence.isSuccess ||
        (candidateDiagnosisId && verified.isSuccess && !verification) ? (
        <p className="muted">
          当前未取得报告对应的诊断轨迹，出处编号以纯文本显示；可在原任务中核对授权与历史记录。
        </p>
      ) : null}
    </>
  );
}
