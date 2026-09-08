# AI 诊断方案

## 目标

用户只描述问题，例如“检查两台 Worker 的 CPU、内存和磁盘是否异常”。系统负责发现可用目标、选择安全范围、提出可证伪假设、调用采集工具、验证证据并生成报告。

## 自主流程

```text
用户问题
  -> 服务端发现在线 Agent 与新鲜进程快照
  -> 生成短时有效的 opaque binding_id
  -> Scope Agent 在合格候选中选择一个 binding_id
  -> 服务端再次验证 binding 与诊断版本
  -> Knowledge RAG 检索本地目录，给出取证要求和限制
  -> Skill 用本地混合检索选择路线；命中后按需加载完整 SKILL.md
  -> Diagnosis Agent 扩展/评估多个可证伪候选
  -> LATS 用 UCT（或显式 PUCT 扩展）选择下一分支和 allowed_tool
  -> Policy/预算/权限门禁
  -> Worker 采集，Analyzer 产出 Evidence
  -> 支持证据 + 反证检查 -> reward/backprop + reflection
  -> 证据不足则在预算内转向/扩展，或接受人工干预
  -> VERIFIED / INSUFFICIENT_EVIDENCE 报告
```

候选不唯一时不再要求用户抄写 Agent/PID。模型只接收服务、环境、实例、进程类型、采集能力与不透明 `binding_id`；原始 PID、启动时间、命名空间和执行身份留在服务端。模型不可创造新目标。

AI不可用、超时或返回非法候选时，服务端使用能力感知的确定性排序，从同一批已签发候选中选择；没有合格候选时按限频策略继续发现，不产生热循环。

### 中文输出与自由探索

用户可见的主假设、决策依据、期望观察和证伪条件默认使用简体中文；CPU、JVM、eBPF、py-spy、函数名、系统调用和工具 ID 等必要技术标识可以保留英文。Planner 提示明确约束中文输出；如果模型仍返回英文占主导的长段落或非法结构，服务端会生成可审计的中文确定性规划兜底，而不是把任意机器翻译包装成模型的新判断。

实时自主路径不是把首轮假设列表循环四遍。它最多推进 4 个诊断轮次，并维护已尝试工具、已覆盖证据域、观察和反思：

- 支持证据达到门禁且反证检查通过、并且已经完成场景要求的最少“有持久化报告的诊断轮次”后，才可以生成 `VERIFIED` 终态报告；
- `PARTIAL_WITHOUT_COUNTER` 只表示当前分支部分支持，只要还有可用的交叉验证工具就继续调查；
- 证据反驳、证据不足、工具失败/不可观测或策略/人工门禁拒绝时，保留原节点和审计事件，降低该分支奖励，再扩展尚未覆盖的 CPU、内存、I/O、网络、运行时或依赖域；
- 所有“其他未知原因”“OTHER/UNKNOWN”等别名按规范化语义去重，只保留一个开放世界兜底；
- 单个分支得到 `INSUFFICIENT_EVIDENCE` 时先保持调查态并重新规划；只有达到 4 轮、工具/时间预算耗尽、没有新的合格证据域、取消或失败时，才把整个会话收敛为终态。门禁拒绝不是科学反证，本身不能证明原假设为假。

Skill 命中只能改变路线先验和探针顺序；Skill 命中与未命中都必须在当前会话重新执行允许的取证，不能复用旧事故 Evidence。初始类别不再硬挡住其他 Skill：强文本信号可以跨类别纠偏；若结果低分或歧义，Skill 主动弃权。仓库内置 Skill 只在命中后加载并校验完整 `SKILL.md`，所以这是“先看目录、再读正文”的渐进式披露，而不是把全部 Skill 塞进每轮 Prompt。

## 证据原则

