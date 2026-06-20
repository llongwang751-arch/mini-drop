# Mini-Drop 学习笔记与复盘

## 1. 我对项目的理解

Mini-Drop 的目标不是做一个普通后台页面，而是快速复刻公司内部 Drop 的主要能力：让用户可以在 Web 上创建性能诊断任务，由 Server 统一调度，Agent 到目标环境采集数据，Analyzer 生成火焰图、TopN 热点函数和诊断结论，最后回到 Web 展示。

我理解的核心链路是：

```text
Web 创建任务 -> Server 持久化和调度 -> Agent 真实采集 -> Analyzer 分析 -> Web 展示
```

这个项目解决的是后台开发、SRE、性能优化工程师排查线上卡顿、CPU 飙高、I/O 异常时的效率问题。以前可能要人工 SSH 到机器上执行 `perf`、`bpftrace`、`py-spy`，再手动分析结果；Mini-Drop 把这些动作产品化、平台化，并且保留任务状态、审计日志和分析证据。

## 2. 选择这个项目的原因

我选择 Mini-Drop 是因为它接近真实基础架构场景，不是单一 CRUD 系统。它要求把前端、后端、Agent、数据库、Docker 部署、Linux 性能采集工具和 AI 辅助分析串起来，能综合训练后端工程、系统可观测性、前端控制台、工程化交付和问题解释能力。

导师反馈中也明确提到：这个项目重点看完整度、真实跑通和演示效果。因此我的目标不是只做一个“看起来像”的页面，而是尽量把端到端链路、采集器、状态机、持续采样、自然语言采集、对象存储和工程化结构都补齐。

## 3. 学习内容

### 3.1 Linux 性能分析

学习重点：

- PID、CPU ticks、RSS、I/O 统计这些基础指标是什么意思。
- `/proc/<pid>/stat`、`/proc/<pid>/status`、`/proc/<pid>/io` 可以读取进程运行状态。
- CPU 采样不是统计某一瞬间，而是在一段时间内抽样调用栈。
- 火焰图用来观察调用栈里哪些函数最“热”。

项目落地：

- Agent 可以采集 CPU、内存 RSS、I/O 等指标。
- Analyzer 把采样栈转换成火焰图和 TopN 热点函数。

### 3.2 perf

学习重点：

- `perf record` 用于采集 CPU 调用栈样本。
- `perf script` 用于解析采样结果。
- 在 WSL2 或 Docker 中可能出现 `[unknown]` 符号，需要符号表和 debuginfo 才能进一步解析。

项目落地：

- perf 采集器执行真实 `perf record + perf script`。
- 采集结果保留 `backend` 和 `degraded` 字段，方便判断是真实采样还是环境权限不足后的降级。

### 3.3 eBPF / bpftrace

学习重点：

- eBPF 可以在内核态挂探针，观察系统调用和内核函数。
- bpftrace 是 eBPF 的高层脚本工具，适合快速验证探针。
- 容器中运行 eBPF 通常需要 `privileged`、内核头文件和对应权限。

项目落地：

- eBPF 采集器使用 `bpftrace` 挂载 `kprobe:vfs_write`。
- 演示时可以通过 `dd` 制造写入，Web 展示写入进程分布。

### 3.4 py-spy

学习重点：

- py-spy 可以不侵入 Python 程序采集语言级调用栈。
- 它和 perf 的语义不同，更贴近 Python 开发者看到的函数调用。

项目落地：

- py-spy 采集器输出 Python 调用栈。
- Web 为 py-spy 单独展示 Python 语言调用栈视图。

### 3.5 后端工程

学习重点：

- FastAPI 提供 REST API、参数校验和 OpenAPI 文档。
- SQLAlchemy 管理数据库模型、查询和事务。
- MySQL 保存任务、状态迁移、Agent 心跳、审计日志和定时任务。
- 状态机要约束非法迁移，避免任务状态混乱。

项目落地：

- Server 使用 FastAPI + SQLAlchemy + MySQL。
- 路由按模块拆分到 `tasks`、`agents`、`audit`、`continuous`、`schedules` 等文件。
- `tasks` 保存任务当前状态，`transitions` 保存每次状态变化和 reason，`agents` 保存心跳，`audit` 保存上线、离线和恢复事件。

### 3.6 前端工程

学习重点：

- React 负责组件化页面。
- TypeScript 提供类型约束，减少接口字段错用。
- React Router 负责页面路由。
- Zustand 管理全局状态。
- Vite 负责本地开发和构建。

项目落地：

- 前端从单文件写法重构为 `api`、`app`、`store`、`pages`、`features`、`components` 分层。
- 页面展示任务列表、任务详情、火焰图、TopN、Agent、审计日志、持续采样和自然语言采集。
- 页面字体已调大，方便演示录屏。

### 3.7 AI 协作与质量验证

学习重点：

- AI 可以加速实现，但不能替代理解需求。
- 判断 AI 生成质量不能只看页面是否能打开，而要看是否满足题目验收点。
- 每次 AI 修改后都要用测试、构建和真实运行验证。

项目落地：

- 用单元测试和端到端测试验证状态机、采集器解析、自然语言规划、定时任务、对象存储和 Server API。
- 用 Docker Compose 验证 React、FastAPI、MySQL、MinIO、Agent 能一起启动。
- 用真实 perf、bpftrace/eBPF、py-spy 任务证明采集器不是纯 mock。

