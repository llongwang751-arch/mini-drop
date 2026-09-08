/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_PROXY_TARGET || "http://localhost:8080";

  return {
  plugins: [react()],
  root: ".",
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setupTests.js",
    include: ["src/**/*.{test,spec}.{js,jsx}"],
    css: false,
  },
  build: {
    outDir: "dist",
    // Keep the budget low enough to catch accidental eager imports. Ant Design
    // is intentionally left to the bundler so lazy routes retain their own UI code.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
        manualChunks(id) {
          const modulePath = id.replaceAll("\\", "/");
          if (!modulePath.includes("/node_modules/")) return undefined;
          if (/\/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//.test(modulePath)) return "react";
          if (modulePath.includes("/node_modules/d3-flame-graph/")) return "flamegraph";
          if (/\/node_modules\/(d3|d3-[^/]+|internmap)\//.test(modulePath)) return "d3";
          if (modulePath.includes("/node_modules/axios/")) return "axios";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // The browser only talks to the Go API. Python remains an internal
        // reasoning/analyzer upstream behind that gateway.
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  };
});
