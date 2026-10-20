import { test, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { gzipSync } from "node:zlib";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";

const directory = path.resolve(
  import.meta.dirname,
  "../../docs/evidence/full/profile-artifact-fixtures-rc6",
);
const checks: Record<string, unknown>[] = [];
type Options = {
  nodes?: number;
  mode?:
    | "expiry"
    | "expired-grant"
    | "twice-expired"
    | "forbidden"
    | "gone"
    | "corrupt"
    | "foreign"
    | "pending";
  gate?: Promise<void>;
};
async function fixture(page: Page, options: Options = {}) {
  const count = options.nodes || 2;
  const tree = {
    name: "all",
    value: count - 1,
    self: 0,
    children: Array.from({ length: count - 1 }, (_, i) => ({
      name: `artifact_leaf_${i}`,
      value: 1,
      self: 1,
      children: [],
    })),
  };
  const inline = { ...tree, name: "INLINE_WORKER_PARSE_SENTINEL" };
  const compressed =
    options.mode === "corrupt"
      ? Buffer.from([31, 139, 1, 2, 3, 4, 5])
      : gzipSync(JSON.stringify(tree));
  const counts = { grants: 0, objects: 0 },
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.text().includes("X-Amz-Signature"))
      errors.push("Signed URL appeared in console");
  });
  await page.addInitScript(() => {
    const probe = {
      workers: [] as { op?: string; terminated: boolean }[],
      layoutNodeCounts: [] as number[],
      signedUrlsInMessages: 0,
    };
    (window as Window & { __profileProbe?: typeof probe }).__profileProbe =
      probe;
    const parse = JSON.parse;
    JSON.parse = (text: string, reviver?: Parameters<typeof JSON.parse>[1]) => {
      if (text.includes("INLINE_WORKER_PARSE_SENTINEL"))
        throw new Error(
          "Main-thread profile JSON.parse is forbidden by fixture",
        );
      return parse(text, reviver);
    };
    const Native = window.Worker;
    window.Worker = class extends Native {
      record = { op: undefined as string | undefined, terminated: false };
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options);
        probe.workers.push(this.record);
        this.addEventListener("message", (event) => {
          if (Array.isArray(event.data?.rects))
            probe.layoutNodeCounts.push(event.data.rects.length);
        });
      }
      postMessage(
        message: unknown,
        transfer?: Transferable[] | StructuredSerializeOptions,
      ) {
        if (message && typeof message === "object" && "op" in message) {
          this.record.op = String(message.op);
          if (JSON.stringify(message).includes("X-Amz-Signature"))
            probe.signedUrlsInMessages++;
        }
        if (transfer === undefined) super.postMessage(message);
        else super.postMessage(message, transfer as Transferable[]);
      }
      terminate() {
        this.record.terminated = true;
        super.terminate();
      }
    };
  });
  await page.route("**/objects/**", async (route) => {
    counts.objects++;
    if (options.gate) await options.gate;
    if (
      (options.mode === "expiry" && counts.objects === 1) ||
      options.mode === "twice-expired"
    )
      return route.fulfill({
        status: 403,
        contentType: "application/xml",
        body: "<Error><Code>RequestExpired</Code></Error>",
      });
    if (options.mode === "forbidden" || options.mode === "gone")
      return route.fulfill({
        status: options.mode === "gone" ? 410 : 403,
        contentType: "application/xml",
        body: "<Error><Code>AccessDenied</Code></Error>",
      });
    await route
      .fulfill({
        status: 200,
        contentType: "application/gzip",
        body: compressed,
      })
      .catch(() => {});
  });
  await page.route("**/api/v1/**", async (route) => {
    const endpoint = new URL(route.request().url()).pathname.replace(
      "/api/v1",
      "",
    );
    if (endpoint === "/tasks/7/artifacts/21/download") {
      counts.grants++;
      return response(route, {
        url:
          options.mode === "foreign"
            ? "https://external.invalid/objects/fixture.gz?X-Amz-Signature=fixture"
            : `http://127.0.0.1:4178/objects/drop-artifacts/results/tasks/7/attempts/11/flamegraph.json.gz?X-Amz-Signature=fixture-${counts.grants}`,
        expires_at:
          options.mode === "expired-grant" && counts.grants === 1
            ? "2000-01-01T00:00:00Z"
            : "2099-01-01T00:00:00Z",
      });
    }
    let data: unknown;
    if (endpoint === "/auth/me")
      data = {
        user: {
          id: "fixture-viewer",
          username: "fixture-viewer",
          display_name: "Imported gzip fixture",
          role: "viewer",
        },
        csrf_token: "fixture-only",
      };
    else if (endpoint === "/task-kinds")
      data = [{ id: "IMPORT_COLLAPSED", display_name: "折叠调用栈导入" }];
    else if (endpoint === "/auth/providers") data = { ai: { enabled: false } };
    else if (endpoint === "/tasks/7")
      data = {
        id: "7",
        name: "Imported gzip artifact fixture",
        kind: "IMPORT_COLLAPSED",
        params: { source: "import" },
        creator_id: "fixture-viewer",
        collection_status: "COLLECTED",
        analysis_status: "SUCCESS",
        permissions: {
          read: true,
          operate: false,
          share: false,
          delete: false,
          reanalyze: false,
        },
        attempts: [
          { id: "11", attempt: 2, status: "COLLECTED" },
          { id: "10", attempt: 1, status: "COLLECTED" },
        ],
        artifacts: [
          {
            id: "20",
            attempt_id: "10",
            kind: "RESULT",
            name: "flamegraph.json.gz",
            status: "ready",
            size: 1,
          },
          {
            id: "21",
            attempt_id: "11",
            kind: "RESULT",
            name: "flamegraph.json.gz",
            status: "ready",
            size: compressed.byteLength,
            sha256: "fixture-only",
            download_allowed: true,
          },
        ],
        suggestions: [],
        created_at: "2026-10-18T00:00:00Z",
        updated_at: "2026-10-18T00:00:01Z",
      };
    else if (endpoint === "/tasks/7/result")
      data = {
        schema_version: 1,
        kind: "IMPORT_COLLAPSED",
        total_samples: count - 1,
        unique_stacks: count - 1,
        node_count: count,
        max_depth: 2,
        flamegraph: inline,
        top_functions: [],
        suggestions: [],
        summary: {
          source: "import",
          duration_seconds: null,
          frequency_hz: null,
          unit: "samples",
          limitations: ["Imported synthetic gzip browser fixture"],
        },
        sections: {},
      };
    else if (
      endpoint === "/tasks/7/events" ||
      endpoint === "/tasks/7/suggestions/requests"
    )
      data = { items: [], total: 0 };
    else {
      errors.push("Unhandled fixture endpoint: " + endpoint);
      return route.fulfill({ status: 404, json: {} });
    }
    return response(route, data);
  });
  return { counts, errors, compressedBytes: compressed.byteLength };
}
const response = (route: Route, data: unknown) =>
  route.fulfill({
    status: 200,
    json: { data, error: null, request_id: "imported-gzip-fixture" },
  });
