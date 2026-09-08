# Mini-Drop 当前项目上下文

> 这是跨聊天、跨任务使用的上下文锚点。开始工作时先读本页，再进入对应专题文档。

最新一次电脑重启前的精确交接状态保存在 `docs/RESTART_HANDOFF.md`。它记录当前云端版本、FULL/BUDGETED LATS 边界、最终测试基线和重启后的恢复动作；不包含任何密码或私钥。

## 现在保留的产品主线

Mini-Drop 是一套面向 Linux 多节点的证据驱动性能诊断系统：

- React Web 提供 AI 诊断、基础采集、任务详情、Agent、计划任务和审计页面。
- Go API 是唯一公开 HTTP/SSE 入口，负责鉴权、状态和内部服务编排。
- C++ Control/Agent 负责注册、心跳、进程快照、任务领取和白名单采集。
- PostgreSQL 保存状态与审计；MinIO 保存原始 Artifact 和分析结果。
- Python Analyzer 处理采集物；Python Diagnosis Worker 通过 LangGraph Runtime 推进受约束的 AI 调查。
- AI 不直接拥有 shell，也不能绕过服务端签发的 Agent/PID 绑定、工具白名单、预算和证据门禁。
- AI Runtime 已真实使用 `LangChain create_agent + LangGraph`；领域状态和执行授权仍由 Mini-Drop 管理。
- Diagnosis Planner 已接入仓库内 `knowledge/catalog.json` 的确定性 Agentic RAG；每轮检索带 Markdown 源文件 hash 持久化为诊断事件，并明确 `is_evidence=false`，不会污染本次 Evidence 链。
- `skills/catalog.json` 当前登记 13 个 Skill，覆盖 CPU、内存、I/O、网络、依赖、队列、锁、FD 以及 Python/Go/JVM/C++ 运行时等路线；Skill 只提供候选 Prior 和探针顺序，命中或未命中都必须重新采集当前会话 Evidence。
- Skill 检索不依赖 Elasticsearch 或向量数据库。候选阶段使用进程内 Python BM25、512 维确定性哈希 n-gram/领域概念特征和结构上下文评分；环境、运行时锚点、目标子系统与 Collector 能力仍是硬门禁，类别只作为可纠正的先验。低分或前两名过近时主动弃权。
- Skill 使用两级渐进式披露：先用 `catalog.json` 的轻量元数据召回；只有命中后才从 `skills/<slug>/SKILL.md` 读取完整正文，校验目录边界、UTF-8、128 KiB 上限、章节合同和 SHA-256，再把正文作为非 Evidence 的路线合同送入当前轮与后续重规划轮。数据库学习型 Skill 没有仓库正文时仍只使用结构化策略。
- 每个 Skill activation 会保存逐轮 `ACTIVATED/REUSED/SWITCHED/DEVIATED/EXHAUSTED` 轨迹，并发布幂等的 `skill.route_activated/reused/exited` 事件。动态探索树用独立虚线泳道显示召回、分类纠偏、完整正文加载、当前探针步骤、跨轮沿用与退出；它不改写 LATS 父子拓扑，也不把 Skill 命中计入 Evidence。
- Planner 的用户可见文本实行中文优先：CPU、JVM、eBPF、py-spy、函数名和工具 ID 等必要技术标识可以保留英文；若模型返回整段英文或非法结构，服务端改用可审计的中文确定性规划兜底，不把任意机器翻译伪装成模型结论。
- 实时自主诊断最多推进 4 轮。证据反驳、证据不足、部分支持、工具不可观测或策略/人工门禁拒绝时，会保留原分支的观察、反思和奖励回传，再扩展到尚未覆盖的 CPU、内存、I/O、网络、运行时或依赖域；所有 `OTHER/UNKNOWN` 别名按规范化语义只保留一个未知兜底。
- AI 诊断页采用“单主视图”工作台：历史案例收进抽屉，默认给多轮对话完整宽度；探索树可切到全宽或全屏，需要对照时才开启分屏。探索树默认显示真实父子节点拓扑，可平移、缩放和复位；按轮次列表只作为逐轮阅读的辅助视图。
- 页面底部常驻同一线程的多轮输入，可选择“补充/追问、继续取证、调整方向、寻找反证”；中间区按真实持久化的轮次、假设、Tool Call、Evidence 和 Report 逐轮投影，不把一次聚合结果伪装成完整聊天记录。
- `AgentCockpit` 提供可点击的阶段、计划、RAG、工具、Evidence、记忆、评测与 LATS 搜索入口。RAG 弹窗展示来源、正文 hash 和检索轨迹；LATS 弹窗展示选择分解、最佳/最近路径、预算与环境语义；运行时状态区分 Checkpoint 的 `requested_backend` 与 `actual_backend`，不一致时显示 `DEGRADED`。
- 验证中心提供独立的“完整 LATS 冻结回放”：每次点击都创建一条新的 `REPLAY` Diagnosis，把服务端白名单 fixture 作为不可变快照落库，再逐帧持久化 `lats.*` 事件并通过现有 SSE 更新探索树。它不连接 Agent、不创建真实 Task/Artifact/Evidence/Report，不能冒充当前线上故障证据。
- 人工审批只允许修改策略开放的采样参数；服务端签发的 Agent/PID 目标身份在页面中只读，不能借审批改绑目标。

