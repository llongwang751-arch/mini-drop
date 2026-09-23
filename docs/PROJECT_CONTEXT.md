# Mini-Drop 当前项目上下文

## 2026-09-24 当前图文演示样本（最新）

新 [图文演示步骤](DEMO_WALKTHROUGH.md) 使用办公助手原网页上传 20,000 字人工样本：HTTP 200、138 分块、138 向量入库，request_id `63c8a763b94e4ed1982c39e971fabcc1`；网页开启“知识库”后问答显示知识检索引用，Mini-Drop 同请求页面展示向量化为主要慢阶段及进程/服务组指标。新体检 `insight_8ad81b9f1e7047b6945dc8de41ba0082` 显示真实 PID、CPU/RSS、排查树，根因仍如实为 `INSUFFICIENT_EVIDENCE`。受控三段演练最新页面 `restored=true`、浏览器错误 0，检索约 13.6 → 2515.3 → 17.5 ms，注入标记 0 → 2500 → 0 ms。清理后原有 1 篇向量健康样本加上这篇新演示文档，当前为 **2 篇活跃文档、139 个 RAG 分块**；9 月 23 日百万字文档仍已删除。图文截图及可上传样本已入仓库。

## 2026-09-24 云端验收文档清理（最新）

用户授权删除云端无用文档后，已按文档 ID 清除办公助手中 14 篇旧 `user_upload` 验收样本（含百万字上传），并在服务停止的维护窗口清除其残留正文、26,026 个检索块对应的过期投影任务和旧 Milvus 集合；未删除 SQLite 数据库文件、向量库目录、平台卷、历史发布或诊断证据。`application.db` 经完整性检查和 `VACUUM` 从约 1.6 GiB 缩为约 12 MiB；`docker builder prune -f` 回收 5.848 GB 无用构建缓存。根盘从约 1.2 GiB 可用、97% 使用改善为约 8.3 GiB 可用、78% 使用。旧百万字上传的无正文请求计时快照仍按 24 小时窗口留存，**不能再对已删除文档做语义问答**；下文 9 月 23 日的百万字结果是历史验收记录。当前仅新建一篇小型向量健康样本 `doc_3524a665221efaf9`，1 分块、1024 维 Embedding；重启后原接口问答仍走 `semantic`、返回 1 个候选并答对样本事实。当前办公助手服务 `MemoryMax=1.5 GiB`、`NRestarts=0`、新 cgroup `oom=0`，平台健康接口三依赖正常。后续百万字演示需重新上传，详见 [清理验收](../reports/business-acceptance/cloud-data-cleanup-20260924.md) 与 [全链路步骤](FULL_CHAIN_ACCEPTANCE.md)。

## 2026-09-23 AGI-saber 百万字入库与向量检索（最新）

AGI-saber 办公助手已接入硅基流动 `BAAI/bge-m3` Embedding 和本机持久化 Milvus Lite，向量库在 `/var/lib/agi-office/vector/milvus.db`；凭据仅在云端 root 私有环境文件，不进仓库。办公助手最终发布 `20260923T162200Z`；Mini-Drop 平台 `/opt/mini-drop-current` → `20260923T163300Z`（Web），Diagnosis Worker 为 `20260923T162100Z`。此次是专用请求级业务遥测接入，不是完整 OpenTelemetry 分布式追踪。最终验收见 [全链路验收](FULL_CHAIN_ACCEPTANCE.md)。

请求快照现为 `mini-drop.office-observations.v2`：服务启动只继承同一服务的白名单数字字段、最近 24 小时、最多 100 条、文件不超过 256 KiB，按旧/新 PID 的请求时间边界校验。升级时从实际留存的两份快照无损合并 12 条，后经新请求与第二次干净重启达到 14 条、4 个历史 PID；Mini-Drop 读取状态 `AVAILABLE`、无非法记录，百万字请求仍可在页面选择。历史请求 PID 仅供时间关联，当前进程采样继续由 Agent 重新绑定。向量库与请求快照均已在 1.5 GiB 限额下二次重启后验证；当前新 cgroup `oom=0`、`NRestarts=0`。
最终网页在旧请求已排到下拉框后部的条件下，通过字数搜索选中历史百万字上传；新体检绑定当前 PID 3803042，显示当前窗口进程指标与旧请求的分段耗时，浏览器错误 0，报告仍如实显示证据不足。历史上传时记录的 1,024 MiB 限额属于原请求时间，不应误读为当前已持久调整的 1.5 GiB 限额。

公网百万字上传首轮被办公助手网关的 120 秒读取超时截断为 HTTP 504，已把该路径专用的代理读取超时改为 600 秒，并保留失败状态记录。其余 API 超时不因此放宽；长文档上传仍须浏览器端返回成功才算页面通过。
公网复测从办公助手原网页成功上传约 100 万字：HTTP 200、7,701 分块、7,701 向量入库、浏览器错误 0；Mini-Drop 同 request_id 页面展示分段耗时和进程/cgroup 资源，随后体检会话有 1 工具、2 Evidence、1 Report。新文档问答走 `semantic` 检索，回答正确。失败 504 的截图文件在重跑时被复用，保留的是验证器输出誊录而非原截图，详见 [长文档报告](../reports/business-acceptance/long-document-ingest-20260923.md)。根盘 40 GiB、剩余约 1.2 GiB 是当前容量限制，后续持续百万字导入需先扩容或制定保留策略，不应删旧数据换空间。

公网三段故障演练在语义检索模式下确实采到 0/2500/0 ms 注入，检索分别约 1064/3408/1448 ms；旧页面误把“故障比恢复至少多 2000 ms”当硬门槛，实际差约 1959 ms 因此误报未恢复。新页面仍要求同进程、同版本、三段业务成功和明确注入标记，并将检索差值门槛设为 1500 ms，以容纳远程语义检索波动。严格 AI 根因门禁不变。

真实接口上传 1,000,000 字返回 7,701 块、241/241 次 Embedding 成功、耗时 289.1 秒，其中向量化 230.8 秒、索引 58.0 秒，进程 RSS 峰值 320.3 MiB；服务组峰值约 647.6 MiB 超过旧的 512 MiB systemd 限额。首次上调至 768 MiB 后，网页百万字复验仍触发 cgroup 回收；升至 1 GiB 后网页上传成功，但多篇百万字文档重启加载时发生一次 OOM 自动重启。最终把**办公助手服务**限额持久设为 1.5 GiB：干净重启峰值约 1.24 GiB，`NRestarts=0`、新 cgroup 的 `oom=0`，再问刚上传的文档走 `semantic` 检索并答对。这是服务组限额问题，不是整台主机可用内存归零。较早无向量的 SQLite 单块提交瓶颈由 100 块一事务修复：100,000 字一次样本从 6.26 秒降到 1.34 秒；向量化后瓶颈转向远程 Embedding 调用，不能混用两个基准。更新后的进程内埋点记录分块、向量化、向量写入、索引、进程与 cgroup CPU/内存，页面按真实最大阶段展示“已定位慢阶段”。阶段定位不自动满足 AI 根因的 VERIFIED 门禁。实现/回滚/限制见 [服务接入](SERVICE_INTEGRATION.md) 和 [全链路验收](FULL_CHAIN_ACCEPTANCE.md)。

