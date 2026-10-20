/**
 * 在真实 App 外壳中使用导入会话，验证指南布局、键盘、权限和减少动效。
 * API 写请求会被拒绝，截图不能被当成真实采集或模型诊断执行的证据。
 */
import { test as base, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

type FixtureRole = "platform_admin" | "viewer";
type RequestJournal = {
  reads: string[];
  unexpected: string[];
};

// 显式导入的合成 UI 夹具：运行真实 React 应用外壳和指南页面，
// 不把界面验证当作生产登录、采集或诊断执行的证据。
const test = base.extend<{
  fixtureRole: FixtureRole;
  importedGuideFixture: RequestJournal;
}>({
  fixtureRole: ["platform_admin", { option: true }],
  importedGuideFixture: [
    async ({ page, fixtureRole }, use) => {
      const journal: RequestJournal = { reads: [], unexpected: [] };
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.route("**/api/v1/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const endpoint = url.pathname.replace("/api/v1", "");
        const identity = `${request.method()} ${endpoint}`;
        if (
          url.hostname !== "127.0.0.1" ||
          request.method() !== "GET" ||
          !["/auth/me", "/task-kinds", "/auth/providers"].includes(endpoint)
        ) {
          journal.unexpected.push(identity);
          await route.abort("blockedbyclient");
          return;
        }
        journal.reads.push(identity);
        const data =
          endpoint === "/auth/me"
            ? {
                user: {
                  id: "imported-guide-user",
                  username: "imported-guide-fixture",
                  display_name: "导入界面验证",
                  role: fixtureRole,
                },
                csrf_token: "imported-ui-fixture-token",
              }
            : endpoint === "/auth/providers"
              ? { local: true, oidc: { enabled: false } }
              : [];
        await route.fulfill({
          json: { data, request_id: "imported-guide-ui-fixture" },
        });
      });
      await use(journal);
      expect(
        journal.unexpected,
        "Guide must not mutate or read business data",
      ).toEqual([]);
      expect(
        errors,
        "The real App shell must render without page errors",
      ).toEqual([]);
    },
    { auto: true },
  ],
});

const steps = ["采集", "分析", "诊断", "验证"] as const;

async function openGuide(page: Page) {
  await page.goto("/guide");
  await expect(page.locator(".guide-page")).toBeVisible();
  await expect(
    page.locator(".guide-page").getByRole("heading", { level: 1 }),
  ).toHaveCount(1);
  await page.evaluate(() => document.fonts.ready);
}

async function expectNoHorizontalOverflow(page: Page) {
  const result = await page.evaluate(() => {
    const root = document.documentElement;
    const guide = document.querySelector<HTMLElement>(".guide-page");
    if (!guide) throw new Error("Guide root missing");
    const box = guide.getBoundingClientRect();
    return {
      viewport: window.innerWidth,
      document: root.scrollWidth,
      guide: guide.scrollWidth,
      guideClient: guide.clientWidth,
      guideRight: box.right,
      overflowing: [...guide.querySelectorAll("*")]
        .map((element) => ({
          element: `${element.tagName}.${element.getAttribute("class") || ""}`,
          right: element.getBoundingClientRect().right,
          width: element.getBoundingClientRect().width,
        }))
        .filter((element) => element.right > box.right + 1),
    };
  });
  expect(result.document).toBeLessThanOrEqual(result.viewport);
  expect(result.guide, JSON.stringify(result.overflowing)).toBeLessThanOrEqual(
    result.guideClient + 1,
  );
  expect(result.guideRight).toBeLessThanOrEqual(result.viewport + 1);
}