const loaded = (page: Page) =>
  expect(page.locator('[data-flame-source="gzip-artifact"]')).toBeVisible();
const fallback = (page: Page) =>
  expect(
    page.getByText("压缩制品载入失败；下方保留本次分析的内联调用树。", {
      exact: true,
    }),
  ).toBeVisible();

test.describe("imported authorized gzip artifact lifecycle", () => {
  test.skip(
    process.env.DROP_UI_PREVIEW !== "1",
    "Explicit local preview opt-in required",
  );
  test.afterAll(() => {
    mkdirSync(directory, { recursive: true });
    const html = readFileSync(
      path.resolve(import.meta.dirname, "../dist/index.html"),
      "utf8",
    );
    const assets = [
      ...html.matchAll(/(?:src|href)="(\/assets\/[^\"]+\.(?:js|css))"/g),
    ].map((x) => x[1]);
    writeFileSync(
      path.join(directory, "report.json"),
      JSON.stringify(
        {
          passed: checks.length === 10,
          checked_at: new Date().toISOString(),
          provenance:
            "Imported synthetic API/S3 data; real native Chromium fetch/Worker/gzip/UTF8/JSON validation. No real S3/API/server verification.",
          actual_backend_verified: false,
          main_thread_profile_json_parse_guard: true,
          signed_urls_stored_in_reports: false,
          bundle_sha256: Object.fromEntries(
            assets.map((asset) => [
              asset,
              createHash("sha256")
                .update(
                  readFileSync(
                    path.resolve(import.meta.dirname, "../dist" + asset),
                  ),
                )
                .digest("hex"),
            ]),
          ),
          checks,
        },
        null,
        2,
      ) + "\n",
    );
  });
  test("loads all 100k nodes from gzip in a native Worker with accessible responsive UI", async ({
    page,
  }) => {
    const f = await fixture(page, { nodes: 100000 });
    await page.goto("/tasks/7");
    await loaded(page);
    await expect(
      page.locator('.flame-svg[data-total-nodes="100000"]'),
    ).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(() =>
          (
            window as Window & {
              __profileProbe?: { layoutNodeCounts: number[] };
            }
          ).__profileProbe!.layoutNodeCounts.includes(100000),
        ),
      )
      .toBe(true);
    expect(
      Number(await page.locator(".flame-svg").getAttribute("height")),
    ).toBeLessThanOrEqual(120);
    await expect(page.locator(".flame-thin-notice")).toContainText("99,999");
    const viewports = [];
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      const violations = (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations;
      expect(violations).toEqual([]);
      mkdirSync(directory, { recursive: true });
      await page.screenshot({
        path: path.join(directory, `gzip-100000-${width}.png`),
        fullPage: true,
      });
      viewports.push({ width, axe_violations: 0, document_overflow: false });
    }
    expect(f.counts).toEqual({ grants: 1, objects: 1 });
    await page
      .getByRole("textbox", { name: "搜索函数", exact: true })
      .fill("artifact_leaf_99998");
    await page
      .getByRole("button", { name: "聚焦首个匹配函数，共1处", exact: true })
      .click();
    await expect(page.locator(".flame-svg")).toHaveAttribute(
      "data-total-nodes",
      "1",
    );
    await expect(page.locator(".flame-context")).toContainText(
      "artifact_leaf_99998",
    );
    await expect(page.locator(".flame-thin-notice")).toHaveCount(0);
    expect(f.errors).toEqual([]);
    expect(
      await page.evaluate(
        () =>
          (
            window as Window & {
              __profileProbe?: { signedUrlsInMessages: number };
            }
          ).__profileProbe!.signedUrlsInMessages,
      ),
    ).toBe(0);
    checks.push({
      name: "native-gzip-100000",
      all_nodes: 100000,
      compressed_bytes: f.compressedBytes,
      counts: f.counts,
      viewports,
      main_thread_json_parse: false,
      compact_shallow_graph: true,
      hidden_leaf_search_focus_verified: true,
    });
  });
  for (const mode of ["expiry", "expired-grant"] as const)
    test(mode + " refreshes authorization once", async ({ page }) => {
      const f = await fixture(page, { mode });
      await page.goto("/tasks/7");
      await loaded(page);
      expect(f.counts).toEqual({
        grants: 2,
        objects: mode === "expiry" ? 2 : 1,
      });
      expect(f.errors).toEqual([]);
      checks.push({ name: mode, counts: f.counts });
    });
  test("repeated expiry stops after exactly one refresh and retains inline evidence", async ({
    page,
  }) => {
    const f = await fixture(page, { mode: "twice-expired" });
    await page.goto("/tasks/7");
    await fallback(page);
    await expect(page.getByRole("alert")).toContainText("刷新后仍已过期");
    expect(f.counts).toEqual({ grants: 2, objects: 2 });
    expect(f.errors).toEqual([]);
    checks.push({ name: "expiry-bound", counts: f.counts });
  });
  for (const mode of ["forbidden", "gone"] as const)
    test(
      mode + " is specific and never automatically refreshed",
      async ({ page }) => {
        const f = await fixture(page, { mode });
        await page.goto("/tasks/7");
        await fallback(page);
        await expect(page.getByRole("alert")).toContainText(
          mode === "gone" ? "已超过保留期" : "没有读取",
        );
        await expect(page.getByRole("alert")).toContainText(
          "imported-gzip-fixture",
        );
        await page.waitForTimeout(400);
        expect(f.counts).toEqual({ grants: 1, objects: 1 });
        expect(f.errors).toEqual([]);
        checks.push({ name: mode, counts: f.counts, no_automatic_retry: true });
      },
    );
  test("invalid gzip surfaces a parse error while preserving the inline tree", async ({
    page,
  }) => {
    const f = await fixture(page, { mode: "corrupt" });
    await page.goto("/tasks/7");
    await fallback(page);
    await expect(page.getByRole("alert")).toContainText("gzip 制品损坏");
    await expect(page.locator(".flame-component")).toContainText(
      "INLINE_WORKER_PARSE_SENTINEL",
    );
    expect(f.errors).toEqual([]);
    checks.push({
      name: "corrupt-gzip",
      counts: f.counts,
      inline_fallback: true,
    });
  });
  test("foreign signed origins are refused before any object request", async ({
    page,
  }) => {
    const f = await fixture(page, { mode: "foreign" });
    await page.goto("/tasks/7");
    await fallback(page);
    await expect(page.getByRole("alert")).toContainText("来源或路径无效");
    expect(f.counts).toEqual({ grants: 1, objects: 0 });
    expect(f.errors).toEqual([]);
    checks.push({ name: "foreign-origin", counts: f.counts });
  });
  test("route exit terminates a pending artifact Worker without a duplicate grant", async ({
    page,
  }) => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const f = await fixture(page, { mode: "pending", gate });
    try {
      await page.goto("/tasks/7");
      await expect.poll(() => f.counts.objects).toBe(1);
      await page.locator('a[href="/guide"]').first().click();
      await expect(
        page.getByRole("heading", { level: 1, name: /^从采集到洞察/ }),
      ).toBeVisible();
      await expect
        .poll(() =>
          page.evaluate(() =>
            (
              window as Window & {
                __profileProbe?: {
                  workers: { op: string; terminated: boolean }[];
                };
              }
            ).__profileProbe!.workers.some(
              (w) => w.op === "artifact" && w.terminated,
            ),
          ),
        )
        .toBe(true);
      expect(f.counts.grants).toBe(1);
      expect(f.errors).toEqual([]);
      checks.push({
        name: "route-abort",
        counts: f.counts,
        native_worker_terminated: true,
      });
    } finally {
      release();
    }
  });
  test("total deadline terminates a pending download and retains the inline tree", async ({
    page,
  }) => {
    await page.clock.install();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const f = await fixture(page, { mode: "pending", gate });
    try {
      await page.goto("/tasks/7");
      await expect.poll(() => f.counts.objects).toBe(1);
      await expect(page.locator(".flame-component")).toBeVisible();
      await page.clock.runFor(31000);
      await fallback(page);
      await expect(page.getByRole("alert")).toContainText("载入超时");
      expect(f.counts.grants).toBe(1);
      expect(f.errors).toEqual([]);
      checks.push({
        name: "deadline",
        counts: f.counts,
        virtual_browser_time_ms: 31000,
        native_worker_terminated: true,
      });
    } finally {
      release();
    }
  });
});
