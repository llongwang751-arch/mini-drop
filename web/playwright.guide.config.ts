import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Imported UI fixtures run in the real App shell. Playwright owns this local
// preview process, and no production credentials or API are used by this suite.
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
