/**
 * 应用外壳：先恢复服务端会话，再提供用户上下文、导航和业务路由。
 * 页面只决定哪些入口可见，真正的资源授权仍由 Go API 检查；这里不接触模型或对象存储长期凭据。
 */
import { Component, lazy, Suspense, useEffect, useId, useState } from "react";
import type { ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Routes,
  Route,
  Navigate,
  NavLink,
  Link,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  SquaresFour,
  Stack,
  HardDrives,
  CalendarDots,
  GearSix,
  BookOpen,
  ArrowUpRight,
  MagnifyingGlass,
  SignOut,
  List as ListIcon,
  X,
  Drop,
  ArrowRight,
  ShieldCheck,
  SidebarSimple,
} from "@phosphor-icons/react";
import {
  readCompactNavigation,
  saveCompactNavigation,
} from "./navigation-preference";
import { api, setAuth } from "./api";
import { UserContext } from "./auth-context";
import type { Auth, TaskKind } from "./types";
import { ErrorPanel, Busy } from "./components/ui";
import Overview from "./views/Overview";
import Tasks from "./views/Tasks";
import Compare from "./views/Compare";
import NewTask from "./views/NewTask";
import TaskDetail from "./views/TaskDetail";
import Agents, { AgentDetail } from "./views/Agents";
import Groups from "./views/Groups";
import Rules from "./views/Rules";
import Composites, { CompositeDetail } from "./views/Composites";
import Schedules from "./views/Schedules";
import Settings from "./views/Settings";
// 调查与指南按路由分包，首次进入工作台时无需下载全部教学和修复视图。
const Guide = lazy(() => import("./views/Guide"));
const Investigations = lazy(() => import("./views/Investigations"));

function DropSection() {
  const clipId = useId();
  return (
    <svg
      className="login-emblem"
      viewBox="0 0 560 400"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <clipPath id={clipId}>
          <path d="M280 26C246 77 159 142 159 231a121 121 0 0 0 242 0c0-89-87-154-121-205Z" />
        </clipPath>
      </defs>
      <path
        className="emblem-guide"
        d="M56 230h448M280 8v378M108 88h344M108 352h344"
        fill="none"
      />
      <g clipPath={`url(#${clipId})`}>
        {Array.from({ length: 17 }, (_, i) => (
          <rect
            key={i}
            className={`emblem-band band-${i % 3}`}
            x={i % 2 === 0 ? 148 : 159}
            y={38 + i * 19}
            width={i % 2 === 0 ? 264 : 242}
            height={i % 3 === 0 ? 10 : 7}
            rx="1"
          />
        ))}
        <path className="emblem-cut" d="M259 18h15v348h-15zM288 18h7v348h-7z" />
      </g>
      <path
        className="emblem-outline"
        d="M280 26C246 77 159 142 159 231a121 121 0 0 0 242 0c0-89-87-154-121-205Z"
        fill="none"
      />
      <g className="emblem-markers" fill="none">
        <path d="M99 222v16m-8-8h16M453 222v16m-8-8h16" />
        <path d="M272 376h16m-8-8v16" />
      </g>
    </svg>
  );
}

