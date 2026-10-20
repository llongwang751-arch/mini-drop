/**
 * 浏览器测试的共享默认值；具体套件再选择本地夹具或显式授权的集成环境。
 * 不要把此配置存在默认地址理解成允许任意测试写入生产。
 */
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  timeout: 90000,
  expect: { timeout: 15000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.DROP_BASE_URL || "http://120.24.187.205",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  outputDir: "test-results",
});
