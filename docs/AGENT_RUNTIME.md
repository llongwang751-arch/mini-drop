# Agent Runtime 设计

## 框架决策

生产运行时使用 `LangChain create_agent + LangGraph`。

选择原因：Mini-Drop 需要可恢复图执行、线程级 Checkpoint、状态持久化、工具循环、失败恢复和后续人工审批扩展。这些是在线诊断运行时问题，而不是单次 Prompt 问题。

- Pi/`pi-agent-core` 更适合作为交互式编码终端与可扩展客户端 Harness；本项目借鉴其 skills、themes、extensions/harness 分层，不把它作为后端诊断编排器。
- DSPy 适合离线定义与优化 LM 程序。后续可用它优化范围排序、假设生成和摘要 Prompt，但优化产物必须重新经过当前评测与策略门禁后才能上线。

这不是纸面选型：`server/app/drop_insight/diagnosis_agent.py` 直接调用 `langchain.agents.create_agent`，并使用 LangGraph `InMemorySaver` 或 PostgreSQL Saver。官方机制对比和“不采用第二套 Runtime”的理由见 [`COMPETITOR_DESIGN_DECISIONS.md`](COMPETITOR_DESIGN_DECISIONS.md)。

## 目录边界

```text
server/app/agent_runtime/
  runtime.py   # 框架身份、版本、能力声明
  harness.py   # 模型输入投影、授权句柄复核
  themes.py    # 版本化行为主题与系统提示
  context.py   # 可信上下文归一化、边界裁剪
  memory.py    # Checkpoint 与上下文窗口策略
  retrieval.py # 本地 Knowledge 目录的确定性混合词法检索

server/app/drop_insight/
  diagnosis_agent.py  # Scope Agent 与 Diagnosis Agent
  lats.py             # UCT/PUCT、价值、回传和冻结回放纯函数
  frozen_replay_showcase.py # 白名单 fixture 到持久 Diagnosis/SSE 的桥
  service.py          # 业务状态、证据、策略与执行边界
```

## Harness

Harness 不提供 shell。它把数据库对象转换成 JSON 原生值，只向 Scope Agent 暴露允许显示的候选字段，并验证模型返回的 `binding_id` 仍属于原候选集合。Diagnosis Agent 只能调用语义工具；服务端再把语义工具映射为实际 Collector。

## Agentic RAG

Diagnosis Planner 会在每个初始计划或重规划轮次读取 `knowledge/catalog.json` 及其关联 Markdown，按中英文 token、中文 n-gram 和 BM25 做确定性检索。无需向量数据库或第二套 Agent Runtime。Top-K 结果包含知识 ID、标题、源文件、正文 SHA-256、匹配词、所需证据、限制和短摘录，并进入 LangGraph 的只读规划上下文。

检索结果会以 `planner.knowledge_retrieved` 事件按 Diagnosis 隔离持久化，`GET /api/v2/diagnoses/{id}/retrievals` 可回放当时输入。事件和响应都显式标记 `KNOWLEDGE_PRIOR_ONLY` / `is_evidence=false`：知识只能提示“下一步采什么”，不能生成 `DropInsightEvidence`，也不能替代本次 Task/Artifact 事实。原始用户 query 留在审计轨迹中，但不会被提升到 system prompt 的可信知识块；Markdown 中的指令性文字同样不具备控制权。

Web 的 Agent 驾驶舱会把上述检索轨迹单独放在 RAG 弹窗中，展示 query、来源文件、正文 SHA-256、匹配信息、证据要求与限制。RAG 命中数与 Evidence 数分开统计，避免把“文档里说可能如此”误画成“本次事故已经证明”。

## Skill 渐进式披露

Skill 检索与 Knowledge RAG 是两条相邻但不同的路线先验。Skill 候选阶段只扫描 13 条轻量元数据，使用进程内 BM25、确定性哈希 n-gram/领域概念特征和结构门禁；它不连接 Elasticsearch，也不连接向量数据库。Planner 初始类别是可纠正 Prior，强文本信号可以跨类别找回正确 Skill，但环境、目标运行时、子系统和可用 Collector 仍是一票否决。