- Skill 是路线先验，不是本次根因。
- Knowledge/RAG 也是路线先验，不是本次 Evidence；命中内容只进入 Planner 上下文并留下带源文件 hash 的检索事件。
- 采集失败、Agent 离线、权限不足和门禁拒绝均为 UNKNOWN/不可观测，不能作为反证；若预算与覆盖允许，应改用新的合格证据域继续探索。
- 结论必须引用本轮持久化 Evidence。
- 报告正文必须从 Analyzer 已验证字段中写出具体函数、资源或依赖，不能把 Planner 的宽泛候选原样改名为“诊断结论”。例如 allocation Profile 只能直接确认“哪个 Java 调用路径存在集中对象分配”；GC 压力还必须由同一进程、同一采集窗口的独立 `jvm-gc-metrics.json` 证明分配量增长且 GC 次数或 GC 时间增长，锁竞争也必须有自己的证据，不能互相代替。
- `VERIFIED` 且通过反证/对照门禁的报告显示“根因结论”；`PARTIAL_WITHOUT_COUNTER` 显示“阶段性根因”；没有具体定位字段时只能显示“阶段性判断”或“本轮判断”。置信度与标题不能互相替代。
- 单轮无法同时满足支持条件与反证门禁时，该分支报告标记为 `INSUFFICIENT_EVIDENCE`，但会话在仍有预算和新证据域时继续调查；只有穷尽后才以“证据不足”结束整个诊断。
- 自主模式可预授权低风险只读采集，但仍受工具白名单、预算和会话绑定约束。

Profile 文件上传成功不代表样本可用。Analyzer 还会检查：

- `perf script` 是否产生采样事件；
- 折叠栈是否包含正样本和可渲染函数；
- 分析产物的 `sample_count` 和 `profile_quality` 是否合格。

`NO_PERF_SAMPLES`、`NO_FOLDED_STACKS`、`NO_PROFILE_SAMPLES` 等质量错误会保留“采集已落盘，分析失败”的事实。这类 Artifact 不能进入成功分析和根因 Evidence。旧 Artifact 如果没有质量元数据，合同层为了向后兼容不直接判失败；重放时应由当前 Analyzer 重新计算质量。

历史 `sys_metrics.v1` 产物只有逐秒的进程 RSS、线程数和文件描述符，没有 v2 的 `summary`、CPU、负载、I/O 或网络字段。Web 通过显式兼容适配器从真实采样点计算当前值、峰值和趋势；未采集维度显示“未采集”，绝不补成 0。旧 Artifact 和旧 Report 保持不可变，兼容层只改变展示，不改审计事实。

## Skill AUTO/DISABLED 对照

创建诊断时可以选择会话级 `skill_policy`：

- `AUTO`：允许 Planner 检索已发布 Skill，命中后只调整探针顺序。
- `DISABLED`：绕过 Skill 检索，按基线 Planner 推进。

验证中心的“真实 Skill AUTO vs DISABLED”把同一份请求分别以两种策略创建两条真实 Diagnosis，随后读取各自的 Skill 激活、Tool Call、Evidence、轮次、首条证据耗时、路线和报告。页面不合成分数，也不允许把 `DISABLED` 会话的结果事后改写为 Skill 命中。

为避免返回验证中心后看不到刚创建的两组会话，浏览器会在 `localStorage` 的 `mini-drop:skill-ab-history:v1` 中保存 query、开始时间以及 AUTO/DISABLED 两条 Diagnosis ID，最多保留 12 组。重新打开面板时按这些 ID 从服务端重取实时状态和报告；本地记录只保存入口指针，不复制 Evidence 或评分。清除浏览器数据、禁用本地存储或换一台浏览器后，这份入口历史不会自动同步，因此它不是账号级或服务端实验档案。

公共 API 的自动范围发现可能让两组得到不同目标或时间窗。页面会比较服务端返回的 Scope；不完全一致时显示“当前不是严格同 Scope 实验”，此时只能证明两条真实路线都被执行，不能据此声称 Skill 改善了准确率或速度。

A/B 有效的前提是同一故障、目标、负载和观测窗口。`scripts/run_live_skill_ab_campaign.py` 负责建立 `DISABLED`/`AUTO` 会话并保存服务端 ID 链，它不负责注入/重置故障，也不强制两次自动发现得到相同 Scope。运行人需要在每个实验臂前重置并重放故障，并剔除 Scope 不一致的样本。

### 持久化随机实验

候选代码新增了服务端实验表和 API，用于回答“Skill 的提升是否只是挑案例”这一质疑。实验由服务端对单元键做带盐稳定哈希分桶，浏览器不能指定 `AUTO`/`DISABLED`；原始单元键不落库。每个 Assignment 对应一条真实 Diagnosis，根因准确率必须由人工或受控 Oracle 标注。平台计算样本量、两比例 z 检验、95% 置信区间、效果百分点、验证率和安全护栏，并按新标签追加长期指标快照。

自动化只到“评估并推荐人工发布”为止：满足门槛时状态变为 `ROLLOUT_RECOMMENDED`，审批接口仍要求授权人员操作；系统不会据此自动修改 Prompt、代码、工具权限或生产路由。旧的双会话面板仍用于演示路线差异，不能冒充随机实验。

