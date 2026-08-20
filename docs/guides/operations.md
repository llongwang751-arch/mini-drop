# Mini-Drop 完整运行手册

> 项目目录：`D:\tx\mini-drop`  
> 前端地址：<http://localhost>  
> 更新时间：2026-07-31

## 1. 这套项目启动后有什么

默认启动会运行八个服务：

| 服务 | 作用 | 对外访问 |
|---|---|---|
| `web` | React 管理页面 | <http://localhost> |
| `server` | FastAPI 控制面和 gRPC Server | 经 Web/Nginx 访问 |
| `control-plane` | 独立 gRPC 任务与心跳控制面 | Compose 内网 |
| `agent` | 在目标 Linux 环境执行采集 | 不开放浏览器端口 |
| `analyzer` | 独立处理采集产物 | 不开放浏览器端口 |
| `diagnosis-worker` | 推进 AI 多轮诊断和持续异常自动触发 | 不开放浏览器端口 |
| `postgres` | 保存任务、状态、审计和诊断 | Compose 内网 |
| `minio` | 保存 perf、火焰图等文件 | Compose 内网 |

正常启动顺序：

```text
PostgreSQL + MinIO
  -> Server
  -> Agent + Analyzer
  -> Web
```

## 2. Windows 第一次运行

### 2.1 打开 Docker Desktop

1. 按 Windows 键；
2. 搜索 **Docker Desktop**；
3. 点击打开；
4. 等待左下角显示 **Engine running**；
5. Docker Desktop 保持运行，可以最小化。

Docker Desktop 的 Containers 页面只是查看容器，不需要逐个点击三角形启动。
项目统一由终端中的 `docker compose` 命令启动。

### 2.2 打开项目终端

推荐使用 VS Code：

1. 打开 VS Code；
2. 点击“文件 → 打开文件夹”；
3. 选择 `D:\tx\mini-drop`；
4. 点击“终端 → 新建终端”；
5. 终端右上角确认类型为 **PowerShell**。

也可以单独打开 Windows PowerShell：

1. 按 Windows 键；
2. 搜索 PowerShell；
3. 打开“Windows PowerShell”；
4. 输入：

```powershell
cd D:\tx\mini-drop
```

确认位置：

```powershell
Get-Location
```

应看到：

```text
D:\tx\mini-drop
```

### 2.3 检查 Docker

在刚才的 PowerShell 终端输入：

```powershell
docker version
docker compose version
```

如果 `docker version` 的 Server 部分可见，说明 Docker Engine 已就绪。

### 2.4 创建本地配置

第一次运行：

```powershell
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
}
```

演示环境可以保留默认配置。不要把包含真实密钥的 `.env` 上传到 Git。

### 2.5 首次构建并启动

```powershell
docker compose up -d --build
```

这条命令会：

1. 下载 PostgreSQL 和 MinIO 镜像；
2. 构建 Server、Agent、Analyzer 和 Web；
3. 创建网络与数据卷；
4. 后台启动全部服务。

首次运行取决于网络速度，通常需要数分钟。命令返回不表示所有服务已经健康。

查看状态：

```powershell
docker compose ps
```

等待默认服务均为 `Up`，带健康检查的服务显示 `healthy`。

### 2.6 启动原生 Agent 与多语言演示目标

在项目 PowerShell 中执行：

```powershell
docker compose --profile native-agent --profile demo-targets up -d --build
```

该命令额外启动：

- `native-agent`：真正由 C++ 编写的 Agent，负责原生 perf 采集；
- `go-hotspot`：Go CPU 热点程序，开放真实 pprof；
- `cpp-hotspot`：带符号和 frame pointer 的 C++ CPU 热点程序。

验证：

```powershell
curl.exe http://localhost:6060/health
curl.exe http://localhost:6060/debug/pprof/
curl.exe http://localhost/api/agents
```

预期看到 `ok`、pprof HTML，以及 `agent_native_cpp` 为 `ONLINE`。

原生 Agent 以宿主机 PID 调用 perf，同时读取 `/proc/<PID>/status` 的
`NSpid`。采集产物 metadata 会同时保存 `host_pid`、`namespace_pid`
和 `namespace_mode=host-pid-mapped`，避免把容器内 PID 1 错当成宿主机
PID 1。

### 2.7 持续采样自动触发 AI 诊断

该能力默认开启：

```text
MINI_DROP_CONTINUOUS_AUTO_DIAGNOSIS=1
```

