import { describe, expect, it } from "vitest";
import { reportConclusionTitle, reportRemediation, projectReportScopes, selectBestReport, isLocalizedReport, isCausalRootReport } from "./reportPresentation";

import ioRefutation from "../test/fixtures/performance-io-refutation.json";

describe("live I/O refutation presentation", () => {
  const refuted = ioRefutation.reports.find(r => r.verification.observation_verification.status === "REFUTED");
  it("keeps a fully measured refutation above a later inconclusive host I/O report", () => {
    expect(selectBestReport(ioRefutation.reports)).toEqual(refuted);
  });
  it("distinguishes a measured false hypothesis from missing evidence", () => {
    expect(reportConclusionTitle(refuted)).toBe("已测量，异常假设被反驳");
    expect(isCausalRootReport(refuted)).toBe(false);
  });
  it("preserves an older incomplete report without a verification object", () => {
    expect(() => selectBestReport([{ verification_status: "INSUFFICIENT_EVIDENCE" }])).not.toThrow();
  });
  it.each([
    { counter_evidence_refs: {} }, { counter_evidence_refs: null },
    { verification: { ...refuted.verification, observation_verification: {
      ...refuted.verification.observation_verification, criteria: [null],
    } } },
  ])("rejects malformed refutation payloads without crashing the report projection %j", change => {
    expect(() => selectBestReport([{ ...refuted, ...change }])).not.toThrow();
    expect(reportConclusionTitle({ ...refuted, ...change })).not.toBe("已测量，异常假设被反驳");
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

describe("structured report scope", () => {
  const localized = { verification: {
    status: "VERIFIED", claim_scope: "BOUNDED_OBSERVATION", causal_root_cause_verified: false,
    bottleneck_localization: { status: "LOCALIZED", causal_root_cause_verified: false,
      same_load_fix_verified: false, location: "HTTP 调用耗时路径", evidence_refs: ["ev-1"] },
  } };
  it("presents a localized path while preserving the causal boundary", () => {
    expect(isLocalizedReport(localized)).toBe(true);
    expect(isCausalRootReport(localized)).toBe(false);
    expect(reportConclusionTitle(localized)).toBe("性能路径已定位，根因仍待确认");
  });
  it.each([
    { status: "PARTIAL_WITHOUT_COUNTER" }, { claim_scope: "CAUSAL_ROOT_CAUSE" },
    { causal_root_cause_verified: true },
    { bottleneck_localization: { ...localized.verification.bottleneck_localization, same_load_fix_verified: true } },
    { bottleneck_localization: { ...localized.verification.bottleneck_localization, evidence_refs: [] } },
  ])("does not promote incomplete or contradictory localization %j", (change) => {
    expect(isLocalizedReport({ verification: { ...localized.verification, ...change } })).toBe(false);
  });
  it("keeps historical host-only I/O outside localized process assessments", () => {
    const report = { ...localized, claims: [{ statement: "host block-device tracepoints observed latency" }] };
    expect(isLocalizedReport(report)).toBe(false);
  });
  it("presents a verified bounded observation without a causal title or fixes", () => {
    const report = { verification: {
      status: "VERIFIED", claim_scope: "BOUNDED_OBSERVATION", causal_root_cause_verified: false,
      remediation: { schema_version: 2, mitigations: [{ title: "补采" }], root_cause_fixes: [{ title: "直接修复" }] },
    }, conclusion: "已验证观测：源码函数占有效样本的33.3%。" };
    expect(reportConclusionTitle(report)).toBe("已验证观测");
    expect(reportRemediation(report).root_cause_fixes).toEqual([]);
  });

  it.each(["FUTURE_SCOPE", null, 1, {}, []])("does not promote an unknown or malformed scope %j", (scope) => {
    expect(reportConclusionTitle({ verification: { status: "VERIFIED", claim_scope: scope } })).not.toBe("根因结论");
  });

  it.each([false, "true", 1, null])("requires an exact boolean causal marker when provided: %j", (marker) => {
    expect(reportConclusionTitle({ verification: { status: "VERIFIED", causal_root_cause_verified: marker } })).not.toBe("根因结论");
  });

  it("preserves the legacy report title and explicit causal scope", () => {
    expect(reportConclusionTitle({ verification: { status: "VERIFIED" } })).toBe("根因结论");
    expect(reportConclusionTitle({ verification: { status: "VERIFIED", claim_scope: "CAUSAL_ROOT_CAUSE", causal_root_cause_verified: true } })).toBe("根因结论");
  });

  it("projects report-node titles without changing the persisted tree", () => {
    const node = { id: "report:r-1", kind: "report", title: "根因结论", state: "confirmed" };
    const tree = { nodes: [node, { id: "hypothesis:h-1", title: "原假设" }] };
    const report = { report_id: "r-1", verification: { status: "VERIFIED", claim_scope: "BOUNDED_OBSERVATION" } };
    const display = projectReportScopes(tree, [report]);
    expect(display.nodes[0].title).toBe("已验证观测");
    expect(tree.nodes[0]).toBe(node);
    expect(node.title).toBe("根因结论");
    expect(display.nodes[1]).toBe(tree.nodes[1]);
  });

  it("prioritizes a causal report over a higher-scoring observation", () => {
    const root = { confidence: .6, verification: { status: "VERIFIED", claim_scope: "CAUSAL_ROOT_CAUSE", causal_root_cause_verified: true } };
    const observation = { confidence: .95, verification: { status: "VERIFIED", claim_scope: "BOUNDED_OBSERVATION" } };
    expect(selectBestReport([observation, root])).toBe(root);
  });
});
