/**
 * 导入失败检查点验证页面不会串用旧结果，并覆盖离开路由取消、重分析后清缓存与权限错误。
 */
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { PartialAnalysis } from "../src/partial-analysis";

const shared = JSON.parse(
  readFileSync(
    path.resolve(
      import.meta.dirname,
      "../../contracts/fixtures/partial-result-v1.json",
    ),
    "utf8",
  ),
) as PartialAnalysis;

const directory = path.resolve(
  import.meta.dirname,
  "../../docs/evidence/full/partial-analysis-fixtures-rc6",
);
type Mode = "valid" | "old-run" | "old-input" | "none" | "forbidden" | "hold";

async function fixture(
  page: Page,
  options: { native?: boolean; success?: boolean; mode?: Mode } = {},
) {
  let mode = options.mode || "valid";
  let collection = "COLLECTED";
  let analysis = options.success ? "SUCCESS" : "FAILED";
  let version = 10;
  let attempt = 2;
  let run = 3;
  let release: (() => void) | undefined;
  let aborted = 0;
  const calls: string[] = [];
  const errors: string[] = [];
  const name = "Imported synthetic partial fixture";
  const symbol = '<img src="x" onerror="window.__partial_attack=true">';
  const longSymbol = '&<>"函'.repeat(22) + "abcdef";
  const kind = options.native ? "PERF_CPU" : shared.kind;
  page.on("pageerror", () => errors.push("PAGE_ERROR"));
  page.on("requestfailed", (request) => {
    if (new URL(request.url()).pathname.endsWith("/partial-result")) aborted++;
  });
  const task = () => ({
    id: "7",
    version,
    name,
    kind,
    params: options.native ? {} : { source: "import" },
    creator_id: "fixture-user",
    target_agent_id: options.native ? "fixture-agent" : undefined,
    collection_status: collection,
    analysis_status: analysis,
    request_id: "synthetic-partial-request",
    error_code: analysis === "FAILED" ? "OUTPUT_UPLOAD_FAILED" : undefined,
    error_message:
      analysis === "FAILED" ? "Imported post-parse failure fixture" : undefined,
    created_at: "2026-10-18T00:00:00Z",
    updated_at: "2026-10-18T00:01:00Z",
    analysis_identity: {
      analysis_job_id: shared.analysis_job_id,
      analysis_run: run,
      input_artifact_id: shared.input_artifact_id,
      input_digest: shared.input_digest,
    },
    attempts: [
      {
        id: attempt === 2 ? shared.attempt_id : "21",
        attempt,
        status: collection,
      },
    ],
    artifacts: [
      {
        id: shared.input_artifact_id,
        task_id: "7",
        attempt_id: shared.attempt_id,
        kind: "RAW",
        name: "synthetic.folded",
        content_type: "text/plain",
        size: 60,
        sha256: shared.input_digest,
        status: "ready",
        download_allowed: true,
        created_at: "2026-10-18T00:00:00Z",
      },
    ],
    permissions: {
      read: true,
      operate: true,
      share: false,
      delete: false,
      reanalyze: true,
    },
    events: [],
  });
  await page.route("**/api/v1/**", async (route) => {
    const endpoint = new URL(route.request().url()).pathname.replace(
      "/api/v1",
      "",
    );
    calls.push(endpoint);
    let data: unknown;
    if (endpoint === "/auth/me")
      data = {
        user: {
          id: "fixture-user",
          username: "fixture",
          display_name: "Imported fixture user",
          role: "platform_admin",
        },
        csrf_token: "fixture-csrf",
      };
    else if (endpoint === "/auth/providers") data = { ai: { enabled: false } };
    else if (endpoint === "/task-kinds")
      data = [{ id: kind, display_name: "显式导入测试数据" }];
    else if (endpoint === "/tasks")
      data = { items: [task()], total: 1, page: 1, page_size: 12 };
    else if (endpoint === "/tasks/7") data = task();
    else if (endpoint === "/tasks/7/events") data = { items: [], total: 0 };
    else if (endpoint === "/tasks/7/suggestions/requests")
      data = { items: [], total: 0 };
    else if (endpoint === "/tasks/7/events/stream")
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: ": imported fixture\n\n",
      });
    else if (endpoint === "/tasks/7/partial-result") {
      if (mode === "hold")
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (mode === "none" || mode === "forbidden")
        return route
          .fulfill({
            status: mode === "none" ? 409 : 403,
            json: {
              request_id: "synthetic-partial",
              data: null,
              error: {
                code:
                  mode === "none" ? "PARTIAL_RESULT_NOT_READY" : "FORBIDDEN",
                message: "Imported error fixture",
              },
            },
          })
          .catch(() => {});
      data = {
        ...shared,
        kind,
        analysis_run: mode === "old-run" ? run - 1 : run,
        input_digest:
          mode === "old-input" ? "b".repeat(64) : shared.input_digest,
        summary: {
          ...shared.summary,
          source: options.native ? "perf" : "import",
        },
        top_functions: [
          { ...shared.top_functions[0], name: symbol },
          { ...shared.top_functions[1], name: longSymbol },
        ],
      };
    } else if (
      ["/tasks/7/reanalyze", "/tasks/7/retry", "/tasks/7/cancel"].includes(
        endpoint,
      )
    ) {
      expect(route.request().method()).toBe("POST");
      expect(route.request().headers()["x-csrf-token"]).toBe("fixture-csrf");
      version++;
      if (endpoint.endsWith("/retry")) {
        attempt++;
        collection = "QUEUED";
        analysis = "PENDING";
      } else if (endpoint.endsWith("/cancel")) {
        collection = "CANCELED";
        analysis = "CANCELED";
      } else {
        run++;
        analysis = "PENDING";
      }
      data = task();
    } else if (endpoint === "/tasks/7/result") {
      data = {
        schema_version: 1,
        kind,
        total_samples: 150,
        unique_stacks: 2,
        node_count: 4,
        max_depth: 3,
        flamegraph: {
          name: "all",
          value: 150,
          self: 0,
          children: [
            {
              name: "synthetic_root",
              value: 150,
              self: 0,
              children: [
                { name: "synthetic_hot", value: 100, self: 100, children: [] },
                { name: "synthetic_cold", value: 50, self: 50, children: [] },
              ],
            },
          ],
        },
        top_functions: [],
        suggestions: [],
        summary: {
          duration_seconds: null,
          frequency_hz: null,
          source: "import",
          unit: "samples",
          limitations: [],
        },
        sections: {},
      };
    } else {
      errors.push("UNHANDLED_FIXTURE_ENDPOINT");
      return route.fulfill({ status: 404, json: {} });
    }
    return route
      .fulfill({
        status: 200,
        json: { request_id: "synthetic-partial", error: null, data },
      })
      .catch(() => {});
  });
  return {
    calls,
    errors,
    symbol,
    longSymbol,
    setMode: (value: Mode) => {
      mode = value;
    },
    fail: () => {
      version++;
      run++;
      analysis = "FAILED";
    },
    release: () => release?.(),
    aborted: () => aborted,
  };
}

