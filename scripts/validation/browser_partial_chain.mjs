// 隔离 stage 的真实部分结果验收：核对后台回执后，再让实际浏览器读取同一执行身份。
// 不伪造路由响应，不记录 trace/HAR/录像/登录态文件或原始网络异常。
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { lstatSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const ORIGIN = "http://127.0.0.1:18080";
const PREFIX = "IMPORTED_STAGE_PARTIAL_";
export const IMAGE_PINS = {
  analyzer:
    "sha256:b9e6b659e0494e5002ed5f49a43812a73c3eb7b966d050b2e325b5990e9af136",
  api: "sha256:94ad66ca5cd6a9a866c84c35916626b9cfe41e765c3cd648abb04970777abc47",
  web: "sha256:452ef2a6ca44e9ca5f4f3ff7c14821acad3bf3a7810b9b9f6f65215dc9141959",
  "drop-server":
    "sha256:1310a7f48bf0cfba7f3de7e8b4e0dc3671bbbb91d2707b1d302f5f8db93a0355",
  agent:
    "sha256:1310a7f48bf0cfba7f3de7e8b4e0dc3671bbbb91d2707b1d302f5f8db93a0355",
};
const HEX = /^[0-9a-f]{64}$/;
const identity = (value) =>
  typeof value === "string" &&
  /^[1-9][0-9]{0,18}$/.test(value) &&
  BigInt(value) <= 9223372036854775807n;
function requireValue(ok, code) {
  if (!ok) throw new Error(code);
}
export function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  return value;
}
// 对字段递归排序后计算摘要，避免仅对象键顺序不同就被当成内容发生变化。
export function partialDigest(value) {
  return createHash("sha256")
    .update(JSON.stringify(canonical(value)))
    .digest("hex");
}
// 同时检查执行身份、失败检查点、清理结果和冻结镜像；通过页面渲染不能替代这些后台约束。
export function validateProof(value, pins = IMAGE_PINS) {
  const required = [
    "schema_version",
    "success",
    "phase",
    "run_id",
    "task_id",
    "attempt_id",
    "analysis_job_id",
    "analysis_run",
    "input_artifact_id",
    "input_digest",
    "partial_sha256",
    "summary",
    "checkpoint_event_before_failure",
    "terminal_failed_no_lease",
    "no_published_result_artifacts",
    "orphan_objects_deleted",
    "orphan_object_count",
    "injected_commit_failure_observed",
    "fault_trigger_calls",
    "trigger_removed",
    "function_removed",
    "sequence_removed",
    "recorded_at",
    "project",
    "images",
    "production_running_container_ids_unchanged",
    "analyzer_original_state_restored",
  ];
  try {
    requireValue(
      value &&
        typeof value === "object" &&
        !Array.isArray(value) &&
        Object.keys(value).length === required.length &&
        required.every((key) => Object.hasOwn(value, key)) &&
        value.schema_version === 1 &&
        value.success === true &&
        value.phase === "verified" &&
        value.project === "dropfullstage" &&
        /^[0-9a-f]{32}$/.test(value.run_id) &&
        ["task_id", "attempt_id", "analysis_job_id", "input_artifact_id"].every(
          (key) => identity(value[key]),
        ) &&
        value.analysis_run === 1 &&
        HEX.test(value.input_digest) &&
        HEX.test(value.partial_sha256),
      "PARTIAL_STAGE_PROOF_INVALID",
    );
    requireValue(
      [
        "checkpoint_event_before_failure",
        "terminal_failed_no_lease",
        "no_published_result_artifacts",
        "orphan_objects_deleted",
        "injected_commit_failure_observed",
        "trigger_removed",
        "function_removed",
        "sequence_removed",
        "production_running_container_ids_unchanged",
        "analyzer_original_state_restored",
      ].every((key) => value[key] === true) &&
        value.fault_trigger_calls === 1 &&
        Number.isSafeInteger(value.orphan_object_count) &&
        value.orphan_object_count >= 1 &&
        value.orphan_object_count <= 8,
      "PARTIAL_STAGE_PROOF_INVALID",
    );
    requireValue(
      typeof value.recorded_at === "string",
      "PARTIAL_STAGE_PROOF_INVALID",
    );
    const elapsed = Date.now() - Date.parse(value.recorded_at);
    requireValue(
      Number.isFinite(elapsed) && elapsed >= -300000 && elapsed <= 3600000,
      "PARTIAL_STAGE_PROOF_STALE",
    );
    const summary = value.summary;
    requireValue(
      summary &&
        Object.keys(summary).length === 6 &&
        summary.source === "import" &&
        typeof summary.unit === "string" &&
        /^[A-Za-z0-9_.-]{1,32}$/.test(summary.unit) &&
        summary.total_samples === 100 &&
        summary.unique_stacks === 2 &&
        Number.isSafeInteger(summary.node_count) &&
        summary.node_count > 0 &&
        summary.node_count <= 100000 &&
        Number.isSafeInteger(summary.max_depth) &&
        summary.max_depth > 0 &&
        summary.max_depth <= 128,
      "PARTIAL_STAGE_PROOF_INVALID",
    );
    requireValue(
      value.images &&
        Object.keys(value.images).length === Object.keys(pins).length &&
        Object.entries(pins).every(
          ([role, image]) =>
            /^sha256:[0-9a-f]{64}$/.test(image) && value.images[role] === image,
        ),
      "PARTIAL_STAGE_IMAGES_NOT_FROZEN",
    );
    return structuredClone(value);
  } catch (error) {
    const code = error?.message;
    throw new Error(
      ["PARTIAL_STAGE_PROOF_STALE", "PARTIAL_STAGE_IMAGES_NOT_FROZEN"].includes(
        code,
      )
        ? code
        : "PARTIAL_STAGE_PROOF_INVALID",
    );
  }
}
function readSecret(name, maximum) {
  const path = resolve(ROOT, ".local", name);
  const info = lstatSync(path);
  requireValue(
    info.isFile() &&
      !info.isSymbolicLink() &&
      info.nlink === 1 &&
      info.size > 0 &&
      info.size <= 1024,
    "PARTIAL_STAGE_SECRET_INVALID",
  );
  const value = readFileSync(path, "utf8").replace(/[\r\n]+$/, "");
  requireValue(
    value &&
      Buffer.byteLength(value) <= maximum &&
      !/[\x00-\x1f\x7f]/.test(value),
    "PARTIAL_STAGE_SECRET_INVALID",
  );
  return value;
}

