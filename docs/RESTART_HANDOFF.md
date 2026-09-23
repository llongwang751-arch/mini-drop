# Mini-Drop 重启交接点

## 2026-09-23 AGI-saber 请求级接入

最终发布：平台 `/opt/mini-drop-current` → `20260923T105000Z`（只更新 Web），Python Worker/Analyzer/Chroma 为 `20260923T101442Z`，办公助手 `/opt/agi-office/current` → `20260923T105939Z`。公网真实问答和浏览器通过；诊断 `insight_31065996e81542658cd1800f2bdb00a4` 附着真实请求并采到 15 个系统样本，仍是 `INSUFFICIENT_EVIDENCE`，正常窗口卡片显示“未确认故障”。不要把重复问答的耗时差当作修复效果。详情和回滚见 [发布记录](../reports/architecture/agi-saber-rag-release-20260923.md)。

办公助手最终滚动到 `/opt/agi-office/releases/20260923T105939Z`。首轮真实知识库问答返回 HTTP 200、答案校验通过，并生成同一 trace ID 的阶段快照；该次业务执行约 969 ms，其中生成约 942 ms、检索约 2 ms。快照在 `/var/lib/agi-office/mini-drop-observations/requests.json`，只含有界数字与状态，宿主业务根目录保持 0700，快照文件 0644，专用子目录由 Worker 只读挂载。平台完整接入发布 `/opt/mini-drop-releases/20260923T101442Z`，最终 Web 发布 `20260923T105000Z`，最终云端激活状态与页面验收以 [发布记录](../reports/architecture/agi-saber-rag-release-20260923.md) 为准。不得把请求内历史阶段耗时当作稍后进程采样的同一时间窗，也不得因为正常窗口结果就把根因门禁降为 VERIFIED。办公助手回滚是原子切换 `/opt/agi-office/current` 到 `20260922T094352Z` 并重启 systemd；平台回滚用新发布目录的私有 compose 配置，保留旧卷和证据。

## 2026-09-22 可观测摘要与业务指标发布（最新）

当前平台 `/opt/mini-drop-current` 指向 `20260922T100300Z`，Web 为该标签镜像，Diagnosis Worker、Analyzer、Chroma 沿用 `20260922T094352Z` Python 镜像；四项均 healthy，公网健康三依赖 healthy。办公助手 `/opt/agi-office/current` 指向 `20260922T094352Z`，PID 会随重启变化，不得写死；ASGI 指标快照已由真实 Agent 采进 `application_metrics_analysis.v1` Evidence。正常窗口会显示进程 CPU/RSS/线程/FD，业务请求指标收在展开区；0 表示已接入但窗口无请求，“未采集”表示没有测量。回滚平台使用本版 `private/rollback.compose.json`，办公助手把 current 原子指回 `20260913T092221Z` 后重启；不要删除卷、历史 Evidence 或旧发布。发布细节见 [记录](../reports/architecture/observability-release-20260922.md)。

## 2026-09-19 规划预算增量（最新）

当前云端 `20260919T141800Z`，仅 Worker 增量：范围 25 秒、规划单轮 60 秒、每请求最多 45 秒、摘要 10 秒；采集准入和审批后执行保留 30 秒分析/报告余量，总 300 秒不变。Verifier 明示未覆盖条件索引及独立对照缺口，循证门槛不降低。本地 156 passed、2 skipped，真实检索门禁通过。首轮 141100Z 的三个采集成功但模型全部超时，因此不能称 Agent 验收通过；修订结果和原始记录见 [预算记录](../reports/architecture/agent-deadline-20260919.md)。旧发布、索引、卷与故障实验记录均保留；不要用清理换空间。

修订实测已完成：`insight_106b58e1477b48919b61199881240f12`，191.12 秒，三轮 MODEL / MODEL_REPLAN、3/3 采集、13 产物及13证据、真实混合检索、故障清理全部通过；零 VERIFIED。故障已停止，没有待清理的本轮注入。不要再次重跑同批测试来“确认完成”；下一工作应针对 Python 采样口径与独立对照，以及工作记忆 verification 对象过大（曾超20KB）、确定性缺口→工具匹配，仍未实现这些事项。LATS 与 ReAct 的严格盲测比较、同负载修复闭环未完成。

