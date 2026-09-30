import { describe, expect, it } from "vitest";
import { reportConclusionTitle, reportRemediation, projectReportScopes, selectBestReport } from "./reportPresentation";

describe("structured report scope", () => {
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
