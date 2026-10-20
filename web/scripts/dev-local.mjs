/**
 * 本地开发启动器只负责选择隔离代理并启动 Vite，避免开发页面误连生产。
 */
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// 只连接显式选中的隔离后端，不读取 deploy/.env，也不默认连接生产服务。
const settingsPath = new URL(
  "../../.local/dev/local/settings.json",
  import.meta.url,
);
let port = 18080;
try {
  port = JSON.parse(readFileSync(settingsPath, "utf8")).port;
} catch (error) {
  if (error.code !== "ENOENT")
    throw new Error("Invalid local development settings");
}
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  throw new Error("Invalid local backend port");
}
const child = spawn(
  process.execPath,
  [
    fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
    "--host",
    "127.0.0.1",
  ],
  {
    cwd: fileURLToPath(new URL("../", import.meta.url)),
    env: {
      ...process.env,
      DROP_DEV_PROXY: `http://127.0.0.1:${port}`,
      VITE_DROP_ENVIRONMENT: "本地开发",
    },
    stdio: "inherit",
  },
);
for (const signal of ["SIGINT", "SIGTERM"])
  process.on(signal, () => child.kill(signal));
child.on("exit", (code) => process.exit(code ?? 1));