## 当前部署决定

- 云服务器仍需要运行环境；“上云”不等于“不用 Docker”。
- Control 服务器使用 `docker-compose.control.yml` 运行 PostgreSQL、MinIO、Control、Go API、Python Workers 和 Web。
- 面试现场的可重复故障使用同一份 Control Compose 的可选 `interview-demo` profile：常驻但默认空闲的 `python-hotspot` 与同宿主机 `pid: host` 的 `demo-agent` 生成当次真实 Task/Artifact/Evidence，不依赖历史案例。该 Agent 使用现有 Control CA 签发的独立证书；故障 HTTP 端口不映射公网。
- Worker 可以使用 `docker-compose.worker.yml`，也可以通过 `deploy/systemd/mini-drop-agent.service` 裸机运行 C++ Agent。
- 对宿主机 PID、perf 和 eBPF 的访问要求较高时，Worker 使用 systemd 更直接；Control 保留 Docker Compose 更易维护。
- 不得把 Docker 配置和数据库/MinIO 数据卷当作普通缓存删除。
- 当前线上版本为 `/opt/mini-drop-releases/20260908T094648Z`，`/opt/mini-drop-current` 已在 2026-09-08（UTC+8）原子指向该目录；对外入口是 `https://120.24.187.205/ai-diagnosis`。该版本继承 15 张云端教材截图，并包含意图子句优先级、工具/假设绑定、native perf self-sample 语义和 Java 故障实验内存边界修正。发布只滚动替换 Python Worker/Analyzer，没有重建 ECS、PostgreSQL、MinIO 或其数据卷。
- `20260906T125357Z` 的安全源码包 SHA-256 为 `078D15D7099ECAF47B82228FD42EA261E9B1BE02386E2178A9FB378B177A06F3`；其清单已验证不含 `.env`、证书、私钥、本地数据库、缓存和构建产物。旧 staging `20260906T115542Z` 与本地作废包 `20260906T123931Z` 不得切换为 current。后续发布继续使用版本目录和原子软链接，原生镜像构建保持 `NATIVE_BUILD_JOBS=1`、`COMPOSE_PARALLEL_LIMIT=1`，不得删除 PostgreSQL/MinIO 卷。

## 权威文档

| 文档 | 用途 |
|---|---|
| `INTERVIEW_DEMO_GUIDE.md` | 面试现场可直接照做的页面演示脚本、预期结果和排障清单 |
| `PROJECT_LEARNING_GUIDE.md` | 从页面使用到源码、完整链路、OS/网络和面试知识的项目教材 |
| `REPLICATION.md` | 控制机与 Worker 的复刻和部署步骤 |
| `AI_DIAGNOSIS.md` | AI 诊断对象、状态、自动范围和证据门禁 |
| `AGENT_RUNTIME.md` | Runtime、Harness、Theme、上下文与记忆设计 |
| `SKILLS.md` | Skill 的定义、评测、发布、隔离与回滚 |
| `COMPETITOR_DESIGN_DECISIONS.md` | 官方竞品机制如何转化成项目设计与验收项 |
| `contracts/` | API、Task 参数、Evidence 与 Attempt 的可执行契约 |

