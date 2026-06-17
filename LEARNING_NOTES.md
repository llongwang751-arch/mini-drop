# Mini-Drop 学习笔记与复盘

## 1. 我对项目的理解

Mini-Drop 的目标不是完整复刻公司内部生产级 Drop，而是在有限时间内做出一个可运行、可验证、可解释取舍的性能诊断系统。

核心链路是：

```text
Web 创建任务 -> Server 持久化和调度 -> Agent 采集 -> Analyzer 分析 -> Web 展示
```

这个链路解决的是后台开发和 SRE 排查性能问题时的手工成本：以前需要 SSH 到机器上执行 `perf`、`bpftrace`、`py-spy` 等工具，现在可以通过 Web 统一下发任务、追踪状态和查看结果。

## 2. 学习内容

### 2.1 Linux 性能分析

学习重点：

- 进程 PID、CPU ticks、RSS、I/O 统计
- `/proc/<pid>/stat`、`/proc/<pid>/status`、`/proc/<pid>/io`
- CPU 采样和调用栈
- 火焰图用于观察热点函数

项目落地：

- Agent 采集 CPU、内存 RSS 和 I/O 数据
- Analyzer 将栈数据转成火焰图和 TopN 热点函数

### 2.2 perf

学习重点：

- `perf record` 用于采集 CPU 栈样本
- `perf script` 用于解析采样结果
- 在 WSL2 或容器环境中可能出现 `[unknown]` 符号，需要符号表和 debuginfo 才能进一步解析

项目落地：

- perf 采集器执行真实 `perf record + perf script`
- 采集结果带有 `backend` 和 `degraded` 字段，方便判断是否真实采样或发生降级

### 2.3 eBPF / bpftrace

学习重点：

- eBPF 可以在内核态挂探针，观察系统调用和内核函数
- bpftrace 是 eBPF 的高层脚本工具，适合快速实现探针验证
- 容器中运行 eBPF 通常需要特权权限

项目落地：

- eBPF 采集器使用 `bpftrace` 挂载 `kprobe:vfs_write`
- 演示时通过 `dd` 制造写入，Web 展示写入进程分布

### 2.4 py-spy

学习重点：

- py-spy 可以不侵入 Python 程序采集语言级调用栈
- 它和 perf 的语义不同，更贴近 Python 开发者看到的函数调用

项目落地：

- py-spy 采集器输出 Python 调用栈
- Web 为 py-spy 单独展示“Python 语言调用栈”视图

### 2.5 后端工程

学习重点：

- FastAPI 提供 REST API、参数校验和 OpenAPI 文档
- SQLAlchemy 管理数据库模型和事务
- MySQL 保存任务、状态迁移、Agent 和审计日志
- 状态机需要约束非法迁移，避免任务状态混乱

项目落地：

- Server 使用 FastAPI + SQLAlchemy + MySQL
- `tasks` 保存任务当前状态
- `transitions` 保存每次状态迁移和 reason
- `agents` 保存心跳状态
- `audit` 保存上线、离线和恢复事件

### 2.6 前端工程

学习重点：

- React 适合把任务列表、详情、Agent、审计日志拆成组件
- Vite 负责构建前端静态资源
- 控制台类页面要优先保证信息密度、可读性和状态反馈

项目落地：

- 前端使用 React + Vite
- 页面每 2 秒轮询刷新
- 展示任务状态、火焰图、TopN、eBPF 分布、持续采样时间轴

### 2.7 AI 协作与质量验证

学习重点：

- AI 可以加速实现，但不能替代对题目的理解
- 判断 AI 产物质量不能只看页面是否好看，而要看是否满足题目验收项
- 每次 AI 修改后都要用测试和真实运行验证

项目落地：

- 用测试验证状态机、审计、采集器解析、自然语言规划和端到端链路
- 用 Docker Compose 验证 React、FastAPI、MySQL、Agent 能一起启动
- 用真实 perf、bpftrace、py-spy 任务验证采集器不是模拟数据

## 3. 实现取舍

保留的核心能力：

- Web 下发任务
- Server 调度任务
- Agent 真实采集
- Analyzer 生成结果
- MySQL 持久化状态
- 状态机和 reason
- 心跳和审计
- perf、eBPF、py-spy
- Continuous Profiling
- 自然语言采集

合理简化的生产能力：

- 未实现公司内部鉴权和多租户
- 未接 COS / MinIO 对象存储
- 未引入消息队列
- 未做长期数据保留和清理策略
- 未做生产级 eBPF 权限收敛
- 未做完整 LLM 智能归因

这些简化不影响 Mini-Drop 的核心验证目标，但在设计文档中已经列为后续演进方向。

## 4. 测试与验证

当前验证方式：

- 单元测试和端到端测试：`14/14`
- 覆盖率：`66%`
- Docker Compose：MySQL、FastAPI Server、Agent 健康
- 页面：`http://localhost:8080`
- API 文档：`http://localhost:8080/docs`
- MySQL 表：`agents`、`tasks`、`transitions`、`audit`
- 真实采集：perf、bpftrace/eBPF、py-spy

## 5. 后续学习计划

如果继续完善，我会按这个顺序学习和改进：

1. Alembic 数据库迁移，替代自动建表。
2. MinIO / COS 对象存储，把原始采集文件从 MySQL 中迁出。
3. Celery / Redis / Kafka 等异步队列，让 Analyzer 独立运行。
4. Agent 身份认证和 TLS，避免任意 Agent 接入。
5. eBPF 权限最小化，替代 `privileged: true`。
6. perf 符号解析、debuginfo 和 debuginfod，提高火焰图可读性。
7. LLM 工具调用归因，让模型只能基于结构化证据输出结论。
8. 压测和容量评估，验证多 Agent、多任务下的调度稳定性。

## 6. 复盘

这个项目最大的收获是理解“性能诊断平台”不是单个采集命令，而是一条工程链路：任务如何创建、如何调度、如何采集、如何落库、如何分析、如何展示、失败时如何解释。

Mini-Drop 的价值在于把这些环节串起来，并让每一步都可以被验证。即使做了合理简化，也要说清楚为什么简化、简化了什么、未来如何补齐。
