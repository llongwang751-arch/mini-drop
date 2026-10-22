/** 仅访问本轮可销毁回环实例；网络拦截只检查同origin，不填充或伪造API响应。 */
import { chromium } from "playwright";
import { readFileSync, writeFileSync } from "node:fs";

const configuration = JSON.parse(readFileSync(process.argv[2], "utf8"));
let phase = "configuration";
let browser;
let lastTask;
let lastHttp;
let agentReadiness;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const require = (condition) => {
  if (!condition) throw new Error("QUALITY_BROWSER_ASSERTION");
};
async function main() {
  require(/^txdrop-dev-qa-[a-f0-9]{12}$/.test(configuration.project));
  require(/^http:\/\/127\.0\.0\.1:[0-9]{4,5}$/.test(configuration.origin));
  phase = "launch";
  browser = await chromium.launch({
    headless: true,
    ...(configuration.browser_executable
      ? { executablePath: configuration.browser_executable }
      : {}),
    args: ["--disable-dev-shm-usage", "--no-sandbox"],
  });
  const context = await browser.newContext({
    baseURL: configuration.origin,
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  let blockedExternalRequests = 0;
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === configuration.origin) await route.continue();
    else {
      blockedExternalRequests++;
      await route.abort("blockedbyclient");
    }
  });
  page.setDefaultTimeout(20000);
  let pageErrors = 0;
  page.on("pageerror", () => pageErrors++);
  // 不启用 trace、视频、HAR 或浏览器 storageState 文件，防止凭据和签名 URL 进入报告。
  const data = async (route) => {
    const response = await page.request.get("/api/v1" + route, {
      timeout: 15000,
      maxRedirects: 0,
    });
    // 只记录固定接口类别及状态码，禁止把包含任务ID、查询和凭据的完整URL写进回执。
    lastHttp = {
      area: route.startsWith("/agents?") ? "agents" : "task",
      status: response.status(),
    };
    require(response.ok());
    const raw = await response.body();
    require(raw.byteLength <= 2 * 1024 * 1024);
    return JSON.parse(raw.toString()).data;
  };
  const waitTask = async (id, predicate, timeout = 90000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const task = await data("/tasks/" + id);
      lastTask = {
        id: String(task.id),
        collection_status: task.collection_status,
        analysis_status: task.analysis_status,
        error_code: /^[A-Z0-9_]{1,80}$/.test(task.error_code || "")
          ? task.error_code
          : null,
        attempts: task.attempts.length,
      };
      if (predicate(task)) return task;
      await delay(400);
    }
    throw new Error("QUALITY_BROWSER_TASK_TIMEOUT");
  };
  phase = "login";
  await page.goto("/");
  await page
    .getByLabel("用户名", { exact: true })
    .fill(
      readFileSync(configuration.secrets + "/admin_username", "utf8").trim(),
    );
  await page
    .getByLabel("密码", { exact: true })
    .fill(
      readFileSync(configuration.secrets + "/admin_password", "utf8").trim(),
    );
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
  phase = "login_wait_application_shell";
  await page.locator(".app-shell").waitFor({ state: "visible" });
  phase = "controlled_agent_ready";
  const deadline = Date.now() + 45000;
  while (true) {
    const agents = (await data("/agents?page_size=20")).items;
    require(Array.isArray(agents));
    const controlled = agents.find((a) => a.id === "aliyun-local");
    agentReadiness = {
      listed_agents: Math.min(agents.length, 20),
      controlled_agent_found: !!controlled,
      proc_cpu_available: !!controlled?.capabilities?.includes("PROC_CPU"),
      controlled_uid_published: !!controlled?.resources?.processes?.some(
        (p) => p.uid === 10001,
      ),
    };
    if (
      agents.some(
        (a) =>
          a.id === "aliyun-local" &&
          a.capabilities.includes("PROC_CPU") &&
          a.resources.processes?.some((p) => p.uid === 10001),
      )
    )
      break;
    require(Date.now() < deadline);
    await delay(500);
  }
  const create = async (duration, suffix) => {
    phase = "create_" + suffix + "_open_form";
    await page.goto("/tasks/new");
    phase = "create_" + suffix + "_select_procfs";
    await page.locator(".kind-option").filter({ hasText: "procfs" }).click();
    phase = "create_" + suffix + "_select_agent_and_process";
    await page.getByLabel(/Agent 节点/).selectOption("aliyun-local");
    await page.locator(".process-picks button").first().click();
    phase = "create_" + suffix + "_fill_name";
    await page
      .getByLabel("任务名称", { exact: true })
      .fill("Synthetic browser quality " + suffix);
    // label还包含“秒/Hz”单位；按明确字段前缀匹配，避免把可访问名称误当纯标题。
    phase = "create_" + suffix + "_fill_duration";
    await page.getByLabel(/^采集时长/).fill(String(duration));
    phase = "create_" + suffix + "_fill_frequency";
    await page.getByLabel(/^采样频率/).fill("10");
    phase = "create_" + suffix + "_submit_and_navigate";
    await page.getByRole("button", { name: "创建诊断", exact: true }).click();
    await page.waitForURL(/\/tasks\/[1-9][0-9]*$/);
    return new URL(page.url()).pathname.split("/").pop();
  };
  const showRealResult = async (id) => {
    phase = "result_wait_real_collection_analysis";
    const task = await waitTask(
      id,
      (t) =>
        ["FAILED", "CANCELED"].includes(t.collection_status) ||
        ["SUCCESS", "FAILED"].includes(t.analysis_status),
    );
    require(
      task.collection_status === "COLLECTED" &&
        task.analysis_status === "SUCCESS",
    );
    phase = "result_render_real_gzip_artifact";
    await page.reload();
    await page
      .locator('[data-flame-source="gzip-artifact"]')
      .waitFor({ state: "visible", timeout: 30000 });
    const result = await data("/tasks/" + id + "/result");
    require(
      result.total_samples > 0 &&
        result.summary.unit === "cpu_ticks" &&
        result.summary.source === "procfs",
    );
    return task;
  };
  phase = "browser_create_real_collection";
  const baseline = await create(2, "baseline");
  await showRealResult(baseline);
  phase = "browser_cancel_and_retry";
  const retried = await create(15, "cancel-retry");
  await waitTask(retried, (t) => t.collection_status === "RUNNING", 45000);
  await page.getByRole("button", { name: "取消任务", exact: true }).click();
  const canceled = await waitTask(
    retried,
    (t) => t.collection_status === "CANCELED",
  );
  require(
    canceled.attempts.length === 1 &&
      canceled.attempts[0].status === "CANCELED",
  );
  await page.reload();
  await page.getByRole("button", { name: "重试诊断", exact: true }).click();
  await waitTask(retried, (t) => t.attempts.length === 2);
  const final = await showRealResult(retried);
  require(
    final.attempts.length === 2 &&
      final.attempts[0].status === "CANCELED" &&
      final.attempts[1].attempt === 2,
  );
  require(final.events.filter((e) => e.type === "TASK_CANCELED").length === 1);
  require(final.events.filter((e) => e.type === "TASK_RETRIED").length === 1);
  require(pageErrors === 0);
  require(blockedExternalRequests === 0);
  const receipt = {
    schema_version: 1,
    passed: true,
    provenance:
      "real browser, actual HTTP/mTLS Core/Agent collection, S3 and Analyzer; synthetic controlled workload",
    baseline_task_id: baseline,
    retried_task_id: retried,
    browser_login: true,
    browser_created_tasks: 2,
    canceled_attempt_preserved: true,
    retry_created_attempt_2: true,
    rendered_actual_gzip_artifact: true,
    route_interception_used: true,
    route_interception_scope: "same-origin network allowlist only",
    business_response_fulfillment_used: false,
    blocked_external_requests: blockedExternalRequests,
    model_calls: 0,
    page_errors: pageErrors,
  };
  writeFileSync(configuration.output, JSON.stringify(receipt, null, 2) + "\n", {
    mode: 0o600,
  });
  console.log(JSON.stringify({ passed: true, phase: "complete" }));
}
try {
  await main();
} catch (error) {
  const failure = {
    schema_version: 1,
    passed: false,
    error_code: "QUALITY_BROWSER_FAILED",
    phase,
    error_name: ["Error", "TimeoutError", "TypeError"].includes(error?.name)
      ? error.name
      : "UnknownError",
    ...(lastTask ? { last_observed_task: lastTask } : {}),
    ...(lastHttp ? { last_http: lastHttp } : {}),
    ...(agentReadiness ? { agent_readiness: agentReadiness } : {}),
  };
  // 固定步骤码和有限任务状态足够定位门禁失败，不保存页面/错误正文/签名URL。
  writeFileSync(configuration.output, JSON.stringify(failure, null, 2) + "\n", {
    mode: 0o600,
  });
  console.log(JSON.stringify(failure));
  process.exitCode = 1;
} finally {
  await browser?.close();
}