// 此函数在真实页面中运行，但不返回凭据、请求头或原生网络异常。
// 每个响应都按有上限的字节流读取，不能因为是验收工具就放弃资源边界。
async function browserRequest({ path, login }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  let reader;
  try {
    if (
      !/^\/api\/v1\/(auth\/login|tasks\/[1-9][0-9]{0,18}(\/(partial-result|result))?)$/.test(
        path,
      )
    )
      return { ok: false, code: "PATH_INVALID" };
    const response = await fetch(path, {
      method: login ? "POST" : "GET",
      credentials: "same-origin",
      redirect: "error",
      cache: "no-store",
      signal: controller.signal,
      headers: login ? { "Content-Type": "application/json" } : {},
      body: login ? JSON.stringify(login) : undefined,
    });
    const limit = path.endsWith("/partial-result") ? 17408 : 1024 * 1024;
    reader = response.body?.getReader();
    if (!reader) return { ok: false, code: "BODY_MISSING" };
    const chunks = [];
    let size = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) return { ok: false, code: "BODY_LIMIT" };
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.length;
    }
    const envelope = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    );
    if (login)
      return {
        ok:
          response.status === 200 &&
          envelope.error === null &&
          ["admin", "platform_admin"].includes(envelope.data?.user?.role),
      };
    return {
      ok: true,
      status: response.status,
      data: envelope.data,
      error_code: envelope.error?.code ?? null,
    };
  } catch {
    return { ok: false, code: "REQUEST_FAILED" };
  } finally {
    if (reader) await reader.cancel().catch(() => {});
    clearTimeout(timer);
  }
}