### 2026-09-06 云端受控验收

页面入口是 `https://120.24.187.205/ai-diagnosis`。当前 `/opt/mini-drop-current` 指向 `/opt/mini-drop-releases/20260908T094648Z`；该运行基线已完成镜像构建、健康检查、完整 Skill 路线树投影、540/500 量化展示，并在同一版本跑通 Python 7、Go 4、Java 5、C++ 5 共 21 条受控 Linux live 诊断链。PID 会随容器或主机重启变化，现场演示必须用新 Diagnosis 重新发现目标并使用服务端签发的不可变绑定。

最新代码回归为 Python 501 passed、3 skipped，Web 32 个测试文件/133 tests，Go 两个模块全包通过，OpenAPI 80 个 method/path 对通过，Web 生产构建和 bundle 检查通过。这些数字只证明代码合同；`reports/ai-diagnosis/fault-plaza-full-21-final-v2-20260908.json` 才证明当前 21 场景部署路径。

`source-hotspot` 的两次全新诊断结果：

- `DISABLED`：`insight_e581f5ac05704a2e96ba8a899a9786bd`，`COMPLETED`，4 个有报告执行轮次、4 次工具调用、16 条 Evidence、py-spy 根样本 2968、TopN 8 行，无 Skill activation，报告置信度 0.69。
- `AUTO`：`insight_85bff86fb8fc4c9abd72e6e26496b77b`，`COMPLETED`，4 个有报告执行轮次、4 次工具调用、16 条 Evidence、py-spy 根样本 2968、TopN 5 行，内置 `python-runtime` Skill 激活一次，报告置信度 0.73。

两条诊断都识别到 `source_hot_function`，实际执行轮次均为 `[1,2,3,4]`，精确重复 LATS 事件为 0，完成后故障状态为未激活。完整服务端 lineage 保存在 `reports/ai-diagnosis/interview-demo-live-acceptance-20260906T142517Z.json`，文件 SHA-256 为 `9A8EA9DB4DF645B6F7CFD34C8A707D8E03E96FC719C604B09E320B1AC7146D3B`。

这是同一不可变 demo 进程上的两次隔离、顺序受控重放，不是同一墙钟时间窗的严格性能 A/B。它可以证明故障广场能随时产生新故障、两条策略都建立了新的 Task/Artifact/Evidence/Report 链且 Skill activation 分支真实存在；不能据此宣称普遍的准确率或速度提升。

`go-cpu-hotspot` 的第二组强验收使用新的 Diagnosis 和 Task，不复用上述 Python 结果：

- `DISABLED`：`insight_5dc98348c4374b1e8a0089c88d7dfc71`，首工具 `collect_sys_metrics`；
- `AUTO`：`insight_bcf79060ab2242e19a5131a36857a651`，激活 Go Runtime Skill，首工具 `collect_go_profile`；
- 两组均完成 4 个真实执行轮次，pprof 火焰图和 TopN 命中 `main.goCPUHotFunction`，对应报告引用 SUPPORT Evidence；
- 报告为 `reports/ai-diagnosis/go-interview-demo-live-acceptance-20260907T003100CST.json`，SHA-256 为 `9E24B6528BCF289296E7C8F4B894C4A09BAF17986F2BF3406359B3AB87EEDBD4`。

在 `20260907T063732Z` 发布时完成过一组 fresh Go A/B。`DISABLED=insight_9f94b3910410440b9b8c389319f3cc88` 先走系统指标，`AUTO=insight_108c07dd8c074bd0ab87ef3571ef6eb7` 首轮执行 Go pprof；两组均完成 4 个有报告轮次并绑定同一不可变 PID。AUTO 动态树显示 `ACTIVATED → DEVIATED → DEVIATED`、完整 `SKILL.md` 的 9 个章节，以及关联真实 Tool Call 的 6 个路线步骤。报告为 `reports/ai-diagnosis/skill-route-final-20260907T063732Z.json`，SHA-256 为 `7271D87467DF6EABC94C414AEFB619EC2E5A60DA93AD997AAB715A810B40EEA9`。

Planner 先以服务端确认的目标运行时裁剪工具集：Go 不能调用 py-spy/JVM Profile，Python、Java、Go 的专用 Profile 也不能跨运行时误用。AUTO 命中已发布且兼容的 Skill 时，其首工具不会再被模型或 LATS 覆盖；DISABLED 保留模型/规则基线，因此页面 A/B 能显示真实路线差异，而不是只显示标签差异。

