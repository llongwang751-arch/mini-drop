/**
 * 历史生产验收套件的独立配置；这里只保留测试入口，不会在日常单测中自动执行。
 * 真实环境运行须遵循该套件的显式开关和已确认部署目标。
 */
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  testMatch: ["release-production.spec.ts"],
  timeout: 180000,
  expect: { timeout: 15000 },
  workers: 1,
  retries: 0,
  reporter: [["line"]],
  use: {
    baseURL: "https://120.24.187.205",
    viewport: { width: 1440, height: 1000 },
    trace: "off",
    screenshot: "only-on-failure",
    video: "off",
  },
  outputDir: "test-results-production-20261019",
});