命中仓库内置 Skill 后才按需读取完整 `SKILL.md`。加载器固定根目录、限制大小、验证 UTF-8、目录名、章节合同和 SHA-256；完整正文随 `active_skill.skill_instructions` 进入 LangGraph 与 legacy Agent 的每次 proposal。提示词明确规定它只是经过评审的调查路线，优先级低于 Harness 和服务端策略，也不属于 Evidence。未命中、歧义、完整性失败或能力不匹配时，不把文档正文放进模型上下文。

同一 activation 的当前步骤和完整正文在初始规划、用户反馈、人工干预、可信反证和证据不足重规划中持续存在。反证/改向可以切换 Skill；路线耗尽时仍给模型停止与证伪条件，但 `selected_tool=null`，不能重复调用旧探针。每轮决策以紧凑 `skill.route_*` 事件进入业务事件流，完整 Markdown 不发送到浏览器。

## Themes

Theme 是版本化行为合同，不是颜色主题：

- `safe-autonomous-scope-v1`：只选已签发目标，优先精确语义与采集能力匹配。
- `evidence-first-diagnosis-v1`：先提出可证伪假设，再取证；禁止把采集失败当反证。

Theme 变更必须升级版本并通过测试，避免历史 Checkpoint 在无标记情况下改变含义。

## 上下文与记忆

记忆分四层，其中只有第一层属于当前事故的权威事实：

1. 业务状态：诊断、假设、工具调用、Evidence、报告，存 PostgreSQL，是事实来源。
2. 线程短期记忆：LangGraph Checkpoint，按 diagnosis ID 隔离；超出窗口后摘要并保留最近消息。
3. 用户显式偏好记忆：按认证 principal 隔离并存 PostgreSQL，目前只允许语言、解释详略、时区和“低风险工具优先”四类白名单字段；它是可删除的提示上下文，不能保存查询、PID、旧根因、工具权限或目标绑定。
4. 长期路线记忆：通过验证并发布的 Skill；它只影响检索和探针顺序，不携带旧案例结论。

Knowledge/RAG 不属于上述四类记忆：它是版本化仓库材料的按需读取。Skill 记录“验证过的路线”，Knowledge 解释“某类现象通常需要哪些证据”，两者都不能成为当前事故事实。

默认窗口由 `MINI_DROP_AGENT_MEMORY_MAX_MESSAGES`、`MINI_DROP_AGENT_MEMORY_MAX_TOKENS`、`MINI_DROP_AGENT_MEMORY_KEEP_TOKENS` 控制。范围发现重试由 `MINI_DROP_AUTO_SCOPE_RETRY_SEC` 限频。

上下文压缩只处理模型消息，不删除业务事实。重启后应从 PostgreSQL 的诊断、树事件、Evidence、报告和 Checkpoint 恢复；如果生产环境把 Checkpoint 配成内存模式，模型短期消息会丢，但已落库的证据链不能跟着消失。

用户偏好不会从每轮对话自动抽取。只有用户在“记忆”面板显式保存后才写入，服务端仍会再次做字段和值域校验；加载失败时返回空偏好并继续确定性诊断。这样解决跨诊断的表达偏好复用，同时避免把一次故障描述或错误根因永久化。

## Checkpoint 真实健康状态

`GET /agent-runtime/status` 是 diagnosis-worker 的内部 RPC 读接口。它区分配置期望的 `requested_backend` 与当前真正承载消息的 `actual_backend`，并返回 `HEALTHY`/`DEGRADED`、PostgreSQL checkpoint 表初始化状态和是否能跨 Worker 重启恢复。

生产配置请求 PostgreSQL、但连接或 `PostgresSaver.setup()` 失败时，Worker 为了保留业务服务会降级到进程内存，不会因 Checkpoint 故障让整个诊断服务无法启动；状态必须同时标为 `DEGRADED`，提案记录的也是实际 `memory` 后端。返回值和日志只记录异常类型与固定提示，禁止暴露 DSN、密码或数据库驱动的原始异常消息。

