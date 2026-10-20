import { test, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const evidence = path.join(
  root,
  "docs/evidence/full/request-lifecycle-fixtures-rc6",
);
const records: Record<string, unknown>[] = [];
const epoch = Date.UTC(2026, 9, 17, 0, 0);
const task = {
  id: "7",
  name: "Imported request lifecycle fixture",
  kind: "IMPORT_COLLAPSED",
  params: { source: "import" },
  creator_id: "fixture-viewer",
  collection_status: "COLLECTED",
  analysis_status: "RUNNING",
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

async function fixtures(page: Page, tasks: (route: Route) => Promise<void>) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    const events: {
      url: string;
      started: number;
      aborted?: number;
      reason?: string;
    }[] = [];
    (window as Window & { __requestProbe?: unknown }).__requestProbe = events;
    window.fetch = (input, init) => {
      const url = input instanceof Request ? input.url : String(input),
        entry = { url, started: Date.now() } as (typeof events)[number];
      events.push(entry);
      init?.signal?.addEventListener(
        "abort",
        () => {
          entry.aborted = Date.now();
          entry.reason = init.signal?.reason?.name || "unknown";
        },
        { once: true },
      );
      return nativeFetch(input, init);
    };
  });
  await page.route("**/api/v1/**", async (route) => {
    const endpoint = new URL(route.request().url()).pathname.replace(
      "/api/v1",
      "",
    );
    if (endpoint === "/tasks" || endpoint === "/tasks/7") return tasks(route);
    let data: unknown;
    if (endpoint === "/auth/me")
      data = {
        user: {
          id: "fixture-viewer",
          username: "fixture-viewer",
          display_name: "Imported lifecycle fixture",
          role: "viewer",
        },
        csrf_token: "fixture-only",
      };
    else if (endpoint === "/task-kinds")
      data = [{ id: "IMPORT_COLLAPSED", display_name: "折叠调用栈导入" }];
    else if (endpoint === "/auth/providers") data = { ai: { enabled: false } };
    else if (endpoint === "/tasks/7/events") data = { items: [], total: 0 };
    else if (endpoint === "/tasks/7/events/stream")
      return route.fulfill({
        status: 503,
        body: "explicit imported event-stream outage fixture",
      });
    else {
      errors.push("Unexpected fixture endpoint: " + endpoint);
      return route.fulfill({
        status: 404,
        json: { error: { code: "FIXTURE_UNHANDLED" } },
      });
    }
    await route.fulfill({
      status: 200,
      json: { data, error: null, request_id: "imported-lifecycle-fixture" },
    });
  });
  return errors;
}
const success = (route: Route) =>
  route.fulfill({
    status: 200,
    json: {
      data: { items: [task], total: 1 },
      error: null,
      request_id: "imported-lifecycle-fixture",
    },
  });
async function pauseClock(page: Page) {
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 50));
}
async function hidden(page: Page, value: boolean) {
  // Explicit document-visibility simulation, exercising the actual app's
  // TanStack focus listener. This does not emulate an OS suspending the process.
  await page.evaluate((hidden) => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => (hidden ? "hidden" : "visible"),
    });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  }, value);
}

