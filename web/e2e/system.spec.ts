import { test, expect, request as playwrightRequest } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import {
  safeStageAPI,
  safeRouteFetch,
  stageArtifactBytes,
} from "./safe-stage-io";
const root = path.resolve(import.meta.dirname, "../..");
const username =
  process.env.DROP_USERNAME ||
  readFileSync(path.join(root, ".local/admin_username"), "utf8").trim();
const password =
  process.env.DROP_PASSWORD ||
  readFileSync(path.join(root, ".local/admin_password"), "utf8").trim();
const baseURL = process.env.DROP_BASE_URL || "http://120.24.187.205";
const state = path.join(
  root,
  baseURL.includes("127.0.0.1")
    ? ".local/stage-system-state.json"
    : ".local/admin-state.json",
);
const evidence = path.join(
  root,
  process.env.DROP_EVIDENCE_DIR || "docs/evidence",
);
let csrf = "";
let importedTask = "";
let realTask = "";
let adminAPI: APIRequestContext;
async function post(
  api: APIRequestContext,
  url: string,
  data: unknown,
  key = crypto.randomUUID(),
) {
  return api.post("/api/v1" + url, {
    data,
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": key },
  });
}
async function waitComplete(id: string) {
  await expect
    .poll(
      async () => {
        const r = await adminAPI.get(`/api/v1/tasks/${id}`);
        const t = (await r.json()).data;
        return t.analysis_status === "SUCCESS"
          ? "SUCCESS"
          : t.collection_status === "FAILED"
            ? `FAILED:${t.error_code}`
            : t.analysis_status;
      },
      { timeout: 60000, intervals: [1000, 2000, 3000] },
    )
    .toBe("SUCCESS");
}
test.beforeAll(async () => {
  mkdirSync(evidence, { recursive: true });
  adminAPI = safeStageAPI(await playwrightRequest.newContext({ baseURL }));
  const r = await adminAPI.post("/api/v1/auth/login", {
    data: { username, password },
  });
  expect(r.status()).toBe(200);
  csrf = (await r.json()).data.csrf_token;
  await adminAPI.storageState({ path: state });
});
test.afterAll(async () => {
  await adminAPI?.dispose();
});
test("public login layout, anonymous boundary and reduced motion", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "登录工作空间" }),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(evidence, "login-desktop.png"),
    fullPage: true,
  });
  const a = await new AxeBuilder({ page }).analyze();
  writeFileSync(
    path.join(evidence, "axe-login.json"),
    JSON.stringify(a.violations, null, 2),
  );
  expect(a.violations).toEqual([]);
  expect((await page.request.get("/api/v1/tasks")).status()).toBe(401);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.getByRole("button", { name: "进入工作台" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(evidence, "login-mobile.png"),
    fullPage: true,
  });
});
test("imports real folded input, preserves lost-response idempotency, renders flamegraph and signed download", async ({
  browser,
}) => {
  const context = await browser.newContext({
    storageState: state,
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("pageerror", () => errors.push("PAGE_ERROR"));
  await page.goto("/tasks/new");
  await page.getByRole("button", { name: "导入原始文件", exact: true }).click();
  await page.getByRole("button", { name: /导入折叠调用栈/ }).click();
  await page
    .getByLabel("或粘贴折叠调用栈")
    .fill(
      "imported-worker;httpHandler;queryInventory 700\nimported-worker;httpHandler;malloc 300",
    );
  await page
    .getByLabel("任务名称", { exact: true })
    .fill(`验收 · 导入调用栈 ${Date.now()}`);
  let submissions = 0;
  let firstID = "";
  const keys: string[] = [];
  await page.route("**/api/v1/tasks", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    submissions++;
    keys.push(route.request().headers()["idempotency-key"]);
    const response = await safeRouteFetch(() => route.fetch());
    if (submissions === 1) {
      firstID = (await response.json()).data.id;
      await route.abort("failed");
    } else await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "创建诊断", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "创建诊断", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${firstID}$`));
  expect(keys[0]).toBe(keys[1]);
  importedTask = firstID;
  await waitComplete(importedTask);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "调用栈火焰图" }),
  ).toBeVisible();
  await expect(page.getByRole("group", { name: /可交互火焰图/ })).toBeVisible();
  await page.getByRole("textbox", { name: "搜索函数" }).fill("queryInventory");
  await expect(page.locator(".flame-svg g")).not.toHaveCount(0);
  await page.getByRole("button", { name: "重置", exact: true }).click();
  await page.screenshot({
    path: path.join(evidence, "result-desktop.png"),
    fullPage: true,
  });
  const axe = await new AxeBuilder({ page }).analyze();
  writeFileSync(
    path.join(evidence, "axe-result.json"),
    JSON.stringify(axe.violations, null, 2),
  );
  expect(axe.violations).toEqual([]);
  await page.getByRole("tab", { name: /制品文件/ }).click();
  const signed = await adminAPI.get(`/api/v1/tasks/${importedTask}/artifacts`);
  const artifacts = (await signed.json()).data.items;
  expect(artifacts.length).toBeGreaterThanOrEqual(5);
  const dl = await adminAPI.get(
    `/api/v1/tasks/${importedTask}/artifacts/${artifacts[0].id}/download`,
  );
  expect(dl.status()).toBe(200);
  const url = (await dl.json()).data.url;
  const file = await stageArtifactBytes(
    baseURL,
    url,
    Number(artifacts[0].size),
  );
  expect(file.length).toBe(Number(artifacts[0].size));
  expect(createHash("sha256").update(file).digest("hex")).toBe(
    artifacts[0].sha256,
  );
  expect(errors).toEqual([]);
  await context.close();
});
test("actual Agent procfs collection completes end to end", async () => {
  const r = await adminAPI.get("/api/v1/agents");
  const agents = (await r.json()).data.items;
  const a = agents.find(
    (v: { status: string; capabilities: string[] }) =>
      v.status === "online" && v.capabilities.includes("PROC_CPU"),
  );
  expect(a).toBeTruthy();
  const pid = a.resources.processes[0].pid;
  const created = await post(adminAPI, "/tasks", {
    name: "验收 · 真实线程 CPU 采集",
    kind: "PROC_CPU",
    target_agent_id: a.id,
    parameters: {
      pid,
      duration_seconds: 3,
      frequency_hz: 10,
      scope: "process",
    },
  });
  expect(created.status()).toBe(201);
  realTask = (await created.json()).data.id;
  await waitComplete(realTask);
  const result = (
    await (await adminAPI.get(`/api/v1/tasks/${realTask}/result`)).json()
  ).data;
  expect(result.summary.source).toBe("procfs");
  expect(result.summary.unit).toBe("cpu_ticks");
  expect(result.total_samples).toBeGreaterThan(0);
  expect(result.summary.limitations.length).toBeGreaterThan(0);
});
test("cancel then immediate retry preserves new attempt and finishes", async () => {
  const a = (await (await adminAPI.get("/api/v1/agents")).json()).data.items[0];
  const created = await post(adminAPI, "/tasks", {
    name: "验收 · 取消与重试隔离",
    kind: "PROC_CPU",
    target_agent_id: a.id,
    parameters: {
      pid: a.resources.processes[0].pid,
      duration_seconds: 3,
      frequency_hz: 10,
      scope: "process",
    },
  });
  expect(created.status()).toBe(201);
  const id = (await created.json()).data.id;
  const canceled = await post(adminAPI, `/tasks/${id}/cancel`, {});
  expect(canceled.ok()).toBe(true);
  const retried = await post(adminAPI, `/tasks/${id}/retry`, {});
  expect(retried.ok()).toBe(true);
  await waitComplete(id);
  const detail = (await (await adminAPI.get(`/api/v1/tasks/${id}`)).json())
    .data;
  expect(detail.attempts).toHaveLength(2);
});
test("workspace navigation, responsive widths, keyboard and accessibility", async ({
  browser,
}) => {
  const context = await browser.newContext({ storageState: state });
  const page = await context.newPage();
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "看见性能的每一面." }),
  ).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1060 });
  await page.screenshot({
    path: path.join(evidence, "overview-desktop.png"),
    fullPage: true,
  });
  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(async () => {
      await Promise.all(
        document
          .getAnimations()
          .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
          .map((a) => a.finished.catch(() => {})),
      );
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    if (width === 375) {
      await expect(page.locator(".sidebar")).toHaveAttribute("inert", "");
      await page.getByRole("button", { name: "打开菜单" }).click();
      await expect(
        page.getByRole("navigation", { name: "工作空间导航" }),
      ).toBeVisible();
      await page.keyboard.press("Shift+Tab");
      await expect(
        page.getByRole("button", { name: "退出登录" }),
      ).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(
        page.getByRole("button", { name: "打开菜单" }),
      ).toBeFocused();
      await expect(page.locator(".sidebar")).toHaveAttribute("inert", "");
      await page.evaluate(async () => {
        await Promise.all(
          document
            .getAnimations()
            .filter(
              (a) => a.effect?.getComputedTiming().iterations !== Infinity,
            )
            .map((a) => a.finished.catch(() => {})),
        );
      });
      await page.screenshot({
        path: path.join(evidence, "overview-mobile.png"),
        fullPage: true,
      });
    }
  }
  for (const route of [
    "/tasks",
    "/agents",
    "/schedules",
    "/settings",
    "/guide",
  ]) {
    await page.goto(route);
    await expect(page.locator("main h1")).toBeVisible();
    const error = await page.getByRole("alert").count();
    expect(error, route).toBe(0);
  }
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "看见性能的每一面." }),
  ).toBeVisible();
  await page.keyboard.press("n");
  await expect(page).toHaveURL(/tasks\/new/);
  await page.goto("/schedules");
  await page.getByRole("button", { name: "创建计划", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.goto("/");
  const axe = await new AxeBuilder({ page }).analyze();
  writeFileSync(
    path.join(evidence, "axe-overview.json"),
    JSON.stringify(axe.violations, null, 2),
  );
  expect(axe.violations).toEqual([]);
  await context.close();
});
test("CSRF and owner/role access are enforced across task and artifact routes", async () => {
  const existing = (
    await (
      await adminAPI.get("/api/v1/tasks?kind=IMPORT_COLLAPSED&page_size=1")
    ).json()
  ).data.items[0];
  expect(existing?.id).toBeTruthy();
  const importedTask = existing.id;
  const noCSRF = await adminAPI.post("/api/v1/tasks", { data: {} });
  expect(noCSRF.status()).toBe(403);
  const viewerName = `viewer-${Date.now()}`;
  const viewerPassword = crypto.randomUUID() + "Aa1!";
  const create = await post(adminAPI, "/users", {
    username: viewerName,
    password: viewerPassword,
    display_name: "验收观察者",
    role: "viewer",
  });
  expect(create.status()).toBe(201);
  const viewer = safeStageAPI(await playwrightRequest.newContext({ baseURL }));
  const login = await viewer.post("/api/v1/auth/login", {
    data: { username: viewerName, password: viewerPassword },
  });
  expect(login.status()).toBe(200);
  const viewerCSRF = (await login.json()).data.csrf_token;
  const list = await viewer.get("/api/v1/tasks");
  expect((await list.json()).data.total).toBe(0);
  for (const endpoint of [
    `/tasks/${importedTask}`,
    `/tasks/${importedTask}/result`,
    `/tasks/${importedTask}/events`,
    `/tasks/${importedTask}/artifacts/1/download`,
  ]) {
    const res = await viewer.get("/api/v1" + endpoint);
    expect([403, 404]).toContain(res.status());
  }
  expect(
    (
      await viewer.post("/api/v1/tasks", {
        data: {},
        headers: {
          "X-CSRF-Token": viewerCSRF,
          "Idempotency-Key": crypto.randomUUID(),
        },
      })
    ).status(),
  ).toBe(403);
  await viewer.dispose();
});
