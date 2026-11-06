import { createRequire } from "node:module";
const { chromium } = createRequire(path.resolve("web/package.json"))(
  "playwright",
);
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { pathToFileURL } from "node:url";

const base = path.resolve("docs/diagrams/drop-system");
const atlas = JSON.parse(
  await fs.readFile(path.join(base, "atlas-data.json"), "utf8"),
);
const dest = path.join(base, "read-png");
await fs.mkdir(dest, { recursive: true });
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
const report = {
  scope:
    "Native browser screenshots of each main SVG in default READ/Still/light, excluding viewer toolbar and supplementary conclusion cards. These are not canonical fine exports.",
  viewport: { width: 2048, height: 1320 },
  diagrams: [],
  errors: [],
  external_requests: [],
};
const browser = await chromium.launch({
  headless: true,
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const page = await browser.newPage({
  viewport: report.viewport,
  deviceScaleFactor: 1,
  colorScheme: "light",
});
page.on("pageerror", (error) => report.errors.push(error.message));
page.on("request", (req) => {
  if (/^https?:/.test(req.url())) report.external_requests.push(req.url());
});
try {
  for (const d of atlas.diagrams) {
    const html = await fs.readFile(path.join(base, d.file + ".html"));
    const proof = JSON.parse(
      await fs.readFile(path.join(base, d.file + ".visual-check.json"), "utf8"),
    );
    if (proof.artifact.sha256 !== sha(html) || proof.status !== "pass")
      throw new Error("Unverified " + d.file);
    await page.goto(pathToFileURL(path.join(base, d.file + ".html")).href);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(120);
    const svg = page.locator(
      'svg[aria-labelledby="archify-diagram-title archify-diagram-description"]',
    );
    const box = await svg.boundingBox();
    const state = await page.evaluate(() => ({
      detail:
        document.body.dataset.detail || document.documentElement.dataset.detail,
      theme:
        document.documentElement.dataset.theme || document.body.dataset.theme,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
      innerWidth,
      innerHeight,
    }));
    if (
      state.scrollWidth > state.innerWidth ||
      state.scrollHeight > state.innerHeight
    )
      throw new Error("Overflow " + d.file);
    const out = path.join(dest, d.file + ".png");
    await svg.screenshot({ path: out, animations: "disabled" });
    const png = await fs.readFile(out);
    report.diagrams.push({
      id: d.id,
      file: d.file,
      artifact_sha256: sha(html),
      artifact_bytes: html.length,
      png: {
        path: "read-png/" + d.file + ".png",
        sha256: sha(png),
        bytes: png.length,
        width: png.readUInt32BE(16),
        height: png.readUInt32BE(20),
      },
      state,
      bounding_box: box,
    });
  }
} finally {
  await browser.close();
}
report.passed =
  report.errors.length === 0 &&
  report.external_requests.length === 0 &&
  report.diagrams.length === atlas.diagrams.length;
await fs.writeFile(
  path.join(base, "read-png-receipt.json"),
  JSON.stringify(report, null, 2) + "\n",
);
console.log(
  JSON.stringify({
    passed: report.passed,
    diagrams: report.diagrams.length,
    errors: report.errors,
    external_requests: report.external_requests.length,
  }),
);
if (!report.passed) process.exitCode = 1;
