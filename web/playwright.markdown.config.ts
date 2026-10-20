import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Test-owned local preview, stopped by Playwright after this independent suite.
export default defineConfig({
  ...base,
  testMatch: ["safe-markdown.spec.ts"],
  use: { ...base.use, baseURL: "http://127.0.0.1:4186", actionTimeout: 15000 },
  webServer: {
    command:
      "node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 4186 --strictPort",
    url: "http://127.0.0.1:4186",
    timeout: 30000,
    reuseExistingServer: false,
  },
});