页面读取并展示 `requested_backend`、`actual_backend`、Checkpoint 表初始化结果、线程 ID 与是否支持跨重启恢复。只有实际后端满足持久化条件时才能显示健康；配置文本写着 PostgreSQL 但实际落到内存时，必须显示 `DEGRADED`，不能用期望值覆盖事实。

## 页面上的 Runtime 投影

`AgentCockpit` 不是另一套 Runtime，而是对当前 Diagnosis 状态的可审计投影：

| 入口 | 展示什么 | 不代表什么 |
|---|---|---|
| 阶段/轮次 | 当前状态机阶段和真实轮次 | 不代表所有步骤都由 LLM 决定 |
| 计划 | 当前假设、下一步和重规划轨迹 | 不开放任意计划执行 |
| RAG | 知识来源、hash、检索轨迹 | 不计入当前事故 Evidence |
| Skill 路线 | 召回、完整正文加载、类别纠偏、跨轮沿用与退出 | 不修改 LATS 评分，不计入 Evidence |
| 工具 | 申请、审批、执行与结果 | 不提供 shell；Agent/PID 不可编辑 |
| Evidence | 通过门禁的证据和报告引用 | 上传成功不自动等于证据有效 |
| 记忆 | 当前线程、领域事件、Checkpoint 状态和用户显式偏好 | 偏好不能授予工具权限；Checkpoint 降级到内存时不承诺短期消息跨重启恢复 |
| 评测 | 已存在的反馈、门禁或路线对照 | 不伪造未运行的线上指标 |
| LATS 搜索 | 阶段、选择分解、最佳路径、预算、停止和环境语义 | 分数不是 Evidence；LIVE 不代表环境可回退 |

工作台默认把对话作为单一主视图，探索树可以切为全宽、全屏或显式分屏。对话按领域事件逐轮重建，底部 Composer 发出的四类动作仍进入同一 Diagnosis 线程；它们经过业务状态机后才影响计划和工具，而不是直接拼接到一个无约束的大模型聊天上下文。

## 尚未实现的 Runtime 能力

- 没有可任意调用第三方工具、浏览互联网或执行 shell 的通用 Agent。
- 没有独立持久化的通用 Chat Message/Assistant Message 模型；当前消息是诊断领域事件的逐轮投影。
- 没有在线自动改 Prompt、代码或权限的自修改机制。
- 已有服务端持久化的 Skill 随机实验平台，但只有被人工或受控 Oracle 标注的样本才进入根因准确率；它自动计算快照和发布建议，不自动修改 Prompt、代码、权限或生产策略。
- 没有在 Checkpoint 降级为内存时伪装成“会话全局记忆”；长期复用只来自通过门禁发布的 Skill。

## LATS 在 Runtime 里的位置

LATS 不是第二套 Agent 框架，也不绕开 LangGraph。LangGraph 继续负责线程、节点调度、Checkpoint 和人工恢复；`drop_insight/lats.py` 提供纯函数搜索原语，`service.py` 仍负责目标绑定、Policy、预算、真实 Tool Call、Evidence Gate 和持久化事件。这样重启后可以从领域事件折叠出搜索状态，而不是依赖某个 Python 进程内的树对象。

持久事件合同为：

```text
lats.search_started
lats.candidates_expanded
lats.candidates_evaluated
lats.node_selected
lats.action_proposed
lats.awaiting_approval
lats.action_blocked
lats.simulation_started
lats.action_dispatched
lats.observation_recorded
lats.reflection_recorded
lats.backpropagated
lats.node_pruned
lats.search_terminated
```

公开 `tree.search` 投影包含 `algorithm`、`algorithm_version`、`phase`、`iteration`、`selected_node_id`、`latest_selection`、`best_path_node_ids`、`latest_path_node_ids`、`terminated_path_node_ids`、`termination`、环境语义和预算；每个 hypothesis 节点的 `search_metrics` 才承载 Visits、Value、Prior、分数、奖励、剪枝、观察和反思。只有相应服务端事件存在时这些值才有历史含义；页面不得用终态报告反推缺失的搜索过程。页面七槽把审批、阻断、模拟开始和派发等细事件合并进“行动”，不是丢弃这些持久记录。`best_path` 按累计 Mean/Q 选择，`latest_path` 只是最近回传路径，`terminated_path` 只是触发停止的路径，三者不能混写。