## 实时界面

浏览器先用 API Key 创建同源 HttpOnly 会话，再建立原生 EventSource。SSE 断开时才使用后台轮询；后台刷新不会触发整页遮罩，因此回放和诊断内容不会反复闪烁。

每轮检索轨迹可由 `GET /api/v2/diagnoses/{id}/retrievals` 读取。页面可以展示命中的知识、匹配词和要求补齐的 Evidence，但不得把检索数量并入“可信证据”计数。

诊断工作台不再同时固定占用“导航、案例、对话、树”四列：

- 历史案例通过“诊断案例”抽屉打开，选择后抽屉自动收起；
- 默认视图是全宽多轮对话，按用户输入/人工干预和对应轮次的 AI 调查结果顺序展示；
- “探索树”切换为全宽视图，也可以进入全屏；只有主动选择“分屏”时才把对话和树并排；
- 探索树默认使用真实父子节点“树状图”，直接呈现分叉、剪枝、回溯和当前根因路径，并保留缩放、拖动与复位；“分轮列表”作为逐轮阅读长文本的辅助视图；
- 树上方的 **Skill 调查路线** 是独立的虚线非证据泳道：显示 BM25/本地特征召回、类别纠偏、完整 `SKILL.md` 加载状态、探针顺序、当前步骤、第 N 轮沿用以及偏离/退出。点击阶段或步骤可看来源 hash、已加载章节和命中依据；实际工具节点会显示“Skill 路线第 N 步 · 已采用”。
- 底部输入框常驻当前线程，可选择“补充/追问、继续取证、调整方向、寻找反证”。刷新资源不会清空未提交的输入。

页面的逐轮消息是领域事件的真实投影：它引用已保存的 Hypothesis、Tool Call、Evidence、Report 和 intervention，而不是重新让模型编造历史对话。当前还没有独立的、任意闲聊式 Assistant Message 数据模型，因此应称为“多轮诊断会话”，不能宣传成通用聊天 Agent。

`AgentCockpit` 把关键状态压缩成八个可点击入口：阶段/轮次、计划、RAG、工具、Evidence、记忆、评测、LATS 搜索。弹窗只展示服务端返回或可从当前记录确定性派生的数据；缺失的数据明确显示缺失，不填充演示数字。RAG 详情包含 query、知识源、文件 hash、匹配词、证据要求和限制，并固定标记“知识先验，不是 Evidence”。

人工审批弹窗中的 Agent、PID 与目标绑定只读。用户可以在策略允许范围内调整采集时长、采样率等参数，但不能通过页面把 Tool Call 换到另一个进程。

## 故障广场

故障广场是面试和验收用的受控入口。当前代码提供 21 个白名单场景、覆盖四种运行时：

| 运行时 | 场景 |
|---|---|
| Python/基础实验室（7） | CPU 计算热点、Python 源码热点、进程内存增长、同步写入压力、同机噪声邻居、入口负载饱和、生产消费队列堆积 |
| Go（4） | Go 服务 CPU 热点、Go 网络等待与下游慢响应、Go 堆内存持续增长、Go 同步文件写入 |
| Java（5） | Java 分配风暴与 GC 压力、Java 锁竞争、Java 下游依赖慢响应、Java 堆外内存持续增长、Java 同步文件写入 |
| C++（5） | C++ 计算热点、C++ 互斥锁竞争、C++ 有界内存保留、C++ 同步文件写入、C++ 下游响应变慢 |

页面默认推荐顺序为 Go → Java → C++ → Python，并提供“推荐、全部、Go、Java、C++、Python”筛选。成熟度必须按独立 live 报告解释：`source-hotspot`、`go-cpu-hotspot`、`cpp-cpu-hotspot` 与 `java-gc-pressure` 已有故障、采集、Evidence、报告和清理链路的真机报告；其余场景目前只保证受控故障能够启动和停止。页面卡片的服务端成熟度标签若尚未同步，不得替代这份报告边界。

每个场景都由服务端下发 `min_diagnosis_rounds=3` 和“系统初筛 → 专项取证 → 反证/恢复观察”三阶段提示；这里统计的是已经持久化 Report 的不同诊断轮次，不是仅创建了三个假设。实时自主路径最多 4 轮，避免第一轮按场景名直接下结论。线上是否已经具备完整四运行时矩阵，以 `PROJECT_CONTEXT.md` 的最新发布与验收记录为准。

