/**
 * 浏览器唯一装配入口：挂载 Router、共享 Query 缓存和页面错误边界所需的上下文。
 * 请求的取消、短暂故障重试由下层统一处理，页面不各自维护一套全局请求状态。
 */
import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "@fontsource-variable/manrope";
import "@fontsource-variable/bricolage-grotesque";
import "./styles.css";
import "./styles/detail.css";
import "./styles/task-table.css";
import "./styles/tasks-compare.css";
import "./styles/full-capabilities.css";
import "./styles/flame-explorer.css";
import App from "./App";
import { retryQuery } from "./query-policy";
const Book = React.lazy(() => import("./book/Book"));
const LabWorkbench = React.lazy(() => import("./book/LabWorkbench"));
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: retryQuery,
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 10000),
      staleTime: 5000,
      refetchOnWindowFocus: true,
      refetchIntervalInBackground: false,
    },
  },
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route
            path="/book"
            element={<Navigate to="/book/chapter1/" replace />}
          />
          <Route
            path="/book/workbench"
            element={
              <React.Suspense
                fallback={<p role="status">正在打开实战工作台…</p>}
              >
                <LabWorkbench />
              </React.Suspense>
            }
          />
          <Route
            path="/book/:chapterSlug"
            element={
              <React.Suspense fallback={<p role="status">正在打开教程…</p>}>
                <Book />
              </React.Suspense>
            }
          />
          <Route
            path="/book/*"
            element={
              <React.Suspense fallback={<p role="status">正在打开教程…</p>}>
                <Book />
              </React.Suspense>
            }
          />
          <Route path="*" element={<App />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