连续采样至少产生三个有效窗口后，系统用前序窗口的中位数作为基线。
只有 CPU 样本量突增或热点集中度明显漂移时，确定性检测器才触发 AI
诊断。触发结果先写入 `continuous_diagnosis_triggers` 做幂等记录，再创建
带目标、时间范围和拓扑快照的诊断会话。LLM 不参与“有没有异常”的判定。

查看 Worker 日志：

```powershell
docker compose logs -f diagnosis-worker
```

### 2.8 打开前端

1. 打开 Edge 或 Chrome；
2. 地址栏输入：

```text
http://localhost
```

3. 首页看到“Agent 在线”且数量至少为 1，即可开始使用。

不要访问 Docker Desktop 列表里的容器名称；浏览器统一访问
<http://localhost>。

## 3. Windows 日常启动

电脑重启后的标准步骤：

1. 打开 Docker Desktop；
2. 等待 `Engine running`；
3. 打开 PowerShell；
4. 输入：

```powershell
cd D:\tx\mini-drop
docker compose up -d
docker compose ps
```

5. 浏览器打开 <http://localhost>。

源代码、依赖或 Dockerfile 改动后使用：

```powershell
docker compose up -d --build
```

只重建某个服务：

```powershell
docker compose up -d --build server
docker compose up -d --build agent
docker compose up -d --build analyzer
docker compose up -d --build web
```

## 4. Ubuntu/Linux 第一次运行

### 4.1 安装基础工具

在 Ubuntu 终端执行：

```bash
sudo apt-get update
sudo apt-get install -y git make curl ca-certificates
```

按照 Docker 官方方式安装 Docker Engine 和 Compose Plugin。安装后确认：

```bash
docker version
docker compose version
```

如当前用户未加入 Docker 组：

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

### 4.2 克隆并启动

```bash
git clone <团队仓库地址> mini-drop
cd mini-drop
cp .env.example .env
docker compose up -d --build
docker compose ps
```

浏览器访问：

```text
http://<Ubuntu主机IP>
```

本机桌面 Ubuntu 直接访问：

```text
http://localhost
```

## 5. 服务检查

### 5.1 PowerShell

```powershell
curl.exe -s http://localhost/api/healthz
curl.exe -s http://localhost/api/agents
curl.exe -s http://localhost/api/tasks
```

### 5.2 Linux

```bash
curl -s http://localhost/api/healthz
curl -s http://localhost/api/agents
curl -s http://localhost/api/tasks
```

健康接口应返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "healthy": true
  }
}
```

## 6. Docker Desktop 里怎么看 Mini-Drop

1. 打开 Docker Desktop；
2. 点击左侧 **Containers**；
3. 找到名为 `mini-drop` 的 Compose 项目；
4. 点击左侧箭头展开；
5. 可以看到 `web/server/agent/analyzer/postgres/minio`；
6. 绿色圆点表示正在运行；
7. 点击具体容器可查看日志和资源占用。

Docker Desktop 里的蓝色方块按钮表示停止整个 Compose 项目。正常使用时不要点击。

## 7. 前端页面怎么使用

### 7.1 首页 `/`

用途：

- 查看在线 Agent；
- 创建采集任务；
- 查看任务状态；
- 搜索、排序和分页；
- 停止或删除任务；
- 查看持续采样时间轴。

### 7.2 创建普通采集任务

1. 打开 <http://localhost>；
2. 点击右上角“新建任务”；
3. “目标 Agent”选择在线 Agent；
4. “目标 PID”填写 Linux 进程 PID；
5. 选择采集器；
6. 填写采样时长和采样率；
7. 点击“创建”；
8. 等待状态完成；
9. 点击“查看结果”。

### 7.3 任务结果 `/task/:taskId`

可以查看：

- 任务参数；
- 状态历史；
- 采集是否真实或 degraded；
- 火焰图、TopN、IO 分布或内存结果；
- 可验证归因；
- Artifact 和 AnalysisJob 信息。

### 7.4 Agent 详情 `/agent/:agentId`

从首页点击 Agent ID，查看：

- 主机名和 IP；
- 系统内核；
- 最后心跳；
- 在线状态；
- 支持的采集器；
- Agent 自身 CPU、内存和 IO。

### 7.5 审计日志 `/audit`

用于查看：

- Agent 离线和恢复；
- 任务状态迁移；
- 任务取消和删除；
- AI 工具审批；
- 诊断状态变化。

### 7.6 AI 诊断 `/ai-diagnosis`

这是自然语言与证据优先能力合并后的统一 AI 诊断控制台：

1. 新建诊断会话；
2. 生成可证伪假设；
3. 申请白名单工具；
4. 人工审批深度采集；
5. 导入真实任务证据；
6. 生成可追溯报告；
7. 查看审计时间线。

旧 `/drop-insight` 和 `/diagnoses` 仅保留重定向兼容，都会跳转到本页面。

### 7.7 设置 `/settings`

查看 AI Provider 配置状态和系统运行配置。页面不会显示真实 API Key。

## 8. 创建一个可采样的 CPU 目标

不要长期采集 PID 1。PID 1 经常处于空闲状态，火焰图样本很少。

在项目 PowerShell 终端执行：

```powershell
$pidText = docker compose exec -T agent sh -lc `
  'nohup yes mini-drop >/dev/null 2>&1 & echo $! | tee /tmp/mini-drop-cpu.pid'

$targetPid = [int]$pidText.Trim()
$targetPid
```

