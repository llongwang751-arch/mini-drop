/** 本地导入夹具的知识 UI 回归，固定回环地址，不使用生产登录态或模型。 */
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";
export default defineConfig({
  ...base,
  testMatch: ["knowledge.spec.ts"],
  timeout: 45000,
  reporter: [
    ["list"],
    ["json", { outputFile: "../.local/knowledge-20261021/ui/results.json" }],
  ],
  outputDir: "../.local/knowledge-20261021/ui/artifacts",
  use: {
    ...base.use,
    baseURL: "http://127.0.0.1:4193",
    reducedMotion: "reduce",
    actionTimeout: 10000,
  },
  webServer: {
    command:
      "node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 4193 --strictPort",
    url: "http://127.0.0.1:4193",
    timeout: 30000,
    reuseExistingServer: false,
  },
});
