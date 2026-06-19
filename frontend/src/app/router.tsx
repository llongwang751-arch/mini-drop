import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "./AppShell";
import { AgentsPage } from "../pages/AgentsPage";
import { AuditPage } from "../pages/AuditPage";
import { OverviewPage } from "../pages/OverviewPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "agents", element: <AgentsPage /> },
      { path: "audit", element: <AuditPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
