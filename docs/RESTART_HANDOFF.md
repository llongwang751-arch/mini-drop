# Mini-Drop 重启交接点

> 保存时间：2026-09-08。此文件只保存项目状态与恢复入口，不保存密码、API Key、私钥或其他凭据。

## 重启后从这里继续

用户刚完成“参考 LATS，实现可在页面演示的完整 LATS”这一轮开发，准备重启电脑。重启后的新会话不得把这一轮当成未开始，也不要重复创建一套平行实现；先核对线上健康状态，再从用户新的验收反馈继续。

当前用户最关心的是：所有 Agent 能力必须在页面中可演示，而不只是后台测试。页面应保留多轮对话、动态树、人工干预、Agentic RAG、Skill 复用与禁用对照、指标弹窗、故障广场和可重复的新诊断会话。

用户在重启后再次要求确认“是否真的能在页面演示”，并要求一份专用演示文档。`docs/INTERVIEW_DEMO_GUIDE.md` 已作为现场入口：按“点哪里、说什么、应看到什么、失败怎么办”组织，后续页面验收反馈优先同步到该文档和对应权威专题文档。

## 当前线上基线

- 当前版本目录：`/opt/mini-drop-releases/20260908T094648Z`，`/opt/mini-drop-current` 已原子指向该目录。该目录从已验收运行基线继承，继续保留 15 张教材截图，并新增意图子句路由、工具/假设绑定、native perf self-sample 证据语义、显式网络症状优先级和 Java 故障实验内存边界修正。数据库、MinIO 与对象数据均未重建。
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

- Python：501 passed，3 skipped，0 failed。
- Web：32 个测试文件、133 tests 通过；生产构建和 bundle 检查通过。并发全量运行曾出现统一超时，单 worker 复跑全部通过，属于测试进程资源争抢而非断言回归。
- Go：`go test ./...` 通过。
- OpenAPI：80 个 method/path 对通过。
- 受控根因评测：540 条中关闭 Skill 231/540（42.78%），启用 Skill 382/540（70.74%）；固定 500 组同题同两次工具预算为 41.20% 对 68.40%，提升 27.20 个百分点，改善 136、退化 0、关键采集器提前 187 组。这是 21 个可执行故障合同的确定性回放，不是 540 次公网真机注入。

当前版本的真实 `source-hotspot` Skill A/B 已通过：

- `DISABLED`：`insight_e581f5ac05704a2e96ba8a899a9786bd`，4 个有报告执行轮次、4 次真实工具调用、16 条 Evidence、火焰图根样本 2968、Skill 激活 0、最终置信度 0.69。
- `AUTO`：`insight_85bff86fb8fc4c9abd72e6e26496b77b`，4 个有报告执行轮次、4 次真实工具调用、16 条 Evidence、火焰图根样本 2968、Skill 激活 1、最终置信度 0.73。
- 两组均发现 `source_hot_function`，实际轮次为 `[1,2,3,4]`，LATS 精确重复事件为 0，故障清理验证通过。
- 报告：`reports/ai-diagnosis/interview-demo-live-acceptance-20260906T142517Z.json`；SHA-256：`9A8EA9DB4DF645B6F7CFD34C8A707D8E03E96FC719C604B09E320B1AC7146D3B`。

当前版本的真实 `go-cpu-hotspot` Skill A/B 也已通过，并且是面试展示多运行时路由的首选：

- `DISABLED`：`insight_5dc98348c4374b1e8a0089c88d7dfc71`，首工具为 `collect_sys_metrics`，随后执行 Go pprof、perf 与 eBPF I/O。
- `AUTO`：`insight_bcf79060ab2242e19a5131a36857a651`，激活内置 Go Runtime Skill，首工具为 `collect_go_profile`，随后执行系统指标、perf 与 eBPF I/O。
- 两组均完成 4 个真实执行轮次；pprof 火焰图与 TopN 命中 `main.goCPUHotFunction`，报告引用对应 SUPPORT Evidence；精确重复 LATS 事件数为 0，结束后故障已清理。
- 报告：`reports/ai-diagnosis/go-interview-demo-live-acceptance-20260907T003100CST.json`；SHA-256：`9E24B6528BCF289296E7C8F4B894C4A09BAF17986F2BF3406359B3AB87EEDBD4`。
- 运行时门禁会拒绝 Go 目标调用 py-spy 或 JVM Profile；旧的已审批错误调用也会在创建 Task 前失败并释放预算。
- 人类可读报告：`reports/ai-diagnosis/AI诊断与Skill复用测试报告-20260906.md`；Word 版：`reports/ai-diagnosis/Mini-Drop-AI诊断与Skill复用测试报告-20260906.docx`。Word 已逐页渲染检查并通过 0 高、0 中、0 低问题的无障碍审计。