## 4. 项目实现取舍

已经完成或覆盖的能力：

- Web 创建任务和查看结果。
- Server 调度任务、持久化任务状态。
- Python Agent 心跳、领取任务、执行采集。
- perf、bpftrace/eBPF、py-spy 多采集器。
- Analyzer 生成火焰图、TopN 热点函数、histogram 和规则归因。
- MySQL 持久化任务、状态迁移、Agent、审计日志。
- MinIO / 对象存储保存原始采集结果和分析结果。
- Continuous Profiling：自动切片、时间窗口回看、停止续建。
- 自然语言采集：把用户输入转换成采集任务计划。
- 轻量鉴权和用户信息头。
- 定时任务。
- React + TypeScript 前端工程化。
- FastAPI + SQLAlchemy 后端工程化。
- Docker Compose 一键启动。
- README、DESIGN、DEMO、ASSESSMENT、CI 和测试。

仍可继续加强的生产级能力：

- 公司内部 SSO、多租户、用户组和细粒度权限。
- gRPC 控制通道。
- 更完整的异步队列，比如 Redis、Kafka 或 Celery。
- 长期数据保留和清理策略。
- 更严格的 eBPF 最小权限治理。
- 更完整的 LLM 工具调用式智能归因。

这些不是当前核心链路的缺口，而是生产化治理能力的后续方向。演示时我会明确说明当前版本已经覆盖诊断主链路，后续可继续补齐权限、队列、长期治理和智能归因。

## 5. 测试与验证

当前验证方式：

- 后端单元测试和端到端测试：`17/17` 通过。
- 覆盖率：`68%`。
- Python 编译检查：通过。
- 前端 TypeScript 类型检查：通过。
- 前端生产构建：通过。
- Docker Compose：用于启动 MySQL、MinIO、FastAPI Server、Agent 和前端。
- 页面地址：`http://localhost:8080`。
- API 文档：`http://localhost:8080/docs`。

## 6. 学习计划

已完成：

1. 阅读《Mini-Drop 题目.md》和《drop系统复刻指南.md》，梳理 Web -> Server -> Agent -> Analyzer -> Web 主链路。
2. 学习 Linux 性能分析基础，理解 PID、调用栈、采样频率、火焰图、TopN。
3. 学习 perf、bpftrace/eBPF、py-spy 的区别，并验证真实采集路径。
4. 学习 FastAPI、SQLAlchemy、MySQL、状态机、事务和审计日志。
5. 学习 React + TypeScript、React Router、Zustand，把前端从单文件改为分层工程。
6. 补齐 Docker Compose、MinIO、轻量鉴权、定时任务、CI 和本地验收脚本。

后续计划：

1. 学习 Alembic 数据库迁移，替代自动建表。
2. 学习 gRPC，让 Agent/Server 通信更接近生产 Drop。
3. 学习 Redis、Kafka 或 Celery，把分析任务异步化。
4. 学习 Agent 身份认证、SSO 和 TLS。
5. 学习 eBPF 最小权限配置，减少 `privileged: true` 依赖。
6. 学习 perf 符号解析、debuginfo 和 debuginfod，提高火焰图可读性。
7. 学习 LLM 工具调用式归因，让模型只能基于结构化证据输出结论。
8. 学习压测和容量评估，验证多 Agent、多任务下的调度稳定性。

## 7. 学习心得

### 问题 1：怎样证明 Mini-Drop 不是静态页面或 mock 项目？

我的理解是，只做页面展示不够。评审会重点看任务是否真的从 Web 下发到 Agent，采集器是否真的执行，状态是否落库，结果是否能回到前端展示。如果 eBPF、perf、py-spy 只是写死数据，项目很容易被问穿。

我采取的措施是：实现 Agent 心跳和任务领取；用 perf、bpftrace/eBPF、py-spy 做真实采集；每次任务状态迁移都写入数据库；用端到端测试覆盖成功路径和异常路径；演示时展示完整状态流转。

导师反馈说演示重点是端到端效果，可以提一下用了哪些采集器。我的理解是：视频里要先展示完整链路，再解释采集器如何支撑真实性。

### 问题 2：AI 能生成代码，如何判断质量是否达标？

我一开始容易只看页面能不能打开，但后来意识到评审更看重完整度、真实跑通和工程规范。代码如果全挤在 `main.jsx` 或 `server.py`，即使能跑，也不像正式项目。AI 生成结果必须用验收项反向检查。

我采取的措施是：前端使用 React + TypeScript + Router + Zustand 分层；后端使用 FastAPI APIRouter、schemas、dependencies、app factory 分层；补齐 `.editorconfig`、`.env.example`、Makefile、PowerShell 检查脚本、GitHub Actions、MinIO、鉴权和定时任务。

导师反馈中也说技术栈可以自选，重点是完整度、真实跑通和演示效果。根据这个反馈，我把项目从“能跑的简化版”继续补成更工程化的复刻版本。

## 8. 总结

这个项目最大的收获是理解了“性能诊断平台”不是单个采集命令，而是一条工程链路：任务如何创建、如何调度、如何采集、如何落库、如何分析、如何展示、失败时如何解释。

Mini-Drop 的价值在于把这些环节串起来，并让每一步都可以被验证。后续如果继续完善，我会围绕真实跑通、覆盖更多采集能力、解释清楚证据和补齐生产级治理能力继续推进。
