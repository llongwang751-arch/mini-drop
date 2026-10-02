# 云端恢复核验（2026-09-19）

用户确认服务器恢复，运行目标切回 <https://120.24.187.205/ai-diagnosis>。本机 Docker 引擎已不运行；停止本地 Compose 的尝试因此未执行，不宣称修改了本地容器重启策略。未重新启动 Desktop，未删除任何本地或云端卷。

## 恢复结果

- 经已有 known_hosts 严格校验 SSH 连接控制机；HTTPS 健康接口确认 Control、PostgreSQL、Diagnosis Worker 均 healthy。Windows curl 对私有 CA 无法完成撤销检查，使用 `--ssl-no-revoke` 检查；保留证书链和主机验证，没有使用 `-k`。
- 云端 `/opt/mini-drop-current` 仍指向 `/opt/mini-drop-releases/20260914T073540Z`，Worker 镜像为 `mini-drop-python-worker:20260914T073540Z`。
- Runtime 为 `diagnosis-agent-v4-lats`，PostgreSQL Checkpoint HEALTHY、无降级。
- 初查 Control 演示 Agent 在线，两台腾讯 Worker 离线。两台 Worker 的机器、业务服务及采集容器实际仍运行，但日志持续报告 `heartbeat_failed / failed to connect to all addresses`。
- 经控制机已有业务隧道 SSH 身份连接两台 Worker，保留 StrictHostKeyChecking 和专用 known_hosts。分别重启 `mini-drop-control-tunnel.service`，随后重启原 `mini-drop-worker-agent-1`。未重建镜像、未改环境或认证配置、未重启业务服务。
- 终验 API：`control-interview-demo-agent`、`tencent-cvm-worker-1`、`tencent-lighthouse-worker-2` 三者均 ONLINE。

## 尚未执行的发布

本次仅恢复连接并核对现有云端服务，没有将本地 9 月 19 日的 Agent v5 改动发布。待发布范围包括 Python Runtime/Harness/证据门禁、知识与 Skill 源文件、Web、检索依赖与 Chroma 私有服务配置。保留云端鉴权、mTLS 和 SSH 传输方式，不可复制本地无鉴权 Compose 配置覆盖云端环境。

后续发布须基于当前云端镜像建立回滚标签，在新版本目录打包源码与静态文件，顺序构建受影响服务；先建知识索引再滚动 Worker/Web，并完成真实诊断和三台 Agent 心跳验收。当前数据库无需根据本轮代码改动自动执行破坏性迁移；不回写旧报告、旧准确率或历史失败记录。

本次 ONLINE/healthy 只证明恢复时连接与服务状态，不代表重新跑过 21 场景、长期稳定性或新版本效果测试。