页面只传 `scenario_id` 和 15～300 秒的时长。服务端把 ID 映射到固定 `/faults/*` 路径和服务端默认参数，不接收浏览器提供的 URL、shell 或任意故障参数。demo target 会自动停止注入。

页面从服务端读取状态和场景；只有 `READY` 时才开放“启动故障”“启动并诊断”“Skill A/B”和“停止”。“Skill A/B”会先启动白名单故障，再把服务端返回的问题带入真实 A/B 面板。

四组实验室分别由 `MINI_DROP_FAULT_LAB_URL`、`MINI_DROP_FAULT_LAB_GO_URL`、`MINI_DROP_FAULT_LAB_JAVA_URL` 和 `MINI_DROP_FAULT_LAB_CPP_URL` 指向隔离的 demo target。某组未配置时，该组返回 `DISABLED`；连接失败时返回 `UNREACHABLE`。`production_safe: false` 是固定声明，不应把任何实验室 URL 指向生产服务。

云端面试环境使用 `docker-compose.control.yml` 的 `interview-demo` profile。`python-hotspot` 不开放宿主机 HTTP 端口，只有 Compose 内的 Diagnosis Worker 能访问；同机 `demo-agent` 使用 `pid: host` 观察本次真实故障进程，并以独立 mTLS 身份上传本次采集物。profile 启动后目标仍默认空闲，只有页面白名单操作会在 15～300 秒内制造故障并自动停止。

host-network 的 demo Agent 只能使用 Control 宿主机回环可达的对象存储入口。除了在 env 文件中配置 `MINIO_AGENT_ENDPOINT=127.0.0.1:19000`，还必须把该变量传入 `diagnosis-worker` 容器；否则它可能为 Task 生成 demo Agent 无法访问的 `minio:9000` 上传地址。

## 动态探索树

探索树不是模型一次性吐出的流程图。服务端根据已持久化的 Hypothesis、Tool Call、Evidence、报告和用户反馈持续重建：

- 新假设创建节点；
- 探针结果补充当前分支的证据；
- 反证成立或用户指出方向错误时剪枝/降低置信度；
- 证据不足、不可观测或门禁拒绝时记录反思和受限奖励，再切换证据域形成新分支；
- 修正后的新假设形成新分支，树版本单调增加，并按规范化语义去重未知兜底；
- 页面只读取当前诊断自己的树，不会跳到示例库里的另一个案例。

同一语义假设、同一轮事件在服务端和页面投影时会去重，避免刷新或并发重试造成重复卡片；Visits、Q 值、Reward、选择状态等真实数值变化仍会保留，不能为了“看起来不重复”丢掉搜索更新。

会话可接收四类人工干预：补充上下文、质疑当前假设、切换方向、继续调查。底部输入会显示“发送并继续诊断”或“基于结论继续一轮”；探索树的假设节点可直接选择“优先调查”或“寻找反证”。每次干预先写入 `diagnosis.intervention_submitted` 事件，再投影为新假设和 Tool Call。浏览器可以携带 `idempotency_key`，重试同一轮不会重复建节点。`expected_version` 用来拒绝过期并发编辑。

人工输入是调查上下文，不是科学反证。质疑或切换方向会把旧假设记为 `DEPRIORITIZED`，保留旧分支和审计记录。新 Tool Call 仍需要通过目标绑定、Capability、Policy 和预算门禁。

“树状图”和“分轮列表”是同一份持久化树的两个投影，不是两套数据。默认树状图回答“假设之间怎样分叉、剪枝和回溯”；分轮列表辅助回答“第几轮为什么转向、用了什么工具、拿到哪些证据”。视图切换不会新建诊断或改变树版本。

这里必须区分“树深度”和“执行轮次”：假设的 `round_index` 记录它在哪一层被生成，公开树把它保留为 `tree_depth`；`lats.node_selected.iteration` 记录它第几次真正被选中、调用工具并产生报告。LATS 回溯到旧 sibling 时，节点仍挂在原来的父节点下，但本次 Tool Call、Evidence、Report 和页面轮次会显示新的 iteration。最少轮次门禁也只统计有 Report 的实际选择轮次，防止靠一次扩展出多个候选虚增轮数。

## 评测与演进边界

- 诊断页的评测指标来自当前会话记录或已有评测结果；没有结果时显示未运行。
- Skill `AUTO`/`DISABLED` 是真实路线对照，但只有目标、故障、窗口和负载受控一致时才接近严格 A/B。
- 人工反馈可以进入回放与候选 Skill 生成，不能直接修改已发布 Skill。
- 候选代码已有持久化随机分流、统计显著性、护栏和长期指标快照；准确率只统计人工或受控 Oracle 标签，不能从模型自评推导。
- 所谓“自进化”仍是候选生成 → 离线/在线受控评测 → 自动生成发布建议 → 人工审批 → 发布/隔离/回滚。自动建议不是自动上线，更不能自动改 Prompt、代码或权限。

