import { test, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const evidence = path.join(
  root,
  "docs/evidence/full/event-stream-fixtures-rc6",
);
const records: Record<string, unknown>[] = [];
const epoch = Date.UTC(2026, 9, 17);
const task = {
  id: "7",
  name: "Imported SSE lifecycle fixture",
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

async function fixtures(page: Page, stream: (route: Route) => Promise<void>) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window),
      nativeSource = window.EventSource;
    const probe: {
      fetch: { url: string; aborted?: number; reason?: string }[];
      eventSources: { closed: boolean }[];
    } = { fetch: [], eventSources: [] };
    (window as Window & { __streamProbe?: unknown }).__streamProbe = probe;
    window.fetch = (input, init) => {
      const entry = {
        url: input instanceof Request ? input.url : String(input),
      } as (typeof probe.fetch)[number];
      probe.fetch.push(entry);
      init?.signal?.addEventListener(
        "abort",
        () => {
          entry.aborted = Date.now();
          entry.reason = init.signal?.reason?.name;
        },
        { once: true },
      );
      return nativeFetch(input, init);
    };
    window.EventSource = class extends nativeSource {
      private probe: { closed: boolean };
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options);
        this.probe = { closed: false };
        probe.eventSources.push(this.probe);
      }
      close() {
        this.probe.closed = true;
        super.close();
      }
    };
  });
  await page.route("**/api/v1/**", async (route) => {
    const endpoint = new URL(route.request().url()).pathname.replace(
      "/api/v1",
      "",
    );
    if (endpoint === "/tasks/7/events/stream") return stream(route);
    let data: unknown;
    if (endpoint === "/auth/me")
      data = {
        user: {
          id: "fixture-viewer",
          username: "fixture-viewer",
          display_name: "Imported SSE fixture",
          role: "viewer",
        },
        csrf_token: "fixture-only",
      };
    else if (endpoint === "/task-kinds")
      data = [{ id: "IMPORT_COLLAPSED", display_name: "折叠调用栈导入" }];
    else if (endpoint === "/auth/providers") data = { ai: { enabled: false } };
    else if (endpoint === "/tasks/7") data = task;
    else if (endpoint === "/tasks/7/events") data = { items: [], total: 0 };
    else {
      errors.push("Unexpected fixture endpoint: " + endpoint);
      return route.fulfill({
        status: 404,
        json: { error: { code: "FIXTURE_UNHANDLED" } },
      });
    }
    await route.fulfill({
      status: 200,
      json: { data, error: null, request_id: "imported-stream-fixture" },
    });
  });
  return errors;
}
async function visibility(page: Page, hidden: boolean) {
  await page.evaluate((hidden) => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => (hidden ? "hidden" : "visible"),
    });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  }, hidden);
}
async function aborts(page: Page) {
  return page.evaluate(() => {
    const probe = (
      window as Window & {
        __streamProbe?: {
          fetch: { url: string; aborted?: number }[];
          eventSources: { closed: boolean }[];
        };
      }
    ).__streamProbe!;
    return (
      probe.fetch.filter(
        (entry) =>
          entry.url.endsWith("/events/stream") && entry.aborted != null,
      ).length + probe.eventSources.filter((source) => source.closed).length
    );
  });
}
async function pause(page: Page) {
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 50));
}

