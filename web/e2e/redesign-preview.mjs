import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
const base = process.env.DROP_PREVIEW_URL || "http://127.0.0.1:5180";
const root = path.resolve(import.meta.dirname, "../..");
const out = path.join(root, "docs/evidence/redesign");
fs.mkdirSync(out, { recursive: true });
const state = JSON.parse(
  fs.readFileSync(path.join(root, ".local/admin-state.json"), "utf8"),
);
const host = new URL(base).hostname;
state.cookies = state.cookies.map((c) => ({
  ...c,
  domain: host,
  secure: false,
}));
state.origins = [];
const browser = await chromium.launch();
const errors = [];
const context = await browser.newContext({
  storageState: state,
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.on("pageerror", (e) => errors.push(e.message));
const report = { base, checks: [], errors };
page.on("response", (r) => {
  if (r.status() >= 400)
    errors.push(`${r.status()} ${new URL(r.url()).pathname}`);
});
async function capture(route, name, width = 1440) {
  await page.setViewportSize({ width, height: 1000 });
  const auth = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/auth/me",
  );
  await page.goto(base + route, { waitUntil: "domcontentloaded" });
  const authStatus = (await auth).status();
  if (authStatus !== 200)
    throw new Error(
      `Preview authentication returned ${authStatus} at ${route}/${width}`,
    );
  await page.locator("#main-content h1").waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(600);
  await page.evaluate(async () =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => {})),
    ),
  );
  const axe = await new AxeBuilder({ page }).analyze();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth,
  );
  await page.screenshot({
    path: path.join(out, name + ".png"),
    fullPage: true,
  });
  report.checks.push({ route, width, overflow, violations: axe.violations });
  fs.writeFileSync(
    path.join(out, "preview-report.json"),
    JSON.stringify(report, null, 2),
  );
}
for (const [route, name] of [
  ["/", "overview"],
  ["/tasks", "tasks"],
  ["/tasks/19", "result"],
  ["/tasks/new", "create"],
  ["/agents", "agents"],
  ["/schedules", "schedules"],
  ["/settings", "members"],
  ["/guide", "guide"],
])
  await capture(route, name);
for (const [route, name] of [
  ["/", "overview-mobile"],
  ["/tasks", "tasks-mobile"],
  ["/tasks/19", "result-mobile"],
  ["/tasks/new", "create-mobile"],
])
  await capture(route, name, 375);
const loginContext = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const login = await loginContext.newPage();
await login.goto(base);
await login.getByRole("heading", { name: "登录工作空间" }).waitFor();
await login.evaluate(() => document.fonts.ready);
await login.screenshot({ path: path.join(out, "login.png"), fullPage: true });
report.checks.push({
  route: "login",
  width: 1440,
  violations: (await new AxeBuilder({ page: login }).analyze()).violations,
});
await login.setViewportSize({ width: 375, height: 812 });
await login.screenshot({
  path: path.join(out, "login-mobile.png"),
  fullPage: true,
});
fs.writeFileSync(
  path.join(out, "preview-report.json"),
  JSON.stringify(report, null, 2),
);
console.log(
  JSON.stringify(
    {
      checks: report.checks.map((c) => ({
        route: c.route,
        width: c.width,
        overflow: c.overflow,
        violations: c.violations.map((v) => ({
          id: v.id,
          impact: v.impact,
          nodes: v.nodes.map((n) => ({
            target: n.target,
            summary: n.failureSummary,
          })),
        })),
      })),
      errors,
    },
    null,
    2,
  ),
);
await browser.close();