## 时间窗口与幂等

诊断会话保留两个时间概念：

- `requested_time_range`：用户说的故障窗口，一旦确认就不允许改写。
- `effective_time_range`：`REPRODUCTION` 模式中实际注入和采集的窗口，需经 `OPEN` 和 `FINALIZED` 两个状态。

页面的 `datetime-local` 只保留到分钟。服务端先按声明时区转成 UTC：跨 offset 但 UTC 时刻相同的请求等价；当浏览器只提交整分钟时，同一显示分钟的重复确认也幂等。移动任意边界到另一分钟会触发“请求窗口不可变”错误并回滚。窗口锁定后页面不再在补充问题时重交或改写 `time_range`。

受控复现的实际窗口也按参数幂等：相同 `opened_at` 可重试开启，相同 `observed_start/observed_end` 可重试结束，不同参数会被拒绝。Evidence 只能在实际窗口已 `FINALIZED` 后导入。

## 可复现验收

```bash
# 生成全新 540-case 输入/私有答案，再跑生产 Skill 选择器
python scripts/generate_diagnosis_benchmark_v2.py
python scripts/run_diagnosis_benchmark_v2.py

# 真正的线上 A/B：API Key 只从环境变量读取
python scripts/run_live_skill_ab_campaign.py \
  --base-url https://control.example.com \
  --query "检查订单服务 CPU 升高并排除 IO" \
  --output reports/live/skill-ab.json
```

第一组只测 Skill 路线选择、能力漂移与安全拒绝。仓库另有 `root-cause-v1`：21 个可执行故障合同生成 540 条受控根因回放，并固定其中 500 条做同题同两次工具预算 A/B；关闭/启用 Skill 的 540 条根因 Top-1 为 42.78%/70.37%，500 组为 41.20%/68.00%。它仍不是 540 次公网真机注入。2026-09-07 已对受控 Python `source-hotspot`、Go `go-cpu-hotspot`、C++ `cpp-cpu-hotspot`、Java `java-gc-pressure` 以及持续 perf/eBPF 专项完成云端 Linux live 验收。Java 报告为 `reports/ai-diagnosis/java-gc-pressure-live-ab-20260907T1535Z.json`：AUTO 的 allocation Profile 有 2,842 个样本并命中 `Hotspot`，最终结论保留“缺少独立计数器”的限制，不把部分证据写成完全确认。

## LATS：AI 为什么不是“一句话后只跑一条固定链”

原论文把 LATS 写成六个阶段。Mini-Drop 页面为了让工具执行可读，把其中的 Simulation 拆成“行动”和“观察”，所以界面会看到七格，但算法含义没有改变：

1. **Selection（选择）**：从现有树中挑一个最值得继续调查的节点。
2. **Expansion（扩展）**：生成多个可证伪候选，去重后保留有限的 Top-K，并保留 `OTHER/UNKNOWN` 开放世界分支。
3. **Evaluation（评估）**：给候选一个先验和初始价值。它只用于排序，不是 Evidence。
4. **Simulation（模拟/执行）**：离线回放读取冻结观察；实时诊断只让被选中的分支执行一次受控真实工具，并把 Artifact/Evidence Gate 结果写回该分支。
5. **Backpropagation（回传）**：把外部结果转成有边界的奖励，沿根到叶路径增加 Visits、Value Sum 和 Mean Value。
6. **Reflection（反思）**：记录验证、被反证、工具失败或证据不足后的下一步决定；后续扩展必须能读到这次反思，不能重复撞同一堵墙。

### 公式怎样读

原论文的 UCT 选择可以写成：

```text
UCT(s) = V(s) + w * sqrt( ln N(parent(s)) / N(s) )
```

左边的 `V` 鼓励利用已经表现好的分支，右边的访问次数项鼓励探索访问较少的分支。论文评估阶段还把语言模型价值与自一致性合并：

```text
V_heuristic(s) = lambda * V_LM(s) + (1 - lambda) * SC(s)
SC(s) = 多次独立采样中支持同一候选答案的比例
```

`SC` 只说明模型多次采样是否一致，一致地猜错仍然可能发生，因此不能代替现场 Evidence。

