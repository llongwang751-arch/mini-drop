import { defineConfig } from "@playwright/test";
import base from "./playwright.preview.config";
export default defineConfig({
  ...base,
  testMatch: ["diagnosis-agent.spec.ts", "investigations.spec.ts"],
  reporter: [["line"]],
  use: { ...base.use, trace: "off", video: "off" },
  outputDir: "test-results-diagnosis",
});