终端会输出一个数字，例如：

```text
9425
```

在前端创建任务时把这个数字填入“目标 PID”。

停止 CPU 目标：

```powershell
docker compose exec -T agent sh -lc `
  'test -f /tmp/mini-drop-cpu.pid && kill $(cat /tmp/mini-drop-cpu.pid) || true'
```

## 9. perf 权限诊断

### 9.1 收集诊断上下文

`kernel.perf_event_paranoid` 是权限诊断上下文之一，不能单独判断 privileged/root Agent
是否能够采样指定 PID。最终结果以目标任务执行的 `perf record` exit code 和 stderr 为准。

PowerShell：

```powershell
docker compose exec -T agent sh -lc `
  'id; cat /proc/sys/kernel/perf_event_paranoid; grep -E "^(CapEff|Seccomp):" /proc/self/status'
```

原生 Linux：

```bash
id
cat /proc/sys/kernel/perf_event_paranoid
grep -E '^(CapEff|Seccomp):' /proc/self/status
```

轻量检查 perf 工具和用户态事件是否可用，不执行系统级全局采样：

```bash
docker compose exec agent perf stat -e cpu-clock:u -- true
```

该检查成功不代表稍后一定能 attach 其他 PID；它只用于排除 perf 工具或基础事件不可用。

### 9.2 目标任务排查顺序

先保留任务返回的真实 perf stderr 和 exit code，再逐项确认：

- Agent 的有效 UID 和 `CapEff`，以及容器 seccomp、宿主机 LSM 策略；
- Agent 是否能在自己的 PID namespace 中看到 `/proc/<目标 PID>`；
- 目标进程在整个采样时间窗内是否存活；
- 指定 event（例如 `cpu-cycles:u`）是否受当前内核和虚拟化环境支持；
- `fp`、`dwarf` 等 callgraph 模式是否适合目标程序及其编译选项。

Mini-Drop 不自动修改宿主机 sysctl。只有真实 `perf record` 明确返回内核权限拒绝，且
UID、capabilities、seccomp/LSM、PID 可见性、目标存活、event 和 callgraph 均已确认后，
才应另行申请临时主机策略变更。持久化 sysctl 必须单独进行安全评审。

### 9.3 Docker Desktop 边界

Docker Desktop 还可能在 Linux VM 或虚拟化层限制 `perf_event_open`。容器拥有 root 或
相关 capability 也不保证虚拟化层放行；真实 perf/eBPF 最终验收应使用原生 Linux 云节点。

## 10. eBPF 权限

原生 Ubuntu：

```bash
sudo mount -t tracefs tracefs /sys/kernel/tracing 2>/dev/null || true
sudo mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null || true
docker compose exec agent bpftrace --version
```

查看 Agent 能否读取追踪事件：

```bash
docker compose exec agent sh -lc \
  'test -r /sys/kernel/tracing/available_events && echo OK'
```

Docker Desktop 对内核模块、tracefs 和 BPF 能力有额外限制，真实 eBPF 验收推荐原生 Linux。

## 11. AI 智能诊断怎么运行

### 11.1 页面方式

1. 打开 <http://localhost/drop-insight>；
2. “问题描述”填写：

```text
服务 demo-service CPU 变慢，请先采集低风险指标，再用反证探针验证根因
```

3. 服务名填写 `demo-service`；
4. 环境填写 `staging`；
5. Agent ID 填首页在线 Agent 的 ID；
6. PID 填真实目标 PID；
7. 点击“创建”；
8. 点击“生成诊断计划”；
9. 查看假设和工具申请；
10. R2 深度探针出现后点击“通过”；
11. 点击“推进诊断”；
12. 查看证据、报告和审计时间线。

