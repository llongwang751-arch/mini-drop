import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const apiKey = process.env.MINI_DROP_ACCEPTANCE_API_KEY;
if (!apiKey) {
  throw new Error("MINI_DROP_ACCEPTANCE_API_KEY is required");
}

const root = path.resolve(import.meta.dirname, "..");
const outputDir = path.join(root, "output", "acceptance", "20260907T181512Z");
const chromePath = process.env.MINI_DROP_ACCEPTANCE_CHROME || path.join(
  process.env.LOCALAPPDATA || "",
  "ms-playwright",
  "chromium-1161",
  "chrome-win",
  "chrome.exe",
);
const debuggingPort = 9333;
const profileDir = path.join(os.tmpdir(), `mini-drop-ui-acceptance-${process.pid}`);
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
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await send("Page.addScriptToEvaluateOnNewDocument", {
    source: `localStorage.setItem("mini-drop-api-key", ${JSON.stringify(apiKey)});`,
  });

  async function openAndWait(url, expectedText) {
    await send("Page.navigate", { url });
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await wait(500);
      const response = await send("Runtime.evaluate", {
        expression: "document.body ? document.body.innerText : ''",
        returnByValue: true,
      });
      const body = response.result?.value || "";
      if (expectedText.every((text) => body.includes(text))) return body;
    }
    throw new Error(`Timed out waiting for: ${expectedText.join(" | ")}`);
  }

  async function screenshot(filename) {
    const response = await send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    await writeFile(path.join(outputDir, filename), Buffer.from(response.data, "base64"));
  }

  async function scrollCardIntoView(text) {
    await send("Runtime.evaluate", {
      expression: `(() => {
        const node = [...document.querySelectorAll("*")]
          .find((item) => item.children.length === 0 && item.textContent?.trim() === ${JSON.stringify(text)});
        const target = node?.closest(".ant-card") || node;
        target?.scrollIntoView({ block: "center" });
        return Boolean(target);
      })()`,
      returnByValue: true,
    });
    await wait(500);
  }

  await openAndWait(
    "https://120.24.187.205/ai-diagnosis?case=drop_insight_v2%3Ainsight_c23e8c442c2343f1bd10cb39bb8666af",
    [
      "阶段性根因",
      "Hotspot.lambda$startWorkers$1",
      "Java 对象分配热点",
      "没有独立证明 GC 暂停或锁竞争",
    ],
  );
  await scrollCardIntoView("阶段性根因");
  await screenshot("qualified-java-root-cause.png");

  const sysMetricsBody = await openAndWait(
    "https://120.24.187.205/task/task_20260907_072702_5a7b9c",
    [
      "已恢复历史系统指标",
      "15",
      "CPU、负载、I/O 与网络未被采集",
      "系统指标产物已完成契约校验",
    ],
  );
  if (sysMetricsBody.includes("系统多维指标已通过 sys_metrics.v2 契约验证")) {
    throw new Error("Historical sys_metrics.v1 task still displays the false v2 claim");
  }
  await scrollCardIntoView("已恢复历史系统指标");
  await screenshot("sys-metrics-v1-compatible.png");

  process.stdout.write(`${JSON.stringify({
    pageAcceptance: "PASS",
    java: {
      title: "阶段性根因",
      function: "Hotspot.lambda$startWorkers$1",
    },
    sysMetrics: {
      samples: 15,
      contract: "sys_metrics.v1 compatible",
    },
  }, null, 2)}\n`);
} finally {
  socket?.close();
  browser.kill();
}
