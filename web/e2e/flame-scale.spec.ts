/**
 * 大规模合成调用树的真实 Worker 浏览器验收，观察完整节点保留、虚拟滚动和可交互性。
 * 夹具的节点数可复现，但不是业务系统采集结果或性能基准。
 */
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

type TreeNode = {
  name: string;
  value: number;
  self: number;
  children: TreeNode[];
};
type WorkerRecord = {
  id: number;
  posted_at: number;
  roundtrip_ms?: number;
  returned_nodes?: number;
  error?: string;
  terminated?: boolean;
};
type Phase = {
  started_at: number | null;
  ready_at: number | null;
  expected_nodes: number;
};
type BrowserProbe = {
  workers: WorkerRecord[];
  result_fetch_started_at: number | null;
  initial_ready_at: number | null;
  phases: Record<string, Phase>;
};
declare global {
  interface Window {
    __dropScaleProbe: BrowserProbe;
  }
}

const root = path.resolve(import.meta.dirname, "../..");
const evidence = path.join(root, "docs/evidence/full/flame-scale-browser-rc6");
const results: Record<string, unknown>[] = [];
const sizes = [10_000, 50_000, 100_000];
const sourceFiles = [
  "web/src/components/Flamegraph.tsx",
  "web/src/components/flame.worker.ts",
  "web/src/components/flame-analysis.ts",
  "web/src/views/TaskDetail.tsx",
];
const hashes = (files: string[]) =>
  Object.fromEntries(
    files.map((file) => [
      file,
      createHash("sha256")
        .update(readFileSync(path.join(root, file)))
        .digest("hex"),
    ]),
  );
const sourcesAtStart = hashes(sourceFiles);

// 显式导入的合成输入，不将它冒充实测 profile。
// 构造 96 层调用链以覆盖真正的视口外深度，并保持节点总计/自身权重一致。
// 细小叶节点还用于验证低于像素阈值的渲染裁剪，不改变原始数据。
function fixtureTree(count: number) {
  const tree: TreeNode = { name: "all", value: 0, self: 0, children: [] };
  const spine: TreeNode[] = [];
  let parent = tree;
  for (let depth = 1; depth <= 96; depth++) {
    const node: TreeNode = {
      name:
        depth === 80
          ? `offscreen_target_${count}`
          : `imported_spine_${String(depth).padStart(2, "0")}`,
      value: 0,
      self: 1,
      children: [],
    };
    parent.children.push(node);
    spine.push(node);
    parent = node;
  }
  const leaves = count - 1 - spine.length;
  for (let index = 0; index < leaves; index++) {
    spine[index % spine.length].children.push({
      name: `imported_leaf_${index}`,
      value: 1,
      self: 1,
      children: [],
    });
  }
  for (let index = spine.length - 1; index >= 0; index--) {
    const node = spine[index];
    node.value =
      node.self + node.children.reduce((sum, child) => sum + child.value, 0);
  }
  tree.value = tree.children[0].value;
  return { tree, focusNodes: spine[79].value, leaves };
}