## 当前学习入口

主教材是 `docs/PROJECT_LEARNING_GUIDE.md`，目前包含：

1. 全部真实页面、按钮、输入框和使用示例，并配有 15 张 2026-09-08 当前云端只读截图；图片保存在 `docs/assets/learning-guide/`，通过 `scripts/capture_learning_guide_screenshots.py` 可重复抓取。
2. 基础采集与 AI 自主诊断的端到端链路。
3. 仓库目录、核心文件、状态机和数据模型。
4. LangGraph、Harness、Theme、Context、Memory 与 Skill 的关系。
5. 性能诊断相关的操作系统、网络、分布式系统与面试知识。
6. 七节从零开始的实操课程。
7. 由 `scripts/generate_learning_guide_file_index.py` 从实际工作树维护的逐文件字典；生成代码、测试、部署、报告和样式也标明来源与用途。

后续教学内容继续更新同一份主教材，不另起名称相似但互相冲突的版本。

需要直接排练或面试演示时，使用 `docs/INTERVIEW_DEMO_GUIDE.md`。它不是第二份架构真相，而是从主教材提炼出的页面操作脚本，并明确区分 FULL_LATS 冻结回放与真实 BUDGETED_LATS 诊断。

## 最近验证基线

2026-09-08 当前代码完成了包含自由探索、实际轮次回溯、运行时路由门禁和 FULL_LATS 冻结回放桥在内的全量回归；代码测试与真实公网验收分开记录：

- Python：501 passed，3 skipped，0 failed。
- Web：32 个测试文件、133 tests 全部通过；生产构建与 bundle 检查通过。全量测试需单 worker 顺序运行，避免本机并发导入 Ant Design/ECharts 造成统一的 5 秒资源争抢超时。
- Go：`go test ./...` 全包 0 failed。
- OpenAPI：80 个 method/path 对通过。
- 新 Skill 路线评测：540 个新提示词 Case，506 个通过；正例 334/360，负例 172/180，误激活 8/180，不复用路线的弃权基线为 180/540。这组数字只表示路线选择与拒绝，不是真实根因准确率。
- 新受控根因评测：21 个可执行故障合同生成 540 条确定性回放，关闭 Skill 为 231/540（42.78%），启用 Skill 为 382/540（70.74%）；其中 500 组同题同两次工具预算为 41.20% 对 68.40%，提升 27.20 个百分点，改善 136、退化 0、关键路线提前 187 组。它测量受控合同上的 Evidence 合格代理 Top-1，不是 540 次公网真机注入。
- 新 Linux live E2E：同一发布版本顺序执行故障广场全部 21 个白名单场景，Python 7/7、Go 4/4、Java 5/5、C++ 5/5，21 条诊断链和 21 次故障清理全部通过。全轮包含 83 次真实 Tool Call、236 条 Evidence 和 70 份 Report；机器报告为 `reports/ai-diagnosis/fault-plaza-full-21-final-v2-20260908.json`，规范化 payload SHA-256 为 `c8f7d0413fce94601cd905238533553f2d20509ae2492035e03f8c78faa07eea`。这是受控真机闭环，不是生产准确率。
- 新增连续窗口 Bundle、安全解包、调用图、兼容矩阵和聚合对照测试；受控 Python 源码热点、Go CPU 热点、C++ CPU 热点和 Java GC 压力已经分别完成云端 live E2E，`continuous_perf` 长周期与独立 eBPF Campaign 也已通过专项真机验收。Java 结论属于 `PARTIAL_WITHOUT_COUNTER`：真实 JVM allocation Profile 足以支持当前热点判断，但缺少独立计数器交叉验证，页面和报告都会保留这项限制。
- Windows Docker Desktop 当时不可用，但六个发布镜像由独立 WSL Docker 构建并加载到云端；是否通过 Linux live E2E 以云端报告为准，而不是以本机 Docker 状态推断。

## 2026-09-07 云端发布与现场验收