test("partial analysis shows only this failed run as escaped readable data at 375 and 1440", async ({
  page,
  baseURL,
}) => {
  expect(new URL(baseURL!).hostname).toBe("127.0.0.1");
  mkdirSync(directory, { recursive: true });
  const data = await fixture(page);
  await page.goto("/tasks/7");
  const panel = page.getByRole("region", {
    name: "本次未完成分析的已解析摘要",
  });
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("来源：用户导入数据");
  await expect(panel).toContainText(data.symbol);
  await expect(panel).toContainText(data.longSymbol);
  await expect(panel.locator("img,script,iframe")).toHaveCount(0);
  expect(
    await page.evaluate(
      () => (window as unknown as Record<string, unknown>).__partial_attack,
    ),
  ).toBeUndefined();
  await expect(page.getByRole("heading", { name: "调用栈火焰图" })).toHaveCount(
    0,
  );
  await expect(
    page.locator('.progress-stage[data-stage-state="failed"]'),
  ).toContainText("分析中");
  const checks = [];
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.evaluate(() => document.fonts.ready);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const violations = (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations;
    expect(violations).toEqual([]);
    const summary = panel.locator("summary");
    const box = await summary.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(panel.locator("details")).toHaveAttribute("open", "");
    await expect(panel).toContainText("30 / 3");
    await page.keyboard.press("Enter");
    await expect(panel.locator("details")).not.toHaveAttribute("open", "");
    await page.screenshot({
      path: path.join(directory, `partial-${width}.png`),
      fullPage: true,
    });
    await panel.screenshot({
      path: path.join(directory, `partial-panel-${width}.png`),
    });
    checks.push({
      width,
      axe_violations: violations.length,
      document_overflow: false,
      keyboard_disclosure: true,
      minimum_touch_height: 44,
    });
  }
  expect(
    data.calls.filter((value) => value === "/tasks/7/result"),
  ).toHaveLength(0);
  expect(data.errors).toEqual([]);
  writeFileSync(
    path.join(directory, "report.json"),
    JSON.stringify(
      {
        provenance:
          "Explicit imported synthetic API checkpoint fixture; no actual Analyzer/backend/S3 failure",
        checks,
        current_failed_run_only: true,
        escaped_maximum_utf8_symbols: true,
        no_success_result_query: true,
      },
      null,
      2,
    ),
  );
});

