// 显式构造的导入回执和合成签名，只验证元数据与授权路径边界，不读取 stage 账户或启动浏览器。
import test from "node:test";
import assert from "node:assert/strict";
import { validateProof, validateGrant } from "./browser_ce_gzip.mjs";

const proof = {
  schema_version: 1,
  success: true,
  run_id: "a".repeat(32),
  task_id: "7",
  artifact_id: "21",
  attempt_id: "11",
  collection_attempt: 1,
  size: 200,
  sha256: "b".repeat(64),
  metadata_test: {
    content_encoding: "gzip",
    stored_size_unchanged: true,
    stored_sha256_unchanged: true,
    user_metadata_preserved: true,
    other_system_metadata_preserved: true,
    encryption_metadata_preserved: true,
  },
};
const base =
  "http://127.0.0.1:18080/objects/drop-artifacts/results/tasks/7/attempts/1/analysis/3/runs/1/flamegraph.json.gz";
const query =
  "?response-content-encoding=identity&X-Amz-Signature=" + "c".repeat(64);

test("only a complete safe fixture proof is accepted", () => {
  assert.deepEqual(validateProof(proof), proof);
  for (const changed of [
    { ...proof, success: false },
    { ...proof, size: 65537 },
    { ...proof, task_id: "0" },
    { ...proof, collection_attempt: 2 },
    { ...proof, sha256: "private-url" },
    { ...proof, metadata_test: {} },
  ]) {
    assert.throws(() => validateProof(changed), /^Error: PROOF_INVALID$/);
  }
});

test("a grant is confined to this fixture and signed identity override", () => {
  assert.equal(validateGrant(base + query, proof), base + query);
  for (const invalid of [
    base.replace("127.0.0.1:18080", "120.24.187.205") + query,
    base.replace("tasks/7/", "tasks/8/") + query,
    base.replace("drop-artifacts", "production") + query,
    base + "?X-Amz-Signature=" + "c".repeat(64),
    base + query + "&response-content-encoding=gzip",
    base.replace("flamegraph", "%2e%2e/flamegraph") + query,
  ]) {
    assert.throws(
      () => validateGrant(invalid, proof),
      /^Error: GRANT_INVALID$/,
    );
  }
});