- 当前线上目录是 `/opt/mini-drop-releases/20260908T094648Z`。它包含完整 `SKILL.md` 渐进式披露、跨类别检索纠偏、跨轮 Skill 上下文、动态树中的 Skill 非证据路线泳道、540/500 量化卡，以及意图子句路由、工具/假设绑定、native perf self-sample 语义、显式网络症状优先级和 Java 故障实验内存边界修正；公网 `/api/healthz` 与 AI 诊断页均返回 HTTP 200。
- 报告生成不再把 Planner 的宽泛候选原样写成“诊断结论”。新报告从通过门禁的 Analyzer metadata/predicate 中提取具体函数、样本占比、事件类型和可用对象类型；完整验证显示“根因结论”，缺少独立反证/对照显示“阶段性根因”。不可变旧报告由 Web 使用其既有 claims 做确定性展示恢复，不修改数据库审计记录。
- `sys_metrics.v1` 与当前页面的结构错位已通过显式兼容适配器修复：历史逐秒 RSS、线程和 FD 会显示真实当前值、峰值、趋势与采样表；没有采集的 CPU、负载、I/O 和网络保持“未采集”，不会伪造成 0。未来 Analyzer 状态文案也不再把 v1 产物误称为 v2。
- 公网业务健康检查使用 `/api/healthz`。公网 `/readyz` 当前匹配 Nginx 的 SPA fallback 并返回 `index.html`，因此只能把 `/api/healthz` 的结构化结果或容器内 `/readyz` 当作 readiness 证据。
- 页面入口：`https://120.24.187.205/ai-diagnosis`。
- 发布后检查确认 Agent Runtime 为 `HEALTHY`，请求和实际 Checkpoint 后端均为 PostgreSQL；容器内 RAG 可返回知识结果，九个 Control/演示服务均正常运行。
- 当前故障广场在线枚举 21 个白名单场景（Python 7、Go 4、Java 5、C++ 5），`control-interview-demo-agent` 和四个隔离实验室均可用。PID 会变化，只能使用每条 Diagnosis 内服务端签发的不可变目标绑定，不能把历史 PID 当成配置。
- 本次发布后又从故障广场创建一组全新的 Go CPU A/B：关闭 Skill 为 `insight_33af01c2aeff443ca74b0ebf1b8c7201`，开启 Skill 为 `insight_212795b892964cebbf059b7dfbc67085`。两组均完成 4 轮、4 次真实工具调用、16 条 Evidence 和 4 份报告；AUTO 组先执行 Go pprof，动态树返回完整 `SKILL.md`、9 个已校验章节、SHA-256、逐轮偏离/耗尽轨迹及 3 步路线覆盖层。故障清理已验证。报告为 `reports/ai-diagnosis/skill-route-live-20260907T062103Z.json`，SHA-256 为 `DC3BC84DFF73FA078B46EBB2887C7D0C795089EBDBE3490860673E23A0EB7270`。
- 最终版本切换后再次运行修正后的真实 Go A/B：`DISABLED=insight_9f94b3910410440b9b8c389319f3cc88`，路线为系统指标 → Go pprof → perf → eBPF I/O；`AUTO=insight_108c07dd8c074bd0ab87ef3571ef6eb7`，路线为 Go pprof → 系统指标 → perf → 连续剖析。两组绑定同一不可变 PID，均完成 4 个报告轮次；AUTO 树实际保存 `ACTIVATED → DEVIATED → DEVIATED`，完整加载 9 个 Skill 章节，并把 6 个路线步骤关联到真实树节点。报告为 `reports/ai-diagnosis/skill-route-final-20260907T063732Z.json`，SHA-256 为 `7271D87467DF6EABC94C414AEFB619EC2E5A60DA93AD997AAB715A810B40EEA9`，故障清理已验证。
- 故障广场默认按 **Go → Java → C++ → Python** 各展示一个推荐案例，并提供“推荐、全部、Go、Java、C++、Python”筛选。服务端成熟度标签仍以场景合同为准；2026-09-08 的完整 live Campaign 已证明当前 21 个白名单场景逐项闭环，但不能把受控演示结果外推为生产事故准确率。
- `DISABLED` 路线新建诊断 `insight_e581f5ac05704a2e96ba8a899a9786bd`，终态 `COMPLETED`，完成 4 个有报告执行轮次、4 次真实工具调用和 16 条 Evidence；py-spy 火焰图根节点 2968 个样本、TopN 8 行，无 Skill activation，最终报告置信度 0.69。
- `AUTO` 路线新建诊断 `insight_85bff86fb8fc4c9abd72e6e26496b77b`，终态 `COMPLETED`，完成 4 个有报告执行轮次、4 次真实工具调用和 16 条 Evidence；py-spy 火焰图根节点 2968 个样本、TopN 5 行，激活内置 `python-runtime` Skill 一次，最终报告置信度 0.73。
- 两条链路都发现 `source_hot_function`，报告的实际轮次均为 `[1,2,3,4]`，LATS 事件精确重复数均为 0；收尾检查确认故障已停止（`fault_active=false`）。
- Python 源码热点验收报告为 `reports/ai-diagnosis/interview-demo-live-acceptance-20260906T142517Z.json`，SHA-256 为 `9A8EA9DB4DF645B6F7CFD34C8A707D8E03E96FC719C604B09E320B1AC7146D3B`。
- 最新 Go 强验收报告为 `reports/ai-diagnosis/go-interview-demo-live-acceptance-20260907T003100CST.json`，SHA-256 为 `9E24B6528BCF289296E7C8F4B894C4A09BAF17986F2BF3406359B3AB87EEDBD4`。关闭 Skill 的新诊断 `insight_5dc98348c4374b1e8a0089c88d7dfc71` 先走 `collect_sys_metrics`，开启 Skill 的新诊断 `insight_bcf79060ab2242e19a5131a36857a651` 先走 `collect_go_profile`；两组都完成 4 个真实执行轮次，Go pprof 火焰图和 TopN 均命中 `main.goCPUHotFunction`，报告引用对应 SUPPORT Evidence，且故障清理已验证。
- C++ CPU 热点真实 Skill A/B 报告为 `reports/ai-diagnosis/cpp-cpu-hotspot-live-ab-20260907T104202Z.json`，SHA-256 为 `975D6DAB0532552BB2382137FC2D55D0C484EEB3F39E3038D35668371A63F303`。两组均完成 4 轮，AUTO 的 perf 产物命中 `cpp_cpu_hot_function`，Task、Artifact、Evidence 与 Report 链路通过。
- 持续 perf 与独立 eBPF 专项报告为 `reports/ai-diagnosis/priority-collectors-live-20260907T0932Z.json`，SHA-256 为 `f14eb214ce86ca7b3f03bbd807383a059c8a5abcc52661def9fd58d1256bc559`。持续任务运行 60 秒、生成 4 个窗口；eBPF 产物包含非零真实样本，结束后清理通过。
- Java GC 压力真实 A/B 报告为 `reports/ai-diagnosis/java-gc-pressure-live-ab-20260907T1535Z.json`，SHA-256 为 `7efeb124d295b112abe67abc759856d239b6c2dd41c0ddb15ac658e4df4fb63c`。关闭 Skill 的新诊断 `insight_98f5e80170d24e078e7def200fcd6169` 终态为证据不足；开启 Skill 的新诊断 `insight_c23e8c442c2343f1bd10cb39bb8666af` 终态为带限制完成。两组都先做低风险系统初筛，但完整路线分别为“系统指标 → perf → eBPF I/O → JVM Profile”和“系统指标 → 内存 Profile → JVM allocation Profile → perf”；AUTO 组真实 async-profiler 产物包含 2,842 个样本并命中 `Hotspot`，新增 1 条可支撑结论的 Evidence，故障清理已验证。故障广场的 `java-gc-pressure` 与此前已通过的 `cpp-cpu-hotspot` 现均返回 `LIVE_DIAGNOSIS_VERIFIED`。
- 面向阅读和答辩的最新测试报告为 `reports/ai-diagnosis/AI诊断与Skill复用测试报告-20260906.md`，对应 Word 版为 `reports/ai-diagnosis/Mini-Drop-AI诊断与Skill复用测试报告-20260906.docx`。Word 版已逐页渲染检查，并通过 0 高、0 中、0 低问题的无障碍审计。
- FULL_LATS 公网验收报告为 `reports/ai-diagnosis/lats-replay-acceptance-public-20260906T071335Z.json`，SHA-256 为 `D337B15661524799D72EB94AEB7588C624D27DFE34549EF4ADAF4EC408D2D9F6`。脚本通过 `https://120.24.187.205` 连续创建两条 fresh Diagnosis：各观察到 6 个树 revision 和 4 次冻结 simulation；两次 snapshot digest 相同，节点 namespace 不相交，真实 Tool Call/Evidence/Report 均为 0。
- 这次 A/B 是对同一个不可变 demo 进程做两次隔离、顺序执行的受控故障重放。它证明页面可随时创建新故障、新 Diagnosis/Task/Artifact/Evidence/Report 链以及 Skill 路线差异；两臂不是同一墙钟时间窗，不能把耗时差写成严格性能收益。
- host-network 的 demo Agent 通过回环 MinIO 端口上传。`diagnosis-worker` 必须实际接收到 `MINIO_AGENT_ENDPOINT=127.0.0.1:19000`；只把变量写进 env 文件、但没有传入该容器，会生成 demo Agent 无法访问的上传地址。

