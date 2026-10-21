/** 合成公开协议夹具：只检查呈现/边界/下载 SHA，不能充当真实缺陷、模型或容器性能验收。 */
import { createHash } from "node:crypto";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import DiagnosticFacts, { parseDiagnosticFacts } from "./DiagnosticFacts";
import RepairValidationExtensions, {
  parsePatchBundle,
  verifyPatchBundleContent,
} from "./RepairValidationExtensions";

function bundleFixture() {
  const content =
    "--- a/business.py\n+++ b/business.py\n@@ -1 +1 @@\n-before\n+after\n--- /dev/null\n+++ b/drop_regression/test_edge.py\n@@ -0,0 +1 @@\n+assert True\n";
  return {
    schema_version: 1,
    format: "unified-diff",
    source_revision: "a".repeat(64),
    content,
    sha256: createHash("sha256").update(content).digest("hex"),
    code_files: ["business.py"],
    test_files: ["drop_regression/test_edge.py"],
    applied_to: "isolated_snapshot",
    production_deployed: false,
  };
}
function validationFixture() {
  return {
    regression_test_validation: {
      baseline_failed: true,
      candidate_passed: true,
      same_test_files: true,
      baseline: {
        tests: 1,
        failures: 1,
        errors: 0,
        skipped: 0,
        expected_failures: 0,
        unexpected_successes: 0,
        success: false,
      },
      candidate: {
        tests: 1,
        failures: 0,
        errors: 0,
        skipped: 0,
        expected_failures: 0,
        unexpected_successes: 0,
        success: true,
      },
    },
    original_tests_unchanged: true,
    negative_control: {
      kind: "identical_frozen_baseline",
      ratios: [1, 1, 1, 1, 1, 1],
      median_ratio: 1,
      stable: true,
    },
    patch_bundle: bundleFixture(),
  };
}
describe("repair delivery extension boundaries", () => {
  it("keeps old receipts unchanged and shows separate real before/after counters", () => {
    expect(
      renderToStaticMarkup(
        createElement(RepairValidationExtensions, {
          validation: { accepted: true },
        }),
      ),
    ).toBe("");
    const html = renderToStaticMarkup(
      createElement(RepairValidationExtensions, {
        validation: validationFixture(),
      }),
    );
    expect(html).toContain("独立回归前后验证通过");
    expect(html).toContain("修复前");
    expect(html).toContain("修复后");
    expect(html).toContain("原仓库未自动发布");
    expect(html).toContain("核对 SHA 并下载补丁");
    expect(html).not.toContain("根因已确认");
  });
  it("does not turn missing regression fields or a skipped candidate into passed", () => {
    const current = validationFixture();
    current.regression_test_validation.candidate.skipped = 1;
    const failed = renderToStaticMarkup(
      createElement(RepairValidationExtensions, { validation: current }),
    );
    expect(failed).not.toContain("独立回归前后验证通过");
    const malformed = renderToStaticMarkup(
      createElement(RepairValidationExtensions, {
        validation: { regression_test_validation: {} },
      }),
    );
    expect(malformed).toContain("字段不完整");
    expect(malformed).toContain("未提供下载");
  });
  it("rejects path escapes, missing isolation, already-published and oversized delivery", () => {
    const bundle = bundleFixture();
    expect(parsePatchBundle(bundle)).toBeDefined();
    for (const value of [
      { ...bundle, code_files: ["../business.py"] },
      { ...bundle, test_files: ["https://outside/test.py"] },
      { ...bundle, production_deployed: true },
      { ...bundle, applied_to: "original_repository" },
      { ...bundle, content: "字".repeat(9000) },
    ])
      expect(parsePatchBundle(value)).toBeUndefined();
  });
  it("requires complete success and unexpected/expected failure counters before upgrading a receipt", () => {
    for (const change of [
      { expected_failures: 1 },
      { unexpected_successes: 1 },
      { success: false },
    ]) {
      const current = validationFixture();
      Object.assign(current.regression_test_validation.candidate, change);
      const html = renderToStaticMarkup(
        createElement(RepairValidationExtensions, { validation: current }),
      );
      expect(html).not.toContain("独立回归前后验证通过");
    }
    const current = validationFixture();
    const html = renderToStaticMarkup(
      createElement(RepairValidationExtensions, {
        validation: { ...current, negative_control: { stable: true } },
      }),
    );
    expect(html).toContain("未通过或未提供");
  });
  it("verifies exact UTF-8 bytes before download and rejects content substitution", async () => {
    const bundle = parsePatchBundle(bundleFixture())!;
    expect(await verifyPatchBundleContent(bundle)).toBe(bundle.content);
    await expect(
      verifyPatchBundleContent({
        ...bundle,
        content: bundle.content + "changed\n",
      }),
    ).rejects.toThrow("SHA 不一致");
  });
  it("escapes proposed patch code as text", () => {
    const value = validationFixture();
    value.patch_bundle.content = '<img src=x onerror="alert(1)">';
    value.patch_bundle.sha256 = createHash("sha256")
      .update(value.patch_bundle.content)
      .digest("hex");
    const html = renderToStaticMarkup(
      createElement(RepairValidationExtensions, { validation: value }),
    );
    expect(html).toContain("&lt;img");
    expect(html).not.toContain("<img");
  });
});

