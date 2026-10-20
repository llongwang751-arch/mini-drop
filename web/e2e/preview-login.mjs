import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.goto(process.env.PREVIEW_URL || "http://127.0.0.1:5173");
await page.getByRole("heading", { name: "登录工作空间" }).waitFor();
await page.evaluate(() => document.fonts.ready);
await mkdir("../docs/evidence", { recursive: true });
await page.screenshot({
  path: "../docs/evidence/login-preview.png",
  fullPage: true,
});
await browser.close();