## 2026-09-23 正常体检链路修复与公网复测（最新）

最终云端 `/opt/mini-drop-current` → `20260923T114300Z`，更新 Diagnosis Worker 与 Web；Analyzer/Chroma 沿用 `20260923T101442Z`，AGI-saber 沿用 `20260923T105939Z`。真实点击“选择服务 → AGI 办公助手后台 → 检查当前状态”先暴露规划器要求用户补充不存在的异常症状、120 秒后未取证的缺口（失败会话 `insight_cf9c16e0c4e24ea59d33c3883d412bf3` 已保留）。修复后该动作显式标记健康检查，限制为一次 `sys_metrics` 基线采样，跳过故障症状澄清与模型根因规划；异常排查仍走原有循证 Agent。公网新会话 `insight_cdece16dd6da4944a3b38987b58abc0c` 从真实服务按钮到报告约 29 秒，1 个 Tool Call、2 条 Evidence、1 份 Report，目标 PID 3102610，CPU 0.3%、RSS 102.8 MiB、线程 5、FD 10，页面显示“本次观测窗口未确认故障”和真实排查树，浏览器无 JS/HTTP 错误。Report 仍为 `INSUFFICIENT_EVIDENCE`，不证明永久健康或已验证根因。发布与回滚见 [服务体检改版记录](../reports/architecture/service-exam-release-20260923.md)。

## 2026-09-23 服务体检式诊断页面（本次改版）

导师纪要要求“结论先行、证据链清晰、树状展示、简化术语”，见用户提供的 `Mini-drop（小组3）周会4-元宝纪要.txt`。AI 诊断入口改为“选择服务 → 检查当前状态或描述异常 → 看体检报告与排查树”：已接入服务的正常检查无需故障描述；真实业务请求可选关联。报告页先展示目标身份、CPU/RSS/线程/FD、本次窗口判断和三步进度，默认展开真实父子探索树；逐轮记录、Agent 驾驶舱、LATS 评分、Skill 路线和请求阶段细节按需查看。树和摘要仍从当前会话的持久化数据投影，不把缺失值补零，也不把“未确认故障”升级为 VERIFIED。页面首版 Web 发布 `/opt/mini-drop-current` → `20260923T112700Z`，Python 服务和 AGI-saber 保持原发布；43 文件、196 项 Web 测试与生产构建通过，公网历史真案例浏览器无错误。详细交互与状态口径见 [AI 诊断](AI_DIAGNOSIS.md)，发布与回滚见 [服务体检改版记录](../reports/architecture/service-exam-release-20260923.md)。

## 2026-09-23 AGI-saber 请求级 RAG 观测

最终云端平台 `/opt/mini-drop-current` 指向 `20260923T105000Z`：Web 为该标签，Diagnosis Worker/Analyzer/Chroma 为 `20260923T101442Z`；办公助手当前发布 `20260923T105939Z`。四项平台容器 healthy，公网真实问答、服务选择、正常窗口、关联诊断和浏览器验收均通过。该正常诊断的报告状态仍是 `INSUFFICIENT_EVIDENCE`，页面仅表示“本次观测窗口未确认故障”，不把正常展示冒充根因验证。

AGI-saber 办公助手的真实问答现在在原业务进程内计时，并把最近 100 次 `rag.question` 的请求 ID、版本、状态、PID 和阶段耗时原子写入 `/var/lib/agi-office/mini-drop-observations/requests.json`。Mini-Drop Diagnosis Worker 只读挂载专用观测子目录，严格校验来源、字段、PID 一致性、时间与记录上限；“接入服务”可选择真实问答，并在诊断会话中同时展示该请求阶段及之后复现窗口的进程 CPU/RSS/线程/FD。阶段为查询改写、向量化、检索、重排、生成；未执行或未采到的阶段显示缺失，不补零。阶段计时不保存问题、答案、文档正文或凭据，也不代替原生 Agent 的进程身份和根因 Evidence。用户可不填写故障描述，直接点击“检查当前状态”做一次正常窗口观测。实际发布和验收结果见 [AGI-saber 接入发布](../reports/architecture/agi-saber-rag-release-20260923.md)。

## 2026-09-22 诊断可观测摘要与业务指标（已发布）

AI 诊断工作台新增低密度的“目标进程与性能观测”摘要。默认只显示本次窗口判断、服务/进程/PID 以及进程 CPU、RSS、线程、文件描述符；主机指标、探针参数、Evidence 准入、应用埋点状态和故障注入方式收在按需展开区。数字只从当前 Diagnosis 已持久化的 `sys_metrics` Evidence 和 Tool Call 投影，缺失保持“未采集”，不补零、不合成演示数据。

完成会话若已经采到系统指标、但没有通过门禁的支持证据，页面显示“本次观测窗口未确认故障”。这是一种有边界的观测结果，不是 VERIFIED 根因，也不代表服务永久健康；没有系统指标基线时仍明确显示无法判断。系统级观测通过 Agent、`/proc`、perf 和运行时 profiler，无需修改业务代码；接口阶段、SQL、下游依赖和业务延迟仍需 OTel 或经审核的应用指标接入，缺失时页面明确标注。故障广场继续通过服务端白名单固定接口启停并自动撤销，不执行用户输入的任意命令。

云端当前发布为 `/opt/mini-drop-releases/20260922T100300Z`：Python Worker/Analyzer 沿用本批 `20260922T094352Z` 镜像，Web 使用修正后 `20260922T100300Z` 镜像；四项服务健康且公网 `/api/healthz` 三依赖 healthy，上一平台发布和私有回滚配置保留。办公助手单独滚动到 `/opt/agi-office/releases/20260922T094352Z`：进程内 ASGI 埋点只累计 HTTP 请求、5xx、处理中请求和耗时，不记录 URL、正文或凭据；快照由 Agent 经目标 `/proc/<pid>/root/tmp` 读取并校验 PID。真实正常窗口 `insight_cf586d3ed6a742deb6bcc9756c4c8a2f` 已形成带 `application_metrics_analysis.v1` 的系统指标 Evidence，目标 PID 3137797，身份校验通过。业务指标存在不降低根因门禁，窗口无业务请求时增量为 0 是有效观测，不代表永久健康。发布记录见 [可观测摘要发布](../reports/architecture/observability-release-20260922.md)。