## 2026-09-19 Agent 三路检索与工作记忆发布（最新）

当前云端 `/opt/mini-drop-current` 指向 `20260919T133900Z`，Diagnosis Worker 镜像 `mini-drop-knowledge:20260919T133900Z`。本批发布三路召回与 SQL 当前调查工作记忆；其余服务沿用上一版。知识仍为 17 份文档、39 块，新快照 `md-knowledge-71ac29e6e40c93255b0f152b830e977c`；实际后端 `BM25_ENTITY_CHROMA_RRF_RERANK`。旧双路索引及回滚镜像保留。 本版 LATS 新回归在第三项采集时达到 300 秒总预算，最终 CANCELLED，清理成功；不能宣称完整链路验收通过，失败记录见本批设计报告。

产品要求已明确为“基础链路上增加 SRE 诊断 Agent”，循证与性能诊断树固定保留。当前实现边界、开源源码对照与分层设计见 [诊断 Agent 设计](../reports/architecture/sre-diagnosis-agent-design-20260919.md)。不能称为已完成节点内多步 ReAct 的完整 LATS，也不能称已稳定达到 VERIFIED 根因。

## 2026-09-19 知识发布与采样排查（最新）

当前云端指针为 `20260919T132400Z`，Diagnosis Worker 使用 `mini-drop-knowledge:20260919T132400Z`；其余服务沿用上一版。39 块知识的实际混合检索开发集通过。回滚使用该目录 `private/rollback.compose.json` 只恢复 diagnosis-worker，再把 current 指回 `20260919T123800Z`；私有配置不可打印。131700Z、132000Z 的结果目录权限失败记录保留，未发布。

最新严格 ReAct 回归 `insight_33b3eaf7b0ec44cd83fbd06848654a9a`：3/3 工具成功、13 证据/13 产物、模型与混合检索通过、清理成功；报告仍未 VERIFIED。

Python 默认采样会把 sleep 线程算作热点，GIL 对照约 99.4% 命中源码热点；尚未修改原生采集器，不可称诊断已修复。下一步依 [质量改进记录](../reports/architecture/sre-quality-roadmap-20260919.md) 处理采样语义、独立对照、同负载修复和配对实验。禁止启动本机 Docker 或删除云端卷。

## 2026-09-19 v5 云端发布完成（最新）

当前 `/opt/mini-drop-current` 指向 `20260919T123800Z`。使用云端入口，不启动本机 Docker。三台 Agent 在线，Runtime `diagnosis-agent-v5-retrieval`、PostgreSQL Checkpoint 正常，Chroma 实际混合检索通过。ReAct/LATS 两条真实链路各 3 次工具成功，但根因仍未 VERIFIED。

本次三个更新服务和 Chroma 使用 `/opt/mini-drop-current/private/runtime.compose.json`；其中含凭据，不得打印或提交。其他原服务继续运行，不使用 `--remove-orphans` 或 `down -v`。旧版镜像和配置、所有卷与历史报告保留。完整命令和证据见 [云端发布记录](../reports/architecture/cloud-release-20260919.md)，下方旧版本状态不再作为最新交接依据。

## 2026-09-19 云端已恢复（最新）

用户确认云服务器恢复，后续使用 `https://120.24.187.205/ai-diagnosis`，不再启动本机 Docker。控制面健康、三台 Agent 在线；两个 Worker 的 SSH 隧道和采集 Agent 已恢复，业务容器与数据保持原样。云端仍是 9 月 14 日版本，9 月 19 日 Agent v5 改动尚未发布。详见 [恢复记录](../reports/architecture/cloud-recovery-20260919.md)。下方本地优先指令已被本节取代。

## 2026-09-19 本地运行优先

