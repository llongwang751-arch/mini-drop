import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// A test-owned foreground preview server: no detached/background process and
// Playwright stops it after the suite. All API data in this suite is imported.
export default defineConfig({
  ...base,
  testMatch: [
    "workspace-shell.spec.ts",
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