## 2026-09-05 当前实现增量

- `continuous_perf` 最长 24 小时，按最多 900 秒的窗口保存原始 Profile，支持 CPU 哨兵、配额停止和保留层级。
- Analyzer 可以安全拆解每个连续窗口，并产出窗口级火焰图、TopN 和调用图。
- `mini-drop-gperftools-bridge` 为已显式接入 libprofiler 的 C/C++ 进程提供同 UID、默认非 root 的信号触发桥接。
- Worker 兼容矩阵同时检查内核、`perf_event_paranoid`、`ptrace_scope`、BTF、工具和 capability，能力不满足时明确降级。
- 验证中心的“真实 Skill AUTO vs DISABLED”会用同一份请求建立两条真实 Diagnosis，并分别读取 Skill 激活、Tool Call、Evidence、轮次、首条证据耗时和报告；服务端持久化 `skill_policy`，`DISABLED` 不进入 Skill 检索。浏览器只把 query、开始时间和两条服务端 Diagnosis ID 保存到 `localStorage`（键 `mini-drop:skill-ab-history:v1`，最多 12 组），返回验证中心后按 ID 重新读取服务端状态；这是同一浏览器的入口恢复，不是账号级或服务端 A/B 历史。两组 Scope 不一致时页面明确标为非严格实验，不能据此宣称准确率或速度提升。
- 最新候选代码的故障广场暴露 21 个服务端白名单场景：Python 7 个、Go 4 个、Java 5 个、C++ 5 个。它们只能调用各自隔离 demo target 的固定路径，单次最长 300 秒并自动停止；对应运行时的 `MINI_DROP_FAULT_LAB_*_URL` 未配置时，该组场景明确显示未启用。每个故障要求至少 3 个已经持久化 Report 的不同诊断轮次，不能仅靠创建三个假设满足多轮合同。
- Control 的 `interview-demo` profile 保持故障目标容器自身的 PID 隔离，由专用 Agent 通过宿主机 PID 视图观察它，并给专用 Agent 提供仅回环可达的 `127.0.0.1:19000` MinIO 上传入口；远端 Worker 仍使用各自的安全隧道，不共享 demo Agent 身份。
- 诊断页可在底部继续追问，探索树的假设节点也提供“优先调查”和“寻找反证”。服务端先持久化人工轮次，再生成新假设和受控 Tool Call；用户输入只能改变调查方向，不记为 Evidence。动态树把干预节点、新轮次、剪枝和方向切换按持久化事件投影。
- Agent Checkpoint 健康接口会分别报告请求后端与实际后端。生产请求 PostgreSQL 但初始化失败时，Worker 保持启动并降级到内存，同时明确标记 `DEGRADED`；提案不得再把这种回退误报成 PostgreSQL 持久化，接口与日志均不返回 DSN 或密码。
- 诊断时间范围分为不可变的用户请求窗口和受控复现的实际观测窗口。跨时区格式但 UTC 时刻相同的重试、页面丢失秒/微秒后的同一分钟重试，以及同参数的实际窗口开启/结束操作均为幂等；真正移动到另一分钟会被拒绝并回滚。
- Profile Analyzer 会在入库前检查正样本数和可渲染函数。`NO_PERF_SAMPLES`、`NO_FOLDED_STACKS` 或输出端的零样本会让 AnalysisJob 失败，不得把空火焰图登记为成功证据；Task 结果页显示稳定错误码和重采建议，不渲染空 Artifact。旧 Artifact 缺少质量字段时只表示质量未知。
- Skill 页面只读生成报告，不再在前端硬编码历史数字；报告缺失就展示缺失，不拼装“成功数据”。
- `diagnosis-v2` 的 540-case 集从固定生成器产生，公开输入与私有答案分离，只测路线复用与安全拒绝。另有 `root-cause-v1` 的 540 条受控根因集与 500 组 Skill A/B，页面从生成报告读取量化指标，不硬编码数字。
- `run_multi_cloud_acceptance.py` 会逐 Agent 创建真实 `sys_metrics` Task，并保存 Event/Attempt/Artifact 的服务端链路；未实际运行报告前不宣称云端已经通过。