云服务器已过期，当前操作不连接云端。先打开桌面 Docker Desktop“修复启动”版，执行 `./scripts/local_sre.ps1 Start`；页面为 `http://127.0.0.1:18080/ai-diagnosis`。具体依赖、构建、索引和停止方式见 [REPLICATION.md](REPLICATION.md#2026-09-19-windows-本地-sre-环境)。不要重置 Docker 或删除数据卷；本地环境与旧项目使用不同 Compose 项目名。下方旧云端版本仅供历史参考。

## 2026-09-19 本地 Agent 改造交接

当前新增能力与限制见 [Agent Runtime](AGENT_RUNTIME.md) 和 [实施报告](../reports/architecture/performance-sre-agent-implementation-20260919.md)。本轮没有发布云端；不要把本地测试当成线上验收。Embedding/Reranker 的真实合成查询已通过，事故准确率及 ReAct/LATS 同负载对照尚未评估。旧 95.2% 草稿撤回为无证据结论，历史原始报告保持不变。

## 2026-09-14 清理版本已发布

已发布 `/opt/mini-drop-releases/20260914T073540Z`，更新 Web、Diagnosis Worker、Analyzer。三个 Agent 在线，五个业务被发现；四个轻量业务网页返回 200，34 个公网静态文件与本机构建哈希一致。删除旧组件和死代码、精简文档；采集、数据库 schema 与业务数据不变。发布镜像、回滚版本和检查见 [发布记录](../reports/cleanup-release-20260914.json)。下方带日期的记录属于历史批次，21 场景严格成绩仍为 1 项通过、20 项未通过。

## 2026-09-14 最新交接：四个轻量业务已运行

优先读 [服务接入](SERVICE_INTEGRATION.md) 和 [四业务验收](../reports/business-acceptance/轻量业务接入与验收-20260914.md)，最新发布状态在该报告的数据目录 `final-state.json`。Memos/Files 在 Worker 1，linkding/ntfy 在 Worker 2；原办公助手继续运行。平台“接入服务”可以打开原网页、选择真实网关请求并绑定当前进程诊断。业务数据在 `/var/lib/mini-drop-business/<id>`，禁止清除。

先前“仅选型、没有部署”段落是历史记录。当前八项基础操作通过，四个最新 AI 会话仍为证据不足；15/16 采集任务成功，不能称四个根因均验证。不要恢复旧 Agent：旧版本缺少 JVM 运行时检查，会向非 JVM 业务发送 SIGQUIT。原生三台已部署检查；未知绑定不会因为服务名或用户写了 Java/Go 而得到专用工具授权。第一次失败、第二次 ntfy 轮询限流和最终持续订阅记录独立保存。

File Browser 已归档，只保留隔离演示，文件 API 同时要求平台与应用认证。ntfy 使用持续订阅，不恢复每秒轮询；浏览器桌面通知需要用户授权。网页标题测试源已停、延迟归零，有界流量都已结束。业务服务与日志轮转 timer 正常驻留。下一步是函数阶段/数据库观察、真正业务缺陷定位及同负载修复复测；Vikunja、Miniflux 后续再扩展，不把设计或受控延迟撤销冒充完成。



## 2026-09-13 下一阶段：从业务请求进入诊断

该阶段的轻量业务选型现已执行，最新状态见顶部。请求关联之外的函数阶段、数据库连接器与同负载修复闭环仍待完成，实施范围统一维护在 [业务接入设计](BUSINESS_ONBOARDING_DESIGN.md)。

## 2026-09-13 完整后台服务接入

完整办公助手已常驻，源码、业务库、模型配置、请求关联、进程重启发现与诊断结果见 [服务接入](SERVICE_INTEGRATION.md)。180 请求验收流量已结束；不得恢复旧临时适配器冒充完整后台。

## 2026-09-13 实际 RAG 后续执行

原 RAG 路径已经找到并接入；隔离检索对照、首次误选目标及正确重跑见 [实际 RAG 复盘](../reports/business-acceptance/实际RAG优化与AI联调-20260913.md)。临时测试容器已停止、证据保留，常驻服务以顶部状态为准。

## 2026-09-13 业务验收模块已发布

HTTP 样例、测量合同、CI 测试计划与页面只读结果见 [业务验收](BUSINESS_ACCEPTANCE.md)。样例仍用于回归，不代表原项目；“尚未提供原仓库路径”已是失效待办。

## 2026-09-10 验收口径更正：21 条链路不等于 21 个根因已验证

9 月 8 日旧合同只检查采集链路，不强制 VERIFIED 与修复比较。9 月 10 日严格复验已执行 21 项：Java GC 通过、20 项未通过；随后四项复测仍未达严格门禁。见 [严格验收协议](FAULT_PLAZA_ACCEPTANCE.md) 和 [验收结论](../reports/ai-diagnosis/21场景验收结论-20260910.md)。旧原始报告保留。

## 2026-09-10 全面检查修复（已发布）

Agent 趋势、幂等冲突事务、Outbox 租约并发及 V2 受限账号拒绝策略已修复，见 [全面检查](../reports/ai-diagnosis/项目全面检查与修复-20260910.md)。当前尚不支持 V2 端到端资源范围隔离；数据库 head 保持 `20260910_0008`。

## 2026-09-10 Skill 页面展示调整（已发布）

Skill 示例与沉淀已改为验证中心页面内展示，沿用原有评测、发布、隔离与回滚接口。不要恢复旧大弹窗。

## 2026-09-10 用户截图问题修复（已发布）

工具名、树图例、根因与修复状态、自身指标和兼容迁移已修复，见 [截图问题修复与验收](../reports/ai-diagnosis/截图问题修复与验收-20260910.md)。三个 Agent 均已升级，Worker SSH 访问已恢复。主机 I/O 不得冒充目标进程根因；缺失指标不能填零。数据库回滚不得移除新增列或使用不认识当前 head 的镜像。

## 2026-09-09 后续完善已发布

Java 图体、perf 有效样本检查、JVM 规划与模型反证纠正已修复，见 [后续完善与验收](../reports/ai-diagnosis/后续完善与验收-20260909.md)。旧 Python Agent 已停止且 `restart=no`；备份、独立测试库/桶及原始证据保留。长期压力和生产随机 A/B 尚未完成。

## 2026-09-09 其余 17 场景全链路复验与标签同步

历史 17 条链路与清理记录见 [17 场景复验](../reports/ai-diagnosis/故障广场其余17场景全链路复验-20260909.md)。当前按更严格的根因和恢复标准展示，不能沿用当时的 21/21 绿色标签作为最终成绩。

## 重启后从这里继续

2026-09-09 前端改进已发布到云端：首屏输入、报告摘要前置、紧凑驾驶舱、拓扑优先和刷新失败保留数据。该次目录为 `/opt/mini-drop-releases/20260909T111410Z`，只更新 Web；公网 45 个静态文件与本地构建一致，后端容器 ID 均不变。代码及验证入口见 `PROJECT_CONTEXT.md` 的“前端工作台收尾”。本地合成数据截图与线上真实数据截图分别保存，不能把它们作为新的故障注入验收。

候选已通过 Web 33 文件/137 tests、生产构建、bundle 检查及本地 Chromium 合成数据回归。预览可运行 `node scripts/verify_frontend_workbench.mjs --serve`，监听 `127.0.0.1:5174`；它只展示测试数据，不连接云端。

用户刚完成“参考 LATS，实现可在页面演示的完整 LATS”这一轮开发，准备重启电脑。重启后的新会话不得把这一轮当成未开始，也不要重复创建一套平行实现；先核对线上健康状态，再从用户新的验收反馈继续。

当前用户最关心的是：所有 Agent 能力必须在页面中可演示，而不只是后台测试。页面应保留多轮对话、动态树、人工干预、Agentic RAG、Skill 复用与禁用对照、指标弹窗、故障广场和可重复的新诊断会话。

用户在重启后再次要求确认“是否真的能在页面演示”，并要求一份专用演示文档。`docs/INTERVIEW_DEMO_GUIDE.md` 已作为现场入口：按“点哪里、说什么、应看到什么、失败怎么办”组织，后续页面验收反馈优先同步到该文档和对应权威专题文档。

## 2026-09-09 深入学习教材

用户要求将演示截图、文字/按钮/输入框、目录与文件职责、基础链、AI 诊断、前置知识和面试题集成。已在唯一 `PROJECT_LEARNING_GUIDE.md` 扩充第 0 节、逐页操作、两条源码追踪、40 个专题问答和自动目录/文件字典。HTML 由 `scripts/render_learning_guide.py` 生成，输出在 `output/learning-guide/`，不是新架构来源。新云端截图及控件清单在 `docs/assets/learning-guide/20260909/`，旧截图未覆盖。

初次制作教材时仅只读取证，当时发现 Java 图体空白且随机实验/偏好写入尚缺验收。此问题已在本文顶部的后续完善中解决；原截图与当时记录仍保留，不能再把旧待办当成当前状态。

## 2026-09-09 历史线上基线（当前版本见顶部）

- 当前版本目录：`/opt/mini-drop-releases/20260909T153610Z`，`/opt/mini-drop-current` 指向该目录。Web 版本 `20260909T152520Z`，演示 Agent 原生采集器版本 `20260909T151902Z`；Diagnosis Worker 与挂载 Skill 正文已跟随最新目录。回滚镜像见对应 `hardening-deploy-*.json`，恢复时只滚动受影响服务。
- Java 历史报告 `insight_c23e8c442c2343f1bd10cb39bb8666af` 现在在页面显示“阶段性根因”：对象分配热点位于 `Hotspot.lambda$startWorkers$1`，样本占比 100%，主要对象为 `byte[]`，同时明确尚未独立证明 GC 暂停或锁竞争。旧 Report 本身没有被回写。
- 历史系统指标任务 `task_20260907_072702_5a7b9c` 现在显示 15 个真实 v1 样本、RSS、线程和 FD；CPU、负载、I/O 与网络显示未采集，不再出现空白卡片。
- 该版本修复了真实 LATS 的轮次停滞：树节点的出生深度继续保存在 `Hypothesis.round_index/tree_depth`，有 Report 的真实执行轮次改用单调递增的 `lats.node_selected.iteration`。回溯旧 sibling 时既保留真实父子结构，也能推进到第 3/4 轮。
- 后续发布只重建并滚动替换受影响服务；ECS、PostgreSQL、MinIO 及其数据卷没有删除或重建。
- `/opt/mini-drop-releases/20260906T115542Z` 是废弃 staging，`20260906T123931Z` 是本地作废包，均不得切换为 current。`20260906T125357Z` 是前一可回滚版本；它的安全源码包 SHA-256 为 `078D15D7099ECAF47B82228FD42EA261E9B1BE02386E2178A9FB378B177A06F3`。
- 页面入口：`https://120.24.187.205/ai-diagnosis`。
- 页面和 6 个静态资产均返回 HTTP 200；真实公网健康接口是 `/api/healthz`，返回 Control Plane、PostgreSQL 和 Diagnosis Worker 均 `healthy`。公网 `/readyz` 当前会被 Nginx SPA fallback 返回 `index.html`，其 HTTP 200 不能作为 readiness JSON 证据；容器内部 `/readyz` 正常。
- 2026-09-06 16:18（UTC+8）再次排查“网页无法访问”：服务端与 443 端口均正常，原因是 Windows 重启后浏览器不信任项目私有 CA。公开证书 `CN=Mini-Drop Private CA` 已导入当前 Windows 用户的受信任根证书库，Thumbprint 为 `DBB1906B932906DB6756E3339F2FE1C6C84AEF1A`；未导入私钥。Edge 与 Chrome 实际内核均已成功渲染 AI 诊断页面。旧的 360 浏览器进程可能需要完全退出后重新打开，才能重新加载系统证书库。
- Control、API、Analyzer、Diagnosis Worker、PostgreSQL、MinIO、Web 和演示服务均在运行；核心容器健康检查通过。
- AI 诊断页已有单主视图、多轮输入、全宽/全屏树、人工干预、Agent Cockpit、可追溯 RAG、Skill 路线和真实故障广场。
- 验证中心已有“完整 LATS 冻结回放”；入口为“AI 诊断 → 验证与 A/B → 故障广场 → 完整 LATS 冻结回放 → 新建回放会话”。
- 每次点击都创建新的 REPLAY Diagnosis，不复用历史诊断结果。
- 最新候选代码已实现中文优先的规划输出。必要技术名词保留英文；整段英文或非法模型结构由服务端替换成可审计的中文确定性规划兜底，不把任意翻译当作新结论。
- 实时自主诊断最多 4 轮。证据反驳、证据不足、部分支持、工具不可观测或策略/人工门禁拒绝时，会记录观察、反思、负向奖励/回传和方向切换，再扩展尚未覆盖的证据域；规范化去重后只保留一个 `OTHER/UNKNOWN` 未知兜底。
- 当前线上故障广场共 21 个白名单场景，覆盖 Python 7 个、Go 4 个、Java 5 个、C++ 5 个；最新 live A/B 的预检已从公网 API 确认数量为 21。
- 页面默认先展示 Go、Java、C++、Python 各一个推荐场景，并可按运行时筛选。2026-09-08 已在同一线上版本顺序重跑全部 21 个场景：Python 7/7、Go 4/4、Java 5/5、C++ 5/5，Diagnosis 链和故障清理均为 21/21。机器报告为 `reports/ai-diagnosis/fault-plaza-full-21-final-v2-20260908.json`，规范化 payload SHA-256 为 `c8f7d0413fce94601cd905238533553f2d20509ae2492035e03f8c78faa07eea`；它仍是受控 Linux live E2E，不是生产准确率。
- 仓库 `skills/catalog.json` 当前有 13 个已登记 Skill。Skill 只提供路线 Prior，命中或未命中都必须为本次 Diagnosis 重新取证。
- 最新候选已经补齐 Skill 的三处缺口：进程内 BM25 + 512 维本地哈希 n-gram/领域概念特征可跨初始类别纠偏；命中仓库 Skill 后校验并加载完整 `SKILL.md`；初始、反馈、人工干预、可信反证和证据不足路径都会持久化跨轮复用/换路/退出轨迹。动态探索树新增独立的虚线“Skill 调查路线”，展示召回、完整正文加载、当前步骤和逐轮状态，但不把它计入 Evidence。
- 当前 Skill 检索没有使用 Elasticsearch、向量数据库或外部 embedding。Skill 数量仍为 13 时以可复现的进程内扫描为准；只有规模和实测延迟达到瓶颈后才评估独立索引服务。
- 故障广场会话要求至少 3 个“已经持久化 Report 的不同诊断轮次”。单个分支证据不足时保持调查态并自由跨域重规划，只有预算/工具/证据域穷尽后才把整个会话终结为证据不足。
- Skill A/B 面板把 query、开始时间和 AUTO/DISABLED 两条 Diagnosis ID 保存到同一浏览器的 `localStorage`（最多 12 组），返回验证中心后按 ID 重取服务端实时状态；它不是账号级或服务端实验历史。

## LATS 事实口径

- 冻结、可复位的白名单环境运行 `FULL_LATS / FROZEN_REPLAY`，实现 Selection、Expansion、Simulation、Evaluation、Reflection、Backpropagation、剪枝和预算终止。
- 真实云端诊断运行 `BUDGETED_LATS / LIVE_PROGRESSIVE`。真实系统会随墙钟时间变化，不能把不可回滚现场伪称为完整可复位 LATS。
- 支持 UCT，并保留显式 PUCT 扩展；树、访问次数、价值、选择分数、反思、最佳路径和终止原因来自服务端持久事件。
- `tree_depth` 表示候选在树中的出生层级，`lats.node_selected.iteration` 表示真实选择/取证轮次。最少轮次门禁和页面轮次按有 Report 的 iteration 计算；旧记录缺少选择事件时才回退出生层级。
- 页面上的事件和评分采用中文解释：`node_selected` 是选中分支，`observation_recorded` 是记录观察，`reflection_recorded` 是记录反思，`backpropagated` 是奖励回传，`node_pruned` 是剪枝，`search_terminated` 是终止；`visits`、`value_sum`、`mean_value`、`prior` 与 UCT/PUCT 分数只用于搜索排序，不是 Evidence 或根因置信度。
- 冻结回放不连接真实 Agent，不创建真实 Task、Artifact、Evidence 或 Report，页面必须持续显示这条事实边界。

## 最新代码与线上回归基线

最新已保存的代码回归与业务采集结果见 [四业务验收](../reports/business-acceptance/轻量业务接入与验收-20260914.md)。当前代码修改仍需重新运行对应检查，旧报告不自动背书新版本。

历史逐次 A/B 的诊断 ID、工具顺序、样本数、发布目录与 SHA-256 统一从 [AI 与 Skill 测试报告](../reports/ai-diagnosis/AI诊断与Skill复用测试报告-20260906.md) 和 `reports/ai-diagnosis/` 原始报告读取。现场操作见 [演示指南](INTERVIEW_DEMO_GUIDE.md)，不在交接文档重复维护多套旧成绩。

## 权威实现与教学入口

- 项目总状态：`docs/PROJECT_CONTEXT.md`
- 页面演示脚本：`docs/INTERVIEW_DEMO_GUIDE.md`
- 从 0 到 1 教学：`docs/PROJECT_LEARNING_GUIDE.md`
- AI 诊断语义：`docs/AI_DIAGNOSIS.md`
- Agent Runtime、Harness、上下文和记忆：`docs/AGENT_RUNTIME.md`
- Skill：`docs/SKILLS.md`
- 部署复刻：`docs/REPLICATION.md`
- LATS 核心：`server/app/drop_insight/lats.py`
- 事件存储原语（幂等追加/语义去重/outbox/CAS，从 service 拆出的叶子模块）：`server/app/drop_insight/event_store.py`
- 假设谓词计算（覆盖槽位按判据文本匹配，不硬编码槽位）：`server/app/drop_insight/hypothesis_predicate.py`
- 报告结论渲染：`server/app/drop_insight/report_conclusion.py`
- 冻结回放提供者：`server/app/drop_insight/frozen_replay_showcase.py`
- 页面回放面板：`web/src/components/LatsReplayPanel.jsx`
- 公网验收脚本：`scripts/verify_lats_replay_showcase.py`
- 总教材的逐文件字典生成器：`scripts/generate_learning_guide_file_index.py`；仓库结构改变后运行它，更新 `docs/PROJECT_LEARNING_GUIDE.md` 第 34 节，不另建平行教材。
- 总教材的页面截图：`docs/assets/learning-guide/`；`scripts/capture_learning_guide_screenshots.py` 使用临时无头浏览器只读抓取当前云端页面，凭据只从进程环境变量读取，不写入截图或文档。

## 尚未被本轮声称完成的事项

- 用户尚未完成最终人工页面验收；重启后应根据用户截图继续修复交互问题。
- 21 个场景已有历史采集链路，严格根因验收仍有 20 项未通过；故障撤销与业务缺陷修复需要分别验证。
- A/B 页面能建立 AUTO 与 DISABLED 两条真实路线；服务端随机分流与统计接口已经实现，但仍缺生产随机样本与长期效果验收，策略发布保持人工门禁。
- “自进化”目前是生成候选 Skill、离线评测、人工发布与回滚，不允许在线自动修改生产 Prompt、代码或权限。

## 恢复工作时的安全约束

- 工作树包含用户已有的大量修改，禁止 reset、checkout 覆盖或清理不相关文件。
- 不删除 PostgreSQL/MinIO 卷、Docker 部署文件、测试集、验收报告或教学文档。
- 云端变更继续使用版本目录和 `/opt/mini-drop-current` 原子切换；不要在当前目录直接做不可回滚覆盖。
- 退役 Python Agent `mini-drop-jyl-worker-agent-1` 已停止且禁止自动重启；当前使用三个 C++ Agent，不恢复退役服务。
- 不在聊天、文档、日志或提交中输出密码、API Key、SSH 私钥和 `deploy/env/control.env` 内容。
