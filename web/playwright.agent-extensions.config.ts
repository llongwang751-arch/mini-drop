/** 本机扩展界面验收：预览只监听回环，所有业务请求由显式导入夹具拦截。 */
import { defineConfig } from "@playwright/test";
import base from "./playwright.knowledge.config";
export default defineConfig({
  ...base,
  testMatch: [
    "agent-extensions.spec.ts",
    "knowledge.spec.ts",
    "diagnosis-agent.spec.ts",
    "investigations.spec.ts",
  ],
  reporter: [
    ["list"],
    [
      "json",
      { outputFile: "../.local/agent-extensions-20261021/ui/results.json" },
    ],
  ],
  outputDir: "../.local/agent-extensions-20261021/ui/artifacts",
  use: { ...base.use, baseURL: "http://127.0.0.1:4195" },
  webServer: {
    command:
      "node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 4195 --strictPort",
    url: "http://127.0.0.1:4195",
    timeout: 30000,
    reuseExistingServer: false,
  },
});