## 当前 AI 能力边界

- 已实现的是受约束、可审计的多轮性能调查：实时自主路径最多 4 轮；每轮可以重规划、检索知识、申请一个受控探针、接收人工干预，并从持久化领域事件重建对话和探索树。证据反驳、证据不足、部分支持、不可观测或门禁拒绝不会自动把当前假设写成结论，而会在预算和覆盖允许时切换到未探索的证据域。
- LATS 有两条明确分开的执行路径：实时诊断使用 `BUDGETED_LATS` 调用真实工具；验证中心的白名单冻结 fixture 使用可复位 observation provider 运行 `FULL_LATS / FROZEN_REPLAY`。两者复用搜索事件和树投影，但事实口径不同。
- 页面中的“记忆”同时展示本会话的领域事实和 Runtime 短期线程状态；只有领域事实、Evidence 与报告以 PostgreSQL 为权威。Checkpoint 实际降级到内存时，短期模型消息不能承诺跨进程重启恢复。
- 当前 Knowledge 检索是仓库目录上的确定性混合词法检索，不是向量数据库，也不是可自行访问互联网的通用 RAG。
- 当前评测和 Skill 演进有离线数据集、门禁、发布、隔离和回滚；服务端已经实现持久化随机分流、显著性分析和指标快照，但尚无足够生产随机样本，不能把双会话演示或受控回放冒充线上 A/B 结论。
- “自进化”只允许产生候选 Skill 并经过评测与人工发布，不能表述为模型会在线自行改 Prompt、代码、权限或自动把反馈发布到生产。

