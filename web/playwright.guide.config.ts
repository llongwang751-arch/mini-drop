import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// 导入的 UI 夹具运行在真实应用外壳中；Playwright 负责本地预览进程的启动与退出。
// 本套测试不使用生产凭据，也不访问生产 API。
export default defineConfig({
  ...base,
  testMatch: ["guide.spec.ts"],
  timeout: 45000,
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results-guide/results.json" }],
  ],
  outputDir: "test-results-guide/artifacts",
  use: {
    ...base.use,
    baseURL: "http://127.0.0.1:4191",
    actionTimeout: 10000,
    reducedMotion: "reduce",
  },
  webServer: {
    command:
      "node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 4191 --strictPort",
    url: "http://127.0.0.1:4191",
    timeout: 30000,
    reuseExistingServer: false,
  },
});
