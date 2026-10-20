/**
 * 向真实页面注入不可信规则文本，验证 HTML 清理、链接策略、布局与无障碍边界。
 */
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

// 使用导入的合成规则、模型与 SSE 文本；本套测试不会连接生产环境。
test("renders bounded rule Markdown and omits model and stream displays", async ({
  page,
  baseURL,
}) => {
  expect(new URL(baseURL!).hostname).toBe("127.0.0.1");
  const directory = path.resolve(
    import.meta.dirname,
    "../../docs/evidence/full/markdown-fixtures-rc6",
  );
  mkdirSync(directory, { recursive: true });
  const errors: string[] = [],
    externalRequests: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== new URL(baseURL!).origin)
      externalRequests.push(new URL(request.url()).origin);
  });
  await page.addInitScript(() => {
    (window as Window & { __markdownXss?: number }).__markdownXss = 0;
  });
  let releaseParser!: () => void;
  const parserGate = new Promise<void>((resolve) => {
    releaseParser = resolve;
  });
  await page.route("**/assets/SafeMarkdownContent-*.js", async (route) => {
    await parserGate;
    return route.continue();
  });
  const content = `## 先确认采样边界

**观察**：分配热点占比偏高，*关联信号需要实测*。

- 对照同版本的请求量
- 保留采样与现场配置

1. 重复采样
2. 验证改动

> 导入的示例不能替代生产采样证据。

| 指标 | 观察值 | 建议 |
| --- | --- | --- |
| allocation | 24% | 对照请求量与对象寿命 |

\`malloc\` 与下面的代码仅作展示：

\`\`\`sh
echo "${"synthetic-imported-argument-".repeat(15)}"
<script>window.__markdownXss = 99</script>
\`\`\`

[官方说明](https://example.test/docs) · [任务证据](/tasks/7#evidence)

[危险协议](javascript:alert%281%29) [协议相对](//example.test/pixel)
![禁止加载](https://example.test/pixel.png)

<img src="https://example.test/html-pixel" onerror="window.__markdownXss=99">
<script>window.__markdownXss=99</script>`;
  const rule = {
    id: "51",
    title: "Synthetic rule diagnostic",
    content,
    engine: "rules",
    version: "fixture",
    severity: "warning",
    confidence: 0.65,
    evidence: {},
  };
  const ai = {
    ...rule,
    id: "52",
    title: "Synthetic model diagnostic",
    engine: "ai",
    content:
      "# 模型补充\n\n**只读建议**，请结合现场验证。\n\n- 保留证据\n\n" +
      content,
  };
  const task = {
    id: "7",
    name: "Imported safe Markdown fixture",
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
    artifacts: [],
    suggestions: [rule, ai],
    created_at: "2026-10-17T00:00:00Z",
    updated_at: "2026-10-17T00:00:01Z",
  };
  const result = {
    schema_version: 1,
    kind: "IMPORT_COLLAPSED",
    total_samples: 1,
    unique_stacks: 1,
    node_count: 2,
    max_depth: 2,
    flamegraph: {
      name: "all",
      value: 1,
      self: 0,
      children: [{ name: "synthetic", value: 1, self: 1, children: [] }],
    },
    top_functions: [],
    suggestions: [rule],
    sections: {},
    summary: {
      source: "import",
      duration_seconds: null,
      frequency_hz: null,
      unit: "samples",
      limitations: ["Synthetic imported browser fixture"],
    },
  };
  await page.route("**/api/v1/**", async (route) => {
    const endpoint = new URL(route.request().url()).pathname.replace(
      "/api/v1",
      "",
    );
    if (endpoint === "/tasks/7/events/stream") {
      const event = {
        sequence: "1",
        type: "SUGGESTION_DELTA",
        payload: {
          ai_request_id: "17",
          text: "## 流式验证\n\n- **首段**已到达\n\n![流式图片](https://example.test/stream.png)\n\n[坏链接](data:text/html,unsafe)",
        },
      };
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body:
          "id: 1\nevent: task_status\ndata: " + JSON.stringify(event) + "\n\n",
      });
    }
    let data: unknown;
    if (endpoint === "/auth/me")
      data = {
        user: {
          id: "fixture-viewer",
          username: "fixture",
          display_name: "Imported Markdown fixture",
          role: "viewer",
        },
        csrf_token: "fixture-only",
      };
    else if (endpoint === "/auth/providers")
      data = { ai: { enabled: true, name: "Imported fixture" } };
    else if (endpoint === "/task-kinds")
      data = [{ id: "IMPORT_COLLAPSED", display_name: "折叠调用栈导入" }];
    else if (endpoint === "/tasks/7") data = task;
    else if (endpoint === "/tasks/7/result") data = result;
    else if (endpoint === "/tasks/7/suggestions/requests")
      data = { items: [{ id: "17", status: "running" }], total: 1 };
    else if (endpoint === "/tasks/7/events") data = { items: [], total: 0 };
    else {
      errors.push("Unhandled fixture endpoint: " + endpoint);
      return route.fulfill({ status: 404, json: {} });
    }
    return route.fulfill({
      status: 200,
      json: { data, error: null, request_id: "imported-markdown-fixture" },
    });
  });
  await page.goto("/tasks/7", { waitUntil: "domcontentloaded" });
  try {
    const fallback = page.locator(".safe-markdown-pending").first();
    await expect(fallback).toBeVisible();
    await expect(fallback).toContainText("## 先确认采样边界");
    await expect(fallback.locator("script,img,a")).toHaveCount(0);
    expect(
      await page.evaluate(
        () => (window as Window & { __markdownXss?: number }).__markdownXss,
      ),
    ).toBe(0);
  } finally {
    releaseParser();
  }
  const ruleMarkdown = page.locator(".suggestions .safe-markdown");
  await expect(ruleMarkdown).toHaveAttribute(
    "data-markdown-sanitizer",
    "dompurify",
  );
  await expect(
    ruleMarkdown.getByRole("heading", { name: "先确认采样边界", exact: true }),
  ).toBeVisible();
  // Markdown 的块级排版不能被外层建议元数据的 flex 规则挤坏；
  // 只检查页面是否横向溢出，会漏掉被压成窄列却仍在视口内的正文。
  const headingBox = await ruleMarkdown
    .getByRole("heading", { name: "先确认采样边界", exact: true })
    .boundingBox();
  const paragraphBox = await ruleMarkdown.locator("p").first().boundingBox();
  expect(paragraphBox!.y).toBeGreaterThanOrEqual(
    headingBox!.y + headingBox!.height,
  );
  await expect(ruleMarkdown.locator("ul li")).toHaveCount(2);
  await expect(ruleMarkdown.locator("ol li")).toHaveCount(2);
  await expect(ruleMarkdown.locator("pre code")).toContainText(
    "<script>window.__markdownXss = 99</script>",
  );
  const sanitizerProbe = await page.evaluate(async () => {
    const asset = performance
      .getEntriesByType("resource")
      .map((entry) => entry.name)
      .find((name) => /\/assets\/SafeMarkdownContent-[^/]+\.js$/.test(name));
    if (!asset || new URL(asset).origin !== location.origin)
      throw new Error("SANITIZER_ASSET_MISSING");
    const module = await import(/* @vite-ignore */ asset);
    const clean = module.sanitizeDiagnosticHTML(
      '<p style="background:red" onclick="window.__markdownXss=99">safe</p><svg onload="window.__markdownXss=99"></svg><math><mtext>bad</mtext></math><iframe src="https://example.test/"></iframe><a href="javascript:alert(1)">bad</a><a href="//example.test/">relative</a><code>&lt;script&gt;inert&lt;/script&gt;</code>',
    );
    const doc = new DOMParser().parseFromString(clean, "text/html");
    return {
      unsafeNodes: doc.querySelectorAll(
        "script,iframe,svg,math,style,img,[style],[onclick],[onload]",
      ).length,
      unsafeLinks: doc.querySelectorAll("a[href]").length,
      code: doc.querySelector("code")?.textContent,
    };
  });
  expect(sanitizerProbe).toEqual({
    unsafeNodes: 0,
    unsafeLinks: 0,
    code: "<script>inert</script>",
  });
  await expect(
    page
      .locator(".suggestion-feedback .safe-markdown")
      .getByRole("heading", { name: "模型补充" }),
  ).toHaveCount(0);
  await expect(page.locator(".ai-stream")).toHaveCount(0);
  await expect(
    page.locator(
      ".safe-markdown img, .safe-markdown iframe, .safe-markdown script, .safe-markdown link",
    ),
  ).toHaveCount(0);
  await expect(
    ruleMarkdown.getByRole("link", { name: "官方说明" }),
  ).toHaveAttribute("rel", "noopener noreferrer");
  expect(
    await page.evaluate(
      () => (window as Window & { __markdownXss?: number }).__markdownXss,
    ),
  ).toBe(0);
  for (const href of await page
    .locator(".safe-markdown a")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href"))))
    expect(href).not.toMatch(/^(?:javascript:|data:|\/\/)/i);
  const checks = [];
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.evaluate(() => document.fonts.ready);
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
    writeFileSync(
      path.join(directory, `axe-${width}.json`),
      JSON.stringify(violations, null, 2),
    );
    expect(violations).toEqual([]);
    if (width === 375) {
      await expect(
        ruleMarkdown.getByRole("region", { name: "代码示例" }),
      ).toBeVisible();
      expect(
        await ruleMarkdown
          .locator("pre")
          .evaluate((node) => node.scrollWidth > node.clientWidth),
      ).toBe(true);
      expect(
        await ruleMarkdown
          .locator(".safe-markdown-table")
          .evaluate((node) => node.scrollWidth > node.clientWidth),
      ).toBe(true);
    }
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
      path: path.join(directory, `markdown-${width}.png`),
      fullPage: true,
    });
    checks.push({
      width,
      horizontal_document_overflow: false,
      axe_violations: 0,
    });
  }
  expect(externalRequests).toEqual([]);
  expect(errors).toEqual([]);
  writeFileSync(
    path.join(directory, "report.json"),
    JSON.stringify(
      {
        provenance:
          "Imported synthetic browser fixtures; no production/API/model verification",
        checked_at: new Date().toISOString(),
        rule: true,
        persisted_model_display: false,
        streamed_model_display: false,
        parser_chunk_plaintext_fallback_verified: true,
        xss_executed: false,
        external_requests: externalRequests,
        checks,
        errors,
      },
      null,
      2,
    ),
  );
});
