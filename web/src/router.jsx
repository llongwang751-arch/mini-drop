import { Suspense, lazy } from "react";
import { Spin } from "antd";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppLayout from "./components/AppLayout";
import Dashboard from "./pages/Dashboard";

const AuditLogs = lazy(() => import("./pages/AuditLogs"));
const TaskResult = lazy(() => import("./pages/TaskResult"));
const AIDiagnosis = lazy(() => import("./pages/AIDiagnosis"));
const AgentDetail = lazy(() => import("./pages/AgentDetail"));
const Settings = lazy(() => import("./pages/Settings"));
const Schedules = lazy(() => import("./pages/Schedules"));
const Composites = lazy(() => import("./pages/Composites"));

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
          <Route path="/tasks" element={<Dashboard />} />
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
            path="/settings"
            element={<Lazy><Settings /></Lazy>}
          />
          <Route
            path="/schedules"
            element={<Lazy><Schedules /></Lazy>}
          />
          <Route
            path="/composites"
            element={<Lazy><Composites /></Lazy>}
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
