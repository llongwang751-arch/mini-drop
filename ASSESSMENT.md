# Mini-Drop 导师验收说明

## 项目定位

Mini-Drop 是一个面向 Linux 进程的轻量性能诊断平台。用户从 React Web 创建采集任务，FastAPI Server 通过 SQLAlchemy 和 MySQL 持久化并调度任务，Agent 在目标机器执行 `perf`、`bpftrace` 或 `py-spy`，Analyzer 将原始栈转换为火焰图、热点 TopN、内核写入分布和可验证归因，Web 实时展示全过程。

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
