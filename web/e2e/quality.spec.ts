/** 明确导入的合成摘要和会话；这里只验证渲染、过滤、权限提示与下载，不代表数据库或模型实跑。 */
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const run = {
  schema_version: 1,
  id: "fixture-run-001",
  title: "导入的质量验证夹具",
  category: "agent",
  suite: "fixture-suite",
  status: "passed",
  started_at: "2026-01-01T00:00:00Z",
  finished_at: "2026-01-01T00:00:01Z",
  environment: "linux_isolated",
  source_commit: null,
  source_sha256: "a".repeat(64),
  provenance: "imported_fixture",
  fixture_scope: "明确导入的合成摘要，不代表真实模型运行",
  counts: { passed: 7, failed: 0, skipped: 0, total: 7 },
  metrics: [
    {
      id: "human-accuracy",
      label: "独立人工准确率",
      value: null,
      unit: "ratio",
    },
  ],
  checks: [
    {
      id: "example-check",
      label: "导入的检查项",
      status: "passed",
      detail: "只验证界面状态",
    },
  ],
  limits: ["人工语义评审未完成"],
};
const records = [
  { run, record_sha256: "b".repeat(64) },
  {
    run: {
      ...run,
      id: "fixture-run-002",
      title: "导入的历史失败",
      status: "failed",
      finished_at: "2026-01-01T00:00:02Z",
      counts: { passed: 0, failed: 1, skipped: 0, total: 1 },
      checks: [
        {
          id: "failure-check",
          label: "严格收据解析",
          status: "failed",
          detail: "历史失败必须保留，不被后续成功覆盖",
        },
      ],
    },
    record_sha256: "c".repeat(64),
  },
];
async function setup(
  page: Page,
  options: { role?: string; empty?: boolean; failure?: boolean } = {},
) {
  const unexpected: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    const respond = (data: unknown) =>
      route.fulfill({
        status: 200,
        json: { data, error: null, request_id: "imported-quality-ui" },
      });
    if (path === "/auth/me")
      return respond({
        user: {
          id: "imported-quality-user",
          username: "fixture",
          display_name: "合成界面验收",
          role: options.role ?? "platform_admin",
        },
        csrf_token: "imported-fixture",
      });
    if (path === "/task-kinds") return respond([]);
    if (path === "/auth/providers")
      return respond({
        local: true,
        oidc: { enabled: false },
        ai: { enabled: false },
      });
    if (path === "/quality/runs") {
      if (options.failure)
        return route.fulfill({
          status: 503,
          json: {
            data: null,
            error: {
              code: "QUALITY_REPORTS_UNAVAILABLE",
              message: "质量记录暂时不可读取",
            },
            request_id: "imported-quality-ui",
          },
        });
      return respond({
        items: options.empty ? [] : records,
        total: options.empty ? 0 : records.length,
        page: 1,
        page_size: records.length,
      });
    }
    const row = records.find((x) => path === "/quality/runs/" + x.run.id);
    if (row) return respond(row);
    unexpected.push(path);
    return route.abort();
  });
  return unexpected;
}

for (const width of [1440, 375])
  test(`质量明细与可访问性 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    const unexpected = await setup(page);
    await page.goto("/quality");
    await expect(
      page.getByRole("heading", { name: "质量验证." }),
    ).toBeVisible();
    await expect(page.getByText("未验收", { exact: true })).toBeVisible();
    await page
      .getByLabel("测试套件", { exact: true })
      .selectOption("fixture-suite");
    await expect(
      page.getByRole("heading", { name: "套件运行时间线" }),
    ).toBeVisible();
    await page.getByLabel("执行结果", { exact: true }).selectOption("failed");
    await expect(
      page.getByRole("heading", { name: "导入的历史失败" }),
    ).toBeVisible();
    await expect(
      page.getByText("历史失败必须保留，不被后续成功覆盖"),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    const scan = await new AxeBuilder({ page }).analyze();
    expect(scan.violations).toEqual([]);
    await page.screenshot({
      path: `../.local/jd-depth-20261023/ui/quality-${width}.png`,
      fullPage: true,
    });
    expect(unexpected).toEqual([]);
  });
test("空状态真实显示无记录", async ({ page }) => {
  await setup(page, { empty: true });
  await page.goto("/quality");
  await expect(
    page.getByRole("heading", { name: "还没有发布的执行记录" }),
  ).toBeVisible();
});
test("读取失败不伪装为零记录", async ({ page }) => {
  await setup(page, { failure: true });
  await page.goto("/quality");
  await expect(page.getByText("质量记录暂时不可读取")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "还没有发布的执行记录" }),
  ).toHaveCount(0);
  await expect(
    page.getByLabel("执行记录概览").getByText("—", { exact: true }),
  ).toHaveCount(4);
});
test("普通角色无导航且直接访问受限", async ({ page }) => {
  const unexpected = await setup(page, { role: "operator" });
  await page.goto("/quality");
  await expect(
    page.getByRole("heading", { name: "需要管理员权限" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "质量验证" })).toHaveCount(0);
  expect(unexpected).toEqual([]);
});
test("导出前重新读取记录", async ({ page }) => {
  await setup(page);
  await page.goto("/quality?run=fixture-run-001");
  await expect(page.getByRole("heading", { name: run.title })).toBeVisible();
  const request = page.waitForRequest((r) =>
    r.url().endsWith("/api/v1/quality/runs/fixture-run-001"),
  );
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出记录 JSON" }).click();
  await request;
  expect((await download).suggestedFilename()).toBe("fixture-run-001.json");
});