### 11.2 API 方式

PowerShell：

```powershell
$agents = (curl.exe -s http://localhost/api/agents | ConvertFrom-Json).data.items
$agent = $agents[0]

$body = @{
  query = "服务 demo-service CPU 变慢，请先采集低风险指标，再用反证探针验证根因"
  context = @{
    service_id = "demo-service"
    environment = "staging"
    instances = @(@{
      service_id = "demo-service"
      instance_id = "demo-service-local"
      host_id = $agent.hostname
      agent_id = $agent.id
      pid = 1
      environment = "staging"
    })
  }
  budget_profile = "staging"
} | ConvertTo-Json -Depth 8

$result = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost/api/v1/diagnoses `
  -ContentType "application/json; charset=utf-8" `
  -Body $body

$diagnosisId = $result.data.diagnosis_id
$diagnosisId
```

查询：

```powershell
Invoke-RestMethod `
  "http://localhost/api/v1/diagnoses/$diagnosisId" |
  ConvertTo-Json -Depth 12
```

## 12. 查看日志

全部日志：

```powershell
docker compose logs -f
```

单个服务：

```powershell
docker compose logs -f server
docker compose logs -f agent
docker compose logs -f analyzer
docker compose logs -f web
docker compose logs -f postgres
docker compose logs -f minio
```

只看最近 100 行：

```powershell
docker compose logs --tail=100 agent
```

退出实时日志：按 `Ctrl+C`。这只退出日志查看，不会停止容器。

## 13. 常用接口

```text
GET  /api/healthz
GET  /api/agents
GET  /api/tasks
POST /api/tasks
GET  /api/tasks/{task_id}
POST /api/tasks/{task_id}/cancel
DELETE /api/tasks/{task_id}
GET  /api/tasks/{task_id}/events
GET  /api/tasks/{task_id}/attempts
GET  /api/tasks/{task_id}/artifacts
GET  /api/analysis-jobs
POST /api/analysis-jobs/{job_id}/replay
GET  /api/audit-logs
GET  /api/metrics
POST /api/v1/diagnoses
GET  /api/v1/diagnoses/{diagnosis_id}
POST /api/v1/diagnoses/{diagnosis_id}/approvals
GET  /api/v1/diagnosis-evaluations/golden
```

## 14. OTel orchestrator-backed live subset

该子集只运行 `T1-CPU-001` 和 `T1-MEM-001`，协议固定为 2 案例 × 3 策略 × 3 重复，共 18 个 execution ID 和 6 个 campaign window。它独立于历史 `reports/benchmark/official-90`。

运行前必须满足：

- `external/opentelemetry-demo` 是预期固定版本；
- Control API 健康，目标 Agent 为 `ONLINE` 且具备案例所需 collector；
- CPU 的 `ad` 与内存的 `email` 容器可定位，正式负载固定为 1 个 worker；
- 操作者明确传入 `--approve-r2`；
- 默认故障窗口 120 秒，单次诊断等待上限 35 秒。

受保护的 Control API 还要求运行进程从环境变量读取 `MINI_DROP_API_KEY`。应由当前 shell 的受限凭据加载机制注入该变量；不要把密钥写入命令行参数、Compose 文件、仓库文件、报告或日志，也不要用 `echo`、`printenv`、`set` 等命令显示它。运行前只检查变量是否存在，不输出变量值：

```bash
if [ -n "${MINI_DROP_API_KEY:-}" ]; then
  printf '%s\n' 'MINI_DROP_API_KEY is set'
else
  printf '%s\n' 'MINI_DROP_API_KEY is missing' >&2
  exit 1
fi
```

`run_live_subset.py` 发出的 API 请求会自动添加 `Authorization: Bearer ...`，重试时保留认证头，并在最终 HTTP/URL 错误中脱敏该值。

```powershell
python scripts/run_live_subset.py `
  --otel-root external/opentelemetry-demo `
  --base-url http://localhost `
  --agent-id <online-agent-id> `
  --approve-r2 `
  --output-dir reports/benchmark/live-subset
