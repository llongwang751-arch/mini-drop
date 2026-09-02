# Mini-Drop 诊断三层边界

## 1. Drop：探针与采集底座

主链为 `React -> Go API -> C++ Control -> C++ Agent -> MinIO/PostgreSQL -> Python Analyzer`。

- Go API 接收 HTTP 请求，通过私有 gRPC 调用诊断 Worker，并用 SSE 向页面推送状态。
- C++ Control 维护 Agent 注册、心跳和任务下发。
- C++ Agent 在目标机执行白名单 Collector，采集前复核进程身份、权限、能力和资源限制。
- MinIO 保存原始文件和派生文件；PostgreSQL 保存 Task、Attempt、Artifact、AnalysisJob、状态、哈希和对象键。
- Analyzer 生成火焰图、TopN 和版本化结构结果。

这一层产出 Artifact。Task 成功只表示采集动作完成，Artifact 经过目标、时间窗、完整性、Schema、Analyzer 和样本质量校验后才可成为 Evidence。

## 2. Diagnosis Agent：受控调查

诊断 Worker 使用 `LangChain create_agent`，底层运行时为 LangGraph，模型适配器为 `ChatDeepSeek`。框架负责模型与工具循环、消息状态和 checkpoint；Mini-Drop 继续掌握目标绑定、策略、执行和证据裁决。

### 上下文

每次模型调用都由服务端注入可信上下文：

- 已绑定的 Agent、PID、进程启动时间、服务和时间窗；
- 规则分类与基线计划；
- 已有假设和 Evidence 摘要；
- 当前工具白名单；
- 已发布 Skill 的路线先验；
- 人工纠错和历史成功路线。

用户问题单独放在不可信输入区，不能覆盖系统约束。模型不能修改目标范围，也看不到 Shell 或任意命令执行工具。

### 短期记忆

LangGraph 以 `diagnosis_id` 作为 `thread_id`，用 PostgreSQL Checkpointer 保存消息和工具调用。Worker 重启后可以从 checkpoint 继续同一诊断线程。消息超过配置上限后由 Summarization Middleware 压缩，默认触发值为 24 条。

业务状态仍以 PostgreSQL 中的 Session、Hypothesis、ToolCall、Task、Evidence 和 Event 为事实来源。LangGraph checkpoint 保存模型线程状态，不能覆盖业务状态机。

### 工具调用

模型只有一个工具：`request_diagnostic_probe`。它只提交：

- 简短决策摘要；
- 一个服务端允许的探针名；
- 一至三个可验证假设，每条包含支持条件和反证条件。

工具调用通过 Pydantic Schema 和服务端 allowlist 双重校验。采集失败、权限不足、Agent 离线和超时只能标记为 `UNKNOWN`，不能作为反证。

工具请求返回后仍需经过目标绑定、Agent capability、参数 Schema、风险、预算、并发和人工审批门禁。审批通过后 Go API 才创建 Task，由 C++ Agent 执行。

### 异步恢复

模型回合和采集任务分开持久化。待审批 ToolCall、Task、Attempt 和 Evidence 都在业务数据库中，页面断线或 Worker 重启不会丢失。SSE 只负责展示；后台状态依靠数据库、租约和幂等键推进。

模型调用失败、输出不合规或 Provider 不可用时，本轮回退确定性规则计划，并且不再补发第二次旧式模型请求，避免重复扣费和重复动作。

## 3. Skill Memory：版本化路线记忆

Skill 是跨诊断线程的长期策略记忆。它保存：

- 适用故障类别、环境和服务；
- 真实执行过的工具顺序；
- 必需证据；
- 被有效反证否定的分支和停止条件；
- 来源诊断、版本、评测结果和激活反馈。

生命周期为：

`VERIFIED 报告 -> CANDIDATE -> 正例/误导反例/环境漂移评测 -> ACTIVE -> 激活反馈 -> QUARANTINED/ROLLBACK`

新诊断先对 ACTIVE Skill 做类别、环境、工具能力等硬过滤，再用结构化字段、BM25 和确定性特征向量排序。系统把通过门槛的 Skill 注入 Agent 上下文，Agent 只复用下一步取证路线；本次根因仍需重新采集和验证。出现环境冲突、工具不可用或有效反证时退出 Skill，回到规则基线。

## 可核验运行记录

2026-09-02 本机容器链路完成一次真实验证：

- 诊断：`insight_8cbd699439f84dfd9d21124932ecba18`
- Planner：`LANGGRAPH_AGENT / diagnosis-agent-v1`
- Checkpoint：PostgreSQL，共创建 5 条初始 checkpoint；
- ToolCall：`start_pyspy_profile`，策略结果 `REQUIRE_APPROVAL`；
- Task：`task_20260901_181801_1a6fef`；
- 采样：1476 个 py-spy 样本；
- Analyzer：识别 `_run` 占 90.4%，两份结构化 Evidence 为 `HIGH / ACCEPT_SUPPORT`；
- 报告：置信分 0.73，验证状态 `PARTIAL_WITHOUT_COUNTER`。

这次记录证明 Agent、审批、C++ 采集、Analyzer、Evidence 和报告引用已经串通。因为缺少独立反证或恢复对照，系统没有把报告标为完全验证。

## 指标边界

冻结 Skill 基准包含 15 个路由与生命周期 Case：通用初筛基线通过 `6/15=40%`，启用 Skill 后通过 `15/15=100%`，提升 `60` 个百分点。该结果衡量离线路由和生命周期契约，不是生产根因准确率，也没有测量真实诊断耗时。

真实生产结论仍需同一 Linux Campaign 上的随机交错 A/B，并报告根因准确率、Recall@K、MRR、拒绝率、误激活率、负迁移率、真实工具调用数和 P50/P95 耗时。
