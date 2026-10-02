import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const apiKey = process.env.MINI_DROP_ACCEPTANCE_API_KEY;
if (!apiKey) throw new Error("MINI_DROP_ACCEPTANCE_API_KEY is required");

const baseUrl = (process.env.MINI_DROP_ACCEPTANCE_BASE_URL || "https://120.24.187.205")
  .replace(/\/$/, "");
const diagnosisId = String(process.env.MINI_DROP_ACCEPTANCE_DIAGNOSIS_ID || "").trim();
const release = String(process.env.MINI_DROP_ACCEPTANCE_RELEASE || "candidate")
  .replace(/[^A-Za-z0-9_.-]/g, "_");
const root = path.resolve(import.meta.dirname, "..");
const outputDir = path.join(root, "output", "acceptance", release);
const defaultChrome = path.join(
  process.env.LOCALAPPDATA || "",
  "ms-playwright",
  "chromium-1161",
  "chrome-win",
  "chrome.exe",
);
const chromePath = process.env.MINI_DROP_ACCEPTANCE_CHROME || defaultChrome;
if (!existsSync(chromePath)) {
  throw new Error(`Chromium was not found at ${chromePath}`);
}

const debuggingPort = 9334;
const profileDir = path.join(os.tmpdir(), `mini-drop-final-ui-${process.pid}`);
await mkdir(outputDir, { recursive: true });

const browser = spawn(chromePath, [
  "--headless=new",
  "--ignore-certificate-errors",
  `--remote-debugging-port=${debuggingPort}`,
  `--user-data-dir=${profileDir}`,
  "--no-first-run",
  "--disable-gpu",
  "about:blank",
], { windowsHide: true, stdio: "ignore" });

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function connect() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      await fetch(`http://127.0.0.1:${debuggingPort}/json/version`);
      const response = await fetch(
        `http://127.0.0.1:${debuggingPort}/json/new?about%3Ablank`,
        { method: "PUT" },
      );
      return response.json();
    } catch {
      await wait(250);
    }
  }
  throw new Error("Chromium CDP did not start");
}