Mini-Drop 默认按论文采用 UCT；未访问节点显式优先，访问后按下面的平均回报与探索项选择：

```text
Q(s) = value_sum(s) / visits(s)
score_uct(s) = Q(s) + c * sqrt( ln(parent_visits) / visits(s) )
```

未访问节点先以显式优先标志进入选择；已访问节点才使用上式。实现会把父访问数钳制到至少 1，避免 `ln(0)`，但不会把 `+1` 写进 canonical UCT 公式。

需要显式选择 `lats_selection_policy=PUCT` 时，才启用带 Prior 的扩展：

```text
Q(s) = value_sum(s) / visits(s)                      # 未访问时用 initial_value
score(s) = Q(s)
         + c * prior(s) * sqrt(parent_visits + 1) / (1 + visits(s))
         - virtual_loss(s)

visits(s)    <- visits(s) + 1
value_sum(s) <- value_sum(s) + reward
mean_value(s)<- value_sum(s) / visits(s)
```

这比只按模型置信度排序多了实际访问历史和 Evidence Gate 外部奖励；PUCT 扩展再加入分支 Prior。当前合同保留 `lm_value`、`self_consistency`、`heuristic_value`、`initial_value/value_source` 和 `prior/prior_source`，因此可以区分模型值、SC 和确定性 fallback，不能用一个总置信度冒充全部分量。只有服务端基于独立采样算出的 `server_self_consistency` 才会进入 SC；模型自行返回同名数字不受信任，没有独立样本时 `SC=null`，价值来源标为 `LM_ONLY_SC_UNAVAILABLE` 或 `DETERMINISTIC_FALLBACK`。

### LIVE 为什么不能严格“回到上一步”

线上 CPU、队列、锁和网络都在变化，Profiler 本身也会消耗少量资源。执行完 A 分支再执行 B 分支时，墙钟、负载和系统状态已经不同，所以 LIVE 的兄弟节点不能当成同一起点的严格 rollout。Mini-Drop 用三个模式诚实区分：

| 模式 | 搜索语义 | 可以怎样讲 |
|---|---|---|
| `REPLAY` + 冻结观察提供者 | 冻结 observation snapshot，完整回放 | “同一观察可重复计算，适合算法行为对照。”；仅设置 mode 不足以证明完整回放，当前 showcase 也不等于 Skill A/B |
| 受控 `REPRODUCTION` | 每次分支前重置同一故障/负载/目标 | “满足重置证据时可做接近严格的分支比较。” |
| `LIVE`/自主实时诊断 | 预算约束、渐进扩展、缓存已观测结果 | “是真实工具的 LATS-style 搜索，不声称现场可回滚。” |

实时生产路径尚未接入冻结 observation provider，也未提交受控复现逐 rollout 重置证明，所以普通 LIVE、`REPLAY` 和 `REPRODUCTION` 会话都应显示 `BUDGETED_LATS`。仓库另外实现了一条独立的白名单冻结回放路径：`FrozenReplayObservationProvider` 持有不可变 JSON 快照，使用稳定 `snapshot_digest` 校验自身，每个兄弟 rollout 前返回 reset proof；`run_frozen_replay_lats` 只有在该 provider 验证通过时才输出 `FULL_LATS / FROZEN_REPLAY / REPLAY_SIMULATION`。这不是把 mode 改名，而是不同的环境合同。

冻结回放对每次页面运行使用 `diagnosis_id` 作为 `node_namespace`。相同 fixture 的 `snapshot_digest` 跨运行稳定，而节点 ID、事件 ID 和 effect key 按会话隔离；默认未传 namespace 的纯函数调用也会生成新的运行命名空间。fixture 内优先用 `observation_key` 查找观察，避免两个同名假设在不同状态下误取同一结果。

实时模式中，未执行的兄弟分支只能是候选，不能共享被选中分支的 Evidence，也不能获得环境奖励。最终停止条件包括 Evidence 已验证、带限制结论、搜索/工具预算耗尽、无可执行子节点、取消或失败。

对实时自主会话，“带限制结论”只有在交叉验证已无合格工具或预算/4 轮上限到达时才是终止理由；若仍有未覆盖的证据域，部分支持或证据不足会触发跨域扩展，不会草率收尾。

### 两条页面演示路径不要混讲

进入 **验证与 A/B** 后，页面先展示会真正制造负载的 **实时故障广场**，其后才是用于算法说明的 **完整 LATS 冻结回放**。实时广场默认按 Go、Java、C++、Python 各推荐一个场景，也可按运行时筛选：

