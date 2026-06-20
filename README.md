# Mini-Drop 性能诊断系统

Mini-Drop 是一个可运行的性能诊断平台，用于复刻 Drop 的主要能力：用户在 Web 上创建采集任务，Server 调度任务，Agent 在目标机器执行性能采集，Analyzer 生成可视化结果，最后在 Web 上展示火焰图、热点函数、eBPF 分布、持续采样切片和状态历史。

项目重点不是做静态页面，而是把“创建任务、下发任务、真实采集、状态落库、结果分析、页面展示、审计追踪”这条链路跑通。当前版本覆盖基础题、扩展题和一个加分项，并保留真实 `perf`、`bpftrace/eBPF`、`py-spy` 采集能力。

## 技术栈

- 前端：React 19、TypeScript/TSX、Vite、React Router、Zustand、Phosphor Icons
- 后端：FastAPI、SQLAlchemy、MySQL 8、MinIO 对象存储、Uvicorn
- Agent 与分析器：Python 3.12
- 采集工具：Linux perf、bpftrace/eBPF、py-spy、`/proc`
- 部署：Docker Compose

## 快速启动

环境要求：

- Docker Engine / Docker Desktop，支持 Docker Compose
- Linux 内核 5.4+
- 如需真实 eBPF 采集，需要特权容器权限
- Docker Compose 会自动启动 MySQL 8 和 MinIO

启动项目：

```bash
docker compose up --build
```

打开页面：

- Web 控制台：<http://localhost:8080>
- FastAPI 文档：<http://localhost:8080/docs>

Agent 使用宿主机 PID 命名空间，因此页面中填写的 PID 必须是 Agent 可见的进程 PID。演示时可先使用 `PID 1`，或在 Agent 容器里启动一个 CPU 密集进程后填写对应 PID。

## 已实现功能

- Web 创建采集任务：指定 Agent、PID、采样时长、采样频率和采集器
- 严格任务状态机：`PENDING -> RUNNING -> UPLOADING -> DONE / FAILED`
- 每次状态迁移都写入 MySQL，并带有 `reason`
- Agent 每 5 秒心跳，Server 30 秒无心跳判定离线
- Agent 上线、离线和恢复事件写入审计日志
- 真实 `perf record + perf script` CPU 采集
- 真实 `bpftrace` eBPF 内核探针：`kprobe:vfs_write`
- 真实 `py-spy` Python 语言级调用栈采集
- CPU、内存 RSS、I/O 采样
- 火焰图、热点函数 TopN、eBPF 写入分布、Python 调用栈视图
- Continuous Profiling：自动切片、时间窗口回看、停止续建
- 定时任务：按固定间隔自动创建采集任务
- 自然语言采集规划：从一句话中提取 PID、采集器、时长、频率和 Agent
- 对象存储：Compose 环境使用 MinIO，本地测试默认使用文件存储
- 轻量鉴权：可通过 `MINIDROP_API_KEY` 开启 API Key 校验
- FastAPI 请求校验与 OpenAPI 文档
- SQLAlchemy + MySQL 持久化；测试环境使用内存 SQLite
- 单元测试与端到端测试，17 个测试通过，覆盖率高于题目要求的 50%

## 常用命令

运行测试：

```bash
make test
```

运行覆盖率检查：

```bash
pip install -r requirements-dev.txt
make coverage
```

完整本地验收：

```bash
make check
```

Windows PowerShell 也可以直接执行：

```powershell
.\scripts\check.ps1
```

重新构建并启动：

```bash
docker compose up --build
```

运行脚本演示：

```bash
make demo
```

前端开发模式：

```bash
cd frontend
npm install
npm run dev
```

Vite 会把 `/api` 请求代理到本地 FastAPI Server 的 `8080` 端口。

## 前端工程规范

前端按控制台项目拆分为路由、状态、API、页面和组件：

- `src/main.tsx`：只负责挂载 React 应用
- `src/app/router.tsx`：集中声明页面路由
- `src/store/dashboardStore.ts`：集中管理 Agent、任务、审计、轮询和业务动作
- `src/api/client.ts`：统一封装 API 请求和错误处理
- `src/pages`：页面级组件
- `src/features`：任务详情、持续采样、自然语言采集等业务组件
- `src/components`：状态徽标、指标卡、空状态等通用组件

## 后端工程规范

后端按 FastAPI 多文件应用方式拆分，避免所有接口堆在 `server.py`：

