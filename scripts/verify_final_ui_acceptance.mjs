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
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(String(event.data));
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
    validation_center: "PASS",
    experiment_panel: "PASS",
    diagnosis_memory: diagnosisId ? "PENDING" : "SKIPPED_NO_DIAGNOSIS_ID",
  };
  if (diagnosisId) {
    const caseId = encodeURIComponent(`drop_insight_v2:${diagnosisId}`);
    await openAndWait(`${baseUrl}/ai-diagnosis?case=${caseId}`, ["记忆", "证据", "工具"]);
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

  process.stdout.write(`${JSON.stringify({
    schema: "mini-drop.final-ui-acceptance.v1",
    base_url: baseUrl,
    release,
    diagnosis_id: diagnosisId || null,
    checks,
    output_dir: outputDir,
    human_visual_acceptance: "PENDING_USER_CONFIRMATION",
  }, null, 2)}\n`);
} finally {
  socket?.close();
  browser.kill();
}
