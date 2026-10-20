import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

// Browser interaction fixtures only; these do not prove a live perf collection.
test.describe("imported flame interaction fixtures", () => {
  test.skip(
    process.env.DROP_UI_PREVIEW !== "1",
    "Explicit local preview opt-in required",
  );
  test("preserves evidence through thread/system/self filters and TopN navigation", async ({
    page,
    baseURL,
  }) => {
    expect(new URL(baseURL!).hostname).toMatch(/^(127\.0\.0\.1|localhost)$/);
    const out = path.resolve(
      import.meta.dirname,
      "../../docs/evidence/full/flame-explorer-fixtures-rc6",
    );
    mkdirSync(out, { recursive: true });
    const errors: string[] = [];
    page.on("pageerror", (problem) => errors.push(problem.message));
    const leaf = (name: string, value: number) => ({
      name,
      value,
      self: value,
      children: [],
    });
    const tree = {
      name: "all",
      value: 100,
      self: 0,
      children: [
        {
          name: "worker [pid=7 tid=8]",
          value: 60,
          self: 0,
          children: [
            {
              name: "__libc_start_main",
              value: 60,
              self: 10,
              children: [
                {
                  name: "business",
                  value: 50,
                  self: 20,
                  children: [leaf("parse", 30)],
                },
              ],
            },
          ],
        },
        {
          name: "worker [pid=7 tid=9]",
          value: 40,
          self: 0,
          children: [leaf("io", 40)],
        },
      ],
    };
    const task = {
      id: "1",
      name: "导入交互 fixture · 有明确线程记录",
      kind: "PERF_CPU",
      params: { source: "import" },
      creator_id: "fixture-admin",
      collection_status: "COLLECTED",
      analysis_status: "SUCCESS",
      permissions: {
        read: true,
        operate: true,
        share: true,
        delete: true,
        reanalyze: true,
      },
      artifacts: [],
      suggestions: [],
      created_at: "2026-10-16T12:00:00Z",
      updated_at: "2026-10-16T12:01:00Z",
    };
    await page.route("**/api/v1/**", async (route) => {
      const name = new URL(route.request().url()).pathname.replace(
        "/api/v1",
        "",
      );
      let data: unknown;
      if (name === "/auth/me")
        data = {
          user: {
            id: "fixture-admin",
            username: "fixture",
            display_name: "导入 fixture",
            role: "platform_admin",
          },
          csrf_token: "imported-fixture-only",
        };
      else if (name === "/auth/providers") data = { ai: { enabled: false } };
      else if (name === "/task-kinds")
        data = [{ id: "PERF_CPU", display_name: "CPU 火焰图 · perf" }];
      else if (name === "/tasks/1") data = task;
      else if (name === "/tasks/1/result")
        data = {
          schema_version: 1,
          kind: "PERF_CPU",
          total_samples: 100,
          unique_stacks: 3,
          node_count: 7,
          max_depth: 4,
          flamegraph: tree,
          top_functions: [
            {
              name: "business",
              self: 20,
              total: 50,
              self_percent: 20,
              total_percent: 50,
            },
            {
              name: "parse",
              self: 30,
              total: 30,
              self_percent: 30,
              total_percent: 30,
            },
            {
              name: "io",
              self: 40,
              total: 40,
              self_percent: 40,
              total_percent: 40,
            },
          ],
          suggestions: [],
          summary: {
            source: "import",
            duration_seconds: null,
            frequency_hz: null,
            unit: "samples",
            limitations: [
              "Imported browser fixture; not a live perf collection.",
            ],
          },
          sections: {
            perf: {
              threads: [
                {
                  pid: 7,
                  tid: 8,
                  comm: "worker",
                  root_name: "worker [pid=7 tid=8]",
                  sample_count: 60,
                },
                {
                  pid: 7,
                  tid: 9,
                  comm: "worker",
                  root_name: "worker [pid=7 tid=9]",
                  sample_count: 40,
                },
              ],
              lost_samples: null,
              loss_status: "unknown",
            },
          },
        };
      else if (
        name === "/tasks/1/events" ||
        name === "/tasks/1/suggestions/requests"
      )
        data = { items: [], total: 0 };
      else {
        errors.push("Unexpected fixture endpoint: " + name);
        return route.fulfill({
          status: 404,
          json: { error: { code: "FIXTURE_UNHANDLED" } },
        });
      }
      await route.fulfill({
        status: 200,
        json: { data, error: null, request_id: "imported-ui-fixture" },
      });
    });
    await page.goto("/tasks/1");
    const graph = page.locator(".flame-component");
    const svg = graph.locator(".flame-svg");
    await expect(svg).toHaveAttribute("data-total-nodes", "7");
    await expect(page.getByLabel("任务执行阶段")).toContainText("数据导入");
    await graph.locator(".flame-settings > summary").click();
    await graph
      .getByRole("combobox", { name: "线程", exact: true })
      .selectOption("worker [pid=7 tid=8]");
    await expect(svg).toHaveAttribute("data-total-nodes", "4");
    await expect(graph.locator(".flame-context")).toContainText("60 samples");
    await expect(
      svg.locator('g[aria-label^="worker [pid=7 tid=9]"]'),
    ).toHaveCount(0);
    await graph.getByRole("button", { name: "重置", exact: true }).click();
    await expect(svg).toHaveAttribute("data-total-nodes", "7");
    await graph.getByLabel("隐藏系统栈", { exact: true }).check();
    await expect(svg).toHaveAttribute("data-total-nodes", "6");
    await expect(graph).toContainText("自身权重 10 samples 仍计入根节点");
    await expect(
      svg.locator('g[aria-label^="__libc_start_main,"]'),
    ).toHaveCount(0);
    await graph
      .getByRole("combobox", { name: "权重视图", exact: true })
      .selectOption("self");
    await expect(svg).toHaveAttribute("data-total-nodes", "4");
    await expect(
      svg.locator('g[aria-label^="business, 20 samples,"]'),
    ).toHaveCount(1);
    await graph
      .getByRole("combobox", { name: "排序", exact: true })
      .selectOption("name");
    await graph
      .getByRole("combobox", { name: "颜色方案", exact: true })
      .selectOption("ocean");
    await expect(svg.locator("rect").first()).toHaveAttribute(
      "fill",
      /^#(?:88b9df|8ebad5|a3bbd9|a0ccca|9db1d4|b4c9e2)$/,
    );
    await graph.getByRole("button", { name: "重置", exact: true }).click();
    await expect(svg).toHaveAttribute("data-total-nodes", "7");
    await page
      .getByRole("button", { name: "在火焰图中聚焦 business", exact: true })
      .click();
    await expect(svg).toHaveAttribute("data-total-nodes", "2");
    const relations = graph.locator(".flame-relations");
    await expect(relations).toContainText("__libc_start_main");
    await expect(relations).toContainText("parse");
    await graph
      .getByRole("combobox", { name: "颜色方案", exact: true })
      .selectOption("ocean");
    await expect(svg.locator("rect").first()).toHaveAttribute(
      "fill",
      /^#(?:88b9df|8ebad5|a3bbd9|a0ccca|9db1d4|b4c9e2)$/,
    );
    await expect(svg).toHaveAttribute("data-total-nodes", "2");
    await expect(relations).toContainText("parse");
    await graph
      .getByRole("combobox", { name: "排序", exact: true })
      .selectOption("name");
    await expect(svg).toHaveAttribute("data-total-nodes", "2");
    await relations
      .getByRole("button", { name: "__libc_start_main", exact: true })
      .click();
    await expect(svg).toHaveAttribute("data-total-nodes", "3");
    await page.locator(".perf-evidence > summary").click();
    await expect(
      page.getByRole("heading", { name: "原始线程记录", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".perf-evidence-metrics")).toContainText(
      "已证实丢样未知",
    );
    const checks = [];
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(350);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      const axe = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      writeFileSync(
        path.join(out, `axe-${width}.json`),
        JSON.stringify(axe.violations, null, 2),
      );
      expect(axe.violations).toEqual([]);
      await page.evaluate(() => {
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
        path: path.join(out, `explorer-${width}.png`),
        fullPage: true,
      });
      checks.push({ width, overflow: false, axe_violations: 0 });
    }
    expect(errors).toEqual([]);
    writeFileSync(
      path.join(out, "report.json"),
      JSON.stringify(
        {
          source: "imported-browser-fixtures",
          production_or_backend_verified: false,
          checked_at: new Date().toISOString(),
          thread_filter: true,
          system_weight_preserved: true,
          self_aggregation: true,
          sorting_and_palette: true,
          topn_focus: true,
          focused_subtree_preserved_on_palette_and_sort: true,
          callers_callees: true,
          checks,
          errors,
        },
        null,
        2,
      ),
    );
  });
});
