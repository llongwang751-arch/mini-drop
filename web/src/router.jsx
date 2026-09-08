import { Suspense, lazy } from "react";
import { Spin } from "antd";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppLayout from "./components/AppLayout";

/**
 * 浏览器路由只负责“URL -> 页面组件”的映射，不在这里取业务数据。
 *
 * 阅读顺序：先看 AppLayout 了解全局导航和鉴权，再从下面的 path 找到具体页面。
 * 页面使用 lazy 按需加载，因此首次打开 AI 诊断时不会同时下载任务详情等代码。
 */

const Dashboard = lazy(() => import("./pages/Dashboard"));
const AuditLogs = lazy(() => import("./pages/AuditLogs"));
const TaskResult = lazy(() => import("./pages/TaskResult"));
const AIDiagnosis = lazy(() => import("./pages/AIDiagnosis"));
const AgentDetail = lazy(() => import("./pages/AgentDetail"));
const Schedules = lazy(() => import("./pages/Schedules"));

const Lazy = ({ children }) => (
  <Suspense fallback={<Spin size="large" style={{ display: "block", margin: "40px auto" }} />}>
    {children}
  </Suspense>
);

export default function Router() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          {/* AI diagnosis is the default entry; collection tasks live at /tasks. */}
          <Route path="/" element={<Navigate to="/ai-diagnosis" replace />} />
          <Route path="/tasks" element={<Lazy><Dashboard /></Lazy>} />
          <Route
            path="/audit"
            element={<Lazy><AuditLogs /></Lazy>}
          />
          <Route
            path="/task/:taskId"
            element={<Lazy><TaskResult /></Lazy>}
          />
          <Route
            path="/ai-diagnosis"
            element={<Lazy><AIDiagnosis /></Lazy>}
          />
          <Route
            path="/agent/:agentId"
            element={<Lazy><AgentDetail /></Lazy>}
          />
          <Route
            path="/schedules"
            element={<Lazy><Schedules /></Lazy>}
          />
          {/* Redirect legacy diagnosis routes to the unified AI diagnosis page. */}
          <Route path="/drop-insight" element={<Navigate to="/ai-diagnosis" replace />} />
          <Route path="/diagnoses" element={<Navigate to="/ai-diagnosis" replace />} />
          <Route path="*" element={<Navigate to="/ai-diagnosis" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
