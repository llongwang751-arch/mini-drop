/**
 * 历史发布环境的显式验收入口：包含登录与受授权结果读取，不等同于无凭据的静态页面检查。
 * 本文件的地址和开关属于既有验收约定，普通前端单测不运行它。
 */
import { test, expect, request } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { safeStageAPI } from "./safe-stage-io";

test("public HTTPS release preserves real results and responsive navigation", async ({
  browser,
  baseURL,
}) => {
  test.skip(
    process.env.DROP_PRODUCTION_E2E !== "1",
    "Explicit public read-only validation required",
  );
  expect(baseURL).toBe("https://120.24.187.205");
  const root = path.resolve(import.meta.dirname, "../..");
  const output = path.join(root, "docs/evidence/upgrade-20261019/production");
  mkdirSync(output, { recursive: true });
  const api = safeStageAPI(await request.newContext({ baseURL }));
  const response = await api.post("/api/v1/auth/login", {
    data: {
      username: readFileSync(
        path.join(root, ".local/admin_username"),
        "utf8",
      ).trim(),
      password: readFileSync(
        path.join(root, ".local/admin_password"),
        "utf8",
      ).trim(),
    },
  });
  expect(response.status()).toBe(200);
  const state = await api.storageState();
  expect(state.cookies.length).toBeGreaterThan(0);
  expect(state.cookies.every((cookie) => cookie.secure)).toBe(true);
  const context = await browser.newContext({ storageState: state });
  const page = await context.newPage();
  const errors: string[] = [];
  const reports: object[] = [];
  let modelRequests = 0;
  page.on("pageerror", () => errors.push("PAGE_ERROR"));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/suggestions/requests"))
      modelRequests++;
  });
  const task = process.env.DROP_PRODUCTION_TASK_ID || "29";
  expect(task).toMatch(/^[1-9]\d{0,18}$/);
  for (const route of [
    "/",
    "/tasks",
    "/tasks/" + task,
    "/agents",
    "/tasks/new",
    "/schedules",
    "/groups",
    "/rules",
    "/composites",
    "/settings",
    "/guide",
  ]) {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(route);
    await expect(page.getByRole("main")).toBeVisible();
    await page.waitForFunction(() => !document.querySelector("main .skeleton"));
    if (route === "/tasks/" + task) {
      await expect(
        page.locator('[data-flame-source="gzip-artifact"]'),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "生成模型辅助建议" }),
      ).toHaveCount(0);
    }
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.evaluate(() => document.fonts.ready);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      const violations = (await new AxeBuilder({ page }).analyze()).violations;
      expect(
        violations.map((v) => ({ id: v.id, nodes: v.nodes.length })),
        route + " " + width,
      ).toEqual([]);
      await page.screenshot({
        path: path.join(
          output,
          route.replaceAll("/", "_") + "-" + width + ".png",
        ),
        fullPage: true,
      });
      reports.push({
        route,
        width,
        axe_violations: 0,
        horizontal_overflow: false,
      });
    }
  }
  expect(modelRequests).toBe(0);
  expect(errors).toEqual([]);
  await context.close();
  const anonymous = await browser.newContext();
  const login = await anonymous.newPage();
  await login.goto(baseURL!);
  await expect(login.getByRole("button", { name: "进入工作台" })).toBeVisible();
  await expect(login.locator('a[href="/api/v1/auth/oidc/start"]')).toHaveCount(
    0,
  );
  await login.screenshot({
    path: path.join(output, "login-1440.png"),
    fullPage: true,
  });
  await anonymous.close();
  await api.dispose();
  writeFileSync(
    path.join(output, "report.json"),
    JSON.stringify(
      {
        origin: baseURL,
        real_task_id: task,
        tls_verification_enabled: true,
        secure_session_cookie: true,
        gzip_artifact_worker_verified: true,
        model_requests: modelRequests,
        errors,
        checks: reports,
      },
      null,
      2,
    ),
  );
});
