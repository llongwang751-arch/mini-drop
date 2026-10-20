// 仅用于隔离 stage 的真实浏览器验收，不使用 route 伪响应。
// 禁止启用 tracing、录像、HAR、请求日志、截图或持久化登录态，避免凭据与短期 URL 进入证据。
import { createRequire } from "node:module";
import { readFileSync, lstatSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ORIGIN = "http://127.0.0.1:18080";
const PREFIX = "IMPORTED_STAGE_CE_GZIP_";
const root = fileURLToPath(new URL("../../", import.meta.url));
const identity = (value) =>
  typeof value === "string" &&
  /^[1-9][0-9]{0,18}$/.test(value) &&
  BigInt(value) <= 9223372036854775807n;

/** 校验外部链路生成的受限回执；拒绝额外字段，避免将凭据或任意文本夹带进报告。 */
export function validateProof(value) {
  try {
    const allowed = new Set([
      "schema_version",
      "success",
      "run_id",
      "task_id",
      "artifact_id",
      "attempt_id",
      "collection_attempt",
      "size",
      "sha256",
      "metadata_test",
    ]);
    if (
      !value ||
      typeof value !== "object" ||
      Object.keys(value).some((key) => !allowed.has(key)) ||
      value.schema_version !== 1 ||
      value.success !== true ||
      !/^[0-9a-f]{32}$/.test(value.run_id) ||
      !identity(value.task_id) ||
      !identity(value.artifact_id) ||
      !identity(value.attempt_id) ||
      value.collection_attempt !== 1 ||
      !Number.isSafeInteger(value.size) ||
      value.size <= 0 ||
      value.size > 65536 ||
      !/^[0-9a-f]{64}$/.test(value.sha256)
    )
      throw 0;
    const metadata = value.metadata_test;
    if (
      !metadata ||
      metadata.content_encoding !== "gzip" ||
      Object.keys(metadata).length !== 6 ||
      ![
        "stored_size_unchanged",
        "stored_sha256_unchanged",
        "user_metadata_preserved",
        "other_system_metadata_preserved",
        "encryption_metadata_preserved",
      ].every((key) => metadata[key] === true)
    )
      throw 0;
    return structuredClone(value);
  } catch {
    throw new Error("PROOF_INVALID");
  }
}

/** 短期授权必须属于固定回环 stage 和已核对的任务/制品路径，不能跟随任意对象地址。 */
export function validateGrant(value, proof) {
  try {
    if (
      typeof value !== "string" ||
      value.length > 8192 ||
      /[\s\\]/.test(value)
    )
      throw 0;
    const url = new URL(value);
    const pattern = new RegExp(
      `^/objects/drop-artifacts/results/tasks/${proof.task_id}/attempts/1/analysis/[1-9][0-9]{0,18}/runs/[1-9][0-9]{0,18}/flamegraph\\.json\\.gz$`,
    );
    if (
      url.origin !== ORIGIN ||
      url.username ||
      url.password ||
      url.hash ||
      !pattern.test(url.pathname) ||
      url.searchParams.getAll("response-content-encoding").length !== 1 ||
      url.searchParams.get("response-content-encoding") !== "identity" ||
      url.searchParams.getAll("X-Amz-Signature").length !== 1 ||
      !/^[0-9a-f]{64}$/.test(url.searchParams.get("X-Amz-Signature"))
    )
      throw 0;
    return url.href;
  } catch {
    throw new Error("GRANT_INVALID");
  }
}

function readSecret(name, maximum) {
  const path = resolve(root, ".local", name);
  const info = lstatSync(path);
  if (
    !info.isFile() ||
    info.isSymbolicLink() ||
    info.size <= 0 ||
    info.size > 1024
  )
    throw new Error("STAGE_SECRET_INVALID");
  const value = readFileSync(path, "utf8").replace(/[\r\n]+$/, "");
  if (
    !value ||
    Buffer.byteLength(value) > maximum ||
    /[\x00-\x1f\x7f]/.test(value)
  )
    throw new Error("STAGE_SECRET_INVALID");
  return value;
}

// 只有显式执行主入口并提供 proof 文件才读取 stage 凭据；单元测试导入校验函数不会走这里。
export async function run(argumentsList) {
  let browser;
  let context;
  let stage = "OPTIONS";
  try {
    if (argumentsList.length !== 2 || argumentsList[0] !== "--proof") throw 0;
    const path = resolve(argumentsList[1]);
    const info = lstatSync(path);
    if (!info.isFile() || info.isSymbolicLink() || info.size > 16384) throw 0;
    const proof = validateProof(JSON.parse(readFileSync(path, "utf8")));
    stage = "STAGE_SECRETS";
    const username = readSecret("stage_admin_username", 64);
    const password = readSecret("stage_admin_password", 72);
    // 调试模式可能打印协议参数或请求头，因此必须在载入 Playwright 前清空。
    // 此执行器不提供 verbose 模式，错误只报告固定阶段码。
    process.env.DEBUG = "";
    process.env.PWDEBUG = "0";
    const require = createRequire(
      new URL("../../web/package.json", import.meta.url),
    );
    const { chromium } = require("@playwright/test");
    stage = "BROWSER_START";
    browser = await chromium.launch({ headless: true, timeout: 30000 });
    context = await browser.newContext({
      baseURL: ORIGIN,
      acceptDownloads: false,
      serviceWorkers: "block",
    });
    context.setDefaultTimeout(30000);
    await context.route("**/*", async (route) => {
      try {
        if (new URL(route.request().url()).origin === ORIGIN)
          await route.continue();
        else await route.abort("blockedbyclient");
      } catch {
        await route.abort("failed").catch(() => {});
      }
    });
    const page = await context.newPage();
    let pageError = false;
    page.on("pageerror", () => {
      pageError = true;
    });
    stage = "STAGE_NAVIGATION";
    await page.goto(ORIGIN + "/", {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });
    stage = "STAGE_LOGIN";
    const logged = await page.evaluate(
      async ({ username, password }) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);
        try {
          const response = await fetch("/api/v1/auth/login", {
            method: "POST",
            credentials: "same-origin",
            redirect: "error",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
            signal: controller.signal,
          });
          if (response.status !== 200)
            return { ok: false, code: "LOGIN_FAILED" };
          const envelope = await response.json();
          return {
            ok:
              envelope.error === null &&
              ["admin", "platform_admin"].includes(envelope.data?.user?.role),
          };
        } catch {
          return { ok: false, code: "LOGIN_FAILED" };
        } finally {
          clearTimeout(timer);
        }
      },
      { username, password },
    );
    if (!logged.ok) throw 0;
    stage = "FRESH_AUTHORIZED_GRANT";
    const issued = await page.evaluate(
      async ({ proof, prefix }) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);
        try {
          const detail = await fetch(`/api/v1/tasks/${proof.task_id}`, {
            credentials: "same-origin",
            redirect: "error",
            signal: controller.signal,
          });
          if (detail.status !== 200)
            return { ok: false, code: "TASK_READ_FAILED" };
          const envelope = await detail.json();
          const task = envelope.data;
          if (
            envelope.error !== null ||
            task?.id !== proof.task_id ||
            task.name !== prefix + proof.run_id ||
            task.kind !== "IMPORT_COLLAPSED" ||
            task.params?.source !== "import" ||
            task.analysis_status !== "SUCCESS" ||
            task.collection_status !== "COLLECTED"
          )
            return { ok: false, code: "FIXTURE_CHANGED" };
          const latest = [...task.attempts].sort(
            (a, b) => b.attempt - a.attempt,
          )[0];
          if (
            latest?.id !== proof.attempt_id ||
            latest.attempt !== 1 ||
            latest.status !== "COLLECTED"
          )
            return { ok: false, code: "ATTEMPT_CHANGED" };
          const artifact = task.artifacts.find(
            (a) =>
              a.id === proof.artifact_id &&
              a.attempt_id === proof.attempt_id &&
              a.kind === "RESULT" &&
              a.name === "flamegraph.json.gz" &&
              a.status === "ready" &&
              a.download_allowed === true &&
              a.size === proof.size &&
              a.sha256 === proof.sha256,
          );
          if (!artifact) return { ok: false, code: "ARTIFACT_CHANGED" };
          const grant = await fetch(
            `/api/v1/tasks/${proof.task_id}/artifacts/${proof.artifact_id}/download`,
            {
              credentials: "same-origin",
              redirect: "error",
              signal: controller.signal,
            },
          );
          if (grant.status !== 200) return { ok: false, code: "GRANT_FAILED" };
          const result = await grant.json();
          if (result.error !== null || typeof result.data?.url !== "string")
            return { ok: false, code: "GRANT_FAILED" };
          // 预签名 URL 只留在内存；异常、报告和请求观测回调都不能把它序列化输出。
          return { ok: true, url: result.data.url };
        } catch {
          return { ok: false, code: "GRANT_FAILED" };
        } finally {
          clearTimeout(timer);
        }
      },
      { proof, prefix: PREFIX },
    );
    if (!issued.ok) throw 0;
    const url = validateGrant(issued.url, proof);
    stage = "BROWSER_EXACT_STORED_BYTES";
    const downloaded = await page.evaluate(
      async ({ url, proof }) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 30000);
        try {
          if (new URL(url).origin !== location.origin)
            return { ok: false, code: "ORIGIN_INVALID" };
          const response = await fetch(url, {
            credentials: "omit",
            redirect: "error",
            cache: "no-store",
            signal: controller.signal,
          });
          if (
            response.status !== 200 ||
            response.headers.get("Content-Encoding") !== "identity" ||
            response.headers.get("Content-Length") !== String(proof.size)
          )
            return { ok: false, code: "REPRESENTATION_INVALID" };
          const bytes = await response.arrayBuffer();
          if (
            bytes.byteLength !== proof.size ||
            new Uint8Array(bytes)[0] !== 0x1f ||
            new Uint8Array(bytes)[1] !== 0x8b
          )
            return { ok: false, code: "COMPRESSED_SIZE_INVALID" };
          const digest = [
            ...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
          ]
            .map((value) => value.toString(16).padStart(2, "0"))
            .join("");
          if (digest !== proof.sha256)
            return { ok: false, code: "COMPRESSED_DIGEST_INVALID" };
          return {
            ok: true,
            size: bytes.byteLength,
            sha256: digest,
            response_encoding_identity: true,
            gzip_magic_preserved: true,
          };
        } catch {
          return { ok: false, code: "BROWSER_FETCH_FAILED" };
        } finally {
          clearTimeout(timer);
        }
      },
      { url, proof },
    );
    if (!downloaded.ok) throw 0;
    stage = "REAL_WORKER_RENDER";
    await page.goto(ORIGIN + "/tasks/" + proof.task_id, {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });
    await page
      .locator('[data-flame-source="gzip-artifact"]')
      .waitFor({ state: "visible", timeout: 30000 });
    if (pageError) throw 0;
    const report = {
      schema_version: 1,
      success: true,
      task_id: proof.task_id,
      artifact_id: proof.artifact_id,
      attempt_id: proof.attempt_id,
      size: downloaded.size,
      sha256: downloaded.sha256,
      stored_content_encoding_gzip: true,
      response_encoding_identity: true,
      browser_arraybuffer_exact_sha_size: true,
      gzip_magic_preserved: true,
      real_worker_gzip_artifact_marker: true,
      trace_video_har_enabled: false,
    };
    console.log(JSON.stringify(report));
    return 0;
  } catch {
    console.error(
      JSON.stringify({
        schema_version: 1,
        success: false,
        error_code: stage + "_FAILED",
      }),
    );
    return 1;
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
  }
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  process.exitCode = await run(process.argv.slice(2));
}
