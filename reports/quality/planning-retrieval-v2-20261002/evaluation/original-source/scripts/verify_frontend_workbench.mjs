// Local browser regression with explicitly synthetic API fixtures. No cloud
// connection, credentials, fault injection or production data modification.
import http from "node:http";
import { readFile, mkdir, writeFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import os from "node:os";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const dist = path.join(root, "web/dist");
const outputIndex = process.argv.indexOf("--output");
const output = path.resolve(root, outputIndex > 0 ? process.argv[outputIndex + 1] : "output/frontend-review-20260909");
await mkdir(output, { recursive: true });
// Generated engineering grades supply a realistic table shape, but these local
// API responses remain UI fixtures. No saved live diagnosis is opened here.
const engineeringFixture = JSON.parse(await readFile(path.join(root,
  "web/public/report-assets/engineering-diagnosis/index.json"), "utf8"));
engineeringFixture.historical_root_passes = 0;
engineeringFixture.historical_case_count = 21;
engineeringFixture.cases = engineeringFixture.cases.map(item => ({ ...item,
  title: "[界面测试数据] " + item.title, diagnosis_id: "ui-fixture" }));
const faultScenarios = engineeringFixture.cases.map(item => ({
  scenario_id: item.scenario_id, title: item.title, family: "RUNTIME", active: false,
  target_runtime: item.scenario_id.startsWith("go-") ? "Go"
    : item.scenario_id.startsWith("java-") ? "Java"
      : item.scenario_id.startsWith("cpp-") ? "C++" : "Python",
  available: true, symptom: "[界面测试数据] 观察目标进程的性能窗口。",
  acceptance_level: "HISTORICAL_LINEAGE_VERIFIED",
  latest_acceptance: { passed: false, root_cause_accepted: false,
    diagnosis_id: "ui-retired-result", tested_release: "ui-retired-release" },
}));
let legacyAuditRequests = 0;
const diagnosis = {
  diagnosis_id: "ui-fixture", query: "[界面测试数据] 订单服务 CPU 升高，检查业务热点与同机争抢",
  status: "COMPLETED", mode: "AUTONOMOUS", diagnosis_version: 4,
  created_at: "2026-09-09T09:00:00Z", updated_at: "2026-09-09T09:02:00Z",
  target: { service: "order-service", agent_id: "ui-test-agent", pid: 1234 },
};
const reports = [{ report_id: "ui-report", hypothesis_id: "h-1", version: 1,
  conclusion: "CPU 热点集中在业务循环 `orderService.calculateTotal`，需要对照采样确认其对请求延迟的贡献。",
  confidence: 0.7, evidence_refs: ["ui-evidence"], counter_evidence_refs: [],
  verification: { status: "PARTIAL_WITHOUT_COUNTER" },
  limitations: ["尚未完成修复前后对照"], next_actions: ["缩小热点循环后，在相同负载下重新采样。"],
}];
const hypotheses = [
  { hypothesis_id: "h-1", statement: "业务循环消耗 CPU", status: "SUPPORTED", round_index: 1 },
  { hypothesis_id: "h-2", statement: "同机进程争抢资源", status: "REFUTED", round_index: 2 },
];
const tree = { revision: 8, status: "COMPLETED", stats: { current_round: 2, rounds: 2, nodes: 4, pruned: 1 },
  nodes: [
    { id: "diagnosis:ui-fixture", kind: "diagnosis", title: "定位订单服务 CPU 异常", state: "visited", parent_id: null },
    ...hypotheses.map((h) => ({ id: `hypothesis:${h.hypothesis_id}`, kind: "hypothesis", title: h.statement,
      parent_id: "diagnosis:ui-fixture", round_index: h.round_index, state: h.status === "REFUTED" ? "refuted" : "confirmed" })),
    { id: "tool:ui-tool", kind: "tool_call", parent_id: "hypothesis:h-1", title: "Go pprof 采样", state: "visited" },
  ], search: { algorithm: "LATS-UCT", execution_mode: "BUDGETED_LATS", environment_semantics: "LIVE_PROGRESSIVE",
    phase: "TERMINATED", iteration: 2, budget: { max_iterations: 4, used_iterations: 2 },
    termination: { stopped: true, reason: "BUDGET_EXHAUSTED" } },
};
let failReports = false;
const streams = new Set();
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (url.pathname === "/report-assets/engineering-diagnosis/index.json") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(engineeringFixture)); return;
  }
  if (url.pathname === "/report-assets/performance-audit/index.json") {
    legacyAuditRequests++;
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ schema: "mini-drop.performance-failure-audit.v1",
      historical_root_passes: 0, historical_case_count: 21, recorded_chain_consistent_count: 18,
      cases: faultScenarios.map(item => ({ scenario_id: item.scenario_id })) })); return;
  }
  if (url.pathname.startsWith("/api/")) {
    if (url.pathname.includes("stream")) {
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
      res.write(": local fixture stream\n\n"); streams.add(res); req.on("close", () => streams.delete(res)); return;
    }
    if (req.method !== "GET" && !url.pathname.includes("session")) {
      res.writeHead(405); res.end("Fixture preview is read-only"); return;
    }
    let data = [];
    if (url.pathname === "/api/v2/diagnoses") data = [diagnosis];
    else if (url.pathname === "/api/v2/showcases/fault-plaza") data = { status: "READY", scenarios: faultScenarios };
    else if (url.pathname === "/api/v2/diagnoses/ui-fixture") data = diagnosis;
    else if (url.pathname.endsWith("/reports")) {
      if (failReports) { res.writeHead(503, { "Content-Type": "application/json" }); res.end('{"detail":"fixture refresh failure"}'); return; }
      data = reports;
    }
    else if (url.pathname.endsWith("/exploration-tree")) data = tree;
    else if (url.pathname.endsWith("/hypotheses")) data = hypotheses;
    else if (url.pathname.endsWith("/tool-calls")) data = [{ tool_call_id: "ui-tool", hypothesis_id: "h-1", tool_name: "collect_go_profile", status: "COMPLETED" }];
    else if (url.pathname.endsWith("/budget")) data = {};
    else if (url.pathname.endsWith("/agent-runtime/status")) data = { framework: "langchain-create-agent/langgraph", status: "HEALTHY", checkpoint_backend: "postgres" };
    res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify({ code: 0, data, message: "local fixture" })); return;
  }
  try {
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, "");
    const resolved = path.resolve(dist, relative);
    if (resolved !== dist && !resolved.startsWith(dist + path.sep)) { res.writeHead(403); res.end(); return; }
    const file = relative.startsWith("assets/") ? resolved : path.join(dist, "index.html");
    const content = await readFile(file);
    const mime = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html" }[path.extname(file)] || "application/octet-stream";
    res.writeHead(200, { "Content-Type": mime }); res.end(content);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolve);
});
const httpPort = server.address().port;
// MINI_DROP_ACCEPTANCE_CHROME wins; otherwise use the newest installed
// Playwright Chromium (Windows and Linux layouts) so CI and local runs share one path.
async function resolveChrome() {
  if (process.env.MINI_DROP_ACCEPTANCE_CHROME) return process.env.MINI_DROP_ACCEPTANCE_CHROME;
  const bases = process.platform === "win32"
    ? [path.join(process.env.LOCALAPPDATA ?? "", "ms-playwright")]
    : [path.join(os.homedir(), ".cache", "ms-playwright")];
  const layouts = process.platform === "win32"
    ? [path.join("chrome-win64", "chrome.exe"), path.join("chrome-win", "chrome.exe")]
    : [path.join("chrome-linux", "chrome")];
  for (const base of bases) {
    if (!existsSync(base)) continue;
    const revisions = (await readdir(base)).filter((name) => name.startsWith("chromium-"))
      .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
    for (const revision of revisions) {
      for (const layout of layouts) {
        const executable = path.join(base, revision, layout);
        if (existsSync(executable)) return executable;
      }
    }
  }
  return path.join(process.env.LOCALAPPDATA ?? "", "ms-playwright/chromium-1161/chrome-win/chrome.exe");
}
const chrome = await resolveChrome();
assert(existsSync(chrome), `Set MINI_DROP_ACCEPTANCE_CHROME to a Chromium executable (looked for: ${chrome})`);
const browserProfile = path.join(os.tmpdir(), `mini-drop-ui-${process.pid}`);
const browser = spawn(chrome, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${browserProfile}`, "--no-first-run", "about:blank"], { windowsHide: true, stdio: "ignore" });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let socket;
const errors = [];
const checks = [];
try {
  let target;
  for (let i = 0; i < 40; i++) {
    try {
      const debugPort = (await readFile(path.join(browserProfile, "DevToolsActivePort"), "utf8")).split("\n")[0].trim();
      target = await (await fetch(`http://127.0.0.1:${debugPort}/json/new?about%3Ablank`, { method: "PUT" })).json(); break;
    } catch { await pause(250); }
  }
  assert(target, "Chromium did not start");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let sequence = 0;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const result = JSON.parse(String(event.data));
    if (result.method === "Runtime.exceptionThrown") errors.push(result.params.exceptionDetails.text);
    const callback = pending.get(result.id);
    if (!callback) return;
    pending.delete(result.id);
    result.error ? callback.reject(new Error(result.error.message)) : callback.resolve(result.result);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result?.value;
  };
  const waitFor = async (expression) => {
    for (let i = 0; i < 60; i++) { if (await evaluate(expression)) return; await pause(200); }
    throw new Error(`Timed out: ${expression}`);
  };
  const clickText = (text) => evaluate(`(() => { const el = [...document.querySelectorAll('button, label, summary')].find(el => el.textContent.replace(/\\s/g, '') === ${JSON.stringify(text.replace(/\s/g, ""))}); if (!el) throw new Error('Missing control: ' + ${JSON.stringify(text)}); el.click(); })()`);
  const viewport = (width, height) => send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  const screenshot = async (name) => {
    await pause(650);
    const result = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    await writeFile(path.join(output, name), Buffer.from(result.data, "base64"));
  };
  const navigate = async (route) => { await send("Page.navigate", { url: `http://127.0.0.1:${httpPort}${route}` }); await waitFor("Boolean(document.querySelector('.diagnosis-composer'))"); };
  await send("Page.enable"); await send("Runtime.enable");
  await viewport(1366, 768); await navigate("/ai-diagnosis");
  assert(await evaluate("(() => { const entry = document.querySelector('.diagnosis-welcome button') || document.querySelector('.diagnosis-composer textarea'); const box = entry.getBoundingClientRect(); return box.top >= 0 && box.bottom < innerHeight; })()"), "Primary diagnosis entry must fit in the first viewport");
  await clickText("CPU 升高");
  assert(await evaluate("document.querySelector('textarea').value.includes('订单服务')"));
  await waitFor("document.activeElement === document.querySelector('.diagnosis-composer textarea')");
  assert(await evaluate("document.querySelector('.diagnosis-composer textarea').getBoundingClientRect().bottom < innerHeight"), "Choosing a symptom must focus the visible diagnosis input");
  await screenshot("01-start-desktop.png"); checks.push("primary entry visible; example fills and focuses the visible composer");
  await viewport(390, 844); await navigate("/ai-diagnosis");
  await pause(300);
  assert(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Mobile page must not overflow horizontally");
  await screenshot("02-start-mobile.png"); checks.push("mobile no page overflow");
  await viewport(1366, 768); await navigate("/ai-diagnosis?case=drop_insight_v2%3Aui-fixture");
  await waitFor("Boolean(document.querySelector('.diagnosis-finding'))");
  const resultTop = await evaluate("(document.querySelector('.diagnosis-observation') || document.querySelector('.diagnosis-finding')).getBoundingClientRect().top");
  // The current workbench presents the measured check result first. A detailed
  // root finding remains readable below it and must retain its persisted text.
  const viewportHeight = await evaluate("innerHeight");
  assert(resultTop < viewportHeight, `Check result must be visible without scrolling; measured top=${resultTop} vs viewport=${viewportHeight}`);
  assert(await evaluate("document.querySelector('.diagnosis-finding').innerText.includes('calculateTotal')"));
  await screenshot("03-finding-desktop.png"); checks.push(`check result visible in first viewport; persisted finding retained (top=${Math.round(resultTop)}px)`);
  await viewport(1093, 768); await pause(300);
  assert(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Narrow laptop must not overflow");
  await screenshot("07-finding-narrow.png");
  await viewport(390, 844); await pause(300);
  assert(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Active mobile diagnosis must not overflow");
  await screenshot("08-finding-mobile.png"); checks.push("active diagnosis fits narrow laptop and mobile");
  await viewport(1366, 768); await navigate("/ai-diagnosis?case=drop_insight_v2%3Aui-fixture");
  await waitFor("Boolean(document.querySelector('.diagnosis-finding'))");
  // The exam redesign makes the real parent-child tree the default workbench view;
  // the canvas sits below the exam journey and observability summary, so the
  // position is no longer asserted here — the fullscreen canvas must fill the viewport.
  await waitFor("Boolean(document.querySelector('[aria-label=\"真实父子探索树\"]'))");
  await screenshot("04-tree-desktop.png"); checks.push("default workbench view renders the real parent-child tree");
  await evaluate("document.querySelector('[aria-label=\"全屏查看探索树\"]').click()");
  await waitFor("Boolean(document.querySelector('.diagnosis-tree-fullscreen-modal'))");
  const fullscreenHeight = await evaluate("document.querySelector('.diagnosis-tree-fullscreen-modal .diagnosis-tree-fit-viewport').getBoundingClientRect().height");
  assert(fullscreenHeight > 600, `Fullscreen tree canvas must fill the viewport; measured height=${fullscreenHeight}`);
  await screenshot("05-tree-fullscreen.png"); checks.push(`fullscreen tree canvas fills viewport (height=${Math.round(fullscreenHeight)}px)`);
  await navigate("/ai-diagnosis?case=drop_insight_v2%3Aui-fixture");
  await waitFor("Boolean(document.querySelector('.diagnosis-finding'))");
  failReports = true;
  for (const stream of streams) stream.write(`event: diagnosis_progress\ndata: ${JSON.stringify({ diagnosis_id: "ui-fixture" })}\n\n`);
  await waitFor("document.body.innerText.includes('部分数据加载失败：报告')");
  assert(await evaluate("document.querySelector('.diagnosis-finding').innerText.includes('calculateTotal')"));
  await screenshot("06-stale-data.png");
  failReports = false; await clickText("重试");
  await waitFor("!document.body.innerText.includes('部分数据加载失败：报告')"); checks.push("failed refresh retains report; retry clears warning");
  await navigate("/ai-diagnosis"); await clickText("案例验证");
  await waitFor("document.body.innerText.includes('工程诊断判断通过 21/21') && document.querySelectorAll('.fault-scenario').length === 21");
  assert(await evaluate("document.body.innerText.includes('异常路径定位 6/21') && document.body.innerText.includes('有效反证 8')"),
    "Current engineering scores must retain independent localization and refutation counts");
  assert(await evaluate("!(/原始因果根因|历史严格|根因未通过|为什么旧 21|0\\/21|ui-retired-release/.test(document.body.innerText))"),
    "Legacy scores supplied by mixed-version fixture endpoints must never be displayed");
  assert.equal(legacyAuditRequests, 0, "Retired historical score endpoint must never be loaded");
  assert(await evaluate("document.querySelectorAll('.fault-scenario button').length > 21 && [...document.querySelectorAll('.fault-scenario button')].filter(x => x.textContent.includes('查看工程诊断')).length === 21"),
    "All 21 scenarios must keep their current engineering diagnosis entries");
  assert(await evaluate("!document.querySelector('a[href*=\"performance-audit\"]') && ![...document.querySelectorAll('button')].some(x => x.textContent.includes('查看复验诊断'))"),
    "No legacy score download or diagnosis entry may survive");
  for (const width of [1440, 1024, 768, 375]) {
    await viewport(width, 1000); await pause(250);
    assert(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Current engineering page must fit width " + width);
    await screenshot("09-engineering-current-" + width + ".png");
  }
  checks.push("engineering scores 21/21, localization 6/21 and refutations 8 retain all 21 scenarios at four widths; no legacy score display or fetch despite mixed-version fixtures");
  assert.equal(errors.length, 0, `Browser exceptions: ${errors.join(', ')}`);
  await writeFile(path.join(output, "result.json"), JSON.stringify({ scope: "LOCAL_SYNTHETIC_UI_FIXTURES", checks, browserExceptions: errors,
    legacyAuditRequests, engineeringGrades: { accepted: 21, localized: 6, refuted: 8 }, passed: true }, null, 2));
  console.log(JSON.stringify({ passed: true, checks, output }));
} catch (error) {
  await writeFile(path.join(output, "result.json"), JSON.stringify({ scope: "LOCAL_SYNTHETIC_UI_FIXTURES", checks, browserExceptions: errors, passed: false, error: String(error) }, null, 2));
  throw error;
} finally {
  socket?.close(); browser.kill();
  for (const stream of streams) stream.end();
  if (!process.argv.includes("--serve")) server.close();
  else console.log(`Read-only fixture preview: http://127.0.0.1:${httpPort}/ai-diagnosis`);
}