本次 Skill 渐进式披露发布又创建了一组全新 Go A/B 验收，不复用上述历史会话：

- `DISABLED`：`insight_33af01c2aeff443ca74b0ebf1b8c7201`，路线为系统指标 → Go pprof → perf → eBPF I/O。
- `AUTO`：`insight_212795b892964cebbf059b7dfbc67085`，路线为 Go pprof → 系统指标 → perf → eBPF I/O；Skill activation 为 `skill_activation_03e924d3789840a58685d68fe7433920`。
- 两组均 `COMPLETED`，有报告轮次 `[1,2,3,4]`，各 16 条 Evidence、4 份报告，LATS 精确重复事件为 0；AUTO 树确认 `FULL_SKILL_MD`、9 个章节、有效 SHA-256、`HYBRID_BM25_VECTOR` 和 3 步路线覆盖层。
- 页面兼容层已修正：持久化的 `DEVIATED` 与 `EXHAUSTED` 不再被误显示成普通“沿用”；同一首轮幂等重试不会把历史 `ACTIVATED` 覆盖为 `REUSED`。
- 报告：`reports/ai-diagnosis/skill-route-live-20260907T062103Z.json`；SHA-256：`DC3BC84DFF73FA078B46EBB2887C7D0C795089EBDBE3490860673E23A0EB7270`；故障自动清理验证通过。

最终版本原子切换后又做了一次 fresh Go A/B，作为重启后的首选复核入口：

- `DISABLED`：`insight_9f94b3910410440b9b8c389319f3cc88`，系统指标 → Go pprof → perf → eBPF I/O。
- `AUTO`：`insight_108c07dd8c074bd0ab87ef3571ef6eb7`，Go pprof → 系统指标 → perf → 连续剖析；树内 Skill 状态为 `ACTIVATED → DEVIATED → DEVIATED`，`FULL_SKILL_MD` 共 9 个章节，6 个路线步骤已关联到真实节点。
- 两组均 `COMPLETED`、4 个有报告轮次、绑定同一不可变 PID；`DISABLED` 有 16 条 Evidence，`AUTO` 有 20 条 Evidence，故障清理验证通过。
- 报告：`reports/ai-diagnosis/skill-route-final-20260907T063732Z.json`；SHA-256：`7271D87467DF6EABC94C414AEFB619EC2E5A60DA93AD997AAB715A810B40EEA9`。

后续优先项也已有真机证据：

- C++ CPU 热点 Skill A/B：两组各 4 轮，AUTO 的 perf 产物命中 `cpp_cpu_hot_function`；报告 `reports/ai-diagnosis/cpp-cpu-hotspot-live-ab-20260907T104202Z.json`，SHA-256 `975D6DAB0532552BB2382137FC2D55D0C484EEB3F39E3038D35668371A63F303`。
- 持续 perf 与独立 eBPF Campaign：持续任务 60 秒、4 个窗口，eBPF 有非零真实样本；报告 `reports/ai-diagnosis/priority-collectors-live-20260907T0932Z.json`，SHA-256 `f14eb214ce86ca7b3f03bbd807383a059c8a5abcc52661def9fd58d1256bc559`。
- Java GC 压力最终 live A/B 已通过：报告 `reports/ai-diagnosis/java-gc-pressure-live-ab-20260907T1535Z.json`，SHA-256 `7efeb124d295b112abe67abc759856d239b6c2dd41c0ddb15ac658e4df4fb63c`。`DISABLED=insight_98f5e80170d24e078e7def200fcd6169` 终态为证据不足，`AUTO=insight_c23e8c442c2343f1bd10cb39bb8666af` 终态为带限制完成；AUTO 的 JVM allocation Profile 有 2,842 个样本、命中 `Hotspot` 并形成 1 条 SUPPORT Evidence，清理通过。两组第一步相同是共同的低风险系统基线，A/B 差异应看完整工具路线、运行时 Profile 命中、Evidence 和终态，不能只看首工具。