这些结果说明当前代码和文档注释修改没有破坏已覆盖的行为；故障广场当前 21 个白名单组合已由同版本 Linux live 报告逐项验收。新增运行时、Collector 或生产故障类型仍必须另做匹配环境验收。

## LATS 搜索合同与运行边界（2026-09-06）

Mini-Drop 把诊断规划进一步约束为可审计的 Language Agent Tree Search（LATS）合同。服务端默认使用 `algorithm=LATS-UCT`、`algorithm_version=drop-insight-lats-v1`，也保留显式 `PUCT` 扩展；候选节点可携带 `visits`、`value_sum`、`mean_value`、`prior`、`initial_value`、`uct_score`、`reward`、`selected`、`best_path`、`pruned`、最近观察和最近反思。页面只展示服务端实际返回的搜索字段；旧案例没有这些字段时明确显示未记录，不能补成零或伪造一棵“搜索树”。

运行语义分开记录：

- `REPLAY`：只有实际接入冻结观察提供者后才是 `FULL_LATS / FROZEN_REPLAY`；仅把会话 mode 写成 `REPLAY` 而没有冻结观察证明时仍降级为 `BUDGETED_LATS / REPLAY_PROVIDER_REQUIRED`。
- `REPRODUCTION`：只有编排器能证明每条 rollout 前都把故障、负载、目标和初始状态重置一致时，才可记录为 `FULL_LATS / CONTROLLED_REPRODUCTION / RESET_BETWEEN_ROLLOUTS`；否则仍是 `BUDGETED_LATS`。
- 其余实时模式（含 `AUTONOMOUS`、`ASSISTED`、`OBSERVE_ONLY`）：`BUDGETED_LATS / LIVE_PROGRESSIVE / REAL_TOOL_SINGLE_STEP_NO_ROLLBACK`。真实工具会推进墙钟时间，兄弟分支不能声称来自完全相同的现场。

