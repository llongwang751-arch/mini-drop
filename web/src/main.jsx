import React from "react";
import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import Router from "./router";
import { APP_THEME } from "./theme";
import "./design-system.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={APP_THEME}
    >
      <Router />
    </ConfigProvider>
  </React.StrictMode>,
);