test("partial analysis rejects stale runs and inputs and handles no-checkpoint/forbidden without retries", async ({
  page,
}) => {
  const data = await fixture(page);
  for (const mode of ["old-run", "old-input", "none", "forbidden"] as const) {
    data.setMode(mode);
    const before = data.calls.filter((value) =>
      value.endsWith("/partial-result"),
    ).length;
    await page.goto("/tasks/7");
    if (mode === "old-run" || mode === "old-input")
      await expect(
        page.getByText("部分分析结果与当前执行不符，已停止展示"),
      ).toBeVisible();
    else if (mode === "none")
      await expect(
        page.getByRole("heading", { name: "本次任务未生成分析结果" }),
      ).toBeVisible();
    else
      await expect(page.getByText("当前无法读取此部分分析结果")).toBeVisible();
    await expect(
      page.getByRole("region", { name: "本次未完成分析的已解析摘要" }),
    ).toHaveCount(0);
    expect(
      data.calls.filter((value) => value.endsWith("/partial-result")).length -
        before,
    ).toBe(1);
  }
  expect(
    data.calls.filter((value) => value === "/tasks/7/result"),
  ).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

test("reanalyze and native retry/cancel clear partial presentation and never reuse its cache", async ({
  page,
}) => {
  const data = await fixture(page, { native: true });
  await page.goto("/tasks/7");
  const panel = page.getByRole("region", {
    name: "本次未完成分析的已解析摘要",
  });
  await expect(panel).toBeVisible();
  await page
    .getByRole("button", { name: "重新分析原始数据", exact: true })
    .first()
    .click();
  await expect(panel).toHaveCount(0);
  data.fail();
  await page.getByRole("button", { name: "刷新详情" }).click();
  await expect(panel).toHaveAttribute("data-analysis-run", "5");
  await page.getByRole("button", { name: "重试诊断", exact: true }).click();
  await expect(panel).toHaveCount(0);
  await page.getByRole("button", { name: "取消任务", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "本次执行诊断" }),
  ).toContainText("本次执行已取消");
  await expect(panel).toHaveCount(0);
  expect(
    data.calls.filter((value) => value.endsWith("/partial-result")),
  ).toHaveLength(2);
  expect(
    data.calls.filter((value) => value === "/tasks/7/result"),
  ).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

test("leaving a task cancels its pending partial request and returning reads a fresh checkpoint", async ({
  page,
}) => {
  const data = await fixture(page, { mode: "hold" });
  await page.goto("/tasks/7");
  await expect(page.getByText("正在读取本次分析已保存的摘要…")).toBeVisible();
  await page.getByRole("link", { name: "返回诊断任务", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "诊断任务.", exact: true }),
  ).toBeVisible();
  await expect.poll(data.aborted).toBeGreaterThan(0);
  data.setMode("valid");
  data.release();
  await page
    .getByRole("link", { name: /^Imported synthetic partial fixture/ })
    .click();
  await expect(
    page.getByRole("region", { name: "本次未完成分析的已解析摘要" }),
  ).toBeVisible();
  expect(
    data.calls.filter((value) => value.endsWith("/partial-result")),
  ).toHaveLength(2);
  expect(data.errors).toEqual([]);
});

test("a failed run with no checkpoint cannot render a cached successful result", async ({
  page,
}) => {
  const data = await fixture(page, { success: true, mode: "none" });
  await page.goto("/tasks/7");
  await expect(
    page.getByRole("heading", { name: "调用栈火焰图" }),
  ).toBeVisible();
  data.fail();
  await page.getByRole("button", { name: "刷新详情" }).click();
  await expect(
    page.getByRole("heading", { name: "本次任务未生成分析结果" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "调用栈火焰图" })).toHaveCount(
    0,
  );
  expect(
    data.calls.filter((value) => value === "/tasks/7/result"),
  ).toHaveLength(1);
  expect(data.errors).toEqual([]);
});