async function installProbe(page: Page, expectedNodes: number) {
  await page.addInitScript(
    ({ expected }) => {
      const probe: BrowserProbe = {
        workers: [],
        result_fetch_started_at: null,
        initial_ready_at: null,
        phases: {},
      };
      window.__dropScaleProbe = probe;
      const nativeFetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        const url = input instanceof Request ? input.url : String(input);
        if (/\/api\/v1\/tasks\/\d+\/result(?:\?|$)/.test(url))
          probe.result_fetch_started_at = performance.now();
        return nativeFetch(input, init);
      };
      // 只给真实浏览器 Worker 加观测记录，不用假 Worker 替代：
      // 原生构造过程、脚本、参数、转移对象和消息内容均保持不变。
      const NativeWorker = window.Worker;
      class ObservedNativeWorker extends NativeWorker {
        measurement?: WorkerRecord;
        readonly isLayoutWorker: boolean;
        constructor(url: string | URL, options?: WorkerOptions) {
          super(url, options);
          this.isLayoutWorker = String(url).includes("flame.worker");
          this.addEventListener("message", (event) => {
            if (!this.measurement) return;
            this.measurement.roundtrip_ms =
              performance.now() - this.measurement.posted_at;
            this.measurement.returned_nodes = Array.isArray(event.data?.rects)
              ? event.data.rects.length
              : 0;
            if (event.data?.error)
              this.measurement.error = String(event.data.error);
          });
        }
        postMessage(
          message: unknown,
          transfer?: Transferable[] | StructuredSerializeOptions,
        ) {
          if (this.isLayoutWorker) {
            this.measurement = {
              id: probe.workers.length + 1,
              posted_at: performance.now(),
            };
            probe.workers.push(this.measurement);
          }
          if (transfer === undefined) super.postMessage(message);
          else super.postMessage(message, transfer as Transferable[]);
        }
        terminate() {
          if (this.measurement) this.measurement.terminated = true;
          super.terminate();
        }
      }
      window.Worker = ObservedNativeWorker;
      let pending = false;
      const observer = new MutationObserver(() => {
        if (pending || probe.initial_ready_at != null) return;
        const graph = document.querySelector<SVGElement>(
          `.flame-svg[data-total-nodes="${expected}"]`,
        );
        if (!graph?.querySelector('g[role="button"][tabindex="0"]')) return;
        pending = true;
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            probe.initial_ready_at = performance.now();
            observer.disconnect();
          }),
        );
      });
      observer.observe(document, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["data-total-nodes", "tabindex"],
      });
    },
    { expected: expectedNodes },
  );
}

async function watchPhase(
  page: Page,
  name: string,
  expectedNodes: number,
  selector: string,
  event: "click" | "input",
) {
  await page.evaluate(
    ({ phase, expected, target, eventName }) => {
      const value: Phase = {
        started_at: null,
        ready_at: null,
        expected_nodes: expected,
      };
      window.__dropScaleProbe.phases[phase] = value;
      const element = document.querySelector(target);
      if (!element) throw new Error("Missing phase target");
      element.addEventListener(
        eventName,
        () => {
          value.started_at = performance.now();
        },
        { once: true, capture: true },
      );
      const observer = new MutationObserver(() => {
        if (value.started_at == null || value.ready_at != null) return;
        const svg = document.querySelector(
          `.flame-svg[data-total-nodes="${expected}"]`,
        );
        if (!svg?.querySelector('g[role="button"][tabindex="0"]')) return;
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            if (value.ready_at == null) value.ready_at = performance.now();
            observer.disconnect();
          }),
        );
      });
      observer.observe(document.querySelector(".flame-component")!, {
        subtree: true,
        childList: true,
        attributes: true,
      });
    },
    {
      phase: name,
      expected: expectedNodes,
      target: selector,
      eventName: event,
    },
  );
}

