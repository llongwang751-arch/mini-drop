import assert from "node:assert/strict";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { chromium, request } from "playwright";

const root = path.resolve(import.meta.dirname, "../..");
const baseURL = process.env.DROP_BASE_URL || "http://120.24.187.205";
const evidenceDir = path.join(root, "docs/evidence");
const fullScale = process.env.DROP_FLAME_FULL_SCALE === "1";
const sizes = fullScale
  ? [
      [10000, 100],
      [50000, 400],
      [100000, 800],
    ]
  : [
      [10000, 100],
      [49000, 400],
    ];
const reportLabel =
  process.env.DROP_FLAME_REPORT_LABEL || (fullScale ? "full" : "");
assert(/^[a-z0-9-]*$/.test(reportLabel));
const reportSuffix = reportLabel ? `-${reportLabel}` : "";
const checkpointPath = path.join(
  root,
  `.local/flame-performance${fullScale ? "-full" : ""}-run.json`,
);
const sessionPath =
  process.env.DROP_FLAME_STORAGE_STATE ||
  path.join(root, ".local/admin-state.json");
mkdirSync(evidenceDir, { recursive: true });
const checkpoint = existsSync(checkpointPath)
  ? JSON.parse(readFileSync(checkpointPath, "utf8"))
  : { created_at: new Date().toISOString(), cases: {} };
const saveCheckpoint = () =>
  writeFileSync(checkpointPath, JSON.stringify(checkpoint, null, 2));
if (process.env.DROP_FLAME_TASK_IDS) {
  const ids = process.env.DROP_FLAME_TASK_IDS.split(",");
  assert.equal(ids.length, sizes.length);
  for (const [index, [nodes]] of sizes.entries()) {
    assert(/^[1-9][0-9]*$/.test(ids[index]));
    checkpoint.cases[nodes] = {
      ...checkpoint.cases[nodes],
      task_id: ids[index],
    };
  }
}
for (const name of [
  "flame-performance.json",
  "flame-large.png",
  "flame-10k.png",
]) {
  const source = path.join(evidenceDir, name);
  const previous = path.join(
    evidenceDir,
    name.replace(/\.(json|png)$/, "-before.$1"),
  );
  if (existsSync(source) && !existsSync(previous))
    copyFileSync(source, previous);
}

function makeFolded(targetNodes, branches) {
  const perBranch = Math.floor((targetNodes - 2) / branches);
  const extra = (targetNodes - 2) % branches;
  let totalWeight = 0;
  const stacks = [];
  for (let lane = 0; lane < branches; lane++) {
    const frames = ["synthetic_fixture_process"];
    const depth = perBranch + (lane < extra ? 1 : 0);
    for (let level = 0; level < depth; level++) {
      frames.push(
        `lane_${String(lane).padStart(4, "0")}_${fullScale ? "f" : "frame"}_${String(level).padStart(3, "0")}`,
      );
    }
    // At 800 lanes the smallest weight must still exceed the real layout's
    // tiny-frame threshold. Keep every node observable without changing it.
    const weight = fullScale ? 3 + (lane % 3) : 1 + (lane % 5);
    totalWeight += weight;
    stacks.push(`${frames.join(";")} ${weight}`);
  }
  const text = stacks.join("\n") + "\n";
  const bytes = Buffer.byteLength(text, "utf8");
  assert(
    bytes <= 2 * 1024 * 1024,
    "Fixture must remain under import size limit",
  );
  assert(perBranch + (extra > 0 ? 1 : 0) + 1 <= 128);
  return {
    text,
    bytes,
    totalWeight,
    branches,
    targetNodes,
    search: `lane_${String(branches - 1).padStart(4, "0")}_${fullScale ? "f" : "frame"}_000`,
    offscreenSearch: `lane_${String(branches - 1).padStart(4, "0")}_${fullScale ? "f" : "frame"}_${String(perBranch - 1).padStart(3, "0")}`,
  };
}

function verifyTree(result, fixture) {
  assert.equal(result.node_count, fixture.targetNodes);
  assert.equal(result.total_samples, fixture.totalWeight);
  assert.equal(result.unique_stacks, fixture.branches);
  assert.equal(result.summary.source, "import");
  let count = 0;
  const queue = [result.flamegraph];
  while (queue.length) {
    const node = queue.pop();
    count++;
    assert.equal(
      node.value,
      node.self + node.children.reduce((sum, child) => sum + child.value, 0),
      "Every actual flame node must preserve its inclusive/self invariant",
    );
    queue.push(...node.children);
  }
  assert.equal(count, fixture.targetNodes);
}