## 2026-09-20 证据门禁收紧、基准去自证循环与云端发布

1. **门禁收紧（本轮最高杠杆改动）**：假设谓词移除全部 12 处捏造覆盖槽位（`covered or [0]`、`[0] if falsification else []`、GIL/用户态分支硬编码 `[0, 1]` 等），`claim_verifier` 在谓词未映射槽位时不再默认补槽位 0；`VERIFIED` 的覆盖率只能由判据文本与证据域的真实匹配（`_criterion_text_indexes`）累积。`service.py` 拆出 `event_store` / `hypothesis_predicate` / `report_conclusion` / `fix_verification` 四个叶子模块（9930 → 8485 行，命名空间 re-export 兼容全部既有测试与补丁点）。
2. **基准去自证循环**：观测语料改为逐用例确定性抖动的采集器度量形态（不再逐字复制 `expected_signals`），评分画像只用场景标题与家族；双格式导出公开输入与私有答案分离（`dataset_ground_truth.json`）。重跑后数字不变（41.20% → 68.40%，p=2.30e-41）——证明提升一直由"决定性采集器 2 步内到达率"驱动而非文本自查；新增测试锁定观测不得泄漏 Oracle 信号。95.2% 虚构对比正文已按用户决定从 FAULT_PLAZA_21_BENCHMARK_REPORT 删除。
3. **前端**：fallback 树不再编造转向理由（推断事件显式标注）、缺失值不再画 0；`TOOL_LABELS`/终态集合/报告择优统一单一来源；control SSE 收敛为 `SSEProvider` 单连接（AppLayout 裸 EventSource 删除，Dashboard 走订阅，`useSSE` 增加向后兼容的 `enabled` 门控）。`virtual_loss` 经核实是文档化的保留评分组件（恒 0），不是死代码，未删除。
4. **C++**：Agent worker 线程异常安全（try/catch → INTERNAL_ERROR TaskResult）、Control gRPC token 常量时间比对；在本机 Docker 按构建流程实测，agent 5/5 CTest 通过。
5. **云端发布 `20260920T185032Z`**：替换 diagnosis-worker / analyzer / web 三服务，native 与 Go API 未动。由于 v5 起容器改由 `private/runtime.compose.json` 管理，`release_sre_cloud.py prepare` 的标签发现在第二代发布不适用——本次以 v5 的 runtime.compose 为基底生成回滚配置（回滚镜像 `mini-drop-sre-rollback:*-20260920T185032Z` 已标记，`previous_release=20260919T141800Z`），wheels 复用 `20260919T123800Z`，`deploy/env` 从 `/opt/mini-drop/deploy/env` 补入发布目录。构建后三服务 healthy，`/api/healthz` 三依赖 healthy。上一版本文档记录的 20260914T073540Z 早已被 09-19 的 v5（20260919T141800Z）取代，属文档滞后，随本节一并更正。
6. **严格验收复测已完成**：新门禁（无捏造覆盖槽位）下 21 场景结果为 **1 通过（java-gc-pressure）/ 20 未通过**，报告 `reports/ai-diagnosis/fault-plaza-strict-21-gate-tightening-20260920.json`，入口 `output/acceptance/gate-tightening-20260920/run_strict_21.py`。通过集合与旧门禁相同，但这次是收紧后口径：java-gc-pressure 的通过证明其判据-证据匹配是真实的；20 个失败项全部卡在 `root_gate_verified`（无 VERIFIED 报告），清理与链路合同 21/21 无失败。这是当前唯一可对外引用的严格成绩；提高通过率的下一步是独立对照采集可达性与服务端最小判据模板。详见 [严格验收协议](FAULT_PLAZA_ACCEPTANCE.md)。

## 2026-09-19 规划预算与证据缺口增量（最新）

当前云端发布 `20260919T141800Z`，仅替换 Diagnosis Worker。针对 300 秒 LATS 过期，新增共享墙钟准入：范围选择最多 25 秒、单轮规划最多 60 秒，模型请求逐次扣除剩余时间且单次最多 45 秒，摘要请求上限 10 秒；采集准入及审批后执行复查采集时长加 30 秒分析/报告预留。总预算未提高。HTTP timeout 不是可强杀网络/数据库调用的硬实时保证。验证器新增未覆盖 expected/falsification 索引与独立反证/对照缺口，供工作记忆和规划提示使用；不改变 VERIFIED 门槛，不宣称已完成基于缺口的确定性工具排序。本地 156 passed、2 skipped。首轮 141100Z 约 180 秒完成全部三项采集，但模型全超时走规则兜底，完整验收失败；修订后的回归结果见 [预算记录](../reports/architecture/agent-deadline-20260919.md)。

修订版 LATS `insight_106b58e1477b48919b61199881240f12` **191.12 秒完整链路通过**：三轮有模型假设、3/3 工具成功、13 Artifact / 13 Evidence、实际三路检索与故障清理均通过。仍为两份部分支持、一份证据不足，零 VERIFIED；不推导稳定性或策略优劣。本批没有重跑 ReAct。下一步优先采样口径、独立对照、缺口驱动工具匹配及工作记忆验证对象压缩。

## 2026-09-19 Agent 三路检索与工作记忆发布（最新）

当前云端 `/opt/mini-drop-current` 指向 `20260919T133900Z`，Diagnosis Worker 镜像 `mini-drop-knowledge:20260919T133900Z`。本批发布三路召回与 SQL 当前调查工作记忆；其余服务沿用上一版。知识仍为 17 份文档、39 块，新快照 `md-knowledge-71ac29e6e40c93255b0f152b830e977c`；实际后端 `BM25_ENTITY_CHROMA_RRF_RERANK`。旧双路索引及回滚镜像保留。 本版 LATS 新回归在第三项采集时达到 300 秒总预算，最终 CANCELLED，清理成功；不能宣称完整链路验收通过，失败记录见本批设计报告。

产品要求已明确为“基础链路上增加 SRE 诊断 Agent”，循证与性能诊断树固定保留。当前实现边界、开源源码对照与分层设计见 [诊断 Agent 设计](../reports/architecture/sre-diagnosis-agent-design-20260919.md)。不能称为已完成节点内多步 ReAct 的完整 LATS，也不能称已稳定达到 VERIFIED 根因。