test.describe("real Worker imported flame scale baselines", () => {
  test.skip(
    process.env.DROP_UI_PREVIEW !== "1",
    "Explicit local preview opt-in required",
  );
  test.afterAll(() => {
    mkdirSync(evidence, { recursive: true });
    const sourcesAtEnd = hashes(sourceFiles);
    const bundleFiles = readdirSync(path.join(root, "web/dist/assets"))
      .filter((name) => /\.(?:js|css)$/.test(name))
      .map((name) => "web/dist/assets/" + name);
    writeFileSync(
      path.join(evidence, "report.json"),
      JSON.stringify(
        {
          passed:
            results.length === sizes.length &&
            results.every((row) => row.passed === true),
          source: "imported-synthetic-browser-fixtures",
          real_native_worker: true,
          analyzer_or_backend_verified: false,
          measured_at: new Date().toISOString(),
          environment: {
            node: process.version,
            platform: process.platform,
            arch: process.arch,
            os_release: os.release(),
            cpu_model: os.cpus()[0]?.model,
            logical_cpus: os.cpus().length,
            physical_memory_bytes: os.totalmem(),
            free_memory_at_report_bytes: os.freemem(),
            node_options: process.env.NODE_OPTIONS || "",
            playwright_workers: 1,
          },
          instrumentation:
            "Native Worker subclass forwards unchanged constructor/postMessage/terminate; observes actual message roundtrip. Imported API JSON is the only mocked data. DOM-ready is two requestAnimationFrame ticks after full-count SVG and keyboard frame exist. Timing includes structured cloning, scheduling and instrumentation.",
          limitations: [
            "Local Chromium/headless/UI baseline, not Analyzer parse throughput, network, server capacity or a production SLA.",
            "Main renderer JS heap/TaskDuration excludes dedicated Worker heap and is not an OS process RSS peak; browser process CPU snapshots include instrumentation and browser overhead.",
            "A 96-deep spine with small leaves exercises depth and subpixel culling; other tree shapes can have different DOM and interaction costs.",
          ],
          sources_sha256_at_start: sourcesAtStart,
          sources_sha256_at_end: sourcesAtEnd,
          sources_unchanged:
            JSON.stringify(sourcesAtStart) === JSON.stringify(sourcesAtEnd),
          built_assets_sha256: hashes(bundleFiles),
          cases: results,
        },
        null,
        2,
      ) + "\n",
    );
  });
  for (const count of sizes) {
    test(`${count} nodes retain every Worker node and remain interactive`, async ({
      page,
      browser,
      baseURL,
    }) => {
      test.setTimeout(120000);
      expect(new URL(baseURL!).hostname).toMatch(
        /^(?:127\.0\.0\.1|localhost)$/,
      );
      mkdirSync(evidence, { recursive: true });
      const { tree, focusNodes, leaves } = fixtureTree(count);
      const errors: string[] = [];
      page.on("pageerror", (problem) => errors.push(problem.message));
      const resultBody = JSON.stringify({
        data: {
          schema_version: 1,
          kind: "PERF_CPU",
          total_samples: tree.value,
          unique_stacks: leaves,
          node_count: count,
          max_depth: 97,
          flamegraph: tree,
          top_functions: [],
          suggestions: [],
          sections: {},
          summary: {
            source: "import",
            duration_seconds: null,
            frequency_hz: null,
            unit: "samples",
            limitations: [
              "Explicit imported synthetic browser-scale fixture. Not a measured perf collection.",
            ],
          },
        },
        error: null,
        request_id: "imported-scale-fixture",
      });
      await installProbe(page, count);
      await page.route("**/api/v1/**", async (route) => {
        const endpoint = new URL(route.request().url()).pathname.replace(
          "/api/v1",
          "",
        );
        if (endpoint === `/tasks/${count}/result`)
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: resultBody,
          });
        let data: unknown;
        if (endpoint === "/auth/me")
          data = {
            user: {
              id: "fixture-viewer",
              username: "fixture-viewer",
              display_name: "导入基线 fixture",
              role: "viewer",
            },
            csrf_token: "imported-fixture-only",
          };
        else if (endpoint === "/auth/providers")
          data = { ai: { enabled: false } };
        else if (endpoint === "/task-kinds")
          data = [{ id: "PERF_CPU", display_name: "CPU 火焰图 · perf" }];
        else if (endpoint === `/tasks/${count}`)
          data = {
            id: String(count),
            name: `导入大图 fixture · ${count} nodes`,
            kind: "PERF_CPU",
            creator_id: "fixture-viewer",
            params: { source: "import" },
            collection_status: "COLLECTED",
            analysis_status: "SUCCESS",
            permissions: {
              read: true,
              operate: false,
              share: false,
              delete: false,
              reanalyze: false,
            },
            artifacts: [],
            suggestions: [],
            created_at: "2026-10-16T12:00:00Z",
            updated_at: "2026-10-16T12:01:00Z",
          };
        else if (
          endpoint === `/tasks/${count}/events` ||
          endpoint === `/tasks/${count}/suggestions/requests`
        )
          data = { items: [], total: 0 };
        else {
          errors.push("Unexpected fixture endpoint: " + endpoint);
          return route.fulfill({
            status: 404,
            json: { error: { code: "FIXTURE_UNHANDLED" } },
          });
        }
        await route.fulfill({
          status: 200,
          json: { data, error: null, request_id: "imported-scale-fixture" },
        });
      });
      const cdp = await page.context().newCDPSession(page),
        browserCDP = await browser.newBrowserCDPSession();
      await cdp.send("Performance.enable");
      const cpuBefore = await browserCDP.send("SystemInfo.getProcessInfo");
      const metricsBefore = await cdp.send("Performance.getMetrics");
      const started = Date.now();
      const heapSamples: {
        at_ms: number;
        used_bytes: number;
        total_bytes: number;
      }[] = [];
      const heapSamplingErrors: string[] = [];
      let sampling = true;
      const samplingLoop = (async () => {
        while (sampling) {
          try {
            const value = await cdp.send("Performance.getMetrics");
            const metrics = Object.fromEntries(
              value.metrics.map((item) => [item.name, item.value]),
            );
            heapSamples.push({
              at_ms: Date.now() - started,
              used_bytes: metrics.JSHeapUsedSize,
              total_bytes: metrics.JSHeapTotalSize,
            });
          } catch (error) {
            heapSamplingErrors.push(
              error instanceof Error ? error.message : String(error),
            );
          }
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
      })();
      try {
        await page.goto(`/tasks/${count}`);
        const graph = page.locator(".flame-component"),
          svg = graph.locator(".flame-svg"),
          scroll = graph.locator(".flame-scroll");
        await expect(svg).toHaveAttribute("data-total-nodes", String(count));
        await expect
          .poll(() =>
            page.evaluate(() => window.__dropScaleProbe.initial_ready_at),
          )
          .not.toBeNull();
        await graph.scrollIntoViewIfNeeded();
        const initialDOM = await svg.locator('g[role="button"]').count();
        expect(initialDOM).toBeGreaterThan(0);
        expect(initialDOM).toBeLessThan(2000);
        expect(initialDOM).toBeLessThan(count);
        const targetName = `offscreen_target_${count}`;
        await expect(
          svg.locator(`g[aria-label^="${targetName},"]`),
        ).toHaveCount(0);
        const initialProbe = await page.evaluate(() =>
          structuredClone(window.__dropScaleProbe),
        );
        expect(
          initialProbe.workers.some(
            (worker) => worker.returned_nodes === count && !worker.error,
          ),
        ).toBe(true);

        const rootFrame = svg.locator('g[data-flame-id="1"]');
        await rootFrame.focus();
        await page.keyboard.press("ArrowUp");
        const nextFrame = svg.locator('g[aria-label^="imported_spine_01,"]');
        await expect(nextFrame).toBeFocused();
        const firstKeyboardAt = await page.evaluate(() => performance.now());
        await page.keyboard.press("Home");
        await expect(rootFrame).toBeFocused();

        const bottomBefore = await scroll.evaluate(
          (element) =>
            element.scrollHeight - element.scrollTop - element.clientHeight,
        );
        expect(bottomBefore).toBeLessThanOrEqual(2);
        await graph
          .getByRole("button", { name: "展开火焰图", exact: true })
          .click();
        await expect(graph).toHaveClass(/expanded/);
        await expect
          .poll(() =>
            scroll.evaluate(
              (element) =>
                element.scrollHeight - element.scrollTop - element.clientHeight,
            ),
          )
          .toBeLessThanOrEqual(2);
        await graph
          .getByRole("button", { name: "退出展开", exact: true })
          .click();
        await expect(graph).not.toHaveClass(/expanded/);
        await expect
          .poll(() =>
            scroll.evaluate(
              (element) =>
                element.scrollHeight - element.scrollTop - element.clientHeight,
            ),
          )
          .toBeLessThanOrEqual(2);

        const middleBefore = await scroll.evaluate((element) => {
          element.scrollTop =
            (element.scrollHeight - element.clientHeight) * 0.4;
          return element.scrollTop;
        });
        await graph
          .getByRole("button", { name: "展开火焰图", exact: true })
          .click();
        await expect(graph).toHaveClass(/expanded/);
        await expect
          .poll(() => scroll.evaluate((element) => element.scrollTop))
          .toBeCloseTo(middleBefore, 0);
        await graph
          .getByRole("button", { name: "退出展开", exact: true })
          .click();
        await expect(graph).not.toHaveClass(/expanded/);
        await expect
          .poll(() => scroll.evaluate((element) => element.scrollTop))
          .toBeCloseTo(middleBefore, 0);
        await graph.locator(".flame-settings > summary").click();
        await graph
          .getByRole("combobox", { name: "颜色方案", exact: true })
          .selectOption("ocean");
        await expect(svg.locator("rect").first()).toHaveAttribute(
          "fill",
          /^#(?:88b9df|8ebad5|a3bbd9|a0ccca|9db1d4|b4c9e2)$/,
        );
        await expect
          .poll(() => scroll.evaluate((element) => element.scrollTop))
          .toBeCloseTo(middleBefore, 0);
        const completedBeforeSort = await page.evaluate(
          () =>
            window.__dropScaleProbe.workers.filter(
              (worker) => worker.roundtrip_ms !== undefined,
            ).length,
        );
        await graph
          .getByRole("combobox", { name: "排序", exact: true })
          .selectOption("name");
        await expect
          .poll(() =>
            page.evaluate(
              () =>
                window.__dropScaleProbe.workers.filter(
                  (worker) => worker.roundtrip_ms !== undefined,
                ).length,
            ),
          )
          .toBeGreaterThan(completedBeforeSort);
        await expect(svg).toHaveAttribute("data-total-nodes", String(count));
        await expect
          .poll(() => scroll.evaluate((element) => element.scrollTop))
          .toBeCloseTo(middleBefore, 0);
        await graph.locator(".flame-settings > summary").click();
        await scroll.evaluate((element) => {
          element.scrollTop = element.scrollHeight;
        });

        await page.evaluate(() => {
          const input = document.querySelector<HTMLInputElement>(
            ".flame-toolbar input",
          )!;
          const phase: Phase = {
            started_at: null,
            ready_at: null,
            expected_nodes: 1,
          };
          window.__dropScaleProbe.phases.search = phase;
          input.addEventListener(
            "input",
            () => {
              phase.started_at = performance.now();
            },
            { once: true, capture: true },
          );
          const observer = new MutationObserver(() => {
            const button = document.querySelector<HTMLButtonElement>(
              '.flame-tools button[aria-label^="聚焦首个匹配函数"]',
            );
            if (
              phase.started_at != null &&
              button &&
              !button.disabled &&
              button.getAttribute("aria-label")?.includes("共1处")
            ) {
              phase.ready_at = performance.now();
              observer.disconnect();
            }
          });
          observer.observe(document.querySelector(".flame-component")!, {
            subtree: true,
            childList: true,
            attributes: true,
          });
        });
        await graph
          .getByRole("textbox", { name: "搜索函数", exact: true })
          .fill(targetName);
        const focusButton = graph.getByRole("button", {
          name: "聚焦首个匹配函数，共1处",
          exact: true,
        });
        await expect(focusButton).toBeEnabled();
        await expect
          .poll(() =>
            page.evaluate(() => window.__dropScaleProbe.phases.search.ready_at),
          )
          .not.toBeNull();
        await watchPhase(
          page,
          "focus",
          focusNodes,
          '.flame-tools button[aria-label^="聚焦首个匹配函数"]',
          "click",
        );
        await focusButton.click();
        await expect(svg).toHaveAttribute(
          "data-total-nodes",
          String(focusNodes),
        );
        await expect(graph.locator(".flame-context")).toContainText(targetName);
        await expect
          .poll(() =>
            page.evaluate(() => window.__dropScaleProbe.phases.focus.ready_at),
          )
          .not.toBeNull();
        const focusedDOM = await svg.locator('g[role="button"]').count();
        await graph.getByRole("button", { name: "重置", exact: true }).click();
        await expect(svg).toHaveAttribute("data-total-nodes", String(count));

        const viewports = [];
        for (const width of count === 100000 ? [1440, 375] : [1440]) {
          await page.setViewportSize({ width, height: 1000 });
          await page.emulateMedia({ reducedMotion: "reduce" });
          await page.evaluate(() => document.fonts.ready);
          await page.waitForTimeout(350);
          await graph.scrollIntoViewIfNeeded();
          expect(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth + 1,
            ),
          ).toBe(true);
          await expect(svg).toHaveAttribute("data-total-nodes", String(count));
          const countDOM = await svg.locator('g[role="button"]').count();
          expect(countDOM).toBeGreaterThan(0);
          expect(countDOM).toBeLessThan(2000);
          const keyboardFrame = svg.locator('g[tabindex="0"]').first();
          await keyboardFrame.focus();
          await page.keyboard.press("Home");
          await expect(svg.locator('g[data-flame-id="1"]')).toBeFocused();
          await graph.screenshot({
            path: path.join(evidence, `flame-${count}-${width}.png`),
          });
          viewports.push({
            width,
            rendered_svg_frames: countDOM,
            document_overflow: false,
            keyboard_home: true,
          });
        }
        expect(errors).toEqual([]);
        const probe = await page.evaluate(() =>
          structuredClone(window.__dropScaleProbe),
        );
        sampling = false;
        await samplingLoop;
        const metricsAfter = await cdp.send("Performance.getMetrics"),
          dom = await cdp.send("Memory.getDOMCounters"),
          cpuAfter = await browserCDP.send("SystemInfo.getProcessInfo"),
          version = await browserCDP.send("Browser.getVersion");
        const before = Object.fromEntries(
            metricsBefore.metrics.map((item) => [item.name, item.value]),
          ),
          after = Object.fromEntries(
            metricsAfter.metrics.map((item) => [item.name, item.value]),
          );
        const previousCPU = new Map(
          cpuBefore.processInfo.map((item) => [item.id, item.cpuTime]),
        );
        const cpu = cpuAfter.processInfo.map((item) => ({
          type: item.type,
          seconds: Math.max(0, item.cpuTime - (previousCPU.get(item.id) || 0)),
        }));
        results.push({
          passed: true,
          input_nodes_including_root: count,
          max_depth: 97,
          input_json_bytes: Buffer.byteLength(resultBody),
          input_sha256: createHash("sha256").update(resultBody).digest("hex"),
          total_weight: tree.value,
          native_worker_records: probe.workers,
          initial_svg_frames: initialDOM,
          focused_subtree_nodes: focusNodes,
          focused_svg_frames: focusedDOM,
          all_worker_nodes_retained: true,
          offscreen_search_match: true,
          focus_and_reset: true,
          expand_and_collapse_bottom_anchor: true,
          expand_and_collapse_middle_scroll_preserved: true,
          palette_and_sort_middle_scroll_preserved: true,
          keyboard_up_and_home: true,
          viewports,
          timings_ms: {
            navigation_to_flame_dom_ready: initialProbe.initial_ready_at,
            result_fetch_to_flame_dom_ready:
              initialProbe.initial_ready_at! -
              initialProbe.result_fetch_started_at!,
            first_keyboard_observed_from_result_fetch:
              firstKeyboardAt - initialProbe.result_fetch_started_at!,
            search_input_to_matching_button:
              probe.phases.search.ready_at! - probe.phases.search.started_at!,
            focus_click_to_subtree_dom_ready:
              probe.phases.focus.ready_at! - probe.phases.focus.started_at!,
            entire_test_wall: Date.now() - started,
          },
          main_renderer: {
            js_heap_used_before: before.JSHeapUsedSize,
            js_heap_used_after: after.JSHeapUsedSize,
            js_heap_total_after: after.JSHeapTotalSize,
            cdp_heap_sampling_interval_ms: 100,
            sampled_peak_js_heap_used_bytes: Math.max(
              after.JSHeapUsedSize,
              ...heapSamples.map((sample) => sample.used_bytes),
            ),
            cdp_heap_samples: heapSamples,
            heap_sampling_errors: heapSamplingErrors,
            task_cpu_seconds_delta: after.TaskDuration - before.TaskDuration,
            script_cpu_seconds_delta:
              after.ScriptDuration - before.ScriptDuration,
            dom,
          },
          browser_process_cpu_seconds_delta: cpu,
          browser: version,
          navigator: await page.evaluate(() => ({
            hardware_concurrency: navigator.hardwareConcurrency,
            device_memory_gib:
              (navigator as Navigator & { deviceMemory?: number })
                .deviceMemory ?? null,
            user_agent: navigator.userAgent,
            device_pixel_ratio: devicePixelRatio,
          })),
          errors,
        });
      } catch (error) {
        results.push({
          passed: false,
          input_nodes_including_root: count,
          error: error instanceof Error ? error.message : String(error),
          errors,
          probe: await page
            .evaluate(() => window.__dropScaleProbe)
            .catch(() => null),
        });
        await page.screenshot({
          path: path.join(evidence, `failure-${count}.png`),
          fullPage: true,
        });
        throw error;
      } finally {
        sampling = false;
        await samplingLoop;
        await cdp.detach();
        await browserCDP.detach();
      }
    });
  }
});