test.describe("imported request lifecycle fixtures", () => {
  test.skip(
    process.env.DROP_UI_PREVIEW !== "1",
    "Explicit local preview opt-in required",
  );
  test.afterAll(() => {
    mkdirSync(evidence, { recursive: true });
    const html = readFileSync(path.join(root, "web/dist/index.html"), "utf8");
    const bundles = [
      ...html.matchAll(/(?:src|href)="(\/assets\/[^\"]+\.(?:js|css))"/g),
    ].map((match) => match[1]);
    writeFileSync(
      path.join(evidence, "report.json"),
      JSON.stringify(
        {
          passed:
            records.length === 4 && records.every((record) => record.passed),
          source: "imported-browser-fixtures-with-controlled-clock",
          production_or_backend_verified: false,
          checked_at: new Date().toISOString(),
          browser_clock_is_virtual: true,
          visibility_is_document_event_simulation: true,
          real_native_fetch_abort_signal: true,
          bundle_sha256: Object.fromEntries(
            bundles.map((bundle) => [
              bundle,
              createHash("sha256")
                .update(readFileSync(path.join(root, "web/dist", bundle)))
                .digest("hex"),
            ]),
          ),
          checks: records,
        },
        null,
        2,
      ) + "\n",
    );
  });

  test("backs off between failed polling cycles and resets after success", async ({
    page,
  }) => {
    await page.clock.install({ time: epoch });
    const calls: number[] = [];
    const errors = await fixtures(page, async (route) => {
      calls.push(await page.evaluate(() => Date.now()));
      if (calls.length <= 4)
        await route.fulfill({
          status: 503,
          json: {
            data: null,
            request_id: "fixture",
            error: {
              code: "FIXTURE_TRANSIENT",
              message: `fixture transient ${calls.length}`,
            },
          },
        });
      else await success(route);
    });
    await page.goto("/tasks");
    await expect(page.getByRole("alert")).toContainText("fixture transient 2");
    await pauseClock(page);
    expect(calls).toHaveLength(2);
    await page.clock.runFor(19000);
    await page.waitForTimeout(50);
    expect(calls).toHaveLength(2);
    await page.clock.runFor(1500);
    await expect.poll(() => calls.length).toBe(3);
    await page.clock.resume();
    await expect(page.getByRole("alert")).toContainText("fixture transient 4");
    await pauseClock(page);
    expect(calls).toHaveLength(4);
    await page.clock.runFor(39000);
    await page.waitForTimeout(50);
    expect(calls).toHaveLength(4);
    await page.clock.runFor(1500);
    await page.clock.resume();
    await expect(
      page.getByRole("link", { name: new RegExp("^" + task.name) }),
    ).toBeVisible();
    await pauseClock(page);
    expect(calls).toHaveLength(5);
    await page.clock.runFor(9000);
    await page.waitForTimeout(50);
    expect(calls).toHaveLength(5);
    await page.clock.runFor(1500);
    await expect.poll(() => calls.length).toBe(6);
    expect(calls[2] - calls[1]).toBeGreaterThanOrEqual(20000);
    expect(calls[4] - calls[3]).toBeGreaterThanOrEqual(40000);
    expect(calls[5] - calls[4]).toBeGreaterThanOrEqual(10000);
    expect(errors).toEqual([]);
    records.push({
      name: "consecutive-error-backoff-and-reset",
      passed: true,
      virtual_request_times: calls,
      first_failed_cycle_backoff_ms: 20000,
      second_failed_cycle_backoff_ms: 40000,
      successful_interval_restored_ms: 10000,
      per_fetch_retry_count: 1,
    });
  });

  test("hidden document suspends polling and visible document resumes", async ({
    page,
  }) => {
    await page.clock.install({ time: epoch });
    const calls: number[] = [];
    const errors = await fixtures(page, async (route) => {
      calls.push(await page.evaluate(() => Date.now()));
      await success(route);
    });
    await page.goto("/tasks");
    await expect(
      page.getByRole("link", { name: new RegExp("^" + task.name) }),
    ).toBeVisible();
    await pauseClock(page);
    const initial = calls.length;
    await hidden(page, true);
    await page.clock.runFor(60000);
    expect(calls).toHaveLength(initial);
    await hidden(page, false);
    await expect.poll(() => calls.length).toBe(initial + 1);
    expect(errors).toEqual([]);
    records.push({
      name: "hidden-document-poll-suspension",
      passed: true,
      initial_requests: initial,
      hidden_virtual_ms: 60000,
      hidden_requests: 0,
      resumed_requests: 1,
      virtual_request_times: calls,
    });
  });

  test("deduplicates a pending poll and aborts its native fetch on route exit", async ({
    page,
  }) => {
    await page.clock.install({ time: epoch });
    let calls = 0,
      release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const errors = await fixtures(page, async (route) => {
      calls++;
      await gate;
      await success(route).catch(() => {});
    });
    await page.goto("/tasks");
    await expect.poll(() => calls).toBe(1);
    await pauseClock(page);
    await page.clock.runFor(10000);
    expect(calls).toBe(1);
    await page.locator('a[href="/guide"]').first().click();
    // Lazy route loading schedules a React Suspense retry. Let that commit
    // on the real clock before returning to the controlled polling timeline.
    await page.clock.resume();
    await expect(
      page.getByRole("heading", { level: 1, name: /^从采集到洞察/ }),
    ).toBeVisible();
    await pauseClock(page);
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            (
              (
                window as Window & {
                  __requestProbe?: { url: string; aborted?: number }[];
                }
              ).__requestProbe || []
            ).filter(
              (entry) =>
                entry.url.startsWith("/api/v1/tasks?") && entry.aborted != null,
            ).length,
        ),
      )
      .toBe(1);
    release();
    await page.clock.runFor(30000);
    expect(calls).toBe(1);
    const probes = await page.evaluate(
      () => (window as Window & { __requestProbe?: unknown }).__requestProbe,
    );
    expect(errors).toEqual([]);
    records.push({
      name: "inflight-deduplication-and-navigation-abort",
      passed: true,
      requests_while_pending: calls,
      overlapping_polls: 0,
      wait_virtual_ms: 10000,
      requests_after_exit: 0,
      native_fetch_signal_events: probes,
    });
  });

  test("stops terminal task polling and permanent forbidden retries", async ({
    page,
  }) => {
    await page.clock.install({ time: epoch });
    let detailCalls = 0,
      listCalls = 0;
    const errors = await fixtures(page, async (route) => {
      if (new URL(route.request().url()).pathname.endsWith("/tasks/7")) {
        detailCalls++;
        await route.fulfill({
          status: 200,
          json: {
            data: {
              ...task,
              analysis_status: detailCalls === 1 ? "RUNNING" : "FAILED",
              ...(detailCalls === 1
                ? {}
                : {
                    error_code: "ANALYSIS_FIXTURE_FAILURE",
                    error_message: "Explicit imported terminal fixture",
                  }),
            },
            error: null,
            request_id: "fixture",
          },
        });
      } else {
        listCalls++;
        await route.fulfill({
          status: 403,
          json: {
            data: null,
            error: { code: "FORBIDDEN", message: "fixture access forbidden" },
            request_id: "fixture",
          },
        });
      }
    });
    await page.goto("/tasks/7");
    await expect(
      page.getByRole("heading", { level: 1, name: task.name, exact: true }),
    ).toBeVisible();
    await pauseClock(page);
    await page.clock.runFor(6000);
    await page.clock.resume();
    await expect(page.locator(".detail-state-pair")).toContainText("失败");
    await pauseClock(page);
    const terminalCalls = detailCalls;
    await page.clock.runFor(60000);
    expect(detailCalls).toBe(terminalCalls);
    await page.clock.resume();
    await page.locator('a[href="/tasks"]').first().click();
    await expect(page.getByRole("alert")).toContainText(
      "fixture access forbidden",
    );
    expect(listCalls).toBe(1);
    await pauseClock(page);
    await page.clock.runFor(60000);
    expect(listCalls).toBe(1);
    expect(errors).toEqual([]);
    records.push({
      name: "terminal-and-permanent-error-stop",
      passed: true,
      task_requests_before_terminal: terminalCalls,
      requests_after_terminal_virtual_60s: 0,
      forbidden_requests: listCalls,
      automatic_forbidden_retries: 0,
    });
  });
});