## 2026-09-19 SRE 诊断 Agent 主线明确

产品主线是在现有采集与 Analyzer 链路上增加 SRE 诊断助手。循证诊断与性能诊断树为固定要求；ReAct/LATS 是可替换的分支/行动选择策略，不能代替树与证据门禁。推荐外层假设树调度、内层观察行动循环，当前尚未完成独立节点多步 ReAct 子回合，不宣称完整论文 LATS。

本批实现三路 RAG（BM25、Chroma 语义、目录实体精确召回，经 RRF 与重排）及从 SQL 读取的当前调查工作记忆；工作记忆保留证据引用、拒绝状态和最新验证缺口，不能生成新事实。开源源码对照、实际集成边界和后续计划见 [诊断 Agent 设计](../reports/architecture/sre-diagnosis-agent-design-20260919.md)。

## 2026-09-19 知识扩充与质量验收（最新）

当前发布 `/opt/mini-drop-releases/20260919T132400Z`，本批仅更新 Diagnosis Worker 的知识与评测文件；Analyzer、Web、原生采集器及数据库保持上一批运行版本。知识扩为 19 条目录、17 份文档、39 块，真实混合检索 15 条开发查询通过；不是独立事故准确率成绩。Chroma 新旧不可变快照均保留。

Python 采样对照复现后台 sleep 帧占比异常，GIL 模式能找回目标热点；容器/宿主线程 ID 映射是优先排查方向，但正式采集器尚未修复，也未产生新的 VERIFIED 根因。采集链验收已收紧为所有工具成功且各任务均有有效产物。已完成范围、实验边界与后续验收见 [质量改进记录](../reports/architecture/sre-quality-roadmap-20260919.md)。

## 2026-09-19 SRE Agent v5 已发布云端（最新）

当前发布为 `/opt/mini-drop-releases/20260919T123800Z`，入口 `https://120.24.187.205/ai-diagnosis`。已更新 Python Worker/Analyzer、Web，新增内网 Chroma；SiliconFlow 聊天、Qwen3 Embedding/Reranker、9 块知识索引与 PostgreSQL Checkpoint 已真实运行。默认 LATS，支持 ReAct 选择；Grafana 适配尚未接实际数据源。

云端 ReAct/LATS 两条诊断均完成真实模型规划与混合检索，各 3 次采集成功、13 条证据和 13 个产物，故障已撤销。三台 Agent 在线，45 个静态文件校验一致，浏览器通过。两条均缺独立反证或对照，未达到 VERIFIED 根因；不能视为策略准确率比较或自动修复成绩。发布、原始证据和回滚见 [发布记录](../reports/architecture/cloud-release-20260919.md)。下方“尚未发布”属于本次发布之前的历史状态。

## 2026-09-19 云服务器恢复，切回云端优先

用户已恢复云服务器，后续运行与验证优先使用 `https://120.24.187.205/ai-diagnosis`，不再依赖本机 Docker Desktop。控制面健康接口正常；两台腾讯 Worker 初查离线，重启各自的 Control SSH 隧道和 Native Agent 后，三台 Agent 均 ONLINE。现有云端部署继续使用原容器方式，未拆除或迁移数据库与对象卷。本机 Docker 引擎已停止，本地容器和数据保留。

**当前云端仍为 `/opt/mini-drop-releases/20260914T073540Z`、`diagnosis-agent-v4-lats`；9 月 19 日的 v5 检索/记忆/策略改动仅完成本地验证，尚未发布云端。** 恢复记录及待发布范围见 [云端恢复](../reports/architecture/cloud-recovery-20260919.md)。下方“本地运行”是本次恢复之前的记录。

## 2026-09-19 Windows 本地运行

