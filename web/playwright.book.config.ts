import { defineConfig } from "@playwright/test";

// 只运行本地公开教程，无会话、业务数据夹具或生产请求。
export default defineConfig({
  testDir: "./e2e",
  testMatch: "book.spec.ts",
  timeout: 30000,
  expect: { timeout: 10000 },
  workers: 1,
  retries: 0,
  reporter: "list",
  outputDir: "test-results-book",
  use: {
    baseURL: "http://127.0.0.1:4193",
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 4193 --strictPort",
    url: "http://127.0.0.1:4193/book/chapter1/",
    reuseExistingServer: false,
  },
});