async function unpack(response) {
  if (!response.ok()) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      `API ${response.status()} ${body.error?.code || "HTTP_ERROR"}`,
    );
  }
  return (await response.json()).data;
}

const api = await request.newContext({ baseURL, storageState: sessionPath });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  baseURL,
  storageState: sessionPath,
  viewport: { width: 1440, height: 1000 },
});

// Observe genuine worker timings. Requests and response data are unchanged.
await context.addInitScript(() => {
  window.__flameWorkerMetrics = [];
  window.__flameLongTasks = [];
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (window.__flameLongTasks.length < 100)
          window.__flameLongTasks.push({
            start_ms: entry.startTime,
            duration_ms: entry.duration,
          });
      }
    }).observe({ type: "longtask", buffered: true });
  } catch {
    // Some browsers do not expose the long-task observer.
  }
  const NativeWorker = window.Worker;
  window.Worker = class MeasuredWorker extends NativeWorker {
    constructor(...args) {
      super(...args);
      this.metric = undefined;
      this.addEventListener("message", (event) => {
        if (this.metric && Array.isArray(event.data?.rects)) {
          this.metric.received_at_ms = performance.now();
          this.metric.layout_round_trip_ms =
            this.metric.received_at_ms - this.metric.sent_at_ms;
          this.metric.returned_rects = event.data.rects.length;
          this.metric.layout_height = event.data.height;
          this.metric.total_weight = event.data.total;
        }
      });
    }
    postMessage(message, ...args) {
      if (message?.root) {
        this.metric = { sent_at_ms: performance.now() };
        window.__flameWorkerMetrics.push(this.metric);
      }
      return super.postMessage(message, ...args);
    }
  };
});

const baseline = {
  created_at: new Date().toISOString(),
  target: baseURL,
  browser: browser.version(),
  client: {
    platform: os.platform(),
    architecture: os.arch(),
    cpu_model: os.cpus()[0]?.model,
    logical_cpus: os.cpus().length,
    total_memory_gib: Number((os.totalmem() / 1024 ** 3).toFixed(2)),
    viewport: "1440x1000",
    headless: true,
    cpu_or_network_throttling: false,
  },
  methodology: [
    "One IMPORT_COLLAPSED task per size, executed sequentially against the actual public API and Analyzer.",
    "Fixtures are explicitly synthetic imported data, not a claim of host sampling.",
    "Each branch is wide enough to avoid the UI's 0.6-unit tiny-frame filter. Actual layout node count and visible DOM rect count are independently checked; row virtualization may draw a subset.",
    "Native Worker postMessage/message timestamps are observed; payloads and actual worker code are not mocked or replaced.",
    "Worker round trip includes structured cloning and worker scheduling/layout; DOM-ready time includes React commit and two animation frames.",
    "Search timing starts at the real browser input event and ends after the matching stroke is committed plus two animation frames.",
    "Single trials on this client/server/network are baselines, not latency guarantees or an SLA. The second page may reuse cached assets.",
  ],
  cases: [],
};