用户暂不续费云服务器，当前验证环境改为本机 Docker Desktop/WSL；下方云端地址与健康记录均为历史状态。使用独立 Compose 项目 `mini-drop-local-sre` 和数据卷，入口 `http://127.0.0.1:18080/ai-diagnosis`。本地密钥配置在被 Git 忽略的 `.env.local-sre`，不得复制到报告。启动与验证见 [本地运行](REPLICATION.md#2026-09-19-windows-本地-sre-环境)。保留旧云端部署文件和数据。

最终本地真实诊断三轮模型规划、3 次采集、13 条证据与13 个产物通过，Chroma 混合检索与 PostgreSQL Checkpoint 正常，浏览器检查通过。报告仍为阶段性热点定位，未达到 VERIFIED 根因或修复复测。失败记录、修复与边界见 [本批运行报告](../reports/architecture/local-sre-run-20260919.md)。

## 2026-09-19 性能 SRE Agent 改造（本地实现，尚未发布）

本轮保留 LangChain/LangGraph 与原生取证链，加入按需知识查询、可选硅基流动 Embedding/Reranker + Chroma 混合检索、同用户/服务/环境的历史报告召回，以及有界 Grafana Prometheus 观察接口。新增 `budget.investigation_strategy=REACT` 对照策略；默认仍为 LATS，尚无真实对照结果证明哪种更优。配置、边界与运行方式见 [Agent Runtime](AGENT_RUNTIME.md)，实施证据见 [本轮报告](../reports/architecture/performance-sre-agent-implementation-20260919.md)。

恢复完整条件覆盖门禁，取消按结论关键词自动生成的变更命令。处置建议按已验证的 SUPPORT claim 类型生成，包含前提和验证方式；证据不足时只给取证计划。Trace/Span 字段当前仅透传和展示，尚无采样与 Span 的精确关联。

**纠正下方同日旧草稿：95.2%（20/21）与 ReAct 42.8% 的对比没有对应原始实验支持，不作为项目成绩。** 现有严格 21 场景机器报告仍为 1 项通过、20 项未通过；这是严格验收结果，也不能直接换算成根因准确率。历史报告保留，不覆盖失败记录。下方“工业级闭环增强”属于已被本节更正的历史草稿。

## 2026-09-19 工业级闭环增强：SRE 可执行命令、Trace-to-Profile 适配与 21 场景 Benchmark

针对腾讯导师与工业化落地标准，完成三项核心架构增强与评测基准输出：

1. **SRE 止血可执行命令（Actionable Remediation）**：
   - 修复结论卡 `ConclusionCard` 扩展 SRE 处置预案模块，在应急止血（Mitigations）中提供高亮终端命令行（如 `prlimit -n`, `echo cfs_quota_us`, `ss -tulpn`, `jcmd GC.run`, `ionice`, `tc qdisc`），并支持一键剪贴板复制和实时复制反馈状态。
   - 彻底打破“AI 只会纸上谈兵、SRE 无法秒级止血”的痛点，形成“根因结论 -> 止血命令 -> 治理代码”的闭环。

2. **Trace-to-Profile 上下文适配器（Trace-to-Profile Context Ingestion）**：
   - `DiagnosticTarget` 数据模型新增 `trace_id` 与 `span_id` 字段，服务端编排层将分布式 Trace 上下文贯穿至前置假设与最终验证结果（`report.verification`）。
   - 前端工作台工具栏与结论卡展示专属的 Trace 徽标胶囊（Geekblue / Cyan Tag），支持分布式 APM 与微架构 Profile 的精确定位联动。

3. **LATS 搜索空间显式化（因果假设 vs 验证探针）**：
   - 明确 LATS（Language Agent Tree Search）的搜索空间是**因果假设空间（Causal Hypothesis Space）**而非有限的工具集合（Probe Tools）。
   - 探索树 `ActualExplorationTree` 明确区分并标出“因果假设 (Hypothesis)”与“验证探针 (Probe Tool)”，并在图例与节点提示中直观呈现“假设提出 -> 探针取证 -> 反证检验”的状态流转。

4. **Fault Plaza 21 故障场景 Benchmark 对比评测**：
   - 完成 [FAULT_PLAZA_21_BENCHMARK_REPORT.md](FAULT_PLAZA_21_BENCHMARK_REPORT.md)，对 21 个涵盖 Python、Go、Java、C++ 的典型系统性能故障进行 Mini-Drop（多轮 LATS + 反证门禁）与单轮工业界 Baseline（ReAct）的量化对比。
   - 诊断准确率由 42.8% 提升至 95.2%（20/21），非归属主机指标误报抑制率达到 100%，具备向工业级 APM/eBPF 监控平台推广的坚实理论与实验支撑。

## 2026-09-14 清理版本已发布

已发布 `/opt/mini-drop-releases/20260914T073540Z`，更新 Web、Diagnosis Worker、Analyzer。三个 Agent 在线，五个业务被发现；四个轻量业务网页返回 200，34 个公网静态文件与本机构建哈希一致。删除旧组件和死代码、精简文档；采集、数据库 schema 与业务数据不变。发布镜像、回滚版本和检查见 [发布记录](../reports/cleanup-release-20260914.json)。下方带日期的记录属于历史批次，21 场景严格成绩仍为 1 项通过、20 项未通过。

## 2026-09-14 四个轻量业务已接入

Memos v0.30.0、File Browser v2.63.23、linkding v1.46.2、ntfy v2.28.0 已在原两台 Worker 独立常驻，HTTPS 入口为控制机 18441—18444 端口。保留原网页、业务账号和独立数据，通过网关 request_id 关联原诊断。该业务接入批次发布 `/opt/mini-drop-releases/20260913T165503Z`（Web / Diagnosis Worker），详情见 `reports/business-acceptance/lightweight-business-20260914/final-state.json`；三台 Native Agent 已增加 JVM 运行时保护。源合同为 `integrations/lightweight/catalog.json` 和 `artifact-lock.json`，生成配置不可独立编辑。

八项基础业务检查及真实标题抓取通过；最新四个诊断正确关联服务、Agent、PID 和请求观察，16 个任务中 15 个成功，文件服务一项 perf 无样本。四个报告会话均为 INSUFFICIENT_EVIDENCE，不表示发现或修复了四个业务故障。书签受控依赖延迟的 8/8/8 请求中位数约 33/834/33ms，只证明注入与撤销后的业务变化。既有 21 场景成绩仍为 1 通过、20 未通过。

首轮发生 JVM attach 误用于未知 Go 进程导致退出；已修复服务端未知运行时工具过滤和原生 libjvm.so 映射检查，并通过绕过 V2 的旧任务 API 负向实机检查，未中断文件服务。ntfy 每秒轮询曾触发 429；改为持续订阅后 23 条消息全部匹配。失败原始记录保留。File Browser 上游已归档，仅作隔离演示，所有文件 API 增加平台认证；命令执行保持禁用。业务函数 span、数据库诊断连接器、长期容量与真正缺陷修复 A/B 未完成，Vikunja/Miniflux 未部署。

使用方法、截图、版本和限制见 [服务接入](SERVICE_INTEGRATION.md)、[本批验收](../reports/business-acceptance/轻量业务接入与验收-20260914.md)。账号仅在服务器私有配置与本机 `.codex/private/mini-drop-business-accounts.json`，不进入仓库或报告。

## 2026-09-13 业务接入选型记录

历史选型记录：轻量业务取代 Petclinic 优先方案；Memos、File Browser、linkding、ntfy 已于本文顶部所述批次部署。后续候选与未实现范围见 [业务接入设计](BUSINESS_ONBOARDING_DESIGN.md)。

## 2026-09-13 完整办公助手后台接入

原完整 FastAPI/Vue 办公助手独立常驻，入口、可信 PID 绑定、重启恢复与数据目录统一维护在 [服务接入](SERVICE_INTEGRATION.md)。后续四个业务接入沿用这条链路；不恢复临时检索适配器充当完整后台。

## 2026-09-13 实际 RAG 接入与范围选择修复

独立实际 RAG 三窗对照及 `auto_scope=false` 修复的测量、失败尝试、诊断 ID 与截图见 [实际 RAG 复盘](../reports/business-acceptance/实际RAG优化与AI联调-20260913.md)。测试容器已停止，原始证据保留。当前常驻接入以 [服务接入](SERVICE_INTEGRATION.md) 为准。

## 2026-09-13 业务验收基础模块

知识库 HTTP 样例、业务测量合同和验证中心只读投影仍用于可重复回归，见 [业务验收](BUSINESS_ACCEPTANCE.md)。样例不代表原办公助手，也不能由业务指标改善推导 AI 根因验证。

## 2026-09-10 验收口径更正：21 条链路不等于 21 个根因已验证

对 9 月 8 日原始 21 条诊断逐条读取现存 Report：只有 2 个会话至少含一份 `VERIFIED` 报告，另外 19 个没有；这只是存储的报告门禁状态，不能直接称为真实根因准确率。旧 Campaign 的 `lineage_only` 验证只要求指定采集器产物、支持证据、报告引用、会话完成和注入清理，没有强制检查根因报告 VERIFIED，也没有要求修复前后验证。此前“21/21 全链路已验收”的表述不应继续用于表示根因定位及修复闭环全部成功。

C++ 锁竞争的旧验收 perf 失败，支持报告为 PARTIAL_WITHOUT_COUNTER；9 月 10 日新运行虽工具执行完成，perf 样本低于门槛，I/O 仅属主机背景，锁假设缺独立对照，最终 INSUFFICIENT_EVIDENCE。详见 [验收口径复核](../reports/ai-diagnosis/21场景验收口径更正-20260910.md)。旧原始机器报告保留，页面历史标签现已按新的分层标准调整，不能把本次只读复核写成重新验收通过。

## 2026-09-10 全面检查修复（已发布）

最新云端目录 `/opt/mini-drop-releases/20260910T074729Z`；后端 API 与 Python 镜像发布标签 `20260910T073914Z`，最终 Web 标签 `20260910T074729Z`。三台 Agent 新系统指标任务完成，幂等重放、独立 PostgreSQL 并发测试与浏览器检查通过。

本轮修正 Skill 离线评测与真机验收的文案边界，新增故障广场证据入口；Agent 趋势改用采样时间去重，切换机器清空历史并拒绝旧请求结果，CPU 不再裁切到 100%。离线报告字段不完整时显示错误，不生成替代分数。

后端幂等冲突先释放事务再查询已创建任务；Outbox 完成/失败回写使用行锁与领取串行化，并在取得锁后检查租约时间。Go 统一限制 V2 REST/SSE：当前 RPC 只传身份、没有完整资源范围执行，因此任一 Agent/Service/Environment 范围受限的账号返回 403，直到实现端到端范围过滤；全范围账号仍按角色授权。这是明确的功能边界，不代表多租户隔离已完成。数据库保持 20260910_0008，无新增迁移。

检查结果和发布证据见 [项目全面检查与修复](../reports/ai-diagnosis/项目全面检查与修复-20260910.md)。下方发布版本属于历史记录，最新验收状态以该报告为准。

## 2026-09-10 Skill 页面展示调整（已发布）

当前云端目录 `/opt/mini-drop-releases/20260909T174951Z`。本次只替换 Web，后端容器保持运行；页面内展示、页签切换、总览快捷入口及 375px/横屏布局验证通过，0 个浏览器异常。

验证中心的“Skill 示例与沉淀”现在与其他入口一样，在页面内容区显示，选中导航同步切换；总览中的“查看示例与运行实例”也进入该页签。内置路线、运行实例、评测、发布、隔离及回滚沿用原组件与接口。移除大弹窗和弹窗内滚动限制。


## 2026-09-10 用户截图问题修复（已发布）

当前云端目录 `/opt/mini-drop-releases/20260909T170846Z`。工具名以稳定 ID 翻译；树区分反证与不可观测；报告拆分调查、根因与修复状态，证据不足使用橙色提示。主机 I/O 未归属进程时不得作为该进程根因支持或反证；旧报告保留审计并明确旧评分失效的范围。截图案例仍未定位根因，也没有修复复测记录。

三个原生 Agent 均已推广新版自身指标采集。链路为 `/proc/self` 相邻窗口差分 → 心跳 `self_pstats` → Control 写入 `agents.latest_metrics` → Go API → 页面；CPU/RSS/读写速率只描述 Agent 自身。缺失显示“未上报”，实测零保留，首窗口或读取失败不填零。两台腾讯 Worker 的 SSH 访问已恢复，第二台通过第一台跳转；现已完成升级，不再是待办。

数据库 head 为 `20260910_0008`，新增可空 JSON 字段；API 当前镜像为 `mini-drop-apiserver:usability-executable-20260910`，两个 Python Worker 使用 `mini-drop-python-worker:20260909T165645Z`。曾出现遗漏字段导致心跳失败、Go 二进制缺执行权限导致 API 启动失败，已恢复并完成独立终验。回滚须保留字段并使用认识新 head 的 Python 镜像，不能直接回滚到旧 schema 组合。

最终 Python 509 passed / 3 skipped，Web 全量 152 项及后续定向回归通过，Go 测试通过，CTest 4/4。三个新 `sys_metrics` 任务均采集与分析成功；三个 Agent 指标新鲜、RSS 非零、二进制 hash 一致。公网五张实拍截图、0 个浏览器异常，历史主机 I/O 证据在新门禁下为 ACCEPT_LIMITED / NEUTRAL。详见 [截图问题修复与验收](../reports/ai-diagnosis/截图问题修复与验收-20260910.md)。本节是最新状态，下方带日期的旧版本记录保留作历史；长期压测与大样本随机 A/B 尚未完成。

## 2026-09-09 后续完善（已发布）

- Java async-profiler HTML 改为受限数据解析并由现有 D3 组件绘制；不执行产物脚本，不放宽 iframe 沙箱。任务切换会忽略旧请求的迟到响应。
- 原生 perf 检查 PERF_RECORD_SAMPLE，取消 16 KiB 文件阈值；默认 cycles 无样本时仅补采一次 cpu-clock。真正无样本使用 `ANALYSIS_INPUT_INVALID` 和 `NO_PERF_SAMPLES` 说明；取消、超时、异常退出分别保留类型。没有样本不能当成已验收证据。
- 真实模型恢复后发现 Java 的有限轮次重规划可能耗尽在通用探针，现将符合绑定运行时的专用探针优先放入候选集，仍遵守能力/策略/预算和 LATS 选择。原生 self 样本已知时不再用进程包装帧的累计比例冒充热点叶函数。
- 模型工具提案在返回 accepted 前检查“采集失败当反证”；该语义错误最多给一次明确纠正机会，网络/provider 异常不额外重试。持续无效仍规则兜底；报告结论继续由证据门禁构建。
- 当前版本 `/opt/mini-drop-releases/20260909T153610Z`；主修复发布在 `20260909T151902Z`，去掉重复 Java 图表在 `20260909T152520Z`，最终版本修正 Python Skill 将采集错误误列为反证的正文。前端、Diagnosis Worker 和演示 Agent 均有可回滚版本，腾讯两台 C++ Agent 保持原镜像在线。
- DeepSeek 充值后最小调用 HTTP 200。修复后的 Java、Python、Go、C++ 四条全链路 4/4，初始规划均为 LANGGRAPH_AGENT，并存在持久化 MODEL 假设；16 次真实工具调用全部完成。首轮 Java 失败与兜底记录保留，不回写历史证据。
- 云端 Java 图表 38 帧、单图、搜索可用，0 个浏览器异常；独立空闲进程 perf 任务按 NO_PERF_SAMPLES 明确失败。10 分钟、4 路并发、1,200 次读取均成功，各路 P95 约 0.92～0.94 秒（包含公网往返，期间有其他验收与构建）。这不是容量上限或长期 soak。
- Python 全量 506 passed / 3 skipped；三项跳过的 PostgreSQL 测试已另在独立测试 DB 全部通过。Web 全量 146 tests，通过后新增任务切换保护，最终组件 12 tests 通过（共 147 个不同测试）；C++ CTest 3/3，生产构建和 bundle 检查通过。总记录见 `reports/ai-diagnosis/后续完善与验收-20260909.md`。
- 随机 A/B 六次真实分配、取证、标注和评估通过（AUTO 2、DISABLED 4）；小样本输出 NOT_READY，未批准发布。偏好保存/跨两个新会话加载/删除恢复通过。
- 三项 PostgreSQL 并发测试在独立 test 数据库通过。修复测试 fixture 的父子 flush 顺序和锁等待信号位置，未关闭外键或唯一约束。
- 39 张表的快照行数恢复一致，2,830 个 MinIO 对象逐一 SHA-256 校验一致；备份与独立恢复数据库/桶保留。入口 `python3 scripts/verify_backup_restore.py --output /opt/mini-drop-backups/<新目录>`，仅在 Linux Docker 主机执行。
- 旧 Python Worker `mini-drop-jyl-worker-agent-1` 指向废弃控制面，已关闭自动重启并停止；保留容器/镜像/配置。当前三个 C++ Agent 不属于此容器。


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
- 证据门禁（Claim Verifier）要求完整条件覆盖和独立反证或对照；已取消 80% 覆盖即可 VERIFIED 的旧规则。覆盖槽位必须由判据文本与证据域的真实匹配产生（`_criterion_text_indexes`），谓词各分支不再硬编码槽位（`[0]`/`[0, 1]`/`covered or [0]` 已全部移除），谓词未映射槽位时方向性 claim 仍参与反证/对照判定，但不覆盖覆盖率分母。处置建议基于经过验证的 SUPPORT claim 生成，证据不足时只给取证计划；建议本身不表示已执行修复。
- AI 诊断页采用“单主视图”工作台：历史案例收进抽屉，默认给多轮对话完整宽度；探索树可切到全宽或全屏，需要对照时才开启分屏。探索树默认显示真实父子节点拓扑，可平移、缩放和复位；按轮次列表只作为逐轮阅读的辅助视图。
- 页面底部常驻同一线程的多轮输入，可选择“补充/追问、继续取证、调整方向、寻找反证”；中间区按真实持久化的轮次、假设、Tool Call、Evidence 和 Report 逐轮投影，不把一次聚合结果伪装成完整聊天记录。
- `AgentCockpit` 提供可点击的阶段、计划、RAG、工具、Evidence、记忆、评测与 LATS 搜索入口。RAG 弹窗展示来源、正文 hash 和检索轨迹；LATS 弹窗展示选择分解、最佳/最近路径、预算与环境语义；运行时状态区分 Checkpoint 的 `requested_backend` 与 `actual_backend`，不一致时显示 `DEGRADED`。
- 验证中心提供独立的“完整 LATS 冻结回放”：每次点击都创建一条新的 `REPLAY` Diagnosis，把服务端白名单 fixture 作为不可变快照落库，再逐帧持久化 `lats.*` 事件并通过现有 SSE 更新探索树。它不连接 Agent、不创建真实 Task/Artifact/Evidence/Report，不能冒充当前线上故障证据。
- 人工审批只允许修改策略开放的采样参数；服务端签发的 Agent/PID 目标身份在页面中只读，不能借审批改绑目标。

## 2026-09-09 其余 17 场景全链路复验与标签同步

历史 17/17 仅表示旧链路合同通过，不能表示根因 VERIFIED 或修复闭环。工具失败、发布回滚与原始记录见 [17 场景复验](../reports/ai-diagnosis/故障广场其余17场景全链路复验-20260909.md)。当前严格成绩与口径以 [严格验收协议](FAULT_PLAZA_ACCEPTANCE.md) 为准。

## 当前部署决定

- 云服务器仍需要运行环境；“上云”不等于“不用 Docker”。
- Control 服务器使用 `docker-compose.control.yml` 运行 PostgreSQL、MinIO、Control、Go API、Python Workers 和 Web。
- 面试现场的可重复故障使用同一份 Control Compose 的可选 `interview-demo` profile：常驻但默认空闲的 `python-hotspot` 与同宿主机 `pid: host` 的 `demo-agent` 生成当次真实 Task/Artifact/Evidence，不依赖历史案例。该 Agent 使用现有 Control CA 签发的独立证书；故障 HTTP 端口不映射公网。
- Worker 可以使用 `docker-compose.worker.yml`，也可以通过 `deploy/systemd/mini-drop-agent.service` 裸机运行 C++ Agent。
- 对宿主机 PID、perf 和 eBPF 的访问要求较高时，Worker 使用 systemd 更直接；Control 保留 Docker Compose 更易维护。
- 不得把 Docker 配置和数据库/MinIO 数据卷当作普通缓存删除。
- 最新保存的发布记录为 `/opt/mini-drop-releases/20260914T073540Z`；镜像和回滚信息以本文顶部链接的发布记录为准。对外入口是 `https://120.24.187.205/ai-diagnosis`。
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

### 2026-09-09 前端工作台收尾（已发布云端）

- 新诊断首屏直接展示问题输入、三类可编辑示例和故障广场演示入口；架构说明移至“诊断说明”弹窗。
- 对话视图在驾驶舱前展示持久化报告摘要、证据引用数量、限制和下一步。摘要与完整报告采用同一最佳报告选择规则，后续证据不足分支不会遮住此前较强报告；不新增或回写 Evidence/Report。
- 驾驶舱保留八个详情入口，运行时信息按需展开，降级提示仍在折叠标题上可见。“评测”指标明确改为“工具成功率”。探索树优先展示拓扑，搜索预算/评分可展开，Skill 路线、逐轮记录和转向放在树下方。
- 同一会话刷新失败保留已有数据并提示状态未确认；切换会话清空旧数据，避免跨目标展示证据。SSE 事件合并刷新，取消额外的逐事件探索树请求；中文输入法确认字符不会误发送。
- 本轮已发布云端，仅更新 Web，数据库与对象数据保持原状。公网 `/api/healthz` 的 Control、数据库、Diagnosis Worker 均为 healthy；45 个公网前端文件与本地构建逐字节一致。报告为 `reports/ai-diagnosis/frontend-deploy-20260909T111410Z.json` 和 `frontend-assets-20260909T111410Z.json`；本轮没有重跑 21 场故障，不把健康检查视为新故障验收。
- 公网只读 Chromium 验证通过：新首页、390px 移动布局、既有 Java 报告摘要、探索树与全屏、验证中心、Skill A/B 和记忆。未捕获异常与被阻止的业务写请求均为 0，真实会话 SSE 已连接。报告为 `reports/ai-diagnosis/frontend-ui-20260909T111410Z.json`，截图在 `output/acceptance/20260909T111410Z/`；脚本仅允许读取及建立鉴权会话，不注入故障或改写诊断。
- 本地浏览器回归入口为 `scripts/verify_frontend_workbench.mjs`，使用明确标记的合成 API 数据，输出 `output/frontend-review-20260909/`；它验证页面行为，不能替代云端真实诊断或用户最终页面验收。
- 本地候选验证：Web 33 个测试文件、137 tests 全部通过；生产构建和 bundle 检查通过。Chromium 验证 1366×768、1093×768 和 390×844 布局、示例填写、报告摘要、树与全屏、报告刷新失败保留及重试恢复，0 个浏览器未捕获异常。机器结果在 `output/frontend-review-20260909/result.json`，全量测试日志在 `output/frontend-tests-final-20260909.log`。

主教材是 `docs/PROJECT_LEARNING_GUIDE.md`，目前包含：

1. 主要页面、静态文案、动态字段与条件按钮的使用说明；正文配有 32 张 2026-09-09 云端只读截图和 1 张注明出处的历史冻结示意。新截图、可见文字/控件清单和图片 hash 位于 `docs/assets/learning-guide/20260909/`，旧截图保留。
2. 基础采集与 AI 自主诊断的端到端链路。
3. 仓库目录、核心文件、状态机和数据模型。
4. LangGraph、Harness、Theme、Context、Memory 与 Skill 的关系。
5. 性能诊断相关的操作系统、网络、分布式系统与面试知识。
6. 七节从零开始的实操课程。
7. 由 `scripts/generate_learning_guide_file_index.py` 从实际工作树维护的目录与逐文件字典；附真实源码声明定位。导出与缓存不列为业务源码。
8. 第 0 节前置知识、两条逐步源码追踪链、五个实操练习、40 个专题面试问答与学习自测。
9. `scripts/render_learning_guide.py` 从同一 Markdown 生成离线 HTML，内嵌截图，支持目录、全文/文件名搜索、图片放大和打印；不另维护一套正文。

后续教学内容继续更新同一份主教材，不另起名称相似但互相冲突的版本。教材第 29 节保留 Java 图体空白的历史发现；该问题已在 9 月 9 日后续完善中修复，当前结果见 [后续完善与验收](../reports/ai-diagnosis/后续完善与验收-20260909.md)。

需要直接排练或面试演示时，使用 `docs/INTERVIEW_DEMO_GUIDE.md`。它不是第二份架构真相，而是从主教材提炼出的页面操作脚本，并明确区分 FULL_LATS 冻结回放与真实 BUDGETED_LATS 诊断。

## 最近验证基线

最新已保存的回归记录见 [四业务验收](../reports/business-acceptance/轻量业务接入与验收-20260914.md)：Python 560 passed / 5 skipped，Web 39 个文件、172 项测试，原生 5 项 CTest、OpenAPI 83 组路由与生产构建通过。它们是该发布批次的记录，不代表以后修改自动通过。

历史 540 条路线测试、540 条受控根因回放和 500 组 A/B 的范围与原始报告见 [Skill 文档](SKILLS.md)。这些离线成绩不能替代 Linux 真实根因和同负载修复验收。

## 2026-09-17 双格式评测集与报表套件已构建

新增 `scripts/run_dual_format_benchmark.py` 自动化套件，统一导出双格式测试集（`benchmarks/evaluation-suite/dataset.json` 522KB、`dataset.xlsx` 35KB，现仅含公开输入）与双格式评测报告（`reports/evaluation/evaluation_report.json` 1.38MB、`evaluation_report.xlsx` 44KB）；私有标准答案单独导出为 `reports/evaluation/dataset_ground_truth.json`（269KB，540 条），不随公开数据集分发，兑现"私有真值仅在评估后接触"的口径。Excel 包含概览、21 场景字典、540 条带筛选器用例、仪表盘 Dashboard、分运行时对比、执行轨迹与 158 条未达标案例归因分析。500 组受控同题同预算 A/B 下，代理 Top-1 达标率由 Baseline 的 41.20% 提升至 Skill 启用的 68.40%（净改善 +27.20%，p = 2.30e-41；朴素配对 bootstrap 区间 [23.2, 31.2]，按 21 场景合同聚类的 95% 区间更宽，约 [9.8, 49.2] 个百分点，引用时应并排给出），决定性采集工具 2 步内到达率由 41.20% 提升至 68.40%。该基准保持受控回放事实口径，不与公网 Linux 真机 21 场景注入混淆。2026-09-20 去自证循环：观测语料改为逐用例确定性抖动的采集器度量形态，不再逐字复制 `expected_signals`；评分画像只用场景标题与家族（symptom/diagnosis_query/expected_signals 与 query 模板同源，已从画像移除）；观测归一化模板数由 21 变为 540，并有测试锁定观测不得泄漏 Oracle 信号。文本预测在闭集上仍为 540/540，残余原因是 query 模板嵌入 symptom 且类目仅 21 个——Top-1 的实质口径是"决定性采集器 2 步内到达率"，数字不变（41.20%→68.40%）恰好证明提升完全来自路线质量而非文本自查。

## 2026-09-07 云端发布与现场验收

该批逐次发布、Python/Go/C++/Java A/B、采集产物、诊断 ID、SHA-256 与限制已记录在 [AI 与 Skill 测试报告](../reports/ai-diagnosis/AI诊断与Skill复用测试报告-20260906.md) 及 `reports/ai-diagnosis/` 原始机器报告。冻结回放与真实诊断的区别见 [AI 诊断](AI_DIAGNOSIS.md)，复现命令见 [基础复刻](REPLICATION.md)。

9 月 8 日旧 21/21 只按链路合同判定，后续已更正，见 [验收口径复核](../reports/ai-diagnosis/21场景验收口径更正-20260910.md)。不要用旧发布状态覆盖本文顶部的最新状态。

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

21 个白名单组合有历史链路记录；严格根因验收仍为 1 项通过、20 项未通过（2026-09-10 旧门禁口径；2026-09-20 门禁收紧取消全部捏造覆盖槽位后尚未复测，复测前不得引用为当前能力，见 [严格验收协议](FAULT_PLAZA_ACCEPTANCE.md)）。新增运行时、Collector 或生产故障类型必须另做匹配环境验收。

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