// 实际浏览器执行入口可能读取 stage 凭据；纯元数据测试只导入上面的校验函数。
export async function run(args) {
  let browser;
  let context;
  let phase = "OPTIONS";
  try {
    requireValue(
      args.length === 3 && args[0] === "--stage-ready" && args[1] === "--proof",
      "PARTIAL_STAGE_OPTIONS_INVALID",
    );
    const proofPath = resolve(args[2]);
    requireValue(
      proofPath ===
        resolve(ROOT, "docs/evidence/full/stage-partial-chain-2026-10-19.json"),
      "PARTIAL_STAGE_PROOF_PATH_INVALID",
    );
    const info = lstatSync(proofPath);
    requireValue(
      info.isFile() &&
        !info.isSymbolicLink() &&
        info.nlink === 1 &&
        info.size > 0 &&
        info.size <= 16384,
      "PARTIAL_STAGE_PROOF_INVALID",
    );
    const proof = validateProof(JSON.parse(readFileSync(proofPath, "utf8")));
    phase = "CREDENTIALS";
    const login = {
      username: readSecret("stage_admin_username", 64),
      password: readSecret("stage_admin_password", 72),
    };
    for (const key of ["DEBUG", "PWDEBUG", "DEBUG_FILE", "NODE_DEBUG"])
      delete process.env[key];
    const require = createRequire(
      new URL("../../web/package.json", import.meta.url),
    );
    const { chromium } = require("@playwright/test");
    const AxeBuilder = require("@axe-core/playwright").default;
    phase = "BROWSER_START";
    browser = await chromium.launch({ headless: true, timeout: 30000 });
    context = await browser.newContext({
      baseURL: ORIGIN,
      acceptDownloads: false,
      serviceWorkers: "block",
    });
    context.setDefaultTimeout(30000);
    const page = await context.newPage();
    let pageError = false;
    let externalRequest = false;
    page.on("pageerror", () => {
      pageError = true;
    });
    page.on("request", (request) => {
      try {
        if (new URL(request.url()).origin !== ORIGIN) externalRequest = true;
      } catch {
        externalRequest = true;
      }
    });
    await page.goto(ORIGIN + "/", { waitUntil: "domcontentloaded" });
    phase = "LOGIN";
    requireValue(
      (
        await page.evaluate(browserRequest, {
          path: "/api/v1/auth/login",
          login,
        })
      ).ok,
      "PARTIAL_STAGE_LOGIN_FAILED",
    );
    phase = "ACTUAL_API_BINDING";
    const detail = await page.evaluate(browserRequest, {
      path: `/api/v1/tasks/${proof.task_id}`,
    });
    const task = detail.data;
    requireValue(
      detail.ok &&
        detail.status === 200 &&
        detail.error_code === null &&
        task?.id === proof.task_id &&
        task.name === PREFIX + proof.run_id &&
        task.kind === "IMPORT_COLLAPSED" &&
        task.collection_status === "COLLECTED" &&
        task.analysis_status === "FAILED" &&
        Number.isSafeInteger(task.version) &&
        task.analysis_identity?.analysis_job_id === proof.analysis_job_id &&
        task.analysis_identity.analysis_run === proof.analysis_run &&
        task.analysis_identity.input_artifact_id === proof.input_artifact_id &&
        task.analysis_identity.input_digest === proof.input_digest,
      "PARTIAL_STAGE_CURRENT_BINDING_FAILED",
    );
    const partial = await page.evaluate(browserRequest, {
      path: `/api/v1/tasks/${proof.task_id}/partial-result`,
    });
    requireValue(
      partial.ok &&
        partial.status === 200 &&
        partial.error_code === null &&
        partialDigest(partial.data) === proof.partial_sha256 &&
        partial.data?.completeness === "partial" &&
        partial.data.task_id === proof.task_id &&
        partial.data.attempt_id === proof.attempt_id &&
        partial.data.analysis_job_id === proof.analysis_job_id &&
        partial.data.analysis_run === proof.analysis_run &&
        partial.data.input_artifact_id === proof.input_artifact_id &&
        partial.data.input_digest === proof.input_digest,
      "PARTIAL_STAGE_API_CHECKPOINT_FAILED",
    );
    const successful = await page.evaluate(browserRequest, {
      path: `/api/v1/tasks/${proof.task_id}/result`,
    });
    requireValue(
      successful.ok && successful.status === 409,
      "PARTIAL_STAGE_SUCCESS_ENDPOINT_FAILED",
    );
    const destination = resolve(
      ROOT,
      "docs/evidence/full",
      "stage-partial-browser-" + proof.run_id,
    );
    mkdirSync(destination, { recursive: false });
    const checks = [];
    let successFallbacks = 0;
    let aiCalls = 0;
    page.on("request", (request) => {
      try {
        const path = new URL(request.url()).pathname;
        if (path === `/api/v1/tasks/${proof.task_id}/result`)
          successFallbacks += 1;
        if (path.endsWith("/suggestions/requests")) aiCalls += 1;
      } catch {
        externalRequest = true;
      }
    });
    for (const width of [1440, 375]) {
      phase = "UI_" + width;
      await page.setViewportSize({ width, height: 1000 });
      const response = page
        .waitForResponse((value) => {
          const url = new URL(value.url());
          return (
            url.origin === ORIGIN &&
            url.pathname === `/api/v1/tasks/${proof.task_id}/partial-result`
          );
        })
        .then((value) => value.status())
        .catch(() => 0);
      await page.goto(`${ORIGIN}/tasks/${proof.task_id}`, {
        waitUntil: "domcontentloaded",
      });
      requireValue((await response) === 200, "PARTIAL_STAGE_UI_QUERY_FAILED");
      const panel = page.getByRole("region", {
        name: "本次未完成分析的已解析摘要",
        exact: true,
      });
      await panel.waitFor({ state: "visible" });
      requireValue(
        (await panel.getAttribute("data-analysis-run")) ===
          String(proof.analysis_run) &&
          (await panel
            .getByRole("heading", {
              name: "未完成分析 · 已解析摘要",
              exact: true,
            })
            .count()) === 1 &&
          (
            await panel
              .locator(".partial-analysis-metrics dd")
              .first()
              .innerText()
          ).trim() === `100 ${proof.summary.unit}` &&
          (await panel.innerText()).includes("来源：用户导入数据") &&
          (await page.locator("[data-flame-source]").count()) === 0,
        "PARTIAL_STAGE_UI_CONTENT_FAILED",
      );
      const disclosure = panel.locator("summary");
      await disclosure.focus();
      await disclosure.press("Enter");
      requireValue(
        await panel.locator("details").evaluate((element) => element.open),
        "PARTIAL_STAGE_KEYBOARD_FAILED",
      );
      const bindingText = (await panel.locator("details").innerText()).replace(
        /\s+/g,
        " ",
      );
      requireValue(
        bindingText.includes(`#${proof.task_id} / ${proof.attempt_id}`) &&
          bindingText.includes(
            `${proof.analysis_job_id} / ${proof.analysis_run}`,
          ) &&
          bindingText.includes(
            `#${proof.input_artifact_id} · ${proof.input_digest}`,
          ),
        "PARTIAL_STAGE_RENDERED_BINDING_FAILED",
      );
      await disclosure.press("Enter");
      const accessibility = await new AxeBuilder({ page }).analyze();
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > window.innerWidth,
      }));
      requireValue(
        accessibility.violations.length === 0 &&
          !layout.overflow &&
          !pageError &&
          !externalRequest &&
          successFallbacks === 0 &&
          aiCalls === 0,
        "PARTIAL_STAGE_ACCESSIBILITY_FAILED",
      );
      await page.evaluate(async () => {
        document.activeElement?.blur();
        window.scrollTo(0, 0);
        await new Promise((done) =>
          requestAnimationFrame(() => requestAnimationFrame(done)),
        );
      });
      const viewport = resolve(destination, `partial-${width}-viewport.png`);
      const full = resolve(destination, `partial-${width}-full.png`);
      await page.screenshot({ path: viewport, fullPage: false });
      await page.screenshot({ path: full, fullPage: true });
      checks.push({
        width,
        axe_violations: 0,
        document_horizontal_overflow: false,
        keyboard_disclosure: true,
        screenshots: [viewport, full].map((path) => ({
          path: path.slice(ROOT.length).replaceAll("\\", "/"),
          sha256: createHash("sha256").update(readFileSync(path)).digest("hex"),
        })),
      });
    }
    const report = {
      schema_version: 1,
      success: true,
      recorded_at: new Date().toISOString(),
      scope:
        "Actual isolated stage API/S3/frozen worker, controlled final-commit failure and browser; imported synthetic profile",
      run_id: proof.run_id,
      task_id: proof.task_id,
      analysis_job_id: proof.analysis_job_id,
      analysis_run: proof.analysis_run,
      partial_sha256: proof.partial_sha256,
      seed_proof_sha256: createHash("sha256")
        .update(readFileSync(proofPath))
        .digest("hex"),
      images: proof.images,
      checks,
      successful_result_fallback_requests: successFallbacks,
      partial_driven_ai_requests: aiCalls,
      trace: false,
      har: false,
      video: false,
      production_changed: false,
    };
    writeFileSync(
      resolve(destination, "browser-proof.json"),
      JSON.stringify(report, null, 2) + "\n",
      { flag: "wx" },
    );
    return {
      success: true,
      run_id: proof.run_id,
      widths: checks.map((value) => value.width),
    };
  } catch {
    return {
      success: false,
      error_code: "PARTIAL_STAGE_BROWSER_FAILED",
      phase,
    };
  } finally {
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
  }
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const result = await run(process.argv.slice(2));
  process.stdout.write(JSON.stringify(result) + "\n");
  process.exitCode = result.success ? 0 : 1;
}