// 渲染异常只上报固定错误码，避免把用户数据、页面状态或完整异常栈带进错误收集接口。
class Boundary extends Component<{ children: ReactNode }, { error?: Error }> {
  state: { error?: Error } = {};
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch() {
    void api("/client-errors", {
      method: "POST",
      body: JSON.stringify({ code: "WEB_RENDER_FAILED" }),
    }).catch(() => {});
  }
  render() {
    return this.state.error ? (
      <div className="boundary">
        <ErrorPanel error={this.state.error} />
        <button
          className="button secondary"
          onClick={() => window.location.assign("/")}
        >
          返回工作台
        </button>
      </div>
    ) : (
      this.props.children
    );
  }
}
// 登录表单是页面 H1；装饰性介绍使用 H2，保证移动端隐藏介绍后仍有清晰的主标题。
// 用户名与密码仅用于当前提交，成功后由会话接口统一恢复用户身份。
function Login({ onLogin }: { onLogin: (auth: Auth) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>();
  return (
    <div className="login-page">
      <header className="login-brand">
        <Link className="brand" to="/">
          <span className="brand-mark">
            <Drop size={28} weight="fill" />
          </span>
          <span>
            drop<span className="brand-period">.</span>
          </span>
        </Link>
        <span className="login-brand-note">性能诊断工作空间</span>
      </header>
      <main className="login-layout">
        <div className="login-story">
          <span className="login-kicker">从运行现场，到性能证据</span>
          <h2>
            性能，
            <br />
            <span>清晰可见。</span>
          </h2>
          <p>
            采集现场，分析调用栈。
            <br />
            让每一次性能判断，都有证据。
          </p>
          <div className="login-visual">
            <DropSection />
            <span className="login-emblem-caption">
              CAPTURE / ANALYZE / UNDERSTAND
            </span>
          </div>
          <div className="login-trust">
            <ShieldCheck size={18} />
            <span>受控采集 · 可追溯证据</span>
          </div>
        </div>
        <div className="login-card">
          <div className="login-form-intro">
            <span className="eyebrow">DROP WORKSPACE</span>
            <h1>登录工作空间</h1>
            <p>使用管理员分配的账号继续。</p>
          </div>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setLoading(true);
              setError(undefined);
              try {
                const a = await api<Auth>("/auth/login", {
                  method: "POST",
                  body: JSON.stringify({ username, password }),
                });
                setAuth(a);
                onLogin(a);
              } catch (e) {
                setError(e);
              } finally {
                setLoading(false);
              }
            }}
          >
            <label className="field">
              <span>用户名</span>
              <input
                autoFocus
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="你的用户名"
              />
            </label>
            <label className="field">
              <span>密码</span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="输入登录密码"
              />
            </label>
            {error != null && <ErrorPanel error={error} />}
            <button className="button primary login-submit" disabled={loading}>
              {loading ? (
                <Busy>登录中</Busy>
              ) : (
                <>
                  进入工作台
                  <ArrowRight size={17} />
                </>
              )}
            </button>
          </form>
          <div className="login-footnote">
            <ShieldCheck size={14} />
            仅限已授权的工作空间成员
          </div>
        </div>
      </main>
      <footer className="login-footer">
        <span>Drop · 自托管性能诊断</span>
        <span className="mono">v1.0.0</span>
      </footer>
    </div>
  );
}
export default function App() {
  const [navigationCompact, setNavigationCompact] = useState(
    readCompactNavigation,
  );
  const deploymentLabel =
    import.meta.env.VITE_DROP_ENVIRONMENT ||
    (import.meta.env.DEV ? "本地开发" : "自托管部署");
  const qc = useQueryClient();
  const [auth, setAuthState] = useState<Auth>();
  const [ready, setReady] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [notice, setNotice] = useState<unknown>();
  const [compact, setCompact] = useState(
    () => window.matchMedia("(max-width:800px)").matches,
  );
  const location = useLocation();
  const navigate = useNavigate();
  const [globalSearch, setGlobalSearch] = useState("");
  // 先向服务器确认会话，不用本地标志推断是否登录；认证失败不做循环重试。
  const session = useQuery({
    queryKey: ["session"],
    queryFn: ({ signal }) => api<Auth>("/auth/me", { signal }),
    retry: false,
    staleTime: 60000,
  });
  useQuery({
    queryKey: ["kinds"],
    queryFn: ({ signal }) => api<TaskKind[]>("/task-kinds", { signal }),
    enabled: !!auth,
    staleTime: 60000,
  });
  useEffect(() => {
    const media = window.matchMedia("(max-width:800px)");
    const update = () => {
      setCompact(media.matches);
      if (!media.matches) setDrawer(false);
    };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    // 手机抽屉打开时约束 Tab 焦点，关闭时还给菜单按钮，防止键盘用户落到遮罩背后。
    if (!drawer || !compact) return;
    const items = () =>
      Array.from(
        document.querySelectorAll<HTMLElement>(".sidebar a,.sidebar button"),
      );
    items()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const all = items();
      const first = all[0],
        last = all.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      document.removeEventListener("keydown", trap);
      document.querySelector<HTMLElement>(".mobile-menu")?.focus();
    };
  }, [drawer, compact]);
  useEffect(() => {
    // CSRF 交给 API 客户端，用户信息交给 React 上下文，两者都以同一次会话响应为准。
    if (session.data) {
      setAuth(session.data);
      setAuthState(session.data);
      setReady(true);
    } else if (session.error) setReady(true);
  }, [session.data, session.error]);
  useEffect(() => {
    // 资源请求返回 401 时清空会话和缓存，避免下一位登录用户短暂看到上一个会话的数据。
    const listener = () => {
      setAuthState(undefined);
      qc.clear();
    };
    window.addEventListener("drop:unauthorized", listener);
    return () => window.removeEventListener("drop:unauthorized", listener);
  }, [qc]);
  useEffect(() => {
    setDrawer(false);
    setNotice(undefined);
    window.scrollTo(0, 0);
  }, [location.pathname]);
  useEffect(() => {
    // 输入框中不接管字符键；Ctrl/Meta 等浏览器快捷键只处理明确约定的组合。
    function shortcut(e: KeyboardEvent) {
      if (
        (e.target as HTMLElement)?.matches(
          "input,textarea,select,[contenteditable]",
        )
      )
        return;
      if (
        (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) ||
        ((e.ctrlKey || e.metaKey) && e.key === "k" && !e.altKey)
      ) {
        e.preventDefault();
        document.querySelector<HTMLInputElement>("#global-search")?.focus();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key.toLowerCase() === "n" && auth && auth.user.role !== "viewer") {
        navigate("/tasks/new");
      }
      if (e.key === "Escape") setDrawer(false);
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [navigate, auth?.user.role]);
  // 会话尚未确定前只显示载入态，避免受保护页面先闪现、随后再跳登录。
  if (!ready)
    return (
      <div className="app-loading">
        <span className="brand-mark">
          <Drop size={30} weight="fill" />
        </span>
        <span>正在连接工作空间</span>
      </div>
    );
  if (!auth)
    return (
      <Login
        onLogin={(a) => {
          setAuthState(a);
          qc.invalidateQueries();
        }}
      />
    );
  const pageName = location.pathname.startsWith("/investigations")
    ? "智能性能调查"
    : location.pathname.startsWith("/compare")
      ? "任务对比"
      : location.pathname.startsWith("/groups")
        ? "权限组"
        : location.pathname.startsWith("/rules")
          ? "诊断规则"
          : location.pathname.startsWith("/agents")
            ? "接入节点"
            : location.pathname.startsWith("/composites")
              ? "复合诊断"
              : location.pathname.startsWith("/tasks")
                ? "诊断任务"
                : location.pathname.startsWith("/schedules")
                  ? "计划任务"
                  : location.pathname.startsWith("/settings")
                    ? "工作空间设置"
                    : location.pathname.startsWith("/guide")
                      ? "使用指南"
                      : "工作空间总览";
  const navigation = [
    { to: "/", label: "工作空间总览", icon: SquaresFour },
    { to: "/tasks", label: "诊断任务", icon: Stack },
    { to: "/investigations", label: "智能性能调查", icon: MagnifyingGlass },
    { to: "/composites", label: "复合诊断", icon: Stack },
    { to: "/agents", label: "接入节点", icon: HardDrives },
    { to: "/schedules", label: "计划任务", icon: CalendarDots },
    { to: "/groups", label: "权限组", icon: ShieldCheck },
    { to: "/rules", label: "诊断规则", icon: GearSix },
  ];
  return (
    <UserContext.Provider value={auth.user}>
      <Boundary>
        <div className={`app-shell${navigationCompact ? " nav-compact" : ""}`}>
          {drawer && (
            <div className="drawer-scrim" onClick={() => setDrawer(false)} />
          )}
          <a className="skip-link" href="#main-content">
            跳转到主要内容
          </a>
          <aside
            id="workspace-sidebar"
            aria-label="工作空间侧栏"
            className={`sidebar ${drawer ? "open" : ""}`}
            inert={compact && !drawer}
            aria-hidden={compact && !drawer}
          >
            <Link
              className="brand"
              to="/"
              aria-label="drop. 性能诊断 工作空间总览"
            >
              <span className="brand-mark">
                <Drop size={27} weight="fill" />
              </span>
              <span className="sidebar-brand-copy">
                <strong>
                  drop<span className="brand-period">.</span>
                </strong>
                <small>性能诊断</small>
              </span>
            </Link>
            <div className="workspace-switch" aria-label="当前工作空间">
              <span className="workspace-symbol">D</span>
              <div className="workspace-copy">
                <strong>Drop 工作空间</strong>
                <small>{deploymentLabel}</small>
              </div>
              <span className="workspace-indicator" />
            </div>
            <div className="nav-group-label sidebar-section-label">
              工作空间
            </div>
            <nav className="main-nav" aria-label="工作空间导航">
              {navigation.map(({ to, label, icon: Icon }) => (
                <NavLink
                  to={to}
                  end={to === "/"}
                  key={to}
                  aria-label={label}
                  title={label}
                >
                  <Icon size={19} />
                  <span>{label}</span>
                </NavLink>
              ))}
            </nav>
            <div className="nav-group-label sidebar-section-label second">
              管理与资源
            </div>
            <nav className="main-nav" aria-label="管理与资源导航">
              <NavLink
                to="/settings"
                aria-label="工作空间设置"
                title="工作空间设置"
              >
                <GearSix size={19} />
                <span>工作空间设置</span>
              </NavLink>
              <NavLink to="/guide" aria-label="使用指南" title="使用指南">
                <BookOpen size={19} />
                <span>使用指南</span>
                <ArrowUpRight size={14} />
              </NavLink>
            </nav>
            <div className="sidebar-bottom">
              <div className="sidebar-profile">
                <span className="avatar">
                  {auth.user.display_name.slice(0, 1).toUpperCase()}
                </span>
                <div>
                  <strong>{auth.user.display_name}</strong>
                  <small>
                    {["admin", "platform_admin"].includes(auth.user.role)
                      ? "工作空间管理员"
                      : auth.user.role === "group_admin"
                        ? "资源组管理员"
                        : auth.user.role === "operator"
                          ? "诊断操作员"
                          : "只读观察者"}
                  </small>
                </div>
                <button
                  className="icon-button"
                  aria-label="退出登录"
                  onClick={async () => {
                    try {
                      await api("/auth/logout", { method: "POST", body: "{}" });
                      setAuthState(undefined);
                      qc.clear();
                    } catch (error) {
                      setNotice(error);
                    }
                  }}
                >
                  <SignOut size={18} />
                </button>
              </div>
            </div>
          </aside>
          <div className="main-shell">
            <header className="topbar">
              <button
                className="icon-button desktop-nav-toggle"
                aria-label={navigationCompact ? "展开侧栏" : "收起侧栏"}
                aria-expanded={!navigationCompact}
                aria-controls="workspace-sidebar"
                onClick={() => {
                  const next = !navigationCompact;
                  setNavigationCompact(next);
                  saveCompactNavigation(next);
                }}
              >
                <SidebarSimple size={21} />
              </button>
              <nav className="breadcrumb" aria-label="当前页面">
                <button
                  className="icon-button mobile-menu"
                  aria-expanded={drawer}
                  aria-controls="workspace-sidebar"
                  aria-label={drawer ? "关闭菜单" : "打开菜单"}
                  onClick={() => setDrawer((v) => !v)}
                >
                  {drawer ? <X size={20} /> : <ListIcon size={20} />}
                </button>
                <span className="dim topbar-workspace-name">工作空间</span>
                <span className="breadcrumb-separator">/</span>
                <strong>{pageName}</strong>
              </nav>
              <div className="topbar-right">
                <form
                  className="global-search"
                  role="search"
                  aria-label="搜索诊断任务"
                  onSubmit={(e) => {
                    e.preventDefault();
                    navigate(
                      `/tasks?search=${encodeURIComponent(globalSearch)}`,
                    );
                    setGlobalSearch("");
                  }}
                >
                  <MagnifyingGlass size={16} aria-hidden="true" />
                  <input
                    id="global-search"
                    aria-label="全局搜索诊断任务"
                    placeholder="搜索诊断…"
                    value={globalSearch}
                    onChange={(e) => setGlobalSearch(e.target.value)}
                  />
                  <kbd aria-hidden="true">/</kbd>
                </form>
                <span className="environment topbar-context">
                  <i />
                  {deploymentLabel}
                </span>
              </div>
            </header>
            <main className="main-content" id="main-content" tabIndex={-1}>
              {notice != null && <ErrorPanel error={notice} />}
              <Routes>
                <Route path="/" element={<Overview />} />
                <Route path="/compare" element={<Compare />} />
                <Route
                  path="/investigations"
                  element={
                    <Suspense fallback={<Busy />}>
                      <Investigations />
                    </Suspense>
                  }
                />
                <Route
                  path="/tasks"
                  element={<Tasks key={location.search} />}
                />
                <Route
                  path="/tasks/new"
                  element={
                    auth.user.role === "viewer" ? (
                      <Navigate to="/tasks" replace />
                    ) : (
                      <NewTask />
                    )
                  }
                />
                <Route
                  path="/tasks/:id"
                  element={
                    <TaskDetail readOnly={auth.user.role === "viewer"} />
                  }
                />
                <Route path="/agents" element={<Agents />} />
                <Route path="/agents/:id" element={<AgentDetail />} />
                <Route path="/schedules" element={<Schedules />} />
                <Route path="/groups" element={<Groups />} />
                <Route path="/rules" element={<Rules />} />
                <Route path="/composites" element={<Composites />} />
                <Route path="/composites/:id" element={<CompositeDetail />} />
                <Route
                  path="/settings"
                  element={<Settings user={auth.user} />}
                />
                <Route
                  path="/guide"
                  element={
                    <Suspense fallback={<Busy />}>
                      <Guide />
                    </Suspense>
                  }
                />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
              <footer className="content-footer">
                <span>Drop 性能诊断</span>
                <span>自托管 · v1.0.0</span>
              </footer>
            </main>
          </div>
        </div>
      </Boundary>
    </UserContext.Provider>
  );
}
