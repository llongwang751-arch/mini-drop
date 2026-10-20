import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import AnalysisSections from "./AnalysisSections";

// Explicit imported rendering fixtures. Actual BOLT task57 is verified
// independently on the public server; these do not execute perf or llvm-bolt.
function binaryFixture(mode: string | undefined, branches = 0, failures = 0) {
  return renderToStaticMarkup(
    createElement(AnalysisSections, {
      sections: {
        binary: {
          format: "bolt_fdata",
          mode,
          functions: [{ name: "imported_fixture_function", samples: 145 }],
          branches: [],
          totals: { branches, mispredictions: failures },
        },
      },
    }),
  );
}

const metric = (label: string, value: string) =>
  `<dt>${label}</dt><dd class="mono">${value}</dd>`;

describe("BOLT measurement provenance", () => {
  it("shows unsampled branch counters as unmeasured for basic samples", () => {
    const html = binaryFixture("basic_samples");
    expect(html).toContain(metric("分支观测", "未测量"));
    expect(html).toContain(metric("分支预测失败", "未测量"));
    expect(html).toContain("本次仅采集基础样本，未测量分支及预测失败。");
    expect(html).toContain("imported_fixture_function");
    expect(html).toContain(">145<");
  });

  it("keeps real collected branch and prediction-failure counts", () => {
    const html = binaryFixture("branch", 123, 7);
    expect(html).toContain(metric("分支观测", "123"));
    expect(html).toContain(metric("分支预测失败", "7"));
    expect(html).not.toContain("本次仅采集基础样本");
  });

  it("preserves measured zero instead of treating it as missing", () => {
    const html = binaryFixture("branch", 0, 0);
    expect(html).toContain(metric("分支观测", "0"));
    expect(html).toContain(metric("分支预测失败", "0"));
  });

  it("does not invent branch measurement when provenance is absent", () => {
    const html = binaryFixture(undefined, 12, 4);
    expect(html).toContain(metric("分支观测", "未测量"));
    expect(html).toContain(metric("分支预测失败", "未测量"));
  });
});
