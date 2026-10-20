# AgentScope 与 Drop 工程边界

本项目采用官方 `agentscope==2.0.10`，不是 LangChain/LangGraph，也不是给原来的单次 HTTP 模型请求换名称。实际执行对象为 `agentscope.agent.Agent`，通过 `DeepSeekChatModel`、消息历史、`Toolkit`、自定义 `ToolBase` 和 middleware 执行模型选择工具、读取观测、继续推理和提交结果的循环。当前版本详情及真实验收见[发布记录](releases/20261020-agentscope.md)。

## 一次调查如何执行

1. React 提交问题、已授权的目标身份及可选源码同意。Go 保存调查，绑定 Agent、PID、UID、启动时间和登记源码版本。
2. 规划 Agent 读取实际采集器/schema，提出采集计划。工程师在页面确认之前不采集。
3. C++ Agent/Helper 完成受控采样，Python Analyzer 解析。诊断 Agent 调用热点、质量、调用链和复测计划工具；Go 验证所有参数与引用。
4. Go 的持久化流程执行复测并创建新的诊断。新的 Agent 调用 `compare_profiles`，重复采样的根因状态仍为 `not_tested`。
5. 已绑定并同意源码处理时，源码 Agent 调用真实 cProfile、读取 SHA 对应的片段、生成精确上下文补丁。root broker 在无网络、无凭据的独立容器运行原测试、冻结契约、严格输出对照和交替 CPU 测量。
6. 模型读取实际结果后提交报告。Go 独立验证源码行、证据引用和 `accepted=true`，再保存结论。补丁只应用于隔离副本，不自动发布原 CRM/Saber 仓库。

## 框架与平台各自负责什么

| 部件 | 职责 |
|---|---|
| AgentScope Python 服务 | 原生 ReAct、模型适配、工具选择、消息历史、框架任务取消；仅持有模型凭据 |
| Go API | 用户权限、同意、任务与 attempt、Analyzer 租约、工具白名单/schema、双重预算、证据及最终报告验收 |
| C++ Agent/Helper | 真实采集和目标身份/命名空间校验，不持有长期数据库或对象存储凭据 |
| Python Analyzer | 独立解析原始制品，保持显式采集/分析状态 |
| 源码测试 broker | 只运行管理员登记的固定版本与测量 preset；按不可变容器 ID 停止自己的沙箱 |
| React | 展示实际行动、观测、失败和关联报告，不显示隐藏思维链或模型密钥 |

没有给框架注册任意 shell、文件系统、网络、SQL 或 MCP 工具。源码工具的宿主执行能力也没有放入框架进程。

## 协议、预算与取消

两个独立 Unix socket 使用 `SO_PEERCRED` 核验 Go UID10001 和 runner UID10002。生产者目录为对应 UID / GID10003、2750；消费者挂载只读。临时 root 初始化容器必须加入 supplementary GID10003，保证在不增加 `CAP_FSETID` 的情况下实际保留 setgid 位。

每次运行的公开 UUID 与私有 capability 分开；capability 仅存在于控制连接和内存，不能交给模型或写入日志。Go 对相同 callback ID / 相同 canonical 请求返回缓存结果，改变内容重复使用同一 ID 会终止运行。

| 阶段 | 总预算 |
|---|---|
| 规划 | 6 次模型 / 10 次工具 / 150 秒 |
| 诊断 | 正常 4 次模型；第 4 次报告无效才允许第 5 次纯格式纠错 / 7 次工具回调，加初始证据共 8 条 / 150 秒 |
| 源码修复 | 8 次模型 / 12 次工具 / 最多 2 次实际沙箱测试 / 420 秒 |

每轮最多两个工具。Go 在实际 provider 调用前预留模型预算，并独立检查工具与领域栅栏。SDK/provider 重试及自主上下文压缩禁用，避免隐形请求。取消、撤权或租约失效最多每 500ms 检查一次，关闭模型/工具连接；源码 broker 发现调用方断链后仅清理已核实 job label 和镜像的不可变容器 ID。恢复从新的有界阶段开始，不保证精确恢复上一 token 的框架内存。

`AI_AGENT_BACKEND=legacy|agentscope` 是显式配置。AgentScope 失败会保留失败状态，不自动回退到另一套实现。

## 如何证明实际执行

规划 trace 的最后一项 `agent_runtime`，以及诊断/源码报告的同名字段，来自控制进程核实的实际 distribution、Agent 类、公开 run UUID、模型/工具计数与耗时。模型自己生成这个字段会被拒绝。每个阶段必须有不同 UUID；配置或镜像里安装 SDK 不能独自证明某条报告使用了它。

测试夹具与真实 DeepSeek 分开记录。框架执行证明不等于模型解释语义正确；受控组件 CPU 收益不代表全应用或生产 P95/P99。AgentScope 也有依赖和常驻内存成本，实测资源见发布记录，不将其表述为零依赖或宣称已完成与 LangGraph 的性能对比。

本地 SDK 回归：`python -m unittest discover -s agent_runtime/tests -v`。完整双 UID、Unix socket 与容器初始化需要 Linux，Windows 跳过项不能当作通过。生产组合使用最后追加的 `deploy/compose.agentscope.yml`；构建用 `agent_runtime/requirements.lock` 的固定版本及 SHA256，始终开启 `--require-hashes`。

实现入口：[runner](../agent_runtime/README.md) · [私有协议](../contracts/agent-runtime-v1.json) · [HTTP/领域契约](CONTRACT.md) · [面试步骤](INTERVIEW_DEMO.md)。
