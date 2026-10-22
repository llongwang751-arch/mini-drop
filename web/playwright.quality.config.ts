/** 本地UI合成夹具；完整真实平台验收使用独立system门禁，不能互换通过标签。 */
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";
export default defineConfig({
  ...base,
  testMatch: ["quality.spec.ts"],
  timeout: 45000,
  reporter: [
    ["list"],
    ["json", { outputFile: "../.local/jd-depth-20261023/ui/results.json" }],
  ],
  outputDir: "../.local/jd-depth-20261023/ui/artifacts",
  use: {
    ...base.use,
    baseURL: "http://127.0.0.1:4196",
    reducedMotion: "reduce",
    actionTimeout: 10000,
  },
  webServer: {
    command:
      "node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 4196 --strictPort",
    url: "http://127.0.0.1:4196",
    timeout: 30000,
    reuseExistingServer: false,
  },
});
