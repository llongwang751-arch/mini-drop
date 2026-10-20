import { defineConfig } from "@playwright/test";

const baseURL = process.env.DROP_BASE_URL || "http://127.0.0.1:18080";
const target = new URL(baseURL);
if (target.hostname !== "127.0.0.1" || target.port !== "18080") {
  throw new Error("Full integration requires the isolated loopback stage.");
}
export default defineConfig({
  testDir: "./e2e",
  testMatch: ["full.spec.ts"],
  timeout: 120000,
  expect: { timeout: 15000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["line"]],
  use: {
    baseURL,
    viewport: { width: 1440, height: 1000 },
    trace: "off",
    screenshot: "only-on-failure",
    video: "off",
  },
  outputDir: "test-results-stage-20261019",
});