既有公网 FULL_LATS 冻结双会话结果仍有效：

- 公网 FULL_LATS 双会话验收：PASS。两条 fresh Diagnosis 各观察到 6 个树 revision、4 次 simulation；快照 digest 相同，节点 namespace 不相交，真实 Tool Call/Evidence/Report 行数均为 0。
- 公网验收报告：`reports/ai-diagnosis/lats-replay-acceptance-public-20260906T071335Z.json`。
- 报告 SHA-256：`D337B15661524799D72EB94AEB7588C624D27DFE34549EF4ADAF4EC408D2D9F6`。

## 权威实现与教学入口

- 项目总状态：`docs/PROJECT_CONTEXT.md`
- 页面演示脚本：`docs/INTERVIEW_DEMO_GUIDE.md`
- 从 0 到 1 教学：`docs/PROJECT_LEARNING_GUIDE.md`
- AI 诊断语义：`docs/AI_DIAGNOSIS.md`
- Agent Runtime、Harness、上下文和记忆：`docs/AGENT_RUNTIME.md`
- Skill：`docs/SKILLS.md`
- 部署复刻：`docs/REPLICATION.md`
- LATS 核心：`server/app/drop_insight/lats.py`
- 冻结回放提供者：`server/app/drop_insight/frozen_replay_showcase.py`
- 页面回放面板：`web/src/components/LatsReplayPanel.jsx`
- 公网验收脚本：`scripts/verify_lats_replay_showcase.py`
- 总教材的逐文件字典生成器：`scripts/generate_learning_guide_file_index.py`；仓库结构改变后运行它，更新 `docs/PROJECT_LEARNING_GUIDE.md` 第 34 节，不另建平行教材。
- 总教材的页面截图：`docs/assets/learning-guide/`；`scripts/capture_learning_guide_screenshots.py` 使用临时无头浏览器只读抓取当前云端页面，凭据只从进程环境变量读取，不写入截图或文档。

## 尚未被本轮声称完成的事项

- 用户尚未完成最终人工页面验收；重启后应根据用户截图继续修复交互问题。
- 受控 Python 源码热点、Go CPU 热点、C++ CPU 热点、Java GC 压力以及 `continuous_perf`/独立 eBPF Campaign 已有当前版本 live 证据；其余故障组合仍需在匹配的 Linux Worker 上逐场景收尾。21 个场景在线可枚举不等于 21 个都已跑完根因验收。
- A/B 页面能建立 AUTO 与 DISABLED 两条真实路线，但还不是带随机分流、显著性分析和自动策略发布的统计实验平台。
- “自进化”目前是生成候选 Skill、离线评测、人工发布与回滚，不允许在线自动修改生产 Prompt、代码或权限。

## 恢复工作时的安全约束

- 工作树包含用户已有的大量修改，禁止 reset、checkout 覆盖或清理不相关文件。
- 不删除 PostgreSQL/MinIO 卷、Docker 部署文件、测试集、验收报告或教学文档。
- 云端变更继续使用版本目录和 `/opt/mini-drop-current` 原子切换；不要在当前目录直接做不可回滚覆盖。
- 非 Control 项目的 `mini-drop-jyl-worker-agent-1` 在 2026-09-06 17:00 审计时持续重启（约 689 次）。它未进入 Mini-Drop 的 3 个 ONLINE Agent 列表，不阻塞当前演示；正式面试前应另行核查或隐藏，不要误当成 Control 服务故障。
- 不在聊天、文档、日志或提交中输出密码、API Key、SSH 私钥和 `deploy/env/control.env` 内容。
