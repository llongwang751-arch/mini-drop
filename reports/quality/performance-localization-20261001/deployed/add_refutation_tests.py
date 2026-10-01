from pathlib import Path
import os

stage=Path(__file__).resolve().parent
changes={
 'web/src/utils/reportPresentation.test.js': ( 'describe("structured report scope", () => {', '''import ioRefutation from "../test/fixtures/performance-io-refutation.json";

describe("live I/O refutation presentation", () => {
  const refuted = ioRefutation.reports.find(r => r.verification.observation_verification.status === "REFUTED");
  it("keeps a fully measured refutation above a later inconclusive host I/O report", () => {
    expect(selectBestReport(ioRefutation.reports)).toEqual(refuted);
  });
  it("distinguishes a measured false hypothesis from missing evidence", () => {
    expect(reportConclusionTitle(refuted)).toBe("已测量，异常假设被反驳");
    expect(isCausalRootReport(refuted)).toBe(false);
  });
  it.each([
    { claim_scope: "CAUSAL_ROOT_CAUSE" }, { causal_root_cause_verified: true },
    { observation_verification: { ...refuted.verification.observation_verification, checked_ratio: 0.5 } },
    { observation_verification: { ...refuted.verification.observation_verification, criteria: [] } },
    { observation_verification: { ...refuted.verification.observation_verification, evidence_refs: ["unreferenced"] } },
  ])("does not label an incomplete or contradictory observation as refuted %j", change => {
    expect(reportConclusionTitle({ ...refuted, verification: { ...refuted.verification, ...change } })).not.toBe("已测量，异常假设被反驳");
  });
});

describe("structured report scope", () => {'''),
 'web/src/components/ObservabilityOverview.test.jsx': ('describe("ObservabilityOverview", () => {', '''describe("ObservabilityOverview", () => {
  it("shows a real complete I/O refutation separately from global health", () => {
    const model = buildObservationModel(ioRefutation.detail, ioRefutation);
    expect(model.assessment.code).toBe("OBSERVATION_REFUTED");
    expect(model.assessment.title).toBe("本次观测未发现该性能异常");
    expect(model.assessment.detail).toContain("0.081");
    expect(model.assessment.detail).toContain("684");
    expect(model.assessment.detail).toContain("不代表全部业务正常");
  });
  it("does not let a later invalid window replace a qualified observation", () => {
    const invalid = { ...systemEvidence, evidence_id: "invalid-later", classification: { decision: "REJECT" } };
    const model = buildObservationModel(detail, { ...resources, evidence: [systemEvidence, invalid] });
    expect(model.assessment.code).toBe("NORMAL_OBSERVED");
    expect(model.metricEvidence.evidence_id).toBe(systemEvidence.evidence_id);
  });
  it("keeps unrelated CPU anomalies visible when an I/O hypothesis was refuted", () => {
    const high = structuredClone(ioRefutation.evidence.find(e => e.envelope.observation.metadata?.signals?.io_latency));
    high.envelope.observation.metadata.summary.process_cpu_core_usage = 80;
    high.envelope.observation.metadata.signals.cpu_activity = { detected: true };
    const model = buildObservationModel(ioRefutation.detail, { ...ioRefutation, evidence: [high] });
    expect(model.assessment.code).toBe("ANOMALY_OBSERVED");
  });
  it("does not turn a cancelled workflow into a completed refutation", () => {
    const model = buildObservationModel({ ...ioRefutation.detail, status: "CANCELLED" }, ioRefutation);
    expect(model.assessment.code).toBe("INCOMPLETE");
  });''')}
for name,(old,new) in changes.items():
 p=Path(name);before=p.read_text(encoding='utf-8')
 assert old in before and 'ioRefutation' not in before
 after=before.replace(old,new,1)
 if p.name.endswith('.jsx'):
  after=after.replace('import ObservabilityOverview, { buildObservationModel } from "./ObservabilityOverview";',
       'import ObservabilityOverview, { buildObservationModel } from "./ObservabilityOverview";\nimport ioRefutation from "../test/fixtures/performance-io-refutation.json";',1)
 (stage/('old-'+p.name)).write_text(before,encoding='utf-8')
 pending=stage/('pending-'+p.name);pending.write_text(after,encoding='utf-8')
 os.replace(pending,p)
print('Added live-report regression tests.')
