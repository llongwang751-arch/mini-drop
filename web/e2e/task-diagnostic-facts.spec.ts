/**
 * TaskDetail 的长 v2 事实报告布局回归；全部 API 数据为显式导入夹具，不采集或调用模型。
 * 页面总宽度可能被外层 overflow 隐藏，必须检查报告自身边界及表格内部滚动，不能只看 document。
 */
import { readFile } from "node:fs/promises";
import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function importedTask(page: Page) {
  const unexpected: string[] = [],
    errors: string[] = [],
    csp: string[] = [];
  page.on("pageerror", () => errors.push("PAGE_ERROR"));
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      /Content Security Policy|Content-Security-Policy/.test(message.text())
    )
      csp.push("CSP_ERROR");
  });
  const policy = (await readFile("nginx.conf", "utf8")).match(
    /add_header Content-Security-Policy "([^"]+)" always;/,
  )?.[1];
  expect(policy).toContain("font-src 'self';");
  const facts = Array.from({ length: 24 }, (_, index) => ({
    id: `F${index + 1}`,
    evidence_id: "E3",
    tool: "inspect_hotspots",
    path: `/top_functions/${index % 10}/self_percent`,
    operator: "eq",
    value: index + 0.25,
    unit: "percent",
    description: `导入事实 ${index + 1}：本轮受控采样的 self 权重占比为 ${index + 0.25}%，只能描述当前样本分布，不证明请求耗时或因果根源。`,
  }));
  const list = (items: unknown[]) => ({ items, total: items.length });
  const responses: Record<string, unknown> = {
    "/auth/me": {
      user: {
        id: "imported-user",
        username: "imported-user",
        display_name: "导入长事实布局",
        role: "viewer",
      },
      csrf_token: "imported-csrf",
    },
    "/auth/providers": {
      local: true,
      oidc: { enabled: false },
      ai: { enabled: true, name: "imported-provider" },
    },
    "/task-kinds": [{ id: "PYTHON_CPU", display_name: "导入 Python CPU" }],
    "/tasks/901": {
      id: "901",
      name: "Imported synthetic long v2 report",
      kind: "PYTHON_CPU",
      target_agent_id: "imported-agent",
      creator_id: "imported-user",
      params: { pid: 99, duration_seconds: 8, frequency_hz: 49 },
      collection_status: "COLLECTED",
      analysis_status: "SUCCESS",
      attempts: [{ id: "903", attempt: 1, status: "COLLECTED" }],
      analysis_identity: {
        analysis_job_id: "904",
        analysis_run: 1,
        input_artifact_id: "905",
        input_digest: "a".repeat(64),
      },
      permissions: {
        read: true,
        operate: false,
        share: false,
        delete: false,
        reanalyze: false,
      },
      created_at: "2026-10-21T01:00:00Z",
      updated_at: "2026-10-21T01:00:00Z",
      artifacts: [],
      events: [],
    },
    "/tasks/901/result": {
      schema_version: 1,
      kind: "PYTHON_CPU",
      total_samples: 100,
      unique_stacks: 1,
      node_count: 2,
      max_depth: 2,
      flamegraph: {
        name: "all",
        value: 100,
        self: 0,
        children: [
          { name: "imported_work", value: 100, self: 100, children: [] },
        ],
      },
      top_functions: [
        {
          name: "imported_work",
          self: 100,
          total: 100,
          self_percent: 100,
          total_percent: 100,
        },
      ],
      suggestions: [],
      summary: { unit: "samples", duration_seconds: 8, frequency_hz: 49 },
    },
    "/tasks/901/events": list([]),
    "/tasks/901/verifications": list([]),
    "/tasks/901/diagnoses": list([
      {
        id: "902",
        task_id: "901",
        attempt_id: "903",
        analysis_job_id: "904",
        analysis_run: 1,
        goal: "导入的长事实报告布局检查",
        mode: "model",
        status: "success",
        created_at: "2026-10-21T01:00:00Z",
        trace: [
          {
            evidence_id: "E1",
            tool: "read_profile_summary",
            observation: { imported: true, total_weight: 100 },
          },
          {
            evidence_id: "E3",
            tool: "inspect_hotspots",
            observation: { imported: true },
          },
        ],
        report: {
          engine: "model",
          model: "explicit-imported-provider",
          report_version: 2,
          summary: "明确导入的布局夹具；不代表模型或因果验证已执行。",
          facts,
          hypotheses: [
            {
              claim: "导入的待验证假设",
              evidence_ids: ["E3"],
              verification: "须另行授权受控复测",
              unverified_interpretation:
                "这是导入的未核验解释，不能混入本轮已核对事实。",
            },
          ],
          limitations: ["imported synthetic UI fixture only"],
        },
      },
    ]),
  };
  await page.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (
      url.hostname !== "127.0.0.1" ||
      !["GET", "HEAD"].includes(request.method())
    ) {
      unexpected.push("OUT_OF_SCOPE_REQUEST");
      await route.abort();
      return;
    }
    if (url.pathname.startsWith("/api/v1/")) {
      const endpoint = url.pathname.slice("/api/v1".length);
      if (!(endpoint in responses)) {
        unexpected.push(endpoint);
        await route.abort();
        return;
      }
      await route.fulfill({
        json: {
          data: responses[endpoint],
          request_id: "explicit-imported-task-facts",
        },
      });
    } else if (
      request.isNavigationRequest() &&
      request.resourceType() === "document"
    ) {
      const response = await route.fetch();
      await route.fulfill({
        response,
        headers: { ...response.headers(), "Content-Security-Policy": policy! },
      });
    } else await route.continue();
  });
  await page.goto("/tasks/901");
  await page.getByRole("tab", { name: "智能诊断", exact: true }).click();
  return { unexpected, errors, csp };
}