1. 若要演示算法回放，向下找到冻结卡片并点击 **新建回放会话**。浏览器生成新的 `client_run_id`，服务端创建新的 `REPLAY` Diagnosis 并立即打开它；同一按钮每次正常点击都产生新的诊断命名空间，而网络重试同一 `client_run_id` 保持幂等。
2. 切到全宽 **探索树**。先看 `FULL_LATS`、`FROZEN_REPLAY`、快照摘要和 reset 次数，再按轮次观察候选扩展、UCT 选择、冻结模拟、观察、反思、奖励回传和剪枝。SSE 每收到一帧就刷新同一棵持久化树；刷新浏览器不会换成另一棵演示树。
3. 冻结会话不会创建 Tool Call、Task、Attempt、Artifact、Evidence 或 Report。只要 `target.kind=FROZEN_LATS_SHOWCASE`，即使首个搜索 frame 还没到，页面也会锁定普通 Planner、真实采集、工具审批、人工干预和 Skill 沉淀，并明确写“冻结算法回放，不是实时故障证据”；它只用于证明完整树搜索、可复位兄弟 rollout 和恢复性。
4. 要证明实际诊断能力，回到页面上方实时故障卡片并点击 **启动并诊断**。这条路径必须产生新的 Agent/PID binding、Task、Artifact、Evidence 和 Report，只能称为 `BUDGETED_LATS`，但观察来自真实工具。

冻结桥首先把完整 allow-listed manifest 以 `lats.replay_snapshot_frozen` 事件保存，再逐帧写入普通 `lats.*` 事件。核心搜索事件包括 `search_started`、`candidates_expanded`、`candidates_evaluated`、`node_selected`、`simulation_started`、`observation_recorded`、`reflection_recorded`、`backpropagated`、`node_pruned` 和 `search_terminated`；LIVE 路径还可能出现 `action_proposed`、`awaiting_approval`、`action_blocked` 与 `action_dispatched`。只有真实 Task 已下发时才能记录 action dispatched；未执行兄弟没有 observation/reward。

页面可以保留上述英文事件名作为稳定审计标识，但必须给出中文含义：开始搜索、扩展候选、评估候选、选中节点、开始模拟/执行、记录观察、记录反思、奖励回传、剪枝和搜索终止。LIVE 中的 `action_blocked` 还要显示拒绝原因，并继续写入 observation/reflection/negative reward；只要还有合格分支和预算，就不能紧接着把它当作 `search_terminated`。

评分字段也要分清用途：`visits` 是节点被访问次数，`value_sum` 是累计有边界奖励，`mean_value=value_sum/visits` 是平均回报，`prior` 是候选先验且只在 PUCT 的先验项中起作用；UCT/PUCT 总分决定下一步探索顺序，不是诊断结论的置信度。点击“查看评分”应显示利用项、探索项、可选先验项、最近观察和最近反思，缺失值明确显示未记录。

算法原始依据：[LATS 论文（PMLR 235，ICML 2024）](https://proceedings.mlr.press/v235/zhou24r.html) 与 [作者官方实现](https://github.com/lapisrocks/LanguageAgentTreeSearch)。Mini-Drop 的 PUCT、Evidence Gate、安全工具和 LIVE 预算化语义是工程扩展，不能说成论文逐字实现。

### 页面怎样演示

1. 从“诊断案例”抽屉选择当前案例，抽屉自动收起，避免四列挤压。
2. 在“对话 / 探索树 / 分屏”中先用对话说明问题，再切到全宽探索树；面试现场不要把树压在 22% 宽度里。
3. 树顶部读取服务端 `search`：算法版本、阶段、候选数、当前选择、最佳路径、环境与 rollout 语义、迭代/工具预算和停止条件。
4. 节点卡展示 Visits、Mean Value、Prior 和 UCT/PUCT 分数；点击“查看评分”打开弹窗，说明 Q、探索奖励、先验奖励、虚拟损失、最近观察与反思。
5. 在 LIVE/BUDGETED 会话点击“优先调查”或“寻找反证”提交人工干预。它会创建新的领域事件和后续轮次，不会直接篡改旧 Evidence；冻结 showcase 为只读，不提供这类副作用操作。
6. 点击驾驶舱的 “LATS” 指标入口，在弹窗中串起阶段、选择依据、最佳路径、预算和停止原因。旧案例没有搜索元数据时，页面应显示“未返回”，而不是制造漂亮数字。
