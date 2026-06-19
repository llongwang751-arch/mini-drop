import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { Plus, WarningCircle } from "@phosphor-icons/react";
import { TaskDialog } from "../features/tasks/TaskDialog";
import { useDashboardStore } from "../store/dashboardStore";

const links = [
  { to: "/", label: "任务总览", end: true },
  { to: "/agents", label: "采集节点" },
  { to: "/audit", label: "审计日志" },
];

export function AppShell() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const { agents, error, refresh } = useDashboardStore();

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <b>MD</b>
          <span>
            <strong>Mini-Drop</strong>
            <small>性能诊断控制台</small>
          </span>
        </div>
        <nav aria-label="主导航">
          {links.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="top-actions">
          <span className="online">
            <i />
            控制平面在线
          </span>
          <button className="primary" onClick={() => setDialogOpen(true)}>
            <Plus size={16} weight="bold" />
            新建任务
          </button>
        </div>
      </header>
      <main id="main">
        {error && (
          <div className="banner">
            <WarningCircle size={18} />
            数据刷新失败：{error}
          </div>
        )}
        <Outlet />
      </main>
      {dialogOpen && <TaskDialog agents={agents} onClose={() => setDialogOpen(false)} />}
    </>
  );
}
