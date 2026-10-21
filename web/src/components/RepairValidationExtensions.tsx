/**
 * 展示通用 Python 修复的独立回归与可审阅补丁。浏览器只下载文本，不执行补丁或发布原仓库。
 * 字段不完整时保留待核验状态；下载前再次核对 UTF-8 内容 SHA，避免显示摘要与交付物分离。
 */
import { useState } from "react";
import "../styles/knowledge.css";

type Counts = {
  tests: number;
  failures: number;
  errors: number;
  skipped: number;
  expected_failures: number;
  unexpected_successes: number;
  success: boolean;
};
export type PatchBundle = {
  schema_version: 1;
  format: "unified-diff";
  source_revision: string;
  content: string;
  sha256: string;
  code_files: string[];
  test_files: string[];
  applied_to: "isolated_snapshot";
  production_deployed: false;
};
type Regression = {
  baseline_failed: boolean;
  candidate_passed: boolean;
  same_test_files: boolean;
  baseline: Counts;
  candidate: Counts;
};
const object = (value: unknown): Record<string, unknown> | undefined =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
const hash = (value: unknown): value is string =>
  typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
function counts(value: unknown): value is Counts {
  const row = object(value);
  return (
    !!row &&
    typeof row.success === "boolean" &&
    [
      "tests",
      "failures",
      "errors",
      "skipped",
      "expected_failures",
      "unexpected_successes",
    ].every(
      (key) =>
        Number.isSafeInteger(row[key]) &&
        Number(row[key]) >= 0 &&
        Number(row[key]) <= 10000,
    )
  );
}
function paths(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.length <= 128 &&
    value.every(
      (name) =>
        typeof name === "string" &&
        name.length <= 180 &&
        /^[A-Za-z0-9_][A-Za-z0-9_./-]*$/.test(name) &&
        name
          .split("/")
          .every((part) => part !== ".." && part !== "." && part !== ""),
    )
  );
}
export function parsePatchBundle(value: unknown): PatchBundle | undefined {
  const bundle = object(value);
  return bundle?.schema_version === 1 &&
    bundle.format === "unified-diff" &&
    hash(bundle.source_revision) &&
    hash(bundle.sha256) &&
    typeof bundle.content === "string" &&
    bundle.content.length > 0 &&
    new TextEncoder().encode(bundle.content).byteLength <= 24576 &&
    paths(bundle.code_files) &&
    paths(bundle.test_files) &&
    bundle.applied_to === "isolated_snapshot" &&
    bundle.production_deployed === false
    ? (bundle as PatchBundle)
    : undefined;
}
export async function verifyPatchBundleContent(
  bundle: PatchBundle,
): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error("当前环境无法核对补丁摘要");
  const bytes = new TextEncoder().encode(bundle.content);
  const digest = Array.from(
    new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes)),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  if (digest !== bundle.sha256)
    throw new Error("补丁内容与 SHA 不一致，已停止下载");
  return bundle.content;
}
function regression(value: unknown): Regression | undefined {
  const row = object(value);
  return row &&
    typeof row.baseline_failed === "boolean" &&
    typeof row.candidate_passed === "boolean" &&
    typeof row.same_test_files === "boolean" &&
    counts(row.baseline) &&
    counts(row.candidate)
    ? (row as Regression)
    : undefined;
}