const factsFixture = () => [
  {
    id: "F1",
    evidence_id: "E3",
    tool: "inspect_hotspots",
    path: "/top_functions/0/self_percent",
    operator: "eq",
    value: 70,
    unit: "percent",
    description: "排名1的self权重为70%，不代表请求耗时。",
  },
];
describe("structured facts and unverified interpretations", () => {
  it("never upgrades a legacy report into v2 verified facts", () => {
    for (const reportVersion of [undefined, 0, 1])
      expect(
        renderToStaticMarkup(
          createElement(DiagnosticFacts, {
            reportVersion,
            facts: factsFixture(),
          }),
        ),
      ).toBe("");
  });
  it("shows Go descriptions and exact evidence anchors while separating model interpretation", () => {
    const html = renderToStaticMarkup(
      createElement(DiagnosticFacts, {
        reportVersion: 2,
        facts: factsFixture(),
        hypotheses: [
          { unverified_interpretation: "可能与业务计算有关，仍待对照。" },
        ],
        runID: "42",
      }),
    );
    expect(html).toContain("字段、数值、单位与出处已核对");
    expect(html).toContain('href="#diagnosis-42-E3"');
    expect(html).toContain("未经事实核验的模型解释");
    expect(html).toContain("不属于上方已核对事实");
    expect(html).not.toContain("根因已确认");
  });
  it("rejects non-scalar, duplicate, malformed or non-finite fact values", () => {
    for (const value of [
      [],
      [...factsFixture(), ...factsFixture()],
      [{ ...factsFixture()[0], value: { secret: "x" } }],
      [{ ...factsFixture()[0], value: Infinity }],
      [{ ...factsFixture()[0], evidence_id: "javascript:alert(1)" }],
      [{ ...factsFixture()[0], evidence_id: "E16" }],
      [{ ...factsFixture()[0], tool: "read_diagnostic_knowledge" }],
    ])
      expect(parseDiagnosticFacts(value)).toBeUndefined();
    const html = renderToStaticMarkup(
      createElement(DiagnosticFacts, { reportVersion: 2, facts: [] }),
    );
    expect(html).toContain("缺少完整");
    expect(html).not.toContain('class="badge active"');
  });
  it("keeps business signal units and escapes all descriptions and model text", () => {
    expect(
      parseDiagnosticFacts([
        { ...factsFixture()[0], unit: "requests_per_second" },
      ]),
    ).toBeDefined();
    const html = renderToStaticMarkup(
      createElement(DiagnosticFacts, {
        reportVersion: 2,
        facts: [
          { ...factsFixture()[0], description: "<script>bad()</script>" },
        ],
        hypotheses: [
          { unverified_interpretation: '<img src=x onerror="alert(1)">' },
        ],
        runID: "invalid-run",
      }),
    );
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&lt;img");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain('href="#diagnosis-invalid-run');
  });
});
