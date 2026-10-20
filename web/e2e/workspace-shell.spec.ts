import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync } from "node:fs";
import path from "node:path";

const evidence = path.resolve(
  import.meta.dirname,
  "../../docs/evidence/upgrade-20261019",
);

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const endpoint = new URL(route.request().url()).pathname.replace(
      "/api/v1",
      "",
    );
    let data: unknown = {};
    if (endpoint === "/auth/me")
      data = {
        user: {
          id: "imported-user",
          username: "fixture",
          display_name: "导入界面验证",
          role: "platform_admin",
        },
        csrf_token: "imported-fixture-token",
      };
    else if (endpoint === "/tasks" || endpoint === "/agents")
      data = { items: [], total: 0, page: 1, page_size: 20 };
    else if (endpoint === "/task-kinds") data = [];
    else if (endpoint === "/auth/providers")
      data = { local: true, oidc: { enabled: false } };
    else if (endpoint === "/groups") data = { items: [] };
    await route.fulfill({ json: { data, request_id: "imported-ui-fixture" } });
  });
});

test("desktop navigation collapses, persists and preserves accessible labels", async ({
  page,
}) => {
  await page.goto("/tasks");
  await expect(
    page.getByRole("heading", { name: /^诊断任务/, level: 1 }),
  ).toBeVisible();
  await page.getByRole("button", { name: "收起侧栏" }).click();
  await expect(page.locator(".sidebar")).toHaveCSS("width", "84px");
  await expect(
    page
      .getByRole("navigation", { name: "工作空间导航" })
      .getByRole("link", { name: "接入节点" }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "展开侧栏" })).toBeVisible();
  await page.getByRole("button", { name: "展开侧栏" }).click();
  await expect(page.locator(".sidebar")).toHaveCSS("width", "236px");
  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations).toEqual([]);
  mkdirSync(evidence, { recursive: true });
  await page.screenshot({
    path: path.join(evidence, "workspace-1440.png"),
    fullPage: true,
  });
});

test("stored desktop preference does not hide mobile navigation", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem("drop-navigation-compact-v1", "compact"),
  );
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/tasks");
  await page.getByRole("button", { name: "打开菜单" }).click();
  const link = page
    .getByRole("navigation", { name: "工作空间导航" })
    .getByRole("link", { name: "接入节点" });
  await expect(link).toBeVisible();
  await expect(link.locator("span")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "打开菜单" })).toBeFocused();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  mkdirSync(evidence, { recursive: true });
  await page.screenshot({
    path: path.join(evidence, "workspace-375.png"),
    fullPage: true,
  });
});

test("browser modifier shortcuts remain available", async ({ page }) => {
  await page.goto("/tasks");
  await expect(page.getByRole("button", { name: "收起侧栏" })).toBeVisible();
  const prevented = await page.evaluate(() => {
    const event = new KeyboardEvent("keydown", {
      key: "/",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(prevented).toBe(false);
  await page.keyboard.press("Control+k");
  await expect(
    page.getByRole("textbox", { name: "全局搜索诊断任务" }),
  ).toBeFocused();
});

test("login and guide omit external integration displays", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({ status: 401, json: { error: { code: "UNAUTHORIZED" } } }),
  );
  // Imported provider fixture: even configured enterprise providers stay hidden.
  await page.route("**/api/v1/auth/providers", (route) =>
    route.fulfill({
      json: {
        data: { local: true, oidc: { enabled: true, name: "导入企业" } },
      },
    }),
  );
  await page.goto("/tasks");
  await expect(page.getByRole("button", { name: "进入工作台" })).toBeVisible();
  await expect(page.locator('a[href="/api/v1/auth/oidc/start"]')).toHaveCount(
    0,
  );
  await page.unroute("**/api/v1/auth/me");
  await page.goto("/guide");
  await expect(page.getByText(/企业登录|外部模型|异地备份/)).toHaveCount(0);
});

test("truncated imported Java class labels retain visible and full accessible names", async ({
  page,
}) => {
  const names = [
    "[Ljava.lang.Object:",
    "[Ljava.util.HashMap$Node:",
    "java.lang.String",
  ];
  const flamegraph = {
    name: "all",
    value: 100,
    self: 70,
    children: names.map((name) => ({
      name,
      value: 10,
      self: 10,
      children: [],
    })),
  };
  await page.route("**/api/v1/tasks/7", (route) =>
    route.fulfill({
      json: {
        data: {
          id: "7",
          name: "Imported Java class accessibility fixture",
          kind: "IMPORT_COLLAPSED",
          params: { source: "import" },
          creator_id: "imported-user",
          collection_status: "COLLECTED",
          analysis_status: "SUCCESS",
          artifacts: [],
          suggestions: [],
          created_at: "2026-10-19T00:00:00Z",
          updated_at: "2026-10-19T00:00:01Z",
        },
      },
    }),
  );
  await page.route("**/api/v1/tasks/7/result", (route) =>
    route.fulfill({
      json: {
        data: {
          schema_version: 1,
          kind: "IMPORT_COLLAPSED",
          total_samples: 100,
          unique_stacks: 3,
          node_count: 4,
          max_depth: 2,
          flamegraph,
          top_functions: [],
          suggestions: [],
          sections: {},
          summary: {
            source: "import",
            unit: "bytes",
            limitations: ["Imported browser fixture"],
          },
        },
      },
    }),
  );
  await page.route("**/api/v1/tasks/7/events*", (route) =>
    route.fulfill({ json: { data: { items: [] } } }),
  );
  await page.goto("/tasks/7");
  const frames = page.locator("g[data-flame-id]");
  await expect(frames).toHaveCount(4);
  let truncated = 0;
  for (const frame of await frames.all()) {
    const text = await frame.locator("text").textContent();
    if (text?.endsWith("…")) {
      truncated++;
      expect(await frame.getAttribute("aria-label")).toContain(text);
    }
  }
  expect(truncated).toBeGreaterThan(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
