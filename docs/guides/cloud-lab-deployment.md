# Mini-Drop 云实验环境部署记录

> 部署日期：2026-08-19  
> 部署目录：`/opt/mini-drop-li-mingyuan`  
> 本文不记录服务器密码、API Key、数据库密码或对象存储密钥。

## 1. 已部署拓扑

| 节点 | 职责 | 运行组件 |
|---|---|---|
| control | 控制面 | Web、Go API、Python Server、Diagnosis Worker、Analyzer、PostgreSQL、MinIO |
| worker1 | 采集节点 | Agent、perf、bpftrace/eBPF、py-spy、async-profiler |
| worker2 | 采集节点 | Agent、perf、bpftrace/eBPF、py-spy、async-profiler |

控制面入口：<https://47.112.10.137/>  
HTTP 兼容入口：<http://47.112.10.137:18080/>（跳转到 HTTPS）

HTTPS 使用实验环境自签名证书，浏览器首次访问会提示证书风险。确认访问的是上述实验服务器后，可选择继续访问。

## 2. 当前验证结果

- Web 外网访问：HTTP 200。
- API 健康检查：HTTP 200。
- 2026-08-20 只读预检：控制面 Web、Go API、Python Server、Diagnosis Worker、Analyzer、PostgreSQL 与 MinIO 正常运行，API 健康检查通过，当前无活动任务或诊断。
- 2026-08-20 当前在线采集 Agent 为 `linux-worker-1`、`linux-worker-2`，二者均为 `ONLINE`，并具备本次 CPU/MEM 子集所需的 `perf_cpu`、`memory_smaps` 与 `sys_metrics` 等能力。
- 以下 `control-campaign-agent`、`li-mingyuan-worker-1`、`li-mingyuan-worker-2` 是 2026-08-19 历史验收时使用的 Agent 标识，仅用于关联下列历史 Task/Campaign 记录，不代表当前在线拓扑。
- 历史 worker1 `sys_metrics`：`PENDING → RUNNING → UPLOADING/ANALYZING → DONE`。
- 历史 worker2 `sys_metrics`：采集、上传、SHA-256 完整性校验、分析全部成功。
- 历史 worker1 `ebpf_io`：真实 eBPF 采集成功，共生成 `ebpf_metrics.json` 与 `io_latency.txt`，产物完整性为 `VERIFIED`。
- AI Provider：DeepSeek 已启用，使用 `deepseek-v4-flash`；真实连通性与工具调用能力探测通过，密钥未写入仓库。
- AI 范围确认：模糊问题会明确追问服务、环境、Agent、PID 和时间窗；补齐后进入 `UNDERSTANDING`，不会猜测目标。
- 统一外部测试集：已随 Server 镜像安装，页面可读取 30 个场景、90 次完成运行及逐次审计轨迹。
- 真实故障 Campaign：`LIVE-CPU-001` 已在云端重新执行并完成。基线 CPU 约 0.30%，故障阶段约 96.33%，恢复约 0.29%；关联 Task 为 `DONE`，TaskAttempt 和 Analyzer Job 为 `SUCCEEDED`，产物完整性为 `VERIFIED`，根因与 Oracle 一致，恢复清理成功。
- OTel orchestrator-backed live subset：代码与本地聚焦回归已完成，但**尚未在云端执行或验收**。旧 `LIVE-CPU-001` 记录不能替代新的 `T1-CPU-001`/`T1-MEM-001` 六窗口、18-run 验收。

验收任务示例：

- `task_20260819_060718_46eccfc4`：worker1 系统指标，`DONE`。
- `task_20260819_060809_23ff57c0`：worker2 系统指标，`DONE`。
- `task_20260819_060823_ca819fe2`：worker1 eBPF I/O，`DONE`。
- `campaign_20260819_095524_79b48f`：真实 CPU 故障注入、取证、Oracle 对比与恢复，`COMPLETED`。

## 3. 打开系统

1. 浏览器访问 <https://47.112.10.137/>。
2. 首次访问接受实验环境自签名证书。
3. 页面右上角填写 API Key 并保存。
4. API Key 在 control 节点的受限环境文件中读取：

```bash
ssh root@47.112.10.137
grep '^MINI_DROP_API_KEY=' /opt/mini-drop-li-mingyuan/deploy/env/cloud-control.env | cut -d= -f2-
```

不要将输出复制到聊天记录、截图、Git 仓库或公开文档。

## 4. 查看运行状态

### control 节点

```bash
cd /opt/mini-drop-li-mingyuan
docker compose \
  --env-file deploy/env/cloud-control.env \
  -f docker-compose.cloud-control.yml ps
```

### worker 节点

在 worker1、worker2 分别执行：

```bash
cd /opt/mini-drop-li-mingyuan
docker compose \
  --env-file deploy/env/cloud-worker.env \
  -f docker-compose.worker.yml ps
docker logs --tail 100 mini-drop-worker-agent-1
```

## 5. 启停命令

### 重启控制面

```bash
cd /opt/mini-drop-li-mingyuan
docker compose \
  --env-file deploy/env/cloud-control.env \
  -f docker-compose.cloud-control.yml up -d
```

### 重启 Worker

```bash
cd /opt/mini-drop-li-mingyuan
docker compose \
  --env-file deploy/env/cloud-worker.env \
  -f docker-compose.worker.yml up -d
```

### 停止本项目

控制节点：

```bash
docker compose --env-file deploy/env/cloud-control.env \
  -f docker-compose.cloud-control.yml down
```

Worker 节点：

```bash
docker compose --env-file deploy/env/cloud-worker.env \
  -f docker-compose.worker.yml down
```

不要使用不带 `-f` 的 `docker compose down`，也不要删除其他同学的容器、网络或数据卷。

## 6. 本次为云环境增加的可复现性处理

- 新增 `docker-compose.cloud-control.yml`，补齐 Web、Go API、Python Server、Diagnosis Worker、Analyzer、PostgreSQL、MinIO 的控制面组合。
- Agent 镜像改用国内 Debian 软件源，减少云主机下载等待。
- async-profiler 4.4 使用仓库内离线安装包构建，避免云主机访问 GitHub 失败。
- Dockerfile 在安装 async-profiler 前执行 SHA-256 校验，防止依赖包损坏或被替换。
- API Key、数据库密码、MinIO 密钥与 TLS 私钥只保存在远端 `deploy/env` 和 `deploy/tls`，未写入仓库。
- 统一外部测试集放入 `benchmarks/external` 并随 Server 镜像构建，不依赖宿主机只读源码目录的权限。
- 使用独立 Compose 项目名与独立部署目录，没有停止或覆盖控制节点上原有的其他项目。

## 7. 已知边界

- 当前证书为自签名证书；正式环境应替换为受信任 CA 证书和域名。
- Agent 镜像内置的 async-profiler 离线包是 Linux x86_64 版本；ARM64 节点需准备对应架构包。
- 云安全组应只允许可信来源访问管理入口，并将 50051、9000 限制为 Worker 到 control 的必要通信。