for (const width of [1440, 375]) {
  test(`imported TaskDetail long facts remain within their panel at ${width}px`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    const journal = await importedTask(page);
    const facts = page.getByRole("region", {
      name: "结构化事实与模型解释",
      exact: true,
    });
    const table = facts.getByRole("region", {
      name: "已核对事实来源表",
      exact: true,
    });
    await expect(table.locator("tbody tr")).toHaveCount(24);
    const bounds = await facts.evaluate((node) => {
      const box = node.getBoundingClientRect();
      const panel = node.closest(".diagnosis-agent")!.getBoundingClientRect();
      return {
        left: box.left,
        right: box.right,
        width: box.width,
        panelLeft: panel.left,
        panelRight: panel.right,
        viewport: innerWidth,
      };
    });
    expect(bounds.left).toBeGreaterThanOrEqual(bounds.panelLeft);
    expect(bounds.right).toBeLessThanOrEqual(bounds.panelRight);
    expect(bounds.left).toBeGreaterThanOrEqual(0);
    expect(bounds.right).toBeLessThanOrEqual(width);
    await table.focus();
    await expect(table).toBeFocused();
    if (width === 375) {
      expect(
        await table.evaluate((node) => node.scrollWidth > node.clientWidth),
      ).toBe(true);
      await page.keyboard.press("ArrowRight");
      await expect
        .poll(() => table.evaluate((node) => node.scrollLeft))
        .toBeGreaterThan(0);
      await page.keyboard.press("ArrowLeft");
      await expect
        .poll(() => table.evaluate((node) => node.scrollLeft))
        .toBe(0);
    }
    await expect(facts.getByRole("link").first()).toHaveAttribute(
      "href",
      "#diagnosis-902-E3",
    );
    await facts.getByRole("link").first().click();
    await expect(page.locator("#diagnosis-902-E3")).toBeInViewport();
    await table.evaluate((node) =>
      node.scrollTo({ left: 0, behavior: "instant" }),
    );
    await page.evaluate(() => document.fonts.ready);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    expect(journal.unexpected).toEqual([]);
    expect(journal.errors).toEqual([]);
    expect(journal.csp).toEqual([]);
    await facts.screenshot({
      path: info.outputPath(`long-task-facts-${width}.png`),
    });
  });
}