export default function RepairValidationExtensions({
  validation,
}: {
  validation: unknown;
}) {
  const value = object(validation);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  if (
    !value ||
    !("regression_test_validation" in value || "patch_bundle" in value)
  )
    return null;
  const review = regression(value.regression_test_validation);
  const bundle = parsePatchBundle(value.patch_bundle);
  const negative = object(value.negative_control);
  const redGreen =
    !!review &&
    review.baseline_failed &&
    review.candidate_passed &&
    review.same_test_files &&
    review.baseline.tests > 0 &&
    review.baseline.failures > 0 &&
    !review.baseline.errors &&
    !review.baseline.skipped &&
    !review.baseline.expected_failures &&
    !review.baseline.unexpected_successes &&
    !review.baseline.success &&
    review.candidate.tests === review.baseline.tests &&
    !review.candidate.failures &&
    !review.candidate.errors &&
    !review.candidate.skipped &&
    !review.candidate.expected_failures &&
    !review.candidate.unexpected_successes &&
    review.candidate.success;
  const negativeComplete =
    negative?.kind === "identical_frozen_baseline" &&
    Array.isArray(negative.ratios) &&
    negative.ratios.length === 6 &&
    negative.ratios.every(
      (ratio) =>
        typeof ratio === "number" && Number.isFinite(ratio) && ratio > 0,
    ) &&
    typeof negative.median_ratio === "number" &&
    Number.isFinite(negative.median_ratio) &&
    negative.median_ratio > 0;
  async function download() {
    if (!bundle) return;
    setBusy(true);
    setNotice("");
    try {
      const content = await verifyPatchBundleContent(bundle);
      const url = URL.createObjectURL(
        new Blob([content], { type: "text/x-diff;charset=utf-8" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `drop-${bundle.sha256.slice(0, 12)}.patch`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice(
        "补丁 SHA 已核对，已准备下载。应用前请人工审阅代码与新增测试。",
      );
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "补丁核验失败，已停止下载",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="knowledge-prior" aria-label="缺陷回归与补丁交付">
      <h3>缺陷回归与补丁交付</h3>
      <p className="knowledge-prior-note">
        同一新增用例分别运行原版本和候选；原冻结回归独立保留。红绿结果证明本次用例的前后行为，测试是否覆盖正确业务需求仍须人工审阅。
      </p>
      {review ? (
        <>
          <p>
            <span className={`badge ${redGreen ? "success" : "neutral"}`}>
              {redGreen ? "独立回归前后验证通过" : "缺陷回归尚未成立"}
            </span>
          </p>
          <div
            className="table-scroll"
            role="region"
            aria-label="新增回归用例实际计数"
            tabIndex={0}
          >
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">版本</th>
                  <th scope="col">执行</th>
                  <th scope="col">断言失败</th>
                  <th scope="col">错误</th>
                  <th scope="col">跳过</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["修复前", review.baseline],
                  ["修复后", review.candidate],
                ].map(([label, row]) => {
                  const item = row as Counts;
                  return (
                    <tr key={String(label)}>
                      <th scope="row">{String(label)}</th>
                      <td>{item.tests}</td>
                      <td>{item.failures}</td>
                      <td>{item.errors}</td>
                      <td>{item.skipped}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p role="alert">回归收据字段不完整，请核对原始工具结果。</p>
      )}
      <p>
        原冻结测试：
        {value.original_tests_unchanged === true
          ? "保持不变"
          : "未附加完整核验"}
        ；相同基线负控：
        {negativeComplete && negative?.stable === true
          ? "稳定"
          : "未通过或未提供"}
        。结果仅适用于该登记组件的受控负载。
      </p>
      {bundle ? (
        <>
          <p>
            交付范围：{bundle.code_files.length} 个代码文件、
            {bundle.test_files.length}{" "}
            个新增测试文件。补丁应用于隔离副本，原仓库未自动发布。
          </p>
          <p className="mono">SHA-256：{bundle.sha256}</p>
          <details>
            <summary>审阅代码与新增测试 diff</summary>
            <pre
              role="region"
              aria-label="代码与新增测试补丁"
              tabIndex={0}
              style={{
                maxHeight: 320,
                overflow: "auto",
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
              }}
            >
              {bundle.content}
            </pre>
          </details>
          <button
            className="button secondary"
            type="button"
            disabled={busy}
            onClick={() => void download()}
          >
            {busy ? "正在核对 SHA" : "核对 SHA 并下载补丁"}
          </button>
        </>
      ) : (
        <p role="alert">补丁交付物缺失或协议无效，未提供下载。</p>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