try {
  const me = await unpack(await api.get("/api/v1/auth/me"));
  const csrf = me.csrf_token;
  for (const [nodes, branches] of sizes) {
    const fixture = makeFolded(nodes, branches);
    const name = `验收 · ${nodes / 1000}k节点压力 · 合成折叠栈`;
    const saved = (checkpoint.cases[nodes] ||= {
      idempotency_key: `flame-performance-${crypto.randomUUID()}`,
    });
    const reusedTask = Boolean(saved.task_id);
    saveCheckpoint();
    const started = performance.now();
    let importResponseMs = null;
    if (!saved.task_id) {
      const created = await unpack(
        await api.post("/api/v1/tasks", {
          headers: {
            "X-CSRF-Token": csrf,
            "Idempotency-Key": saved.idempotency_key,
          },
          data: {
            name,
            kind: "IMPORT_COLLAPSED",
            collapsed_text: fixture.text,
          },
          timeout: 30000,
        }),
      );
      saved.task_id = created.id;
      importResponseMs = performance.now() - started;
      saveCheckpoint();
    }
    const taskId = saved.task_id;
    console.log(
      `Waiting for actual ${nodes}-node task #${taskId}; input ${fixture.bytes} bytes`,
    );
    const deadline = performance.now() + 90000;
    let detail;
    while (performance.now() < deadline) {
      detail = await unpack(await api.get(`/api/v1/tasks/${taskId}`));
      if (detail.analysis_status === "SUCCESS") break;
      if (
        ["FAILED", "CANCELED"].includes(detail.analysis_status) ||
        ["FAILED", "CANCELED"].includes(detail.collection_status)
      )
        throw new Error(
          `Task #${taskId} failed: ${detail.error_code || detail.analysis_status}`,
        );
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
    assert.equal(detail.analysis_status, "SUCCESS");
    const observedAnalysisWallMs = performance.now() - started;
    const result = await unpack(
      await api.get(`/api/v1/tasks/${taskId}/result`),
    );
    verifyTree(result, fixture);
    const startEvent = detail.events
      .filter((event) => event.type === "ANALYSIS_STARTED")
      .at(-1);
    const finishEvent = detail.events
      .filter((event) => event.type === "ANALYSIS_SUCCEEDED")
      .at(-1);
    const analyzerDurationMs =
      startEvent && finishEvent
        ? Date.parse(finishEvent.created_at) - Date.parse(startEvent.created_at)
        : null;

    const page = await context.newPage();
    const pageErrors = [];
    const httpFailures = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => {
      if (response.status() >= 400)
        httpFailures.push({
          status: response.status(),
          path: new URL(response.url()).pathname,
        });
    });
    await page.goto(`/tasks/${taskId}`, {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });
    await page.waitForFunction(
      (expected) => {
        const svg = document.querySelector(".flame-svg");
        const drawn = document.querySelectorAll(".flame-svg rect").length;
        return (
          drawn > 0 &&
          Number(svg?.getAttribute("data-total-nodes") || drawn) === expected &&
          window.__flameWorkerMetrics.some(
            (metric) => metric.returned_rects === expected,
          )
        );
      },
      nodes,
      { timeout: 45000 },
    );
    const initial = await page.evaluate(async () => {
      await new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      );
      const now = performance.now();
      const metric = window.__flameWorkerMetrics.at(-1);
      return {
        navigation_to_interactive_ms: now,
        worker: metric,
        worker_received_to_interactive_ms: now - metric.received_at_ms,
        dom_rects: document.querySelectorAll(".flame-svg rect").length,
        dom_elements: document.getElementsByTagName("*").length,
        total_layout_nodes: Number(
          document
            .querySelector(".flame-svg")
            .getAttribute("data-total-nodes") ||
            document.querySelectorAll(".flame-svg rect").length,
        ),
        rendered_frame_height_px: document
          .querySelector(".flame-svg rect")
          .getBoundingClientRect().height,
        initial_scroll_top_px:
          document.querySelector(".flame-scroll").scrollTop,
        keyboard_tab_stops: document.querySelectorAll(
          '.flame-svg g[tabindex="0"]',
        ).length,
        result_requests: performance
          .getEntriesByType("resource")
          .filter((entry) => entry.name.includes("/result"))
          .map((entry) => ({
            path: new URL(entry.name).pathname,
            duration_ms: entry.duration,
            transfer_bytes: entry.transferSize,
            decoded_body_bytes: entry.decodedBodySize,
          })),
        long_tasks: window.__flameLongTasks.slice(),
      };
    });
    await page.evaluate(() => {
      document
        .querySelector('input[placeholder="搜索函数名称…"]')
        .addEventListener(
          "input",
          () => (window.__flameSearchStart = performance.now()),
          { once: true, capture: true },
        );
    });
    await page.getByRole("textbox", { name: "搜索函数" }).fill(fixture.search);
    await page.waitForFunction(
      () =>
        document.querySelectorAll('.flame-svg rect[stroke="#fff"]').length ===
        1,
      null,
      { timeout: 30000 },
    );
    const search = await page.evaluate(async () => {
      await new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      );
      const latency = performance.now() - window.__flameSearchStart;
      return {
        input_to_paint_ms: latency,
        matched_rects: document.querySelectorAll(
          '.flame-svg rect[stroke="#fff"]',
        ).length,
        dimmed_rects: Array.from(
          document.querySelectorAll(".flame-svg rect"),
        ).filter((rect) => getComputedStyle(rect).fill === "rgb(52, 50, 54)")
          .length,
        full_layout_status:
          document.querySelector('.flame-footer [role="status"]')
            ?.textContent || null,
      };
    });
    await page.getByRole("textbox", { name: "搜索函数" }).fill("");
    await page.waitForFunction(
      () =>
        document.querySelector(".flame-svg")?.getAttribute("data-searching") !==
          "true" && !document.querySelector('.flame-svg rect[fill="#343236"]'),
    );
    let virtualizationChecks = null;
    if (initial.dom_rects < nodes) {
      assert.equal(initial.keyboard_tab_stops, 1);
      assert.equal(Math.round(initial.rendered_frame_height_px), 25);
      assert(initial.initial_scroll_top_px > 0);
      await page.evaluate(() => {
        document.querySelector(".flame-scroll").scrollTop = 0;
      });
      await page.waitForFunction(
        () => !document.querySelector('.flame-svg [data-flame-id="1"]'),
      );
      const topRects = await page.locator(".flame-svg rect").count();
      await page.evaluate(() => {
        const scroll = document.querySelector(".flame-scroll");
        scroll.scrollTop = scroll.scrollHeight;
      });
      await page.waitForFunction(
        () => !!document.querySelector('.flame-svg [data-flame-id="1"]'),
      );
      const input = page.getByRole("textbox", { name: "搜索函数" });
      await input.fill(fixture.offscreenSearch);
      await page
        .getByRole("button", { name: "聚焦首个匹配函数，共1处", exact: true })
        .waitFor();
      assert.equal(
        await page.locator('.flame-svg rect[stroke="#fff"]').count(),
        0,
        "Offscreen match is counted without mounting that depth",
      );
      await page
        .getByRole("button", { name: "聚焦首个匹配函数，共1处", exact: true })
        .click();
      await page.waitForFunction(
        () =>
          document
            .querySelector(".flame-svg")
            ?.getAttribute("data-total-nodes") === "1",
      );
      await page.getByRole("button", { name: "重置", exact: true }).click();
      await page.waitForFunction(
        (expected) =>
          Number(
            document
              .querySelector(".flame-svg")
              ?.getAttribute("data-total-nodes"),
          ) === expected,
        nodes,
      );
      await page
        .getByRole("button", { name: "展开火焰图", exact: true })
        .click();
      await page.locator(".flame-component.expanded").waitFor();
      await page.getByRole("button", { name: "退出展开", exact: true }).click();
      const rootFrame = page.locator('.flame-svg [data-flame-id="1"]');
      await rootFrame.focus();
      await rootFrame.press("ArrowUp");
      await page.waitForFunction(
        () => document.activeElement?.getAttribute("data-flame-id") === "2",
      );
      virtualizationChecks = {
        root_initially_visible: true,
        root_unmounted_at_top: true,
        top_viewport_rects: topRects,
        offscreen_match_count: 1,
        offscreen_match_dom_rects: 0,
        offscreen_match_focus_and_reset: true,
        expand_collapse: true,
        roving_arrow_navigation: true,
      };
      await input.focus();
    }
    await page.evaluate(() => {
      const scroll = document.querySelector(".flame-scroll");
      scroll.scrollTop = scroll.scrollHeight;
      if (document.activeElement instanceof HTMLElement)
        document.activeElement.blur();
      window.scrollTo(0, 0);
    });
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)),
        ),
    );
    await page.screenshot({
      path: path.join(
        evidenceDir,
        fullScale
          ? `flame-${nodes / 1000}k${reportSuffix}.png`
          : nodes === 49000
            ? `flame-large${reportSuffix}.png`
            : `flame-10k${reportSuffix}.png`,
      ),
      fullPage: true,
    });
    assert.deepEqual(
      pageErrors,
      [],
      "Actual result page must have no JS errors",
    );
    assert.deepEqual(
      httpFailures,
      [],
      "Actual result page must have no failed HTTP requests",
    );
    const record = {
      task_id: taskId,
      task_name: name,
      source: result.summary.source,
      input_bytes: fixture.bytes,
      sampling_weight: result.total_samples,
      unique_stacks: result.unique_stacks,
      nodes: result.node_count,
      max_depth: result.max_depth,
      import_response_ms: importResponseMs,
      existing_task_reused: reusedTask,
      analysis_observed_wall_ms: observedAnalysisWallMs,
      server_event_analyzer_duration_ms: analyzerDurationMs,
      page: initial,
      search,
      virtualization_checks: virtualizationChecks,
      javascript_errors: pageErrors,
      failed_http_requests: httpFailures,
    };
    baseline.cases.push(record);
    writeFileSync(
      path.join(evidenceDir, `flame-performance${reportSuffix}.json`),
      JSON.stringify(baseline, null, 2),
    );
    console.log(
      JSON.stringify({
        task_id: taskId,
        nodes,
        analyzer_ms: analyzerDurationMs,
        worker_ms: initial.worker.layout_round_trip_ms,
        interactive_ms: initial.navigation_to_interactive_ms,
        dom_rects: initial.dom_rects,
        search_ms: search.input_to_paint_ms,
        page_errors: pageErrors.length,
      }),
    );
    await page.close();
  }
} finally {
  await context.close();
  await browser.close();
  await api.dispose();
}
