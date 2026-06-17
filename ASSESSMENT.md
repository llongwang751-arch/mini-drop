# Mini-Drop 导师验收说明

## 项目定位

Mini-Drop 是一个面向 Linux 进程的轻量性能诊断平台。用户从 React Web 创建采集任务，FastAPI Server 通过 SQLAlchemy 和 MySQL 持久化并调度任务，Agent 在目标机器执行 `perf`、`bpftrace` 或 `py-spy`，Analyzer 将原始栈转换为火焰图、热点 TopN、内核写入分布和可验证归因，Web 实时展示全过程。

## 我对题目的理解

题目的核心不是复刻完整生产 Drop，而是在有限时间内做出一个可以真实运行、可以解释取舍的 Mini-Drop。我的实现按题目层级推进：

- 基础题：完成 Web、Server、Agent、Analyzer、状态机、心跳审计、测试覆盖。
- 扩展题：完成 Continuous Profiling、真实 eBPF 探针、py-spy 语言级采集。
- 加分项：选择自然语言采集规划，而不是冒充 LLM 智能归因。

我做了合理简化：没有实现公司内部鉴权、COS 对象存储、生产级权限系统和横向扩容；这些属于生产化能力，不影响 Mini-Drop 的核心链路验证。相反，我保留了最关键的真实性：真实 `perf`、真实 `bpftrace`、真实 `py-spy`、真实 MySQL 落库和可复现 Docker Compose。

## 逐项验收矩阵

| 考核项 | 实现与证据 | 结论 |
|---|---|---|
| Web 指定 PID/时长/采样率 | 新建任务弹窗与 `POST /api/tasks` | 通过 |
| Server 下发、Agent 采集、Analyzer 分析 | FastAPI + Agent claim/upload 链路，MySQL 落库 | 通过 |
| 火焰图与专项可视化 | 火焰图、TopN、eBPF 分布、Python 语言栈 | 通过 |
| 严格状态机与 reason | 每次迁移同时写 tasks 与 transitions | 通过 |
| 5 秒心跳、30 秒离线、审计 | Agent 独立心跳线程与离线扫描器 | 通过 |
| 日志、错误、测试 | JSON 日志；显式 FAILED；14 个测试；覆盖率 66% | 通过 |
| Continuous Profiling | 自动切片、任意时间窗口查询、停止续建 | 通过 |
| perf 之外两个采集器 | 真正的 bpftrace 内核探针与 py-spy 语言栈 | 通过 |
| 至少一个加分项 | 自然语言采集规划，拒绝无 PID 的不可验证请求 | 通过 |

## 实机验收证据

- Docker Compose：Server 健康，Agent 在线。
- 真实 perf 任务：`e3b1d7624023`，backend 为 `perf record + perf script`，捕获 147 个样本，`degraded=false`。
- 真实 py-spy 任务：`cabe074141e5`，采到 `<module>` 与 `<genexpr>`，`degraded=false`。
- 真实 eBPF 任务：`be376e59a0a0`，使用 `kprobe:vfs_write`，采到 `dd` 写入分布，`degraded=false`。
- 持续采样：查询得到多个 DONE 切片；停止后任务数量不再增长。
- 自动测试：`14/14` 通过；分支覆盖率 `66%`，高于要求的 `50%`。

## 边界与诚实说明

- 当前“性能归因”是带规则和证据的确定性归因，不宣称是 LLM 智能归因；加分项选择的是自然语言采集。
- `perf` 在 WSL2 中可能出现 `[unknown]` 符号，采样本身是真实执行；生产环境需要符号文件、构建 ID 与 debuginfod。
- 为便于十分钟内复现，Compose 自动启动 MySQL 并由 SQLAlchemy 自动建表。生产化仍应加入 Alembic 迁移、认证 Agent、对象存储、队列和最小 eBPF 权限。
- 演示视频与远程 Git 仓库链接仍需提交者录制和上传，这是代码仓库无法自动生成的外部交付物。

## 我如何评估 AI 产物质量

我没有把 AI 输出当成最终答案，而是按考题逐项验收：

- 看是否满足题目硬要求：状态机、reason、心跳、审计、测试覆盖率、三个端到端场景。
- 看是否真实运行：eBPF 必须能采到 `dd` 写入，perf 和 py-spy 不能只是模拟数据。
- 看是否能复现：`docker compose up --build` 要能启动 React、FastAPI、MySQL 和 Agent。
- 看是否可解释：每个功能都能说清楚为什么做、怎么做、哪里做了简化。
- 看是否有工程痕迹：提交历史按功能演进，不是一口气大提交。

修改提示词的方式也是按问题驱动：先让 AI 对照题目找缺口，再要求补真实采集、补测试、补 Docker 验收、补文档取舍说明。每次修改后都用测试、覆盖率、Docker 和实际采集任务验证结果。
