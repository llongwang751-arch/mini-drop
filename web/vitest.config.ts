/**
 * 前端纯逻辑单测配置；浏览器交互、真实 Worker 和服务端链路分别由其他套件验证。
 */
import { defineConfig } from "vitest/config";
export default defineConfig({ test: { include: ["src/**/*.test.ts"] } });
