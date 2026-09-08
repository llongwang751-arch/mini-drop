import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { Button, Input, Layout, Menu, message, Popover, Space, Tag, Tooltip, Typography } from "antd";
import {
  ApiOutlined,
  AuditOutlined,
  DashboardOutlined,
  KeyOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  SettingOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import { createEventSource, getStoredApiKey, saveApiKey, syncBrowserSession } from "../api/client";
import ErrorBoundary from "./ErrorBoundary";
import styles from "./AppLayout.module.css";

const { Sider, Header, Content } = Layout;
const { Text } = Typography;

/**
 * 应用外壳：所有页面共享的侧栏、页头、访问凭据和全局 SSE 连接都在这里。
 * Outlet 才是当前路由页面。把凭据与事件连接放在外壳，能避免切换页面时反复登录；
 * 具体页面只关心自己的 REST 数据和领域交互。
 */

const MENU_ITEMS = [
  { key: "/ai-diagnosis", icon: <RobotOutlined />, label: "AI 诊断" },
  {
    key: "tasks",
    icon: <DashboardOutlined />,
    label: "采集与任务",
    children: [
      { key: "/tasks", label: "任务面板" },
      { key: "/schedules", label: "计划任务" },
    ],
  },
  {
    key: "system",
    icon: <SettingOutlined />,
    label: "系统治理",
    children: [
      { key: "/audit", icon: <AuditOutlined />, label: "审计日志" },
    ],
  },
];

const PAGE_META = {
  "/ai-diagnosis": ["AI 诊断", "多轮取证与 Skill 演进"],
  "/tasks": ["任务面板", "采集任务、执行状态与结果入口"],
  "/schedules": ["计划任务", "周期采集与执行策略"],
  "/audit": ["审计日志", "关键操作与诊断责任链"],
};

function menuSelection(pathname) {
  if (pathname === "/ai-diagnosis") return { selected: "/ai-diagnosis", parent: null };
  if (pathname === "/tasks" || pathname.startsWith("/task/") || pathname.startsWith("/agent/")) {
    return { selected: "/tasks", parent: "tasks" };
  }
  if (pathname === "/schedules") return { selected: "/schedules", parent: "tasks" };
  if (pathname === "/audit") return { selected: "/audit", parent: "system" };
  return { selected: "/ai-diagnosis", parent: null };
}

function pageMeta(pathname) {
  if (pathname.startsWith("/task/")) return ["任务结果", "证据、火焰图与采集产物"];
  if (pathname.startsWith("/agent/")) return ["采集节点", "Agent 状态与能力检查"];
  return PAGE_META[pathname] || PAGE_META["/ai-diagnosis"];
}

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && Boolean(window.matchMedia?.("(max-width: 640px)").matches),
  );
  const [collapsed, setCollapsed] = useState(isMobile);
  const [apiKey, setApiKey] = useState(getStoredApiKey() || "");
  const [credentialOpen, setCredentialOpen] = useState(false);
  const [sseConnected, setSseConnected] = useState(false);
  const [openKeys, setOpenKeys] = useState(() => {
    const { parent } = menuSelection(location.pathname);
    return parent ? [parent] : [];
  });

  const { selected: selectedKey, parent: selectedParent } = menuSelection(location.pathname);
  const [title, description] = pageMeta(location.pathname);
  const diagnosisPage = location.pathname === "/ai-diagnosis";

  useEffect(() => {
    let stream = null;
    let cancelled = false;
    const connect = async () => {
      stream?.close();
      setSseConnected(false);
      try {
        await syncBrowserSession();
      } catch {
        // REST errors surface on the page; SSE remains in explicit fallback.
      }
      if (cancelled) return;
      stream = createEventSource();
      stream.onopen = () => setSseConnected(true);
      stream.onerror = () => setSseConnected(false);
    };
    connect();
    window.addEventListener("mini-drop:credentials-changed", connect);
    return () => {
      cancelled = true;
      window.removeEventListener("mini-drop:credentials-changed", connect);
      stream?.close();
    };
  }, []);

  useEffect(() => {
    const query = window.matchMedia?.("(max-width: 640px)");
    if (!query) return undefined;
    const handleChange = (event) => {
      setIsMobile(event.matches);
      if (event.matches) setCollapsed(true);
    };
    query.addEventListener?.("change", handleChange);
    return () => query.removeEventListener?.("change", handleChange);
  }, []);

  useEffect(() => {
    if (isMobile) setCollapsed(true);
  }, [isMobile, location.pathname]);

  useEffect(() => {
    if (selectedParent) {
      setOpenKeys((current) => current.includes(selectedParent) ? current : [...current, selectedParent]);
    }
  }, [selectedParent]);

  async function handleSaveKey() {
    try {
      await saveApiKey(apiKey.trim());
      setCredentialOpen(false);
      message.success(apiKey.trim() ? "访问凭据已验证，实时连接正在建立" : "访问凭据已清除");
    } catch (error) {
      message.error(error?.message || "访问凭据验证失败");
    }
  }

  const credentialEditor = (
    <div className={styles.credentialEditor}>
      <div>
        <Text strong>Mini-Drop 访问密钥</Text>
        <Text type="secondary">仅用于当前控制台访问，保存后立即生效。</Text>
      </div>
      <Input.Password
        aria-label="Mini-Drop 访问密钥"
        placeholder="输入访问凭据"
        value={apiKey}
        onChange={(event) => setApiKey(event.target.value)}
        onPressEnter={handleSaveKey}
        prefix={<KeyOutlined />}
      />
      <Button type="primary" block onClick={handleSaveKey}>保存凭据</Button>
    </div>
  );

  return (
    <Layout className={styles.layout}>
      {isMobile && !collapsed && (
        <button
          type="button"
          className={styles.mobileNavBackdrop}
          aria-label="关闭导航"
          onClick={() => setCollapsed(true)}
        />
      )}
      <Sider
        className={`${styles.sider} ${isMobile ? styles.mobileSider : ""}`}
        collapsed={collapsed}
        collapsedWidth={isMobile ? 0 : 72}
        width={224}
        theme="dark"
        trigger={null}
      >
        <div className={`${styles.brand} ${collapsed ? styles.brandCollapsed : ""}`}>
          <span className={styles.brandMark}><ApiOutlined /></span>
          {!collapsed && (
            <span className={styles.brandCopy}>
              <b>Mini-Drop</b>
              <small>证据优先的智能诊断</small>
            </span>
          )}
        </div>

        <div className={styles.menuLabel}>{collapsed ? "" : "工作区"}</div>
        <Menu
          className={styles.menu}
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          openKeys={openKeys}
          onOpenChange={setOpenKeys}
          items={MENU_ITEMS}
          onClick={({ key }) => {
            navigate(key);
            if (isMobile) setCollapsed(true);
          }}
        />

        <div className={styles.siderFooter}>
          <Tooltip title={sseConnected ? "实时事件链路正常" : "事件链路断开，页面使用轮询兜底"} placement="right">
            <div className={`${styles.connectionCard} ${sseConnected ? styles.connected : ""}`}>
              <span className={styles.connectionDot} />
              {!collapsed && (
                <span>
                  <b>{sseConnected ? "事件流已连接" : "轮询兜底中"}</b>
                  <small>{sseConnected ? "SSE / Outbox" : "自动重连"}</small>
                </span>
              )}
            </div>
          </Tooltip>
        </div>
      </Sider>

      <Layout className={styles.mainLayout}>
        <Header className={styles.header}>
          <div className={styles.headerIdentity}>
            <Button
              className={styles.collapseButton}
              type="text"
              aria-label={collapsed ? "展开导航" : "收起导航"}
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((value) => !value)}
            />
            <div className={styles.pageIdentity}>
              <Text strong>{title}</Text>
              <Text type="secondary">{description}</Text>
            </div>
          </div>

          <Space className={styles.headerActions} size="small">
            <Tag className={`${styles.liveTag} ${sseConnected ? styles.liveTagOnline : ""}`} icon={<WifiOutlined />}>
              {sseConnected ? "实时" : "兜底"}
            </Tag>
            <Popover
              content={credentialEditor}
              trigger="click"
              placement="bottomRight"
              open={credentialOpen}
              onOpenChange={setCredentialOpen}
            >
              <Button icon={<KeyOutlined />}>访问凭据</Button>
            </Popover>
          </Space>
        </Header>

        <Content className={`${styles.content} ${diagnosisPage ? styles.diagnosisContent : ""}`}>
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </Content>
      </Layout>
    </Layout>
  );
}
