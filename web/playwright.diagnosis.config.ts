/**
 * 智能诊断界面的独立浏览器套件入口，便于只运行与诊断交互相关的夹具。
 */
import { defineConfig } from "@playwright/test";
import base from "./playwright.preview.config";
export default defineConfig({
  ...base,
  testMatch: ["diagnosis-agent.spec.ts", "investigations.spec.ts"],
  reporter: [["line"]],
  use: { ...base.use, trace: "off", video: "off" },
  outputDir: "test-results-diagnosis",
});
