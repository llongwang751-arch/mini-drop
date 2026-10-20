import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// 使用测试自行管理的前台预览服务，避免遗留脱离控制的后台进程。
// Playwright 在套件结束时关闭服务；这里的 API 数据全部来自导入夹具。
export default defineConfig({
  ...base,
  testMatch: [
    "workspace-shell.spec.ts",
    "guide.spec.ts",
    "flame-explorer.spec.ts",
    "flame-scale.spec.ts",
    "request-lifecycle.spec.ts",
    "event-stream.spec.ts",
    "safe-markdown.spec.ts",
    "profile-artifact.spec.ts",
    "task-failure.spec.ts",
    "partial-analysis.spec.ts",
  ],
  use: { ...base.use, baseURL: "http://127.0.0.1:4178", actionTimeout: 15000 },
  webServer: {
    command:
      "node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 4178 --strictPort",
    url: "http://127.0.0.1:4178",
    timeout: 30000,
    reuseExistingServer: false,
  },
});
