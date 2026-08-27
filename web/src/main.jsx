import React from "react";
import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import Router from "./router";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#2563eb",
          colorSuccess: "#0f8a5f",
          colorWarning: "#c98512",
          colorError: "#c93d4a",
          colorText: "#15243b",
          colorTextSecondary: "#64748b",
          colorBorder: "#dce4ee",
          colorBgLayout: "#eef2f7",
          borderRadius: 8,
          fontFamily: 'Inter, "Segoe UI", "Microsoft YaHei", Arial, sans-serif',
          controlHeight: 36,
        },
        components: {
          Button: { fontWeight: 600 },
          Card: { headerFontSize: 14 },
          Menu: { darkItemBg: "#071426", darkSubMenuItemBg: "#09182c" },
        },
      }}
    >
      <Router />
    </ConfigProvider>
  </React.StrictMode>,
);
