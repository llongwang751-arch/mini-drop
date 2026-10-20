// 纯导入元数据单测：不启动浏览器、服务端或网络，也不读取真实凭据。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  IMAGE_PINS,
  partialDigest,
  run,
  validateProof,
} from "./browser_partial_chain.mjs";

const pins = Object.fromEntries(
  Object.keys(IMAGE_PINS).map((key) => [key, "sha256:" + "a".repeat(64)]),
);
function proof() {
  return {
    schema_version: 1,
    success: true,
    phase: "verified",
    run_id: "b".repeat(32),
    task_id: "7",
    attempt_id: "9",
    analysis_job_id: "11",
    analysis_run: 1,
    input_artifact_id: "13",
    input_digest: "c".repeat(64),
    partial_sha256: "d".repeat(64),
    summary: {
      source: "import",
      unit: "samples",
      total_samples: 100,
      unique_stacks: 2,
      node_count: 5,
      max_depth: 3,
    },
    checkpoint_event_before_failure: true,
    terminal_failed_no_lease: true,
    no_published_result_artifacts: true,
    orphan_objects_deleted: true,
    orphan_object_count: 4,
    injected_commit_failure_observed: true,
    fault_trigger_calls: 1,
    trigger_removed: true,
    function_removed: true,
    sequence_removed: true,
    recorded_at: new Date().toISOString(),
    project: "dropfullstage",
    images: { ...pins },
    production_running_container_ids_unchanged: true,
    analyzer_original_state_restored: true,
  };
}

test("strict proof accepts imported metadata only with every actual-fault and cleanup fact", () => {
  const imported = proof();
  assert.deepEqual(validateProof(imported, pins), imported);
  const copy = validateProof(imported, pins);
  copy.summary.total_samples = 0;
  assert.equal(imported.summary.total_samples, 100);
});
test("mismatched evidence cannot reuse the actual frozen release pins", () => {
  for (const image of Object.values(IMAGE_PINS))
    assert.match(image, /^sha256:[0-9a-f]{64}$/);
  assert.throws(
    () => validateProof(proof()),
    /^Error: PARTIAL_STAGE_IMAGES_NOT_FROZEN$/,
  );
});
test("extra secrets, invalid IDs, retries or missing real fault are rejected without echo", () => {
  for (const changes of [
    { cookie: "IMPORTED_SYNTHETIC_SECRET" },
    { task_id: "0" },
    { analysis_run: 2 },
    { fault_trigger_calls: 0 },
    { fault_trigger_calls: 2 },
    { orphan_object_count: 0 },
    { sequence_removed: false },
    { checkpoint_event_before_failure: false },
    { production_running_container_ids_unchanged: false },
  ]) {
    assert.throws(
      () => validateProof({ ...proof(), ...changes }, pins),
      (error) =>
        error.message === "PARTIAL_STAGE_PROOF_INVALID" &&
        !String(error).includes("IMPORTED_SYNTHETIC_SECRET"),
    );
  }
});
test("stale production or mismatched image proof cannot be reused", () => {
  assert.throws(
    () =>
      validateProof({ ...proof(), recorded_at: "2026-01-01T00:00:00Z" }, pins),
    /^Error: PARTIAL_STAGE_PROOF_STALE$/,
  );
  assert.throws(
    () => validateProof({ ...proof(), project: "drop" }, pins),
    /^Error: PARTIAL_STAGE_PROOF_INVALID$/,
  );
  const wrong = proof();
  wrong.images.web = "sha256:" + "f".repeat(64);
  assert.throws(
    () => validateProof(wrong, pins),
    /^Error: PARTIAL_STAGE_IMAGES_NOT_FROZEN$/,
  );
});
test("summary bounds and source remain explicit imported data", () => {
  for (const change of [
    { source: "perf" },
    { unit: 123 },
    { total_samples: 0 },
    { unique_stacks: 3 },
    { node_count: 100001 },
    { max_depth: 129 },
    { extra: true },
  ]) {
    assert.throws(
      () =>
        validateProof(
          { ...proof(), summary: { ...proof().summary, ...change } },
          pins,
        ),
      /^Error: PARTIAL_STAGE_PROOF_INVALID$/,
    );
  }
});
test("canonical checkpoint hash matches independently computed shared Python fixture vector", () => {
  const imported = JSON.parse(
    readFileSync(
      new URL(
        "../../contracts/fixtures/partial-result-v1.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  assert.equal(
    partialDigest(imported),
    "bcaa1c5644addb6e16243d581522d99144fd90ad9b135d5b8727467050b2ac38",
  );
  assert.equal(
    partialDigest({ b: 2, a: { y: 1, x: 0 } }),
    partialDigest({ a: { x: 0, y: 1 }, b: 2 }),
  );
});
test("missing readiness gate or arbitrary proof path returns only fixed options failure", async () => {
  assert.deepEqual(await run([]), {
    success: false,
    error_code: "PARTIAL_STAGE_BROWSER_FAILED",
    phase: "OPTIONS",
  });
  assert.deepEqual(
    await run(["--stage-ready", "--proof", "IMPORTED_SYNTHETIC_SECRET"]),
    {
      success: false,
      error_code: "PARTIAL_STAGE_BROWSER_FAILED",
      phase: "OPTIONS",
    },
  );
});