for (const viewport of [
  { width: 1440, height: 1000 },
  { width: 1280, height: 900 },
  { width: 1024, height: 900 },
  { width: 768, height: 1024 },
  { width: 375, height: 900 },
  { width: 320, height: 812 },
]) {
  test(`imported Guide layout fits ${viewport.width}px without horizontal scrolling`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await openGuide(page);
    const guide = page.locator(".guide-page");
    await expectNoHorizontalOverflow(page);
    await expect(guide.locator('button[aria-pressed="true"]')).toHaveCount(1);
    for (const name of steps) {
      const control = guide.getByRole("button", { name, exact: true });
      await expect(control).toBeVisible();
      const box = await control.boundingBox();
      expect(box?.height).toBeGreaterThanOrEqual(44);
    }
    await page.screenshot({
      path: testInfo.outputPath(`guide-${viewport.width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    if (viewport.width <= 375) {
      await guide.getByRole("button", { name: "验证", exact: true }).click();
      await guide.locator("details summary").first().click();
      await expectNoHorizontalOverflow(page);
    }
  });
}

test("all four workflow controls select their associated live region", async ({
  page,
}) => {
  await openGuide(page);
  const guide = page.locator(".guide-page");
  const panel = guide.locator("#guide-step-panel");
  const panelContents = new Set<string>();
  for (const name of steps) {
    const control = guide.getByRole("button", { name, exact: true });
    await control.click();
    await expect(control).toHaveAttribute("aria-pressed", "true");
    await expect(guide.locator('button[aria-pressed="true"]')).toHaveCount(1);
    await expect(control).toHaveAttribute("aria-controls", "guide-step-panel");
    await expect(panel).toHaveAttribute("id", "guide-step-panel");
    await expect(panel).toHaveAttribute("role", "region");
    await expect(panel).toHaveAttribute(
      "aria-labelledby",
      (await control.getAttribute("id"))!,
    );
    await expect(panel).toBeVisible();
    panelContents.add((await panel.innerText()).trim());
    await expectNoHorizontalOverflow(page);
  }
  expect(
    panelContents.size,
    "Each workflow step should explain different evidence",
  ).toBe(4);
});

test("changing workflow steps keeps the following content stable without clipping", async ({
  page,
}, testInfo) => {
  const evidence = [];
  for (const width of [1440, 768, 375, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await openGuide(page);
    const guide = page.locator(".guide-page");
    const measurements = [];
    for (const name of steps) {
      const control = guide.getByRole("button", { name, exact: true });
      await control.click();
      await expect(control).toHaveAttribute("aria-pressed", "true");
      measurements.push(
        await page.evaluate((step) => {
          const panel =
            document.querySelector<HTMLElement>("#guide-step-panel")!;
          const nextSection = document.querySelector("#guide-scenarios")!;
          return {
            step,
            panelHeight: panel.getBoundingClientRect().height,
            panelScrollHeight: panel.scrollHeight,
            panelClientHeight: panel.clientHeight,
            followingTop: nextSection.getBoundingClientRect().top + scrollY,
          };
        }, name),
      );
    }
    const spread = (values: number[]) =>
      Math.max(...values) - Math.min(...values);
    expect(
      spread(measurements.map((item) => item.panelHeight)),
      `Workflow panel height changes at ${width}px: ${JSON.stringify(measurements)}`,
    ).toBeLessThanOrEqual(1);
    expect(
      spread(measurements.map((item) => item.followingTop)),
      `Following section shifts at ${width}px: ${JSON.stringify(measurements)}`,
    ).toBeLessThanOrEqual(1);
    for (const item of measurements) {
      expect(
        item.panelScrollHeight,
        `Workflow content is clipped at ${width}px in ${item.step}`,
      ).toBeLessThanOrEqual(item.panelClientHeight + 1);
    }
    evidence.push({ width, measurements });
  }
  await testInfo.attach("workflow-layout-stability-imported-ui-fixture", {
    body: Buffer.from(JSON.stringify(evidence, null, 2)),
    contentType: "application/json",
  });
});

test("workflow controls preserve keyboard order, activation and visible focus", async ({
  page,
}) => {
  await openGuide(page);
  const guide = page.locator(".guide-page");
  const first = guide.getByRole("button", { name: "采集", exact: true });
  await first.focus();
  await page.keyboard.press("Tab");
  const analysis = guide.getByRole("button", { name: "分析", exact: true });
  await expect(analysis).toBeFocused();
  expect(
    await analysis.evaluate((element) => element.matches(":focus-visible")),
  ).toBe(true);
  const focus = await analysis.evaluate((element) => {
    const css = getComputedStyle(element);
    return {
      outline: css.outlineStyle,
      width: parseFloat(css.outlineWidth),
      shadow: css.boxShadow,
    };
  });
  expect(
    (focus.outline !== "none" && focus.width > 0) || focus.shadow !== "none",
  ).toBe(true);
  await page.keyboard.press("Enter");
  await expect(analysis).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Tab");
  const diagnosis = guide.getByRole("button", { name: "诊断", exact: true });
  await expect(diagnosis).toBeFocused();
  await page.keyboard.press("Space");
  await expect(diagnosis).toHaveAttribute("aria-pressed", "true");
  await expect(diagnosis).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    guide.getByRole("button", { name: "验证", exact: true }),
  ).toBeFocused();
});

test("FAQ remains a native keyboard-operated disclosure", async ({ page }) => {
  await openGuide(page);
  const disclosure = page.locator(".guide-page details").first();
  const summary = disclosure.locator("summary");
  await expect(summary).toBeVisible();
  await expect(disclosure).not.toHaveAttribute("open", "");
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(disclosure).toHaveAttribute("open", "");
  await expect(summary).toBeFocused();
  await page.keyboard.press("Space");
  await expect(disclosure).not.toHaveAttribute("open", "");
});

test("authorized Guide actions point to supported workspaces without executing them", async ({
  page,
}) => {
  await openGuide(page);
  const guide = page.locator(".guide-page");
  await expect(
    guide.getByRole("link", { name: "创建诊断", exact: true }).first(),
  ).toHaveAttribute("href", "/tasks/new");
  await expect(
    guide.getByRole("link", { name: "发起智能调查", exact: true }).first(),
  ).toHaveAttribute("href", "/investigations");
  await expect(
    guide.getByRole("link", { name: "查看节点", exact: true }).first(),
  ).toHaveAttribute("href", "/agents");
});

test.describe("imported read-only user", () => {
  test.use({ fixtureRole: "viewer" });
  test("Guide hides write actions while retaining node navigation", async ({
    page,
  }) => {
    await openGuide(page);
    const guide = page.locator(".guide-page");
    await expect(
      guide.getByRole("link", { name: "创建诊断", exact: true }),
    ).toHaveCount(0);
    await expect(
      guide.getByRole("link", { name: "发起智能调查", exact: true }),
    ).toHaveCount(0);
    await expect(
      guide.getByRole("link", { name: "查看节点", exact: true }).first(),
    ).toHaveAttribute("href", "/agents");
  });
});

test("reduced-motion preference suppresses Guide animation and transition", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openGuide(page);
  expect(
    await page.evaluate(
      () => matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
  ).toBe(true);
  const moving = await page.locator(".guide-page").evaluate((guide) => {
    const seconds = (value: string) =>
      value
        .split(",")
        .map(
          (part) => parseFloat(part) * (part.trim().endsWith("ms") ? 0.001 : 1),
        );
    return [guide, ...guide.querySelectorAll("*")].flatMap((element) =>
      [null, "::before", "::after"].flatMap((pseudo) => {
        const style = getComputedStyle(element, pseudo);
        const max = Math.max(
          ...seconds(style.animationDuration),
          ...seconds(style.transitionDuration),
        );
        return max > 0.01
          ? [`${element.tagName}.${element.className}${pseudo || ""}: ${max}s`]
          : [];
      }),
    );
  });
  expect(moving).toEqual([]);
});

for (const viewport of [
  { width: 1440, height: 1000 },
  { width: 375, height: 900 },
]) {
  test(`real shell and imported Guide have no axe violations at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await openGuide(page);
    for (const name of ["采集", "验证"] as const) {
      await page
        .locator(".guide-page")
        .getByRole("button", { name, exact: true })
        .click();
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    }
  });
}
