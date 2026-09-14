// Local browser regression with explicitly synthetic API fixtures. No cloud
// connection, credentials, fault injection or production data modification.
import http from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import os from "node:os";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const dist = path.join(root, "web/dist");
const output = path.join(root, "output/frontend-review-20260909");
await mkdir(output, { recursive: true });
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
await new Promise((resolve) => server.listen(5174, "127.0.0.1", resolve));
const chrome = process.env.MINI_DROP_ACCEPTANCE_CHROME || path.join(process.env.LOCALAPPDATA, "ms-playwright/chromium-1161/chrome-win/chrome.exe");
assert(existsSync(chrome), "Set MINI_DROP_ACCEPTANCE_CHROME to a Chromium executable");
const browser = spawn(chrome, ["--headless=new", "--remote-debugging-port=9336", `--user-data-dir=${path.join(os.tmpdir(), `mini-drop-ui-${process.pid}`)}`, "--no-first-run", "about:blank"], { windowsHide: true, stdio: "ignore" });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let socket;
const errors = [];
const checks = [];
try {
  let target;
  for (let i = 0; i < 40; i++) {
    try { target = await (await fetch("http://127.0.0.1:9336/json/new?about%3Ablank", { method: "PUT" })).json(); break; } catch { await pause(250); }
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
  const navigate = async (route) => { await send("Page.navigate", { url: `http://127.0.0.1:5174${route}` }); await waitFor("Boolean(document.querySelector('.diagnosis-composer'))"); };
  await send("Page.enable"); await send("Runtime.enable");
  await viewport(1366, 768); await navigate("/ai-diagnosis");
  assert(await evaluate("document.querySelector('.diagnosis-composer textarea').getBoundingClientRect().bottom < innerHeight"), "New diagnosis input must fit in first viewport");
  await clickText("CPU 升高");
  assert(await evaluate("document.querySelector('textarea').value.includes('订单服务')"));
  await screenshot("01-start-desktop.png"); checks.push("desktop composer visible; example fills input");
  await viewport(390, 844); await navigate("/ai-diagnosis");
  await pause(300);
  assert(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Mobile page must not overflow horizontally");
  await screenshot("02-start-mobile.png"); checks.push("mobile no page overflow");
  await viewport(1366, 768); await navigate("/ai-diagnosis?case=drop_insight_v2%3Aui-fixture");
  await waitFor("Boolean(document.querySelector('.diagnosis-finding'))");
  assert(await evaluate("document.querySelector('.diagnosis-finding').getBoundingClientRect().top < 500"));
  await screenshot("03-finding-desktop.png"); checks.push("persisted finding above investigation");
  await viewport(1093, 768); await pause(300);
  assert(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Narrow laptop must not overflow");
  await screenshot("07-finding-narrow.png");
  await viewport(390, 844); await pause(300);
  assert(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Active mobile diagnosis must not overflow");
  await screenshot("08-finding-mobile.png"); checks.push("active diagnosis fits narrow laptop and mobile");
  await viewport(1366, 768); await navigate("/ai-diagnosis?case=drop_insight_v2%3Aui-fixture");
  await waitFor("Boolean(document.querySelector('.diagnosis-finding'))");
  await clickText("探索树");
  await waitFor("Boolean(document.querySelector('[aria-label=\"真实父子探索树\"]'))");
  assert(await evaluate("document.querySelector('.diagnosis-tree-fit-viewport').getBoundingClientRect().top < 650"), "Tree canvas must start in laptop viewport");
  await screenshot("04-tree-desktop.png");
  await evaluate("document.querySelector('[aria-label=\"全屏查看探索树\"]').click()");
  await waitFor("Boolean(document.querySelector('.diagnosis-tree-fullscreen-modal'))");
  await screenshot("05-tree-fullscreen.png"); checks.push("tree and fullscreen controls work");
  await navigate("/ai-diagnosis?case=drop_insight_v2%3Aui-fixture");
  await waitFor("Boolean(document.querySelector('.diagnosis-finding'))");
  failReports = true;
  for (const stream of streams) stream.write(`event: diagnosis_progress\ndata: ${JSON.stringify({ diagnosis_id: "ui-fixture" })}\n\n`);
  await waitFor("document.body.innerText.includes('部分数据加载失败：报告')");
  assert(await evaluate("document.querySelector('.diagnosis-finding').innerText.includes('calculateTotal')"));
  await screenshot("06-stale-data.png");
  failReports = false; await clickText("重试");
  await waitFor("!document.body.innerText.includes('部分数据加载失败：报告')"); checks.push("failed refresh retains report; retry clears warning");
  assert.equal(errors.length, 0, `Browser exceptions: ${errors.join(', ')}`);
  await writeFile(path.join(output, "result.json"), JSON.stringify({ scope: "LOCAL_SYNTHETIC_UI_FIXTURES", checks, browserExceptions: errors, passed: true }, null, 2));
  console.log(JSON.stringify({ passed: true, checks, output }));
} finally {
  socket?.close(); browser.kill();
  for (const stream of streams) stream.end();
  if (!process.argv.includes("--serve")) server.close();
  else console.log("Read-only fixture preview: http://127.0.0.1:5174/ai-diagnosis");
}