```

云端运行时还应显式传入 CPU/MEM 各自的 `--project-name` 与 Compose 文件参数，以便 manifest 固化配置 hash 和容器来源。`--finalize-only` 只对已经存在的 18 个 submission 和 6 个有效 manifest 做评分门禁，不会补跑缺失窗口。

状态语义：

- `SKIPPED`：该 case/repetition 的三种策略已经完整存在；
- `PUBLISHED`：三策略终态、fixture 信号、清理与分组原子发布全部通过；
- `FIXTURE_FAILED`：保留失败 manifest，停止后续窗口，不发布该窗口 submission；
- `INSUFFICIENT_EVIDENCE`：允许保留的真实诊断终态，不等于 fixture failure，也不能改写为成功根因；
- 进程仅在 `complete=true` 时返回 0，否则返回 1。

部分窗口或重复策略不能自动续跑，必须先人工核对原始 manifest。正式结果要求 18 个 ID 完整唯一、6 个 manifest 均已发布、无 `fixture_failure`、`cleanup.errors` 为空且 Oracle 隔离检查通过。

当前代码和聚焦回归已通过，但尚未生成云端正式 18-run 产物。

## 15. 自动化测试

### 15.1 Windows

项目已经有 `.venv` 时：

```powershell
cd D:\tx\mini-drop
.\.venv\Scripts\python.exe -m pytest -q
```

首次创建：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe dev.py proto
.\.venv\Scripts\python.exe -m pytest -q
```

前端构建：

```powershell
npm.cmd --prefix web install
npm.cmd --prefix web run build
```

### 15.2 Linux

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python dev.py proto
make test
npm --prefix web install
npm --prefix web run build
make eval
```

最近一次记录的 Python 全量回归为 `638 passed`（2026-08-19）；live subset 当前聚焦回归为 `55 passed`、1 个非失败的 pytest-asyncio 弃用警告。该结果只验证实现机制，不作为云端验收证据；版本或依赖变化后应重新执行。

## 16. 停止、重启和清理

### 停止但保留数据

```powershell
docker compose stop
```

### 再次启动

```powershell
docker compose start
```

### 删除容器但保留数据库和 MinIO 数据卷

```powershell
docker compose down
```

### 删除容器和全部项目数据

以下命令会删除 PostgreSQL、MinIO 和历史任务数据：

```powershell
docker compose down -v
```

只在确定不保留数据时执行。

### 完整重建

```powershell
docker compose down
docker compose build --no-cache
docker compose up -d
docker compose ps
```

## 17. 常见问题

### 17.1 Docker 命令提示连接失败

原因：Docker Desktop 尚未启动。

处理：

1. 打开 Docker Desktop；
2. 等待 `Engine running`；
3. 重新运行 `docker compose up -d`。

### 17.2 页面打不开

```powershell
docker compose ps
docker compose logs --tail=100 web
docker compose logs --tail=100 server
curl.exe -s http://localhost/api/healthz
```

### 17.3 Agent 显示离线

```powershell
docker compose ps agent
docker compose logs --tail=100 agent
docker compose restart agent
Start-Sleep -Seconds 10
curl.exe -s http://localhost/api/agents
```

### 17.4 下拉框没有在线 Agent

确认：

- Agent 容器正在运行；
- Server 健康；
- Agent 日志中有注册和心跳；
- `/api/agents` 返回 `ONLINE`。

### 17.5 perf 返回权限不足

查看 Agent 日志：

```powershell
docker compose logs --tail=100 agent
```

如果任务返回权限错误，保留 `perf record` 的 exit code 和 stderr，并按第 9 节依次核对
UID/capabilities、seccomp/LSM、PID 可见性、目标存活、event 与 callgraph。不要仅依据
`perf_event_paranoid` 数值修改宿主机策略。

### 17.6 任务长时间不结束

分别查看：

```powershell
docker compose logs --tail=100 agent
docker compose logs --tail=100 analyzer
curl.exe -s http://localhost/api/analysis-jobs
```

判断任务停在：

- Agent 领取前；
- 采集过程中；
- 上传阶段；
- AnalysisJob 等待；
- Analyzer 重试/死信。

### 17.7 中文在 PowerShell 显示乱码

先执行：

```powershell
chcp 65001
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

随后重新运行命令。

## 18. 推荐的日常使用顺序

```text
打开 Docker Desktop
  -> PowerShell 进入 D:\tx\mini-drop
  -> docker compose up -d
  -> docker compose ps
  -> 浏览器打开 http://localhost
  -> 确认 Agent 在线
  -> 创建真实目标进程
  -> 创建采集任务
  -> 查看状态、AnalysisJob 和结果
  -> 使用 Drop Insight 做证据化诊断
  -> 完成后停止负载
```

完整功能验收请使用：`docs/guides/acceptance.md`。