Hypothesis 上的 `round_index` 是不可变的节点出生深度，公开为 `tree_depth`；真实执行轮次以 `lats.node_selected.iteration` 为权威。服务端把有 Report 的 hypothesis 映射到其选择 iteration，用于最少诊断轮次门禁、下一轮预算和页面分轮投影；没有 LATS 事件的旧数据才回退出生深度。这个分离允许全局 UCT 合法回溯旧 sibling，同时避免实际第 3 次取证被误算成第 1/2 轮。

### 原论文与工程实现的对应

| LATS 概念 | Runtime 落点 | 安全边界 |
|---|---|---|
| Selection | 默认 UCT、可选 PUCT 扩展 + `lats.node_selected` | 只在门禁后的候选内选，不产生 shell |
| Expansion | 模型候选去重、Top-K、UNKNOWN 哨兵 | 模型先验不是 Evidence |
| Evaluation | `initial_value`、`prior` 及其 source | 缺失时使用确定性 fallback，并明确来源 |
| Simulation | 冻结回放，或被选分支的一次真实工具观察 | LIVE 不承诺环境回退 |
| Backpropagation | path 上累加 `visits/value_sum/mean_value` | 奖励来自工具结果和 Evidence Gate |
| Reflection | `decision/summary` 持久事件 | 失败不自动等于反证，反思不能发明事实 |

默认预算合同是 `top_k=3`、`selection_policy=UCT`、`value_lambda=0.5`、`max_iterations=6`、`max_tool_calls=12`、探索常数 `sqrt(2)`；所有值仍受诊断 Budget 和 Tool Policy 的更严格上限控制。工程实现默认保持论文 UCT，也允许显式 PUCT Prior 扩展；只有服务端独立样本才能形成 SC，没有样本就记录 `SC=null`，不会把模型自报的一致性或排名 fallback 冒充 SC。面试时应说“以 LATS 为搜索思想、按生产工具成本做了预算化适配”，并根据页面记录区分 `FULL_LATS` 与 `BUDGETED_LATS`。

当前实时生产编排不会向 `execution_semantics` 提交 `frozen_observations=True` 或 `controlled_reset=True`，所以普通 `REPLAY`、`REPRODUCTION` 与 LIVE 会话都诚实落在 `BUDGETED_LATS`。这条路径允许渐进扩展和缓存观察，但不会把已经推进时间的真实环境伪装成可逆 MCTS；已执行节点也不会被当成可零成本恢复的 sibling rollout 反复利用。

仓库已经有一条真正执行的严格冻结路径，不再只是语义枚举：

```text
allow-listed fixture
  -> FrozenReplayObservationProvider（防御性 JSON 副本 + snapshot_digest）
  -> run_frozen_replay_lats（每条 sibling rollout 前 reset + verify）
  -> node_namespace=diagnosis_id（节点/事件/effect key 会话隔离）
  -> lats.replay_snapshot_frozen + lats.* 持久事件
  -> exploration-tree/search 投影
  -> 现有 Outbox/SSE 逐帧刷新页面
```

`frozen_replay_showcase.py` 先把完整 manifest 和 hash 落为 `lats.replay_snapshot_frozen`，随后一次推进一个可见 frame。进程重启后它只从数据库中的快照事件和已存在 effect key 恢复，不依赖上一次 Python 内存。相同 `client_run_id` 对同一用户和场景幂等，不同运行使用不同 Diagnosis/namespace；同一 fixture 的 snapshot digest 保持稳定，便于证明兄弟 rollout 的起点相同。

冻结运行的 budget 必须把 `max_simulations` 与 `max_tool_calls` 分开：前者统计读取冻结 observation 的 rollout，后者固定为 0。它不会创建 Tool Call、Task、Artifact、Evidence 或 Report；回放中的 `evidence_refs` 是 fixture 内的观察引用，不是本次生产 Evidence。只有未来实现并审计真实环境的逐 rollout 重置器后，受控 `REPRODUCTION` 才能合法升级为另一种 `FULL_LATS`。
