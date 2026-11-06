import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.beforeEach(async ({ page }) => {
  // 教程使用真实静态章节，不需要导入业务夹具；任何 API 请求都算越界。
  await page.route("**/api/**", (route) => route.abort("blockedbyclient"));
});

test("every chapter is readable directly without a business session", async ({
  page,
}, testInfo) => {
  test.setTimeout(60000);
  const apiRequests: string[] = [];
  const errors: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/"))
      apiRequests.push(request.url());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  for (let id = 1; id <= 22; id++) {
    await page.goto(`/book/chapter${id}/`);
    await expect(page.locator(".book-main h1")).toHaveCount(1);
    await expect(page.locator(".book-article h2").first()).toBeVisible();
    expect(
      (await page.locator(".book-article").innerText()).length,
    ).toBeGreaterThan(1000);
    expect(
      await page.locator(".book-article h2").count(),
    ).toBeGreaterThanOrEqual(5);
    await expect(
      page.locator('.book-chapter-link[aria-current="page"]'),
    ).toHaveCount(1);
    if (id === 11 || id === 22)
      await page.screenshot({
        path: testInfo.outputPath(`chapter${id}.png`),
        animations: "disabled",
      });
  }
  expect(apiRequests).toEqual([]);
  expect(errors).toEqual([]);
});

test("late practical chapter content cannot replace the chapter selected afterwards", async ({
  page,
}) => {
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requested = () => {};
  const started = new Promise<void>((resolve) => {
    requested = resolve;
  });
  await page.route(/\/assets\/chapter11-[^/]+\.js$/, async (route) => {
    requested();
    await held;
    await route.continue();
  });
  try {
    await page.goto("/book/chapter11/");
    await started;
    await page.locator('.book-chapter-link[href="/book/chapter12/"]').click();
    await expect(page.locator(".book-article h2").first()).toHaveText(
      "先给采集器一项足够窄的工作",
    );
    const completed = page.waitForResponse(/\/assets\/chapter11-[^/]+\.js$/);
    release();
    await completed;
    await expect(page.locator(".book-main h1")).toHaveText(
      "让 C++ 交出一份可信的数据",
    );
    await expect(page.locator(".book-article h2").first()).toHaveText(
      "先给采集器一项足够窄的工作",
    );
  } finally {
    release();
  }
});