实时生产编排没有接入冻结 observation provider，也没有向搜索层提交逐 rollout 的受控重置证明，因此普通 `REPLAY`、`REPRODUCTION` 和 LIVE 诊断仍只会得到 `BUDGETED_LATS`。仓库同时实现了独立的严格冻结回放运行器 `run_frozen_replay_lats` 和页面 showcase 桥：它持有同一份 JSON 快照的防御性副本，计算稳定 `snapshot_digest`，每个兄弟 rollout 前生成并校验 reset proof，并把 `node_namespace=diagnosis_id` 注入节点、事件与 effect key。只有这条经过验证的路径才记录 `FULL_LATS / FROZEN_REPLAY / REPLAY_SIMULATION`。

默认搜索配置由诊断预算派生：候选 `top_k=3`、`selection_policy=UCT`、价值混合系数 `lambda=0.5`、探索常数 `sqrt(2)`、最多 6 次搜索迭代；其中实时自主编排另设最多 4 个诊断轮次的软上限，并仍受最多 12 次真实工具调用、剩余证据域和门禁限制。冻结回放继续显示独立的 simulation 预算且真实工具调用为 0，不受“实时最多 4 轮”的产品约束冒充。调用方可在安全上下限内显式选择 PUCT 扩展。价值公式只在服务端拥有独立采样统计时使用 `lambda * LM + (1-lambda) * SC`；模型自报的一致性不受信任，缺少独立样本时 `SC=null` 并显式标为 `LM_ONLY_SC_UNAVAILABLE` 或确定性 fallback。

LATS 回溯时要区分两个编号：`Hypothesis.round_index` 是节点创建时的树深度，保持不可变；`lats.node_selected.iteration` 是该节点真正被选择、取证并形成 Report 的执行轮次。服务端最少轮次门禁、下一轮预算和页面“第 N 轮”都以有 Report 的选择 iteration 为准；旧案例缺少选择事件时才回退到节点出生轮次。这样从第 1 层回溯到旧 sibling 仍会显示为第 3/4 次真实调查，但父子拓扑不会被伪造为更深的树。

事件和评分在页面上按中文解释，但内部稳定事件名可保留英文用于审计：`node_selected` 表示按 UCT/PUCT 选中分支，`observation_recorded` 表示写入本轮真实或冻结观察，`reflection_recorded` 表示记录支持、反证、不足或不可观测后的反思，`backpropagated` 表示把有边界的 reward 沿路径回传，`node_pruned` 表示剪枝，`search_terminated` 表示达到证据、预算或覆盖终止条件。`visits` 是访问次数，`value_sum` 是累计奖励，`mean_value=value_sum/visits` 是平均回报，`prior` 只在 PUCT 中参与先验奖励，UCT/PUCT 分数只决定下一步探索顺序，不是根因可信度或 Evidence。

严格冻结回放每次以新的 `client_run_id` 创建新 Diagnosis；幂等重试同一 ID 返回同一会话，不同运行共享稳定快照摘要，但使用不同诊断命名空间，因而节点 ID、事件 ID 和 effect key 不冲突。白名单 manifest 本身以 `lats.replay_snapshot_frozen` 保存并校验 hash；后续 `Selection → Expansion → Evaluation → Simulation → Backpropagation → Reflection` 通过普通 `lats.*` 事件逐帧落盘，Worker 或页面重启后可以从快照事件和事件序列恢复，而不是依赖内存树。

本轮 LATS 代码验收覆盖 canonical UCT、可选 PUCT、Top-K 兄弟、SC 缺失语义、真实/冻结 observation、reflection、reward/backprop、预算停止、事件恢复、snapshot reset proof、会话 namespace、旧案例不补造指标，以及页面 FULL/BUDGETED 语义。云端发布后又通过 `scripts/verify_lats_replay_showcase.py` 从公网 API 创建两条全新会话完成验收；发布目录与报告 hash 见上文，不能用本地测试替代这份证明。
