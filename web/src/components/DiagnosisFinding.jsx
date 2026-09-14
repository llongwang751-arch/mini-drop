import { Tag } from "antd";
import SafeMarkdown from "./SafeMarkdown";
import { chineseDiagnosticText } from "../utils/diagnosisDisplay";
import { reportConclusionText, reportConclusionTitle, selectBestReport, reportLimitations, reportNextActions, hasUnattributedHostIO } from "../utils/reportPresentation";

/** A concise projection of a persisted report; never a new model conclusion. */
export default function DiagnosisFinding({ reports = [], status }) {
  const report = selectBestReport(reports);
  if (!report) return null;
  const limitations = reportLimitations(report);
  const nextActions = reportNextActions(report);
  const verified = report.verification?.status === "VERIFIED";
  const finished = ["COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"].includes(status);
  return (
    <section className={`diagnosis-finding ${verified ? "is-verified" : "is-limited"}`} aria-label="当前诊断结论摘要">
      <div className="diagnosis-finding-heading">
        <h3>{reportConclusionTitle(report)}</h3>
        <Tag color={verified ? "green" : "gold"}>{finished ? "本会话报告摘要" : "调查仍在进行"}</Tag>
      </div>
      <SafeMarkdown>{reportConclusionText(report)}</SafeMarkdown>
      <div className="diagnosis-finding-context">
        <span>{hasUnattributedHostIO(report) ? "历史主机观察引用" : "引用支持证据"} {report.evidence_refs?.length || 0} 条</span>
        <span>引用反证 {report.counter_evidence_refs?.length || 0} 条</span>
        <span>完整报告与证据可在下方调查记录中查看</span>
      </div>
      <p>修复状态：报告本身不证明故障已解决，请查看下方修复复测记录。</p>
      {limitations.length > 0 && <p className="diagnosis-finding-limitations">
        <strong>尚未确认：</strong>{limitations.map((item) => chineseDiagnosticText(item)).join("；")}
      </p>}
      {nextActions.length > 0 && <p className="diagnosis-finding-next">
        <strong>下一步：</strong>{chineseDiagnosticText(nextActions[0])}
      </p>}
    </section>
  );
}