test.describe("imported SSE lifecycle fixtures", () => {
  test.skip(
    process.env.DROP_UI_PREVIEW !== "1",
    "Explicit local preview opt-in required",
  );
  test.afterAll(() => {
    mkdirSync(evidence, { recursive: true });
    const html = readFileSync(path.join(root, "web/dist/index.html"), "utf8");
    const assets = [
      ...html.matchAll(/(?:src|href)="(\/assets\/[^\"]+\.(?:js|css))"/g),
    ].map((match) => match[1]);
    writeFileSync(
      path.join(evidence, "report.json"),
      JSON.stringify(
        {
          passed:
            records.length === 2 && records.every((record) => record.passed),
          source: "imported-browser-SSE-fixtures-with-controlled-clock",
          production_or_backend_verified: false,
          real_native_fetch_stream_and_abort: true,
          visibility_is_document_event_simulation: true,
          browser_clock_is_virtual: true,
          checked_at: new Date().toISOString(),
          bundle_sha256: Object.fromEntries(
            assets.map((asset) => [
              asset,
              createHash("sha256")
                .update(readFileSync(path.join(root, "web/dist", asset)))
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

  test("aborts a hidden stream, suspends reconnects, resumes and cancels on navigation", async ({
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
      await route
        .fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: ": fixture\n\n",
        })
        .catch(() => {});
    });
    try {
      await page.goto("/tasks/7");
      await expect.poll(() => calls).toBe(1);
      await pause(page);
      await visibility(page, true);
      await expect.poll(() => aborts(page)).toBe(1);
      await page.clock.runFor(60000);
      expect(calls).toBe(1);
      await visibility(page, false);
      await expect.poll(() => calls).toBe(2);
      await page.clock.resume();
      await page.locator('a[href="/guide"]').first().click();
      await expect(
        page.getByRole("heading", { level: 1, name: /^从采集到洞察/ }),
      ).toBeVisible();
      await expect.poll(() => aborts(page)).toBe(2);
      await pause(page);
      await page.clock.runFor(60000);
      expect(calls).toBe(2);
      expect(errors).toEqual([]);
      records.push({
        name: "visibility-pause-resume-navigation-abort",
        passed: true,
        initial_streams: 1,
        hidden_virtual_ms: 60000,
        hidden_reconnects: 0,
        resumed_streams: 1,
        native_aborts: 2,
        reconnects_after_exit: 0,
      });
    } finally {
      release();
    }
  });

  test("retains exact Last-Event-ID with exponential reconnects and stops on forbidden", async ({
    page,
  }) => {
    await page.clock.install({ time: epoch });
    const calls: {
      time: number;
      cursor?: string;
      requestId?: string;
      cookies?: string;
    }[] = [];
    const errors = await fixtures(page, async (route) => {
      const headers = route.request().headers();
      calls.push({
        time: await page.evaluate(() => Date.now()),
        cursor: headers["last-event-id"],
        requestId: headers["x-request-id"],
      });
      if (calls.length === 1)
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: 'id: 9007199254740993\r\nevent: fixture_probe\r\ndata: {"source":"import"}\r\n\r\n',
        });
      return route.fulfill({
        status: calls.length < 4 ? 503 : 403,
        body: "explicit imported stream outage fixture",
      });
    });
    await page.goto("/tasks/7");
    await expect(page.locator(".live-indicator")).toContainText("轮询");
    await pause(page);
    expect(calls).toHaveLength(1);
    await page.clock.runFor(1100);
    await expect.poll(() => calls.length).toBe(2);
    await page.waitForTimeout(50);
    await page.clock.runFor(1900);
    await page.waitForTimeout(50);
    expect(calls).toHaveLength(2);
    await page.clock.runFor(200);
    await expect.poll(() => calls.length).toBe(3);
    await page.waitForTimeout(50);
    await page.clock.runFor(3900);
    await page.waitForTimeout(50);
    expect(calls).toHaveLength(3);
    await page.clock.runFor(200);
    await expect.poll(() => calls.length).toBe(4);
    await page.waitForTimeout(50);
    await page.clock.runFor(60000);
    expect(calls).toHaveLength(4);
    await visibility(page, true);
    await visibility(page, false);
    await page.clock.runFor(60000);
    expect(calls).toHaveLength(4);
    expect(
      calls.slice(1).every((call) => call.cursor === "9007199254740993"),
    ).toBe(true);
    expect(
      calls.every((call) => /^[0-9a-f-]{36}$/.test(call.requestId || "")),
    ).toBe(true);
    expect(calls[1].time - calls[0].time).toBeGreaterThanOrEqual(1000);
    expect(calls[2].time - calls[1].time).toBeGreaterThanOrEqual(2000);
    expect(calls[3].time - calls[2].time).toBeGreaterThanOrEqual(4000);
    expect(errors).toEqual([]);
    records.push({
      name: "exact-cursor-backoff-permanent-stop",
      passed: true,
      calls,
      minimum_reconnect_delays_ms: [1000, 2000, 4000],
      last_event_id: "9007199254740993",
      forbidden_reconnects_virtual_120s: 0,
    });
  });
});
