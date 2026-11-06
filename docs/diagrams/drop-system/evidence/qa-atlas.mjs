import { createRequire } from "node:module";
const { chromium } = createRequire(path.resolve("web/package.json"))(
  "playwright",
);
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import crypto from "node:crypto";

const base = path.resolve("docs/diagrams/drop-system");
const atlas = JSON.parse(
  await fs.readFile(path.join(base, "atlas-data.json"), "utf8"),
);
const report = {
  scope: "Offline atlas navigation and layout; no backend or model execution",
  checks: [],
  errors: [],
  network: [],
  screenshots: [],
};
const portalBytes = await fs.readFile(path.join(base, "index.html"));
report.artifact = {
  sha256: crypto.createHash("sha256").update(portalBytes).digest("hex"),
  bytes: portalBytes.length,
};
const browser = await chromium.launch({
  headless: true,
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
});
page.on("pageerror", (error) => report.errors.push(error.message));
page.on("request", (req) => {
  if (/^https?:/.test(req.url())) report.network.push(req.url());
});
const check = (name, ok, detail) => {
  report.checks.push({ name, ok, detail });
  if (!ok) throw new Error(name + ": " + JSON.stringify(detail));
};
try {
  await page.goto(pathToFileURL(path.join(base, "index.html")).href);
  await page.waitForSelector("#figure-title");
  check(
    "initial atlas render",
    (await page.locator("#figure-title").innerText()).includes("系统总览"),
  );
  for (const d of atlas.diagrams) {
    await page.locator(`.side-link[href="#diagram/${d.id}"]`).click();
    await page.waitForFunction(
      (id) =>
        document.getElementById("figure-title").textContent.startsWith(id),
      d.id,
    );
    await page.waitForTimeout(180);
    const frame = page.frames().find((f) => f.url().endsWith(d.file + ".html"));
    check("diagram " + d.id + " viewer loads", !!frame, { title: d.title });
    if (frame) {
      await frame.waitForSelector("svg");
      check(
        "diagram " + d.id + " SVG",
        (await frame.locator("svg").count()) > 0,
      );
    }
  }
  for (const id of [
    "gallery",
    "modules",
    "contracts",
    "collectors",
    "walkthrough",
    "data",
    "about",
  ]) {
    await page.locator(`.side-link[href="#${id}"]`).click();
    check(
      id + " panel visible",
      await page.locator("#panel-" + id).isVisible(),
    );
  }
  check(
    "all gallery previews",
    (await page.locator("#gallery img").count()) === atlas.diagrams.length,
  );
  await page.locator('.side-link[href="#collectors"]').click();
  check(
    "16 collector details",
    (await page.locator("#collectors>details").count()) === 16,
  );
  await page.locator("#collector-search").fill("PYTHON_CPU");
  check(
    "collector search",
    (await page.locator("#collectors>details:visible").count()) === 1,
  );
  await page.locator("#collectors>details:visible summary").click();
  check(
    "collector opens",
    await page.locator("#collectors>details[open]:visible .raw").isVisible(),
  );
  await page.locator("#collector-search").fill("nothing-matches-123");
  check(
    "no-result explanation",
    await page.locator("#collector-empty").isVisible(),
  );
  await page.locator("#collector-search").fill("");
  check(
    "search resets 16",
    (await page.locator("#collectors>details:visible").count()) === 16,
  );
  await page.locator("#collectors>details[open] summary").click();
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({
    path: path.join(base, "atlas-collectors.light.png"),
    fullPage: false,
  });
  report.screenshots.push("atlas-collectors.light.png");
  await page.locator('.side-link[href="#diagram/01"]').click();
  await page.waitForTimeout(350);
  await page.screenshot({
    path: path.join(base, "atlas-overview.light.png"),
    fullPage: false,
  });
  report.screenshots.push("atlas-overview.light.png");
  await page.locator("#theme").click();
  check(
    "dark theme",
    (await page.locator("html").getAttribute("data-theme")) === "dark",
  );
  await page.screenshot({
    path: path.join(base, "atlas-overview.dark.png"),
    fullPage: false,
  });
  report.screenshots.push("atlas-overview.dark.png");
  await page.reload();
  check(
    "theme remembered",
    (await page.locator("html").getAttribute("data-theme")) === "dark",
  );
  await page.locator("#theme").click();
  await page.setViewportSize({ width: 375, height: 812 });
  await page.locator("#menu").click();
  check(
    "mobile menu opens",
    (await page.locator("#menu").getAttribute("aria-expanded")) === "true",
  );
  await page.locator('.side-link[href="#data"]').click();
  check(
    "mobile panel link works",
    await page.locator("#panel-data").isVisible(),
  );
  check(
    "mobile menu closes",
    (await page.locator("#menu").getAttribute("aria-expanded")) === "false",
  );
  for (const w of [320, 375, 768, 1440, 1920]) {
    await page.setViewportSize({ width: w, height: w < 600 ? 812 : 1000 });
    for (const id of [
      "gallery",
      "modules",
      "contracts",
      "collectors",
      "walkthrough",
      "data",
      "about",
      "diagram/01",
    ]) {
      await page.evaluate((h) => (location.hash = h), id);
      await page.waitForTimeout(80);
      const size = await page.evaluate(() => ({
        innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));
      check(
        "no horizontal page overflow " + w + " " + id,
        size.scrollWidth <= size.innerWidth,
        size,
      );
    }
  }
  await page.setViewportSize({ width: 375, height: 812 });
  await page.evaluate(() => (location.hash = "collectors"));
  await page.waitForTimeout(120);
  await page.screenshot({
    path: path.join(base, "atlas-mobile.light.png"),
    fullPage: false,
  });
  report.screenshots.push("atlas-mobile.light.png");
  check("no page errors", report.errors.length === 0, report.errors);
  check("no external requests", report.network.length === 0, report.network);
} catch (error) {
  report.failure = error.stack;
} finally {
  await browser.close();
}
report.passed = !report.failure && report.checks.every((c) => c.ok);
await fs.writeFile(
  path.join(base, "portal-browser-check.json"),
  JSON.stringify(report, null, 2) + "\n",
);
console.log(
  JSON.stringify(
    {
      passed: report.passed,
      checks: report.checks.length,
      failure: report.failure,
      errors: report.errors,
      external_requests: report.network.length,
    },
    null,
    2,
  ),
);
if (!report.passed) process.exitCode = 1;