test("workbench only contacts its local API after explicit connection and keeps a request key after a lost response", async ({
  page,
}) => {
  // Explicitly synthetic transport fixture: this verifies browser behavior, not collection.
  const requests: string[] = [];
  const keys: string[] = [];
  const task = {
    id: "fixture-1",
    attempt: 1,
    iterations: 1000000,
    status: "FAILED",
    error_code: "SYNTHETIC_FAILURE",
    events: [],
  };
  await page.route("**/mini-api/**", async (route) => {
    requests.push(route.request().method());
    if (route.request().method() === "POST") {
      keys.push(route.request().headers()["idempotency-key"]);
      if (keys.length === 1) {
        await route.abort("connectionreset");
        return;
      }
      await route.fulfill({ status: 200, json: { data: task } });
      return;
    }
    await route.fulfill({
      status: 200,
      json: { data: keys.length ? [task] : [] },
    });
  });
  await page.goto("/book/workbench");
  await expect(
    page.getByRole("heading", { name: "你的第一条采集链" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "创建采集任务" }),
  ).toBeDisabled();
  expect(requests).toEqual([]);
  await page.getByRole("button", { name: "连接本地服务" }).click();
  await expect(
    page.getByRole("button", { name: "创建采集任务" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "创建采集任务" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "创建采集任务" }).click();
  await expect(page.locator(".lesson-task-list")).toContainText("fixture-1");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  for (const width of [320, 375, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole("link", { name: "回到实战篇" })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width + 1);
  }
  const audit = await new AxeBuilder({ page })
    .include(".drop-book")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(audit.violations).toEqual([]);
});

test("chapter links, anchors, reading preferences and focus work after reload", async ({
  page,
}) => {
  await page.goto("/book");
  await expect(page).toHaveURL(/\/book\/chapter1\//);
  await page.getByRole("button", { name: "放大字号" }).click();
  await page.getByRole("button", { name: "切换深色模式" }).click();
  await page.reload();
  await expect(page.locator(".drop-book")).toHaveAttribute(
    "data-theme",
    "dark",
  );
  await expect(page.getByLabel("当前字号")).toHaveText("20");
  await page.getByRole("button", { name: "专注阅读", exact: true }).click();
  await expect(page.locator(".book-sidebar")).toBeHidden();
  await page.getByRole("button", { name: "退出专注", exact: true }).click();
  await page.locator('.book-toc a[href="#section-3"]').click();
  await expect(page).toHaveURL(/#section-3$/);
  const top = await page
    .locator("#section-3")
    .evaluate((element) => element.getBoundingClientRect().top);
  expect(top).toBeGreaterThanOrEqual(70);
  expect(top).toBeLessThan(220);
  await page
    .getByRole("navigation", { name: "上一章与下一章" })
    .getByRole("link")
    .last()
    .click();
  await expect(page).toHaveURL(/\/book\/chapter2\//);
  await expect(page.locator(".drop-book")).toHaveAttribute(
    "data-theme",
    "dark",
  );
});

test("mobile navigation supports keyboard dismissal and narrow screens", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/book/chapter1/");
  await page.getByRole("button", { name: "打开章节目录" }).click();
  await expect(page.locator(".book-sidebar")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".book-sidebar")).toBeHidden();
  await page.screenshot({
    path: testInfo.outputPath("mobile-chapter1.png"),
    animations: "disabled",
  });
  await expect(
    page.getByRole("button", { name: "打开章节目录" }),
  ).toBeFocused();
  await page.getByRole("button", { name: "打开章节目录" }).click();
  await page.locator('.book-chapter-link[href="/book/chapter6/"]').click();
  await expect(page).toHaveURL(/\/book\/chapter6\//);
  await expect(page.locator(".book-sidebar")).toBeHidden();
  await page.locator(".book-inline-toc summary").click();
  await page
    .getByRole("navigation", { name: "窄屏本章目录" })
    .locator('a[href="#section-3"]')
    .click();
  await expect(page).toHaveURL(/#section-3$/);
  const sectionTop = await page
    .locator("#section-3")
    .evaluate((element) => element.getBoundingClientRect().top);
  expect(sectionTop).toBeGreaterThanOrEqual(60);
  expect(sectionTop).toBeLessThan(220);
  for (const width of [320, 375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const sizes = await page.evaluate(() => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
    }));
    expect(sizes.document, `${width}px layout`).toBeLessThanOrEqual(
      sizes.viewport + 1,
    );
  }
});

test("public chapter handles unknown paths, unavailable storage and inaccessible clipboard", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", {
      get() {
        throw new Error("storage blocked");
      },
    });
    Object.defineProperty(navigator, "clipboard", { value: undefined });
  });
  await page.goto("/book/chapter3/");
  await expect(page.locator(".book-storage-note")).toContainText(
    "浏览器无法保存阅读设置",
  );
  await page
    .getByRole("button", { name: "复制代码", exact: true })
    .first()
    .click();
  await expect(page.locator(".book-copy-status").first()).toContainText(
    "请选中代码后复制",
  );
  await page.goto("/book/missing/");
  await expect(
    page.getByRole("heading", { name: "这一章还不存在" }),
  ).toBeVisible();
});

test("copying the SSE teaching example preserves the terminating blank line", async ({
  page,
}) => {
  // Synthetic clipboard adapter keeps this protocol check isolated from the desktop clipboard.
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: async (value: string) => {
          (
            window as typeof window & { bookCopiedText?: string }
          ).bookCopiedText = value;
        },
      },
    });
  });
  await page.goto("/book/chapter7/");
  const example = page
    .locator(".book-code-block")
    .filter({ hasText: "event: task_status" });
  await expect(example).toHaveCount(1);
  await example.getByRole("button", { name: "复制代码", exact: true }).click();
  await expect(example.locator(".book-copy-status")).toHaveText("已复制");
  const copied = await page.evaluate(
    () =>
      (window as typeof window & { bookCopiedText?: string }).bookCopiedText,
  );
  expect(copied).toContain('data: {"stage":"采集完成"}');
  expect(copied?.endsWith("\n\n")).toBe(true);
});

test("reading page meets automated accessibility checks in both themes", async ({
  page,
}, testInfo) => {
  await page.goto("/book/chapter1/");
  await expect(page.locator(".book-article h2").first()).toBeVisible();
  for (const theme of ["light", "dark"]) {
    if (theme === "dark")
      await page.getByRole("button", { name: "切换深色模式" }).click();
    await page.screenshot({
      path: testInfo.outputPath(`desktop-${theme}.png`),
      animations: "disabled",
    });
    const audit = await new AxeBuilder({ page })
      .include(".drop-book")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(audit.violations, `${theme} accessibility`).toEqual([]);
  }
  await page.getByRole("button", { name: "切换浅色模式" }).click();
  await page.goto("/book/chapter4/#section-7");
  await expect(page.locator("#section-7")).toBeInViewport();
  await page.screenshot({
    path: testInfo.outputPath("chapter4-outbox.png"),
    animations: "disabled",
  });
});
