# 竞品机制如何驱动 Mini-Drop 的设计

> 本文不是“功能打勾表”。它回答三个问题：成熟方案怎么做、这个机制在 Mini-Drop 的约束下哪里不够、最终哪段代码和哪项测试落实了不同选择。

## 1. 需求证据从哪里来

本轮先读了资料目录中的会议纪要和答辩复盘，再查官方资料。会议里反复出现的可验收问题是：指定进程最长 24 小时采样、原始上传和预聚合的选择、受限容器里的 C/C++ 用户态采样、调用图、Linux 权限/内核兼容、复杂 AI 诊断测试集，以及“竞品信息必须成为设计决策依据”。会议纪要是需求输入，不是产品事实；产品事实仍以当前源码、契约和测试为准。

外部机制依据只采用项目或内核官方资料：

- [Linux perf security](https://docs.kernel.org/admin-guide/perf-security.html)：`perf_event_paranoid`、`CAP_PERFMON` 和资源边界。
- [gperftools CPU profiler](https://gperftools.github.io/gperftools/cpuprofile.html)：`CPUPROFILE`、`CPUPROFILESIGNAL` 与显式应用接入。
- [Grafana Pyroscope](https://grafana.com/docs/pyroscope/latest/)：连续 Profile 的时间维度、元数据和查询模型。
- [Parca overview](https://www.parca.dev/docs/overview/)：持续采样、标签化 Profile 序列、按时间/版本比较。
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview) 与 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：持久执行、Checkpoint、恢复和人工介入。
- [HolmesGPT](https://github.com/HolmesGPT/holmesgpt)：SRE Agent 的只读取证默认边界；写操作由独立、显式启用的 remediation 工具承担。
- [Pi agenticoding](https://pi.dev/packages/pi-agenticoding)：编码 Agent 的 context boundary、notebook、handoff 与可复用工作流。

## 2. 决策一：持续采集保存可重算窗口，不保存一个无限增长的大文件

### 竞品机制

Pyroscope 和 Parca 都把 Profile 视为带时间与标签的序列，因此能够查询历史区间、比较时间段或版本。它们面向常驻、可查询的持续 Profiling 后端。

### Mini-Drop 的约束与选择

Mini-Drop 是任务/证据系统，不是要重造一个 Profile 时序数据库。一个最长 24 小时的单文件会放大失败重试、上传、内存和保留成本。因此这里采用：

1. 对服务端签发的单一 PID binding 分段执行 `perf record`；
2. 每个窗口保存原始 `perf.data`，让 Analyzer 后置重算；
3. 以 `continuous-summary.json` 保存窗口时间、大小、配额和保留层级；
4. 到达输出配额就安全停止，并明确标记 `quota_reached`；
5. CPU 哨兵连续达到阈值后才启动深采样。

这比“采集端先折叠后只上传聚合结果”占用更多存储，但保留了改变符号、过滤和视图后的重算能力。合成聚合实验显示重复栈折叠可以显著缩小运输体积，但该实验只比较编码大小，不冒充生产 CPU 开销证明。报告在 `web/public/report-assets/profiling/aggregation-report.json`。

代码：`native/agent/src/proc_collectors.cpp`、`server/app/analyzer_runner.py`、`contracts/taskkinds.json`。测试：`tests/test_continuous_bundle_analyzer.py`、`tests/test_profile_aggregation_benchmark.py`。

## 3. 决策二：受限 C/C++ 环境使用显式 opt-in，不伪装成“零权限通用附加”

### 竞品机制

Linux 内核明确把 Perf 采样视为可能泄露敏感数据的能力；推荐最小权限的 `CAP_PERFMON`，不应为了方便一律授予 `CAP_SYS_ADMIN`。gperftools 的官方路径则要求应用链接或预加载 `libprofiler`，并通过环境变量/信号开始和结束 Profile。

### Mini-Drop 的选择

- 正常路径继续使用 `perf`，由能力矩阵解释 `perf_event_paranoid`、身份和 capability，而不是只检查二进制是否存在。
- 受限 C/C++ 场景提供独立 `mini-drop-gperftools-bridge`：只允许同 UID 目标，默认拒绝 root，不使用 ptrace、perf、sudo 或 capability；应用必须显式配置 `CPUPROFILE` 和 `CPUPROFILESIGNAL`。
- 若两条路径都不满足条件，降级到 `/proc` 系统指标并标记能力不足，不能把降级数据宣称为函数级 Profile。

它不是“免改任何运行条件”：至少需要重启应用并预加载/链接 libprofiler。这个限制被保留在 README 和兼容矩阵里，避免演示时把 opt-in 说成通用透明附加。

代码：`native/gperftools_bridge/`、`server/app/kernel_compatibility.py`、`scripts/check_worker_compatibility.py`。测试：`tests/test_kernel_compatibility.py`。

## 4. 决策三：火焰图回答占比，调用图回答关系

火焰图把相同调用栈聚合，适合回答“时间主要花在哪里”；导师要求的调用图更适合回答“谁调用了谁、一个热点由哪些入口汇入”。两者共享同一份折叠栈，避免重复采集。

Analyzer 从 root-to-leaf 栈生成有向边、inclusive/self samples，并对节点数设上限。Web 用可拖拽 Graph 图展示 caller/callee，窗口 Profile 也可以逐段查看。代码：`analyzer/mini_drop_analyzer/hotmethod_analyzer.py`、`web/src/components/CallGraphViewer.jsx`。测试：`tests/test_perf_callgraph.py` 和 Web 构建测试。

## 5. 决策四：AI 可以自主选范围，但不能自主创造权限

HolmesGPT 的产品边界给出的重要启发不是“让模型拥有更多命令”，而是只读取证与写操作分离。Mini-Drop 的诊断对象是 Linux 进程，采样比查日志更敏感，所以采用更窄的语义工具：

```text
在线 Agent + 新鲜进程快照
  -> 服务端签发短时 opaque binding
  -> Scope Agent 只能从候选 binding 中选一个
  -> Diagnosis Agent 只能从 allowed_tools 中选一个探针
  -> Policy + Budget + Attempt authority 再校验
  -> Task / Artifact / AnalysisJob
  -> Evidence 门禁
  -> 支持证据与反证共同决定报告
```

模型不接收通用 shell，不得直接填写任意 PID，也不能把采集失败当成反证。自主模式减少用户手填范围，权限模型并没有放宽。动态探索树由真实事件重建；假设被反证或用户纠正时创建分支、剪枝并增加树版本，而不是刷新一张预制图片。

代码：`server/app/drop_insight/diagnosis_agent.py`、`service.py`、`policy.py`、`exploration_tree.py`、`tools.py`。测试：`tests/test_auto_scope_selection.py`、`tests/test_exploration_tree.py`、`tests/test_drop_insight_policy_evidence.py`。

## 6. 决策五：Runtime 用 LangGraph，领域状态和授权仍归 Mini-Drop

LangGraph 官方把 durable execution、Checkpoint、人工介入和恢复作为核心能力，正适合多轮诊断。当前代码已经真实使用 `langchain.agents.create_agent`、LangGraph Checkpointer 和 PostgreSQL Checkpoint，不是仅在文档里挂框架名。

选择边界如下：

- LangGraph 管模型/工具循环、线程 Checkpoint、恢复与短期消息记忆；
- Mini-Drop 管业务状态机、目标授权、预算、Artifact/Evidence 和 Skill 发布；
- Pi 的 notebook/handoff/context-boundary 适合交互式编码 Agent。Mini-Drop 借鉴它“只携带已确认决策”的上下文思想，但不把编码 Harness 当成线上 AIOps Runtime；
- 不额外引入另一套所谓 DSH 框架，因为当前需求没有定义它的准确项目与可替换接口，增加第二个 Runtime 只会制造双状态源。

## 7. 决策六：Skill 是路线记忆，必须允许拒绝复用

Skill 只携带触发条件、探针顺序、预期观察和停止/反证条件。生产选择器做 BM25、概念向量和结构条件混合排序，再经过歧义、能力、环境与冲突门禁；命中后依旧重新取证。

新评测集没有读取旧 Case 文件。生成器固定种子生成 540 个新提示词 Case，分为 360 个正向复用、90 个含误导/信息不足的拒绝场景、90 个能力/环境漂移场景；公开 query 与私有 oracle 分离。测试分类和正确 Skill 来自现有 Skill catalog，因此它是“基于项目分类体系的新提示词盲测”，不是与项目知识完全独立的外部评测。当前结果为：

- Skill 路线选择准确率：`506/540 = 93.7037%`；
- 正向路线复用率：`334/360 = 92.7778%`；
- 负向安全拒绝率：`172/180 = 95.5556%`；
- 错误激活率：`8/180 = 4.4444%`。

这组数只证明“路线选择与拒绝门禁”，不证明真实 Linux 故障根因准确率或耗时。真实效果必须运行 `scripts/run_live_skill_ab_campaign.py`，用同一问题对比 `DISABLED` 与 `AUTO`，并保存服务端签发的 Tool Call、Evidence、Report、Skill activation 与树版本。

代码：`server/app/drop_insight/skill_evolution.py`、`benchmark_v2.py`、`scripts/generate_diagnosis_benchmark_v2.py`。测试：`tests/test_diagnosis_benchmark_v2.py`。

## 8. 现在能说什么，不能说什么

可以说：代码层已实现自主安全选范围、动态树、Skill 混合检索/拒绝、窗口化持续采集、调用图、兼容矩阵和 C++ opt-in bridge；新 540-case 路线评测可复现；真实多云与 Skill A/B 都有保存服务端 ID 链路的验收脚本。

不能说：当前 Windows 测试等于 Linux C++ 真机验收；合成编码缩减率等于生产开销；Skill 命中等于根因；没有最新服务端 Campaign 报告就声称多云线上验收已经完成。

## 9. 决策七：借鉴 LATS，但不把不可回滚的线上环境包装成完整 MCTS

### 一手依据

- [Language Agent Tree Search 论文（PMLR 235）](https://proceedings.mlr.press/v235/zhou24r.html)：Selection、Expansion、Evaluation、Simulation、Backpropagation、Reflection 六阶段，以及 UCT、语言模型价值和自一致性组合。
- [作者官方实现](https://github.com/lapisrocks/LanguageAgentTreeSearch)：论文任务上的树搜索代码与复现实验入口。

原始 LATS 面向可以重放或恢复状态的任务环境。性能事故的真实工具昂贵且会推进时间：一次 perf、py-spy、eBPF 或数据库探针完成后，后续兄弟分支面对的负载与时间窗可能已经变化。直接照抄“每个候选都 rollout 到终局”既昂贵，也会制造错误的可比性。

论文的六个操作在 Mini-Drop 中按下面的证据边界实现。页面把 Simulation 拆成“行动/观察”两个可读阶段，但算法仍是六操作：

| 原论文操作 | Mini-Drop 落点 | 不允许的捷径 |
|---|---|---|
| Selection | 默认 canonical UCT；显式配置时使用 LATS-PUCT 扩展 | 只按模型置信度选一次 |
| Expansion | 每轮 Top-K 兄弟候选、去重与开放世界 sentinel | 永远只建一个假设 |
| Evaluation | LM value；有独立采样才组合 SC；否则明确 fallback | 用模型自报 SC 或 rank 冒充多采样一致率 |
| Simulation | 冻结 provider rollout，或 LIVE 中被选分支的一次真实工具观察 | 把未执行兄弟标成已观测 |
| Backpropagation | 沿实际 path 更新 Visits、Value Sum、Mean/Q | 把终态报告置信度倒填整棵树 |
| Reflection | 由观察和 Evidence Gate 产生，持久化后进入下一次扩展 | 反思发明新事实或不影响后续搜索 |

Mini-Drop 因此做了三项取舍：

1. 默认按论文采用 UCT，把历史平均奖励和探索奖励分开记录；需要利用候选 Prior 时才显式启用 PUCT 扩展。
2. LIVE 只执行被选中的一个真实工具步骤，缓存观察并渐进扩展；未执行候选不获得 Evidence 奖励。
3. 只有接入冻结观察提供者的 `REPLAY`，或有重置证明的受控 `REPRODUCTION`，才标记 `FULL_LATS`；只有 mode 标签、没有环境证明时仍标记 `BUDGETED_LATS`，并在页面提示不可严格回滚。

价值评估遵循论文的组合形式 `V = lambda * V_LM + (1-lambda) * SC`，但 `SC` 必须来自服务端多次独立采样中同候选的频率。当前实时 Planner 没有独立 SC 采样管线，因此正常记录 `SC=null` 与 `LM_ONLY_SC_UNAVAILABLE`；确定性排序只能标 `DETERMINISTIC_FALLBACK`，不能为了公式完整而编一个一致率。默认 `lambda=0.5`，只有 SC 真正存在时才参与混合。

当前实时生产路径仍没有逐 rollout 环境重置器，因此 LIVE、普通 `REPLAY` 和普通 `REPRODUCTION` 都不会自称 `FULL_LATS`。仓库已新增一条与实时链隔离的严格冻结执行路径：`FrozenReplayObservationProvider` 对 JSON observations 做防御性拷贝并计算 `snapshot_digest`，`run_frozen_replay_lats` 在每个兄弟 rollout 前校验 reset proof；`node_namespace=diagnosis_id` 使不同页面运行的节点、事件和 effect key 不冲突。`frozen_replay_showcase.py` 把 allow-listed manifest 与 hash 落库，然后把搜索帧作为 `lats.*` 事件提交到现有 Outbox/SSE。验证中心每次点击“新建回放会话”都会创建一条 fresh Diagnosis；它可以合法显示 `FULL_LATS / FROZEN_REPLAY`，但不会连接 Agent 或创建 Task/Artifact/Evidence/Report，不能冒充实时故障诊断。

验收不能只看“页面上像一棵树”。至少要证明：存在大于一个候选；选择事件含分数分解；只有被选分支获得观察；回传后 Visits 与均值可由事件重算；被反证分支可剪枝；反思进入后续决策；预算或证据门能真实停止；重启后树和搜索值从持久记录恢复。任一项缺失时，只能称为树状诊断流程，不能称为完整可审计 LATS。

对应代码：`server/app/drop_insight/lats.py`、`frozen_replay_showcase.py`、`service.py`、`exploration_tree.py`、`web/src/utils/latsSearch.js`、`web/src/utils/latsReplay.js`、`web/src/components/LatsReplayPanel.jsx`、`ActualExplorationTree.jsx`、`AgentCockpit.jsx`。验收入口是 `scripts/verify_lats_replay_showcase.py` 与 `tests/test_lats_search.py`/`test_lats_service_acceptance.py`；还必须覆盖旧案例缺少 LATS 元数据时不补造数值、LIVE 始终显示不可回滚边界、同快照的多次 fresh run 共享 digest 但 namespace 不同，以及进程重启后从持久事件恢复。