- `minidrop/server.py`：应用工厂，负责创建 FastAPI、注册路由、异常处理和静态前端托管
- `minidrop/api/schemas.py`：Pydantic 请求模型
- `minidrop/api/dependencies.py`：FastAPI `Depends` 依赖注入
- `minidrop/api/routers`：按领域拆分 `agents`、`tasks`、`audit`、`continuous`、`natural_language`、`schedules`、`auth` 和 `health`
- `minidrop/db.py`：SQLAlchemy 模型、事务和状态机持久化
- `minidrop/storage.py`：对象存储抽象，支持本地文件和 MinIO
- `minidrop/collectors.py`：perf、eBPF/bpftrace、py-spy 和 `/proc` 采集器
- `minidrop/analyzer.py`：火焰图、TopN、histogram 和证据归因

## 仓库工程化

- `.editorconfig`：统一编码、缩进和换行
- `.env.example`：列出本地和 Compose 所需环境变量
- `Makefile`：统一测试、覆盖率、前端类型检查、前端构建和 Docker 启停命令
- `scripts/check.ps1`：Windows 下一键执行完整本地验收
- `.github/workflows/ci.yml`：GitHub Actions 自动跑后端测试、覆盖率、前端类型检查和构建
- `Dockerfile` / `docker-compose.yml`：一键启动 MySQL、FastAPI Server、React 静态前端和 Python Agent

## eBPF 演示

为了让 `kprobe:vfs_write` 有明显变化，可以在采集 eBPF 任务时执行：

```bash
dd if=/dev/zero of=/tmp/minidrop-io.bin bs=1M count=512
```

任务完成后，Web 详情页会展示内核写入分布。

Agent 镜像使用 `privileged: true`，因为 bpftrace 需要访问内核 tracing 能力。生产环境应改为最小权限能力集，并使用白名单探针目录。

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/tasks` | 创建采集任务 |
| `GET` | `/api/tasks` | 查询任务列表 |
| `GET` | `/api/tasks/{id}` | 查询任务详情、状态历史和分析结果 |
| `GET` | `/api/agents` | 查询 Agent 列表 |
| `GET` | `/api/audit` | 查询 Agent 上线、离线和恢复审计 |
| `GET` | `/api/continuous/{agent_id}` | 查询持续采样时间窗口 |
| `POST` | `/api/natural-language` | 解析自然语言并创建采集任务 |
| `GET` | `/api/auth/check` | 检查当前用户上下文 |
| `GET` | `/api/users/me` | 查询当前用户、用户组信息 |
| `GET` | `/api/schedules` | 查询定时采集计划 |
| `POST` | `/api/schedules` | 创建定时采集计划 |
| `POST` | `/api/schedules/run-due` | 立即触发到期计划 |
| `POST` | `/api/schedules/{id}/stop` | 停止定时采集计划 |
| `POST` | `/api/tasks/{id}/stop-continuous` | 停止持续采样后续切片 |
| `POST` | `/api/agents/{id}/heartbeat` | Agent 心跳 |
| `POST` | `/api/agents/{id}/claim` | Agent 原子领取任务 |
| `POST` | `/api/tasks/{id}/upload` | 上传原始采集数据并触发分析 |
| `POST` | `/api/tasks/{id}/fail` | 写入任务失败原因 |

## 交付说明

- [DESIGN.md](DESIGN.md)：架构、状态机、关键决策、取舍说明和 AI 协作说明
- [ASSESSMENT.md](ASSESSMENT.md)：逐项验收矩阵、实机证据和生产化差距说明
- [DEMO.md](DEMO.md)：15 分钟演示脚本
- [LEARNING_NOTES.md](LEARNING_NOTES.md)：学习笔记、实现取舍和后续学习计划

## 完成度与生产化差距

Mini-Drop 的目标是尽可能复刻 Drop 的完整诊断能力。当前版本已经覆盖 Web、Server、Agent、Analyzer、状态机、心跳审计、真实采集、Continuous Profiling、自然语言采集规划、MinIO 对象存储、轻量鉴权和定时任务。

和生产级 Drop 相比，当前版本还缺少公司内部 SSO、多租户权限治理、gRPC 控制通道、分布式队列和长期数据保留策略。这些属于生产化能力，不影响演示中的核心诊断链路，但已经在设计文档中列为后续演进方向。

核心链路保持真实可验证：React 下发任务，FastAPI 调度任务，MySQL 持久化状态，Python Agent 调用 `perf`、`bpftrace`、`py-spy` 完成真实性能采集，Analyzer 生成 Web 可展示结果。