let socket;
try {
  const target = await connect();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  let sequence = 0;
  const pending = new Map();
  const browserExceptions = [];
  const blockedMutations = [];
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(String(event.data));
    if (message.method === "Runtime.exceptionThrown") {
      browserExceptions.push(message.params.exceptionDetails.text);
    }
    if (message.method === "Fetch.requestPaused") {
      const { requestId, request } = message.params;
      const isSessionAuthentication = request.method === "POST"
        && new URL(request.url).pathname === "/api/auth/session";
      if (!["GET", "HEAD", "OPTIONS"].includes(request.method) && !isSessionAuthentication) {
        blockedMutations.push({ method: request.method, path: new URL(request.url).pathname });
        void send("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" });
      } else {
        void send("Fetch.continueRequest", { requestId });
      }
    }
    if (!message.id || !pending.has(message.id)) return;
    const handler = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) handler.reject(new Error(message.error.message));
    else handler.resolve(message.result || {});
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });

  await send("Page.enable");
  await send("Runtime.enable");
  // This tour reads existing server data; never start a fault or mutate a Skill.
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*", requestStage: "Request" }] });
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1600,
    height: 1100,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await send("Page.addScriptToEvaluateOnNewDocument", {
    source: `localStorage.setItem("mini-drop-api-key", ${JSON.stringify(apiKey)});`,
  });

  async function bodyText() {
    const response = await send("Runtime.evaluate", {
      expression: "document.body ? document.body.innerText : ''",
      returnByValue: true,
    });
    return response.result?.value || "";
  }

  async function waitForTexts(expectedTexts, attempts = 80) {
    let lastBody = "";
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      await wait(500);
      lastBody = await bodyText();
      if (expectedTexts.every((text) => lastBody.includes(text))) return lastBody;
    }
    const excerpt = lastBody.replace(/\s+/g, " ").trim().slice(0, 1200);
    throw new Error(
      `Timed out waiting for: ${expectedTexts.join(" | ")}; page text: ${excerpt || "<empty>"}`,
    );
  }

  async function openAndWait(url, expectedTexts) {
    await send("Page.navigate", { url });
    return waitForTexts(expectedTexts);
  }

  async function screenshot(filename) {
    const response = await send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    await writeFile(path.join(outputDir, filename), Buffer.from(response.data, "base64"));
  }

  async function clickExactText(text) {
    const response = await send("Runtime.evaluate", {
      expression: `(() => {
        const node = [...document.querySelectorAll("button, [role='button'], .ant-card, *")]
          .find((item) => item.textContent?.trim() === ${JSON.stringify(text)});
        if (!node) return false;
        node.scrollIntoView({ block: "center" });
        node.click();
        return true;
      })()`,
      returnByValue: true,
    });
    if (!response.result?.value) throw new Error(`Could not click text: ${text}`);
  }

  async function clickTextStartsWith(text) {
    const response = await send("Runtime.evaluate", {
      expression: `(() => {
        const node = [...document.querySelectorAll("button, [role='button'], [role='tab'], *")]
          .find((item) => item.textContent?.trim().startsWith(${JSON.stringify(text)}));
        if (!node) return false;
        node.scrollIntoView({ block: "center" });
        node.click();
        return true;
      })()`,
      returnByValue: true,
    });
    if (!response.result?.value) throw new Error(`Could not click text prefix: ${text}`);
  }

  await openAndWait(`${baseUrl}/ai-diagnosis`, [
    "Agent 工作台",
    "验证与 A/B",
  ]);
  await waitForTexts(["诊断说明", "用故障广场开始演示"]);
  await screenshot("workbench-start-desktop.png");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 390, height: 844, deviceScaleFactor: 1, mobile: true,
  });
  await wait(500);
  const mobile = await send("Runtime.evaluate", {
    expression: "document.documentElement.scrollWidth <= innerWidth + 1",
    returnByValue: true,
  });
  if (!mobile.result?.value) throw new Error("Mobile page overflows horizontally");
  await screenshot("workbench-start-mobile.png");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1366, height: 768, deviceScaleFactor: 1, mobile: false,
  });
  await clickExactText("验证与 A/B");
  await waitForTexts(["诊断验证中心", "故障广场", "Skill A/B"]);
  await screenshot("validation-center.png");

  await clickExactText("故障广场");
  await waitForTexts([
    "故障广场",
    "完整 LATS 冻结回放",
  ]);
  await screenshot("fault-plaza-and-lats.png");

  await clickExactText("Skill A/B");
  await waitForTexts([
    "服务端随机实验",
    "新建 50/50 实验",
  ]);
  await screenshot("skill-experiment.png");

  const checks = {
    new_workbench: "PASS",
    mobile_no_horizontal_overflow: "PASS",
    validation_center: "PASS",
    experiment_panel: "PASS",
    diagnosis_memory: diagnosisId ? "PENDING" : "SKIPPED_NO_DIAGNOSIS_ID",
  };
  if (diagnosisId) {
    const caseId = encodeURIComponent(`drop_insight_v2:${diagnosisId}`);
    await openAndWait(`${baseUrl}/ai-diagnosis?case=${caseId}`, ["记忆", "证据", "工具"]);
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const result = await send("Runtime.evaluate", {
        expression: "!!document.querySelector('.diagnosis-finding')", returnByValue: true,
      });
      if (result.result?.value) break;
      if (attempt === 59) throw new Error("Persisted report summary is missing");
      await wait(500);
    }
    await screenshot("persisted-finding.png");
    await clickExactText("探索树");
    await wait(700);
    await screenshot("live-exploration-tree.png");
    const fullscreen = await send("Runtime.evaluate", {
      expression: "(() => { const button = document.querySelector('button[aria-label=\"全屏查看探索树\"]'); if (!button) return false; button.click(); return true; })()",
      returnByValue: true,
    });
    if (!fullscreen.result?.value) throw new Error("Fullscreen tree button is missing");
    await waitForTexts(["实时诊断探索树 · 全屏阅读"]);
    await screenshot("live-exploration-tree-fullscreen.png");
    await send("Runtime.evaluate", { expression: "document.querySelector('.ant-modal-close')?.click()" });
    checks.persisted_finding_and_tree = "PASS";
    await clickExactText("记忆");
    await waitForTexts([
      "跨诊断偏好",
      "长期路线记忆",
      "会话记忆用于续写当前调查",
    ]);
    await clickTextStartsWith("跨诊断偏好");
    await waitForTexts([
      "只保存用户明确选择的稳定偏好",
    ]);
    await screenshot("diagnosis-memory.png");
    checks.diagnosis_memory = "PASS";
  }

  if (browserExceptions.length) throw new Error(`Browser exceptions: ${browserExceptions.join(", ")}`);
  const report = {
    schema: "mini-drop.final-ui-acceptance.v1",
    base_url: baseUrl,
    release,
    diagnosis_id: diagnosisId || null,
    checks,
    scope: "PUBLIC_HTTPS_READ_ONLY_EXISTING_DATA",
    browser_exceptions: browserExceptions,
    blocked_mutations: blockedMutations,
    output_dir: outputDir,
    passed: true,
  };
  await writeFile(path.join(outputDir, "result.json"), `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
} finally {
  socket?.close();
  browser.kill();
}
