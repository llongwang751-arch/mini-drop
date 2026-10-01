# Mini-Drop 重启交接点

2026-10-01 当前正在交付体检独立结束与后续新会话流程：检查 `health_check.completed` 持久化事件，不要求正常体检生成根因报告。候选和发布状态以 [项目上下文](PROJECT_CONTEXT.md) 顶部及 [AI 诊断](AI_DIAGNOSIS.md) 为准。保留旧21类、工程评分与一小时结果；本轮验收资料在 `output/acceptance/health-check-flow-20261001/`。

## 2026-10-01 默认工程诊断验收已验证上线

用户明确要求降低默认诊断门槛。新增engineering-diagnosis.v1：独立因果对照、固定轮次、必须COMPLETED和同负载修复不再作为工程诊断前提；目标身份、来源/SHA、数值真实性、对应信号域、撤销恢复与清理保持必需。严格评分器与冻结旧批次保留为专项，旧0/21不再代表当前项目完成度。数值和Profile仍为BOUNDED_OBSERVATION，不修改因果标志。

当前对上轮冻结真机三案例按新规则重评：工程诊断判断3/3、具体异常路径2/3、有效反证1条；不是新一轮真机或完整21类成绩。21类已注册，18类仍未按工程规则验收。新合同支持多个独立证据清单，通用评分器与内存域复用测试支持扩展，生成物漂移检查加入CI。页面新增当前成绩，并优先用于对应卡片；历史严格分数明确标为历史。实现与扩展流程见[工程诊断验收](DIAGNOSIS_ACCEPTANCE.md)。评分/展示源码0fa79f15的[CI36861902293](https://github.com/llongwang751-arch/mini-drop/actions/runs/36861902293)成功13/13：Python1382通过/10登记跳过、Web299通过、真实PG8通过零跳过；不可变Git源码本地Web299/构建/体积门禁通过。线上Web20261001T122233Z已由并行前端任务发布，包含相同工程组件与语义一致的生成索引；整体为冻结工作树构建、300项测试，并非本提交精确CI产物。本轮直接复验现有发布，保留其界面改动。58份Web、Worker/Analyzer各193份SHA复核，13容器健康，21故障inactive，Office PID1650962/NRestarts=0。真实Chrome新成绩、三张卡与诊断入口、正常检查、9份下载SHA和4种宽度通过，无JS/HTTP错误。未创建新诊断、注入故障或重跑已接受的一小时。原始记录见[本轮交付](../reports/architecture/engineering-diagnosis-20261001.md)与[90文件SHA清单](../reports/quality/engineering-diagnosis-20261001/manifest.json)。

## 2026-10-01 性能路径、反证与案例链接最终交付

最新Web发布`20261001T112302Z` / `ebb1a0e6a3a7396db789bb8a0a47c734d1219aff`；[精确CI36854620615](https://github.com/llongwang751-arch/mini-drop/actions/runs/36854620615)成功13/13，Python1358通过/10登记跳过、Web294通过、真实PostgreSQL8通过零跳过，Chroma与Go race/真实镜像/连续I/O专项通过。仅Web更换，其余12容器保持；Worker/Analyzer/Go demo仍为20261001T094553Z / c59aac566cedef6a239eb1d640ee8fb796524ae3。Worker/Analyzer各193文件、Web56文件SHA复核，13容器健康，API3依赖健康，21故障inactive；Office PID1650962、NRestarts=0，API与Office源码及数据保持。

新独立三案例：CPU具体热函数与HTTP耗时路径已定位，路径定位2/3；I/O同一窗口684次操作平均0.081ms，3/3数值判据已检查，REFUTED反驳慢操作假设。37份原始下载SHA一致，取证/撤销恢复/清理/收束3/3，严格因果0/3；旧21类因果0/21与原证据保留。停止前I/O成功10232次、失败0，8MiB轮转修复采样提前停止，默认64MiB tmpfs不能称真实磁盘瓶颈。

真实Chrome验证三个案例链接、CPU/HTTP路径、I/O反证与数值、健康检查正常（仅已检查范围）、默认21项/工程4项/9份下载SHA、1440/1024/768/375宽度，无JS/HTTP异常。反证展示旧代码5项失败，案例链接状态重放/列表失败重试旧代码2项失败；修复后完整Web294项通过。保留有效反证、ACCEPT_COUNTER准入和最新合格窗口；React函数状态更新保持纯函数，不再提前清空请求链接。

剩余重点是可归属的真实磁盘等待、HTTP与TCP传输拆分，以及固定输入/负载的原因干预。没有完成全部21类因果验收，不用本轮路径与反证改写旧分数。一小时按用户已接受的1/120窗超限不重跑，原严格FAILED保持。以下同日期段落为此前过程快照，以本段为当前状态。全部失败、成功、原始下载、CI与发布回执见[本轮交付](../reports/architecture/performance-localization-20261001.md)及[归档SHA清单](../reports/quality/performance-localization-20261001/manifest.json)。

## 2026-10-01 性能诊断判定与入口已部署

Worker/Analyzer/Web发布`20261001T075239Z`，源码`10002e94cf8aa46a7e4778385138c35ddfc20d7d`；Worker/Analyzer各189份文件、Web56份文件SHA一致，其余10容器保持。13容器运行及健康检查通过，API3依赖健康；Office保持observer-20260930T145902Z/f37f44e、PID484342且NRestarts=0，API保持f9b143a及原二进制SHA。21故障inactive、历史因果根因0/21。

精确源码[CI36832916434](https://github.com/llongwang751-arch/mini-drop/actions/runs/36832916434)成功13/13：Python1299通过/10登记跳过，真实PostgreSQL8通过零跳过，Web264通过；Chroma独立专项通过。本地bundle门禁通过。真实Chrome默认21项性能实验、工程4项及9份下载SHA、1440/1024/768/375宽度均通过，无JS/HTTP异常。

真实Office健康会话`insight_731431c05e5a44588cba6c4204022d54`完成1个系统采集，两份原始下载SHA一致。15样本/14.005秒、身份验证通过，CPU0.428%、RSS217.949MiB且增量0；页面显示“本次检查正常（已检查范围）”及“检查结果：正常”。会话INSUFFICIENT_EVIDENCE仍表示没有证明根因，与范围内资源正常分别展示，不代表全部业务正常。首次真实页面因ACCEPT_NEUTRAL漏判失败，原失败与同输入旧/新源码复现均保留。

归档21复盘由首次17/21纠正为18/21内部链一致，仅3项历史目标错误；不改变旧链路7/21或因果0/21，不把归档复盘算新验收。性能剩余重点是目标I/O延迟归属、TCP传输证据以及固定输入/负载下的原因干预，不继续用相同观测批次尝试刷过因果门槛。一小时不重跑。[结果范围与剩余条件](PERFORMANCE_DIAGNOSIS.md)，[本轮交付](../reports/architecture/performance-diagnosis-20261001.md)。

## 2026-10-01 性能诊断继续开发

用户重新授权处理21类性能诊断失败。恢复性能实验主入口，工程4项仍独立；先修正常结果、数值判据、窗口均值、链路/诊断结果混用，再用独立新证据验证。不能删除或重写旧0/21，不能把归档链18/21一致当新成绩，不重跑一小时。b25fb24与51228f31已完成精确CI并部署Worker/Analyzer/Web，真实正常窗口揭示ACCEPT_NEUTRAL漏判；修正本地Python1299通过/10登记跳过、Web264通过，待精确CI和再次发布。最新状态见PROJECT_CONTEXT顶部及[性能诊断](PERFORMANCE_DIAGNOSIS.md)。

## 2026-10-01 最终演示版本复核

并发案例源码入口已指向实际修改的skill_evolution.py；最终Web为 `20260930T181400Z` / `5bbeb4c`，55份文件SHA通过，其余12容器保持、13容器健康。精确源码CI [36757023388](https://github.com/llongwang751-arch/mini-drop/actions/runs/36757023388)成功13/13。再次实浏览器核验4项案例、9份下载SHA、4种宽度和旧21类停止入口全部通过，旧根因0/21与停用状态保持。修正仅涉及源码链接元数据，不修改测试、阈值或原始成绩。

默认演示是4项工程缺陷修复回归，AI自动根因NOT_EVALUATED。Python1260通过/10登记跳过、Web240通过、真实PG8通过零跳过；用户接受一小时1/120窗超限用于面试，原严格FAILED保留，不再重跑。详见[工程案例](ENGINEERING_CASES.md)。

## 2026-10-01 工程缺陷演示已部署

默认演示已改为4个有旧代码失败和修复后回归证据的工程缺陷，旧21类保留历史入口及停止控制。新Web已部署 `20260930T180319Z` / `dbdf265`，55份dist文件SHA一致，其余12容器未重建；Worker/Analyzer各184文件、API二进制和Office3集成模块保持并复核，13容器及API3依赖健康，21故障inactive、原根因0/21。

本地Python1260通过/10登记跳过，Web240通过；CI [36755582449](https://github.com/llongwang751-arch/mini-drop/actions/runs/36755582449)成功13/13，真实PG8通过零跳过，测试merge2ace4b4。生产依赖审计0漏洞；实浏览器4种宽度通过、9份下载SHA一致、无JS/HTTP错误，历史停止入口可用。生成合同与缺文件/篡改/跳过拒绝回归均通过。

新案例是工程缺陷修复回归，AI自动根因NOT_EVALUATED，不能算旧21类通过。用户接受一小时1/120窗超限用于面试，停止重复发压；原严格FAILED及200ms门槛保留。当前演示、复现及原始证据见[工程缺陷主线](ENGINEERING_CASES.md)。

## 2026-10-01 工程案例主线

用户接受小时实验用于演示，不再启动小时或21类重复验收。默认改为[4个工程缺陷](ENGINEERING_CASES.md)，21类保留历史入口及停止控制。证据由合同SHA固定、生成器维护，不手改静态成绩；工程回归与AI根因独立统计。仅Web需要发布，其他服务版本保持；状态以PROJECT_CONTEXT顶部为准。

## 2026-10-01 最终交付状态

Worker/Analyzer 20260930T144209Z/dc50c47，Web 20260930T164946Z/7403815（Axios1.20.0），Office observer-20260930T145902Z/f37f44e，API继续f9b143a。Office源码CI36733279485及测量源码52881ec的CI36742160900均成功13/13；后者Python1249/10登记跳过、PG8/0、Web228/44。单隧道独立新双机小时实验FAILED：3600秒持续，19,950次计划请求，60RPS P95 68.366ms，持续总体P95 101.374ms，120窗中1窗不达标；资源PASSED、独立原始重算VERIFIED、远端清理确认。实发19,950、未发0；持续成功率100.0000%，质量率100.0000%。

单隧道完整小时1/120窗失败、首次3578秒中断均原样保留；4路短测通过但新长测1102.2秒中断并有9次超时及6/36窗超限，不能算小时完成。默认恢复1路、4路显式选择；未改变200ms门槛或原失败成绩。

Office两组原+2000ms三段均通过，公开浏览器通过；一次明确准备请求不计入三段，首次模型调用仍可19秒。浏览器诊断3工具完成、1门禁拒绝，9份下载SHA通过，结果证据不足。历史21因果0/21保持；当前步骤、完整证据与边界见[性能闭环](../reports/architecture/performance-fix-20260930.md)及[项目上下文](PROJECT_CONTEXT.md)。

最新Web/兼容源码CI36746799170成功13/13，Python1249/10登记跳过、PG8/0、Web228/44；前一b05ec31的CI因生产Axios审计失败保留，升级未绕过门禁。最后仅更新Web，其余12容器不变；逐文件核对、13容器健康及真实报告/树页面通过。

以下带时间与版本的内容是此前过程记录，当前状态以上述最终复验为准。

## 2026-09-30 性能修复进行中

本地全量1232通过/10登记跳过，性能源码db75804的CI36720231319成功13/13。另发现上一归档提交5561b00 CI36715678874的Web全局通知定时器在环境销毁后报错（226断言通过但作业失败）；新通知卸载断言旧源码失败，组件内message.useMessage修复已通过Web226项和构建。发布须等待含Web补丁提交的CI。不要把前一CI通过等同于后续补丁已验收。

HTTP负载连接复用、TCP_NODELAY、1024项完整文本排序缓存及办公助手并行计时修复已实现，113项定向回归通过。旧源码短测持续首窗201.418ms失败；候选短测持续100.757ms、两窗通过。一小时复测源冻结在`output/acceptance/performance-fix-20260930/after-source`与Worker1新目录`/home/ubuntu/mini-drop-perf-source-20260930a`，3600秒运行输出`after-hour/`；不要修改冻结源、覆盖报告或把短测算小时通过。目标仅本次一次性进程，停止由控制EOF或4500秒TTL负责，不影响其他服务。办公助手候选新增内容白名单细分计时，尚未发布；平台仍为`20260930T115734Z`/f9b143a。21场景因果0/21与历史小时失败保留，当前细节见[性能修复](../reports/architecture/performance-fix-20260930.md)。

## 2026-09-30 安全门禁与诊断取消已部署

当前线上 API 使用 Go 1.26.8，pgx 5.9.2、gRPC 1.83.2、x/crypto 0.56.0、x/net 0.58.0 等。CI 使用 go.mod 工具链，固定 govulncheck 1.8.0，源码调用图和 Linux 二进制扫描均阻断；golangci-lint 2.14.0 的 13 条存量告警清理后改为阻断。Trivy 全仓扫描仍是报告模式，不代表整个镜像与其他语言依赖无漏洞。部署二进制保留符号表，仅移除 DWARF，避免 stripped 二进制扫描退化为模块级精度。

新增 `POST /api/v2/diagnoses/{id}/cancel` 与“停止诊断”确认入口。父会话、关联工具、AnalysisJob 和 Task 在一个事务中取消；记录操作者、原因、原状态与任务ID，重复请求（包括原版本）返回同一终态。完成/失败历史不会改为取消；归档/缺失404，非取消终态或首次版本冲突409。Agent 经原有心跳终止采集进程，HTTP成功表示状态已提交，不表示进程已立即退出。取消不自动撤销故障注入，仍须在故障广场停止并恢复。迟到规划、审批、报告导入和 Analyzer 提交不得复活会话。原始失败与复验材料已归档到 `reports/quality/security-cancel-20260930/`，线上四服务为发布 `20260930T115734Z` / `f9b143ae3dfa4409ed621d34b5d2971e90c0a972`；Worker/Analyzer各184文件、Web45文件、API二进制SHA一致，其余9容器未重建。CI36710634578成功13/13；Python1217通过/10登记跳过、真实PG8通过零跳过、Web226通过，Go race及新安全/lint阻断通过。首次包丢执行位的失败自动回滚后，0755与镜像可执行预检修复，重发成功，旧发布均保留。


真机取消：真实页面确认后成为只读，重复原版本请求仅一条取消事件；运行中60秒采集取消后10.293秒内Agent领取后续5秒采集并完成，作为采集退出的上界证明，非精确退出耗时。正常AGI问答答案符合测试文档，request_id `8a85fc5af55c47cb9499f9e2279216d2`关联Diagnosis `insight_2a97fc90606d4379821e4c9ddaf75ac6`，采集/报告完成，两份下载SHA验证；报告仍为INSUFFICIENT_EVIDENCE。真实页面与备用报告/树短录屏通过。额外三段延迟彩排失败（baseline检索7304.892ms、fault7616.624ms、recovery1987.783ms，fault-baseline未达+2000ms），不改阈值，不计为恢复性能通过。详情见[收尾报告](../reports/architecture/security-cancel-delivery-20260930.md)。

## 2026-09-30 验收传输路径修复（本地客户端）

定位上轮失败：系统代理路径复现读取超时，同接口直连成功；原时段74条API非SSE GET均200、最大34ms。重试的JVM/perf采样发生在客户端超时撤销之后，因此零GC/无样本不能用来评估故障期采样能力。新增显式直连选项和方法/路径/异常类型记录，默认路由不变、不重试不确定POST、不放宽判据，TLS链与主机名仍验证。9项旧代码负向复现，相关96项修复通过，全量1201通过/8登记跳过。运行时保持d26bc2c，[CI36704929921](https://github.com/llongwang751-arch/mini-drop/actions/runs/36704929921)成功13/13；测试源码67b428828f4b6b2d93d3917e81b5c18ddd463de4，测试merge8a4f9c3fabf59840c373c86447890af9a6a311c0。Python1201通过/8登记跳过，真实PG专项6通过零跳过，4份CI原始ZIP归档并核对SHA，冻结部署的独立新GC批次已COMPLETED，无传输错误，取证链/注入/撤销恢复/清理/收束均通过，三工具完成；VERIFIED有界观测仍不计因果根因，严格0/1，真实浏览器通过；原两个STOPPED_UNSAFE_TO_CONTINUE记录保留。详情见[传输复盘](../reports/architecture/acceptance-transport-20260930.md)。

## 2026-09-30 三个缺陷闭环已部署

当前后端发布`20260930T100034Z`，源码`d26bc2c7a90ca7b5df29dfd9c2a26e060bf0aeb1`；Worker/Analyzer各183文件SHA一致，其余11个容器不变，私有回滚配置与旧发布保留。Web仍为`20260930T084451Z`/`c9b9964`。[CI36699546451](https://github.com/llongwang751-arch/mini-drop/actions/runs/36699546451)成功13/13，Python1192通过/8项已登记跳过，真实PostgreSQL专项6通过零跳过；测试merge为`7f3cf29db4de628a0f903c3bbb080e594f047223`。

完成重复进程名的目标Agent绑定、Skill激活首插入竞争、Java Profile报告观测范围三项修复。原误选Java三项新批次目标正确3/3、撤销恢复/清理/收束3/3，严格根因0/3；并发问题用独立tmpfs PostgreSQL负向复现并验证修复。旧协议GC单项1/1保留，但缺范围的历史报告不能当因果证明。新报告保留测量门禁、数值和VERIFIED，显式BOUNDED_OBSERVATION/causal=false，历史报告原样返回。

新范围的首次云端批次因公开接口TLS/读取错误停止为STOPPED_UNSAFE_TO_CONTINUE；独立清理确认21故障全部inactive、诊断和三个工具COMPLETED。另存的会话读回及真实浏览器确认新报告已标为已验证观测，页面显示“已验证性能观测，根因仍待确认”，没有浏览器异常。失败批次不改成通过；同部署重试同样读取超时停止，注入/撤销恢复/清理通过；独立检查会话已证据不足、工具2完成1失败（NO_PERF_SAMPLES），无运行中任务。停止重复注入，后续先定位读取超时及采样路径；详细结果见[本轮报告](../reports/architecture/instance-scope-fix-20260930.md)。完整21批次及失败小时成绩保留，子集不拼接为完整成绩。

## 2026-09-30 部署与独立复验已完成

后端 `20260930T071955Z` / `3d8e414` 已部署，Worker与Analyzer各183文件SHA一致；随后Web `20260930T084451Z` / `c9b9964` 已部署，45份dist文件一致，其余12容器未重建，后端再次复核一致。旧发布、镜像与私有回滚配置保留，API三依赖healthy。

完整21场景COMPLETED：严格整项和因果根因均0/21，lineage7/21，4项有已验证观测；注入、撤销恢复、清理、会话收束各21/21，3项选错Agent。成绩由生成器从新完整批次生成并在API核验，最终21故障inactive；历史停止和不同口径成绩保留。

真实一小时双机实验19,950请求成功且质量全通过，但60RPS P95 294.32ms、持续9/120窗超200ms，整体FAILED；持续总P95 160.54ms、资源检查/完整性/清理通过。短测315请求通过不能替代失败的小时实验。

修复旧Web把有界观测显示为根因及无效Skill候选HTTP409：224项Web测试与构建通过，CI36686661615成功13/13。云端同一会话复查已显示“已验证性能观测，根因仍待确认”，没有JS/HTTP错误。最终证据与下一轮优先级见[部署验收报告](../reports/architecture/deployment-validation-20260930.md)及[剩余工作](REMAINING_WORK_20260930.md)。后续优先实例上下文、安全依赖、诊断取消和完整演示彩排；不扩展为全能诊断平台。

## 2026-09-29—30 聚焦测开、后端与 Agent 开发的交付（代码及CI已验证，未部署）

用户已恢复有明确范围的工程收尾。本轮完成可执行Python/Go观察合同、不可执行候选幂等拒绝与正常收束、报告观测范围、最终验收拒绝弱观察冒充根因、浮点分桶和双机报告展示修复；不扩展诊断故障域，不重启已停止的21场景或小时实验。后续入口为[工程交付](ENGINEERING_DELIVERY.md)。

本机全量1170 passed / 7 expected skips，关键覆盖率无违约；快速门禁175 passed、零跳过。最初9项失败定位为两处旧测试替身缺status字段，补齐字段后恢复通过，终态保护未放松。原始失败、最终JUnit/覆盖率与前后负向复现已归档到 `reports/quality/focused-delivery-20260929/`。[CI 36595478139](https://github.com/llongwang751-arch/mini-drop/actions/runs/36595478139) **13/13作业通过**，测试代码 `9d0231dbf4cb6c4beca346230967b0b139be3c29`，测试merge `be98350e1568d71e0411f3eea7d13ecb20d75f2e`。Python1174 passed / 7 expected skips；PostgreSQL、Chroma、Go race、Chromium、业务重复验收及Linux独立热点均由专项实跑。Trivy/部分lint仍为report-only，通过不表示所有安全规则阻断发布。 首次CI `36594980233` 的CPU控制浮点误差失败保留；新增4项反例在旧版失败，按记录端点计算后修复，未钳制负值或放松阈值。后续文档/证据归档提交不与已测试源码混淆。

新报告 `verification.claim_scope=BOUNDED_OBSERVATION`、`causal_root_cause_verified=false`，明确Profile和OS分别窗口；即使VERIFIED与函数词汇命中，也不能计入新根因成绩。最终汇总器补丁已应用；未知/非法scope、非布尔因果标记同样拒绝，历史无scope记录保持兼容且不改写。新口径不与旧分数拼接。

云端仍保持 `20260928T142312Z` / `2400190`，本轮候选未部署。9/21中断批次保持STOPPED_BY_USER、严格0/9、原根因字段1/9，所有故障已停用；一小时原测量FAILED保留。学习课程正文属于用户改动，单独保留；仅由生成器维护文件索引。

## 2026-09-28 用户要求停止（15:05 UTC / 23:05 北京时间）

用户明确要求“停下来吧……就先别做了”。暂停开发、全量测试、提交、发布和后续场景；仅完成正在执行任务的安全中断、故障撤销与交接。当前云端保持 `20260928T142312Z` / `2400190`，不自动回滚已经健康运行的发布。

停止时完整场景已执行9/21：严格整项通过0/9，原协议根因报告门禁1/9（Go CPU），lineage4/9。Go Profile与独立CPU已真实绑定同一假设，但会话整体仍证据不足；这不是因果或同负载修复证明。第10项go-memory-growth已中断且不计完成；当前诊断已CANCELLED，非终态工具数0，21个故障全部inactive，验收进程已退出。Campaign为STOPPED_BY_USER，原RUNNING检查点另存，安全证据位于 `output/acceptance/deployment-20260928/strict21-stop.json`。不要将未完成21项生成COMPLETED页面成绩，也不要把历史不同批次拼成新总分。

一小时双机实验已完成并归档，整体FAILED（恢复P95超限、持续5/120窗超限），19,950次全部成功和质量通过，资源检查及远端清理通过。修复后短链路已自行完成PASSED且remote_cleanup_confirmed=true，仅是短回归，不能替代失败的小时结果。

本地未提交候选保留：Python/Go可执行观察合同、旧Profile强条件拒绝、报告前同假设补采准入、不可执行自动候选幂等拒绝及终态收束、BOUNDED_OBSERVATION结构化范围、观测标题、浮点分桶与双机报告展示。各项定向回归已执行；合并后的全量门禁和远程CI尚未执行，候选未部署。未来strict拒绝观测scope冒充根因的补丁仅在 `output/acceptance/deployment-20260928/bounded-scope-strict.patch`，真实runner未改。

如用户以后明确恢复：先核实中断清理证据及worktree，再完成候选全量回归/CI和人工审查；只有通过后才另建发布与独立复测。剩余资源/I/O/邻居/排队等域仍缺真正可执行独立对照或目标归属，不能靠降低门槛补成绩。用户学习课程正文和历史原始证据保持不变。

## 2026-09-28 可执行观测合同候选（本地开发，未部署）

双机一小时实验已完成：19,950次请求全部成功且固定质量检查通过，持续总体P95 143.88ms；恢复267.92ms和5/120持续窗超过200ms，整体FAILED。资源检查通过、远端退出及4份源码一致性已复核，原始结果与精确源码均归档，详见[DISTRIBUTED_LOAD](DISTRIBUTED_LOAD.md)。

完整 21 场景复验继续使用冻结的云端源码 `2400190`；本地新增代码不属于这轮测量。复验暴露计划校验、证据谓词和报告前补采对条件含义的理解不一致：模型提出跨窗口稳定、均匀性或更高比例等条件，采集器却不能完整证明。

候选版本把 Python/Go 的计划、最终入库、谓词和补采共用一个有限注册表。Python 合同维持最多三个有源码位置函数合计至少 70%，Go 合同维持一个源码业务路径 inclusive 占比至少 20%；独立进程 CPU 阈值由新计划预先声明。注册合同分别描述 Profile 与系统指标窗口，不宣称同窗、跨窗稳定或函数导致全部请求延迟。未实现的条件明确拒绝，不能仅靠关键词把较弱观测映射成更强结论。

旧假设和旧报告不改写；无数据库迁移，不降低覆盖率、独立对照或严格验收的最少轮次。新合同验证的是有限观察，不能直接宣传为因果诊断准确率提升。需要独立回归、CI、单独发布及新会话实测之后，才能给出新版本成绩。

## 2026-09-28 Pilot 后的计划与报告前补采（历史过程，验收已停止）

**当前运行版本 `20260928T142312Z`，源码 `2400190`。** 两个服务健康、各183文件SHA匹配清单，其余11个运行容器未改变，API三依赖healthy。[CI 36435478810](https://github.com/llongwang751-arch/mini-drop/actions/runs/36435478810) 13/13通过，Python1071 passed / 7 expected skips，关键覆盖率无违约；精确测试merge `eeeda1c9`。完整21场景已在此版本串行执行，新分数尚未产生。

真实 Python/Go pilot 严格 0/2，但取证链及撤销恢复均通过。修复不回写旧结果：共享精确 CPU 阈值语法供模型计划准入与谓词复用，明确 CPU 升高类假设没有可执行独立反证时，返回既有一次纠正反馈；中英文、两种 planner runtime 均生效。普通 GIL/锁或仅 profile 集中不被强制当作 CPU 饱和。

每个假设只有一份不可变报告，因此独立补采在首次报告生成之前完成。只有预登记全部为精确 CPU 阈值、可信运行时 profile SUPPORT、没有既有报告/独立对照时，对原 hypothesis_id 请求一次 collect_sys_metrics；权限、预算、截止时间、Agent能力与effect-key去重不变。等待审批/采集时延迟报告，拒绝或失败后仍可输出不足结论，不能无限重试。低 CPU 会真实反驳假设，不以验证字段单独冒充已证实根因。

同一假设两次采集仍只算一轮；没有为凑三轮增加计数或降低严格验收条件。新增独立 worker 集成覆盖等待、重复、失败、反证和旧报告不变。后续根因与lineage仍须分项记录，代码验证不代表云端通过。


## 2026-09-28 云端部署与剩余验收（历史过程，验收已停止）

**已部署**：Control current 已切换到 `20260928T140102Z`，源码 `1d010cc5f3ad55ba5fdcadea3a18728afab0d805`。Diagnosis Worker / Analyzer 已通过候选导入及运行健康检查，其余 11 个运行容器 ID 未改变；API control_plane/database/diagnostic_ai 全部 healthy。旧发布 `20260923T163300Z` 和两个旧镜像、私有 rollback.compose.json 保留。对应 [CI 36432783520](https://github.com/llongwang751-arch/mini-drop/actions/runs/36432783520) 13/13 作业通过。下面的上线前描述为本轮过程记录。

双机短测 315/315 请求成功、质量100%，原始复核与远端退出通过；一小时长测进行中，阶梯至60RPS通过，但恢复P95 267.9ms超过200ms，不能称整轮通过。Python/Go 云端 pilot 已完成：严格 0/2，lineage、同进程六快照、撤销恢复、清理与会话收敛均 2/2，根因都停在 PARTIAL_WITHOUT_COUNTER。真实 /proc 计数可用；模型反证缺可执行阈值且后续采集绑定新假设，开发正在据此修复，不能把本次失败记成通过。并行复盘另定位历史CI浮点分桶31/29缺陷，修复先在隔离副本验证，测量源保持冻结至长测结束。


用户已明确授权部署、严格根因复验，并要求开发与测试并行。当前发布前只读核对：Control 仍为 20260923T163300Z，13 个容器运行、健康端点三依赖 healthy，三个 Agent ONLINE，根盘剩余约 7.4GiB；不清理旧卷、证据或镜像。

本轮开发真实 Agent /proc CPU 独立计数与精确CPU域反证，测试侧独立审查来源和三窗身份；双机执行器使用 Windows 发压、Worker1 专用回环fixture及隔离venv，实际结果尚未产生。全部故障必须串行且finally撤销；双机长跑放在不同Worker上，避免与Control故障测量互相干扰。仅在候选测试/导入通过后更新Diagnosis Worker和Analyzer，保留运行配置及旧镜像回滚；不改数据库或办公助手数据。

只读核对20260920原始21场景发现文档统计有误：真实 lineage_verified 为10/21，严格通过1/21，恢复及清理21/21；这与9月10日旧索引12/21不是同批数据。后续成绩由新campaign生成，不重写旧报告。分数 PID 截断已修复；最终本机门禁 1000 passed / 7 expected skips、关键覆盖率零 breaches、源码指纹一致。两处早期子进程测试失败未再现且根因未定位，保留原记录并增强 stderr 诊断，不能称已修复环境污染。尚未激活新镜像。


## 2026-09-28 结论数值可信度修复（已验证、未部署）

新增报告与假设判据的数值边界回归：NaN/Infinity、布尔值、越界百分比、缺失/负数/非整数计数均不能冒充观测。缺少 GC/数据库计数不再补零形成 COUNTER；GC 独立对照要求真实正时间窗，观察到 GC 但没有分配增量不支持“未观察到 GC”。数据库等待时长保留 lock_wait_ms/max_wait_ms 兼容，缺失不显示 0，冲突字段不晋级。

Java 结论选择业务帧时使用该帧自身百分比，缺失不继承 Lambda 包装帧数字；不完整 GC 计数不渲染为同窗完整观测。修复前新增负向用例实际复现 24 项失败；修复后针对性 135 项通过，report_conclusion 行+分支 100%，合同下限从 69 提升到 100。第一轮本地全量 858 passed / 7 skipped，随后补 7 个窗口/缺失时长边界；最终 [CI 36429192084](https://github.com/llongwang751-arch/mini-drop/actions/runs/36429192084) **13/13 作业通过，Python 865 passed / 7 skipped**（PG/Chroma 依赖由专项实跑），代码 head `6fb7124`，测试 merge `6ef3f51a`。

上一轮归档提交 2775aac 的 CI 36302055589 已成功。此次没有部署或回写历史证据，云端严格 AI 根因仍为 1/21；用户正在修改的学习课程正文保持原样。[本轮缺陷复盘](../reports/architecture/conclusion-integrity-20260928.md) 保存了精确前后输出、修复前失败日志、JUnit、覆盖率与 CI 清单。随后仅提交文档和证据归档；区分该提交与上述已测试代码。


## 2026-09-27 资源稳定性与独立热点对照（已验收、未部署）

已完成目标进程 CPU/RSS/线程/句柄观测、原始 JSONL 重算复核、自包含 HTML 图表，以及隔离 Linux Python/Go 操作系统 CPU 与函数 profile 三窗对照。修复首样本启动过晚及跨阶段采样断档误通过，补充撤销后同 PID 回读和 Go 错函数拒绝。README、测试工程与面试路线已同步。

本机 30 分钟持续阶段 9000/9000 请求成功且引用检查通过，P95 41.15ms；1780 个持续资源样本、0 失败，RSS 中位数增长 -598016 bytes、线程/Windows 句柄增长均 0。整轮仍 INVALID：80 RPS 档 211 次未发出，不能据其认定服务容量。后续容量复测最大在途 128、40/50/60/70 RPS 各 10 秒：全 2575 次发出，60 RPS 达标，70 RPS P95 883.59ms 超标；降载恢复与 60 秒持续均通过，整轮有效 PASSED。两组配置分别保存，不是代码优化前后对比。

[CI 36301690538](https://github.com/llongwang751-arch/mini-drop/actions/runs/36301690538) **13/13 作业通过**，代码 head `3b6809c`、测试 merge `36a40a5f`，Python **780 passed / 7 skipped**，依赖跳过由 PG/Chroma 专项实跑。Python CPU 0.25→94.12→0.50%、新增 1383 个源码热点样本；Go CPU 0.75→104.87→1.00%、pprof 目标累计 95.04%。它们是 CONTROL_VERIFIED，历史 AI 根因仍为 1/21，不将撤销负载冒充同负载代码修复。

全部报告、失败原始记录、精确测量源码与可分享图表见 [本轮实测报告](../reports/architecture/resource-controls-20260927.md)。原实验启动时包含未提交测量源码，报告保留当时 git_head 并以逐字节源码哈希追溯；原始目录不覆盖。后续仅归档文档与证据，区分已测试代码与归档提交。未合并、未部署、不启动本机 Docker。小时级实验、独立压测机容量及云端严格来源接入仍属后续工作。


## 2026-09-27 阶梯负载与持续运行（已实测，未部署）

新增 `scripts/run_load_endurance.py`：在独立本机子进程启动现有 SQLite FTS5 HTTP 样例，同一 PID 执行阶梯负载、卸载后恢复和 600 秒持续请求。固定到达速率，有界在途请求，每个未发出槽位仍记录；延迟从计划到达时刻计算，失败请求不从分母删除，发压端迟到/饱和与服务 SLO 失败分别报告。持续阶段按 30 秒到达窗口判定，任何窗口不达标不能用整体平均掩盖。版本、源码/语料指纹、原始 JSONL、失败报告与哈希保留。

新增 `endurance` 质量 profile 与 Linux 短版 CI（12 秒持续阶段，仅验证执行链路）；本轮 27 项新门禁回归通过，[远程 CI 36298996094](https://github.com/llongwang751-arch/mini-drop/actions/runs/36298996094) **12/12 作业通过**、Python **723 passed / 7 skipped**，代码版本 `be34ff5`。本机默认 600 秒持续阶段完成 3000 请求、成功/引用检查均 100%，P95 39.85ms，20 个窗口均通过。阶梯 5/20/40 请求每秒通过，80 请求每秒因 P95 480.44ms 超过 200ms 判 SLO_FAILED；降载后 P95 40.82ms，完整实验有效且恢复/持续通过。全部 5250 请求、计划、版本、原始失败档与哈希已归档，见 [实测报告](../reports/architecture/load-endurance-20260927.md)。所有流量只到新建回环地址子进程，不启动本机 Docker，不触碰云端。该历史批次没有服务 RSS/CPU 指标；新增资源批次见本文顶部，仍不能称生产容量或小时级长稳已验收。运行说明见 [业务验收](BUSINESS_ACCEPTANCE.md#阶梯负载与持续运行2026-09-27)。


## 2026-09-27 远程 CI 与实际检索回归（最新）

已建立 [草稿 PR #1](https://github.com/llongwang751-arch/mini-drop/pull/1)，目标分支为 `master`。第二轮 CI `36296726561` 中 PostgreSQL 并发 5+1、Go race、真实 Chromium、三轮业务验收、Web、原生构建/Agent CTest、仓库质量及 Chroma 专项（37 passed，零跳过）通过；Python 暴露新机器启动时间依赖；修复后的[第三轮 CI 36297143120](https://github.com/llongwang751-arch/mini-drop/actions/runs/36297143120) **11/11 作业全部通过**，Python **696 passed / 7 skipped**，7 个依赖相关跳过由 PostgreSQL/Chroma 专项实际覆盖。被验证的 PR head 为 `bed58b0`（GitHub 测试 merge 为 `ea09f10c`）；之后仅归档文档和证据。Worker 首轮实验评估现在以 `None` 区分“尚未执行”，不依赖系统 monotonic 已经过 300 秒；测试固定 0/12/900000 秒启动时间，并验证 299.999/300 秒间隔边界。

实际 RAG 历史修复已用冻结源码重新验证：本机 HTTP 三窗 P95 35.27/271.13/37.99ms，结论 `IMPROVEMENT_VERIFIED`。新增独立检索回归脚本检验排序、租户隔离、边界与增删改新鲜度，并保存 cProfile。它需要外部冻结源码，不属于常规 CI，不把合成数据本机结果当作生产指标或 AI VERIFIED 根因。代码未合并、未部署；原始失败和证据均保留。详细命令与证据见 [本轮复盘](../reports/architecture/test-engineering-ci-20260927.md)。


## 2026-09-26 测试工程第三轮增量（历史批次）

第三轮：`fix_verification.py` 分支覆盖率 13.64%→100%（18 项行为测试，`tests/test_fix_verification.py`），`critical_coverage` 下限钉到 100；PG fixture 收拢进 `tests/conftest.py`（原模块保留 re-export，本地 skip 验证通过，PG 真跑待 CI）；hypothesis 进 dev 依赖，6 个 property 测试锁定业务验收统计/判定三角形（`tests/test_business_acceptance_properties.py`）。全量 693 passed / 7 skipped，门禁 `output/quality/qa-python-round3-20260926/` PASSED_WITH_SKIPS、0 breaches。

**提交与推送状态：三轮测试工程改动已提交为 `e4ff878` 并推送到 `personal` 远端的 `release/unified-ai-diagnosis-20260821`。CI 只在 push main/master 或 pull_request 时触发，因此实际运行仍差一步：在 GitHub 上从该分支向 main 创建 PR（本机无 gh CLI，无法代建）。PR 跑完后核对 PostgreSQL 并发作业（5+1 用例零跳过）、Go race、web-browser 与 business 重复步骤的真实结果，再更新本文。**

## 2026-09-26 测试工程第二轮增量

在第一轮测试工程之上：业务验收新增 `--repeat`（逐轮独立、聚合 P95 波动，`stability` profile），重复运行暴露并修复 RAG-03 场景缺陷（基线现锚定 10ms 依赖常数；失败证据保留在 `output/qa-business-repeat-20260926/campaign.json`，修复后两轮 DEGRADED_AVAILABLE）；`verify_frontend_workbench.mjs` 自动探测 Chromium 并新增 `--output`，断言随体检改版更新（结论摘要首屏可见、全屏树占满视口），7 项检查 0 浏览器异常（`output/qa-browser-20260926/browser8/`）；`python-all` 开启分支覆盖，5 个关键模块按模块下限门禁（fix_verification 仅 13.64% 是已知最大补测目标），最终门禁 `output/quality/qa-python-final-20260926/` 为 PASSED_WITH_SKIPS，Python 全量 669 passed / 7 skipped。新增 `browser`/`stability` profile、CI web-browser 作业与 business 重复步骤。三轮改动已随 `e4ff878` 推送 `personal` 远端；CI 待 PR 触发，不把本地全绿当成 CI 或生产验收。 下一优先级见 [测试工程](TEST_ENGINEERING.md) 第 5 节。

## 2026-09-26 测试工程本地增量

用户主投测开岗位，本轮已补一键风险回归报告、CI PostgreSQL 并发专项、Go race 与 Agent 零测试拒绝，修复业务阶段缺失补零及低质量降级误通过。详细当前入口见 [测试工程](TEST_ENGINEERING.md)，运行结果在新的 `output/quality/`，不得覆盖历史报告。代码未部署；改动已随 `e4ff878` 推送，CI 实际运行待 PR 触发，不把本地全绿当成 PostgreSQL/原生 Linux 或生产验收。原云端状态仍以下文部署记录为准，不启动本机 Docker。后续优先真实浏览器 CI、可选 Chroma 集成、同负载重复性能实验及一个独立根因/修复闭环。

## 2026-09-24 图文演示复测（最新）

当前云端在清理后又从原办公助手网页上传一篇 20,000 字人工演示文档（138 块、138 向量，HTTP 200），并开启知识库完成有引用问答；Mini-Drop 同请求指标、正常体检与排查树页面均已新拍截图，报告仍为 `INSUFFICIENT_EVIDENCE`。三段受控慢检索页面再次得到 `restored=true`，注入 0/2500/0 ms、检索约 13.6/2515.3/17.5 ms；故障已撤销。当前办公助手为 2 篇活跃文档、139 个 RAG 块；旧百万字压力文档仍已删除。步骤、样本文档与截图见 [图文演示](DEMO_WALKTHROUGH.md)。

## 2026-09-24 云端清理后状态（最新）

14 篇旧办公助手验收上传已从应用及其本地 SQLite 正文、检索分块和 Milvus 索引中清除；只保留新建的 1 篇小型向量健康样本。原 `application.db` 从约 1.6 GiB 缩至约 12 MiB，Docker 构建缓存回收 5.848 GB，根盘现约 8.3 GiB 可用（78% 使用）。重启后小样本 Embedding 为 1024 维、语义检索返回 1 候选且回答正确；旧百万字结果只可作历史验收证据，继续演示须重新上传。办公助手当前活跃、`MemoryMax=1536M`、`NRestarts=0`、cgroup 无 OOM，平台 `/api/healthz` 三依赖健康。未删数据库文件、向量库目录、平台卷、历史发布或诊断证据。细节见 [清理验收](../reports/business-acceptance/cloud-data-cleanup-20260924.md)。下方 9 月 23 日记录是清理前状态。

## 2026-09-23 百万字向量上传与公网验收（最新）

办公助手 `/opt/agi-office/current` → `20260923T162200Z`，硅基流动 `BAAI/bge-m3` + 本机 Milvus Lite；平台 `/opt/mini-drop-current` → `20260923T163300Z`，Worker 为 `20260923T162100Z`、Web 为 `20260923T163300Z`，办公助手专属 600 秒 Nginx 超时生效。办公助手服务 `MemoryMax=1536M`（1.5 GiB）已持久化：旧 512 MiB 不足，1 GiB 也在多篇百万字文档重启加载时发生过一次 OOM 自动重启；最终 1.5 GiB 干净重启峰值约 1.21 GiB，`NRestarts=0`、新 cgroup `oom=0`，向量库、SQLite、平台卷保留，网页百万字文档重启后语义问答通过。百万字 API 7,701 块 / 241 次 Embedding 成功。公网首轮百万字网页上传因旧 120 秒代理超时收到 504，后台后来成功入库；新配置下网页返回 HTTP 200、7,701 块，Mini-Drop 同 request_id 显示 7,701 已入库向量、向量化慢阶段与进程/cgroup 数字。最新结果、原始失败与具体步骤见 [长文档验收](../reports/business-acceptance/long-document-ingest-20260923.md) 与 [全链路步骤](FULL_CHAIN_ACCEPTANCE.md)。严格 AI 根因门禁仍为 1/21，业务阶段慢定位不冒充 VERIFIED。根盘 40 GiB 已用约 97%，只剩约 1.2 GiB，不能为扩容删除用户数据或旧发布。

旧版 `requests.json` 会在新进程第一条请求后丢弃历史；v2 启动时只继承合格的数字记录。云端使用两份真实快照合并 12 条并保留部署前备份；新问答和再次重启后为 14 条、4 个历史 PID，百万字上传仍在 Mini-Drop 请求列表，`invalid_records=0`。旧 PID 不授权当前进程采集。办公助手旧发布 `20260923T152700Z` 和平台旧发布 `20260923T160200Z` 留存，数据目录不变。

最终网页三段慢检索复测已通过：受控注入 0/2500/0 ms、检索 1457/3517/1204 ms，恢复窗口回落、同 PID/版本、无浏览器错误。早一次复测因旧 2000 ms 差值门槛误报未恢复，页面阈值已改成仍有 1500 ms 差值与注入标记联合判定。相关 AI 诊断 `insight_d913bd0a67784d25b6e707dde7912964` 最终为 `INSUFFICIENT_EVIDENCE`（4 工具、9 Evidence、3 Report），受控恢复不冒充 VERIFIED 根因。

## 2026-09-23 正常体检链路修复（最新）

最终 `/opt/mini-drop-current` → `20260923T114300Z`，Diagnosis Worker 和 Web 使用本发布镜像；Analyzer/Chroma 保持 `20260923T101442Z`，AGI-saber 保持 `20260923T105939Z`。前一 Web-only 页面发布 `20260923T112700Z` 留存。初次真实“检查当前状态”被故障症状澄清阻断，旧失败会话保留；最终 Worker 对显式健康检查只做一次有界 `sys_metrics` 初筛。新公网会话 `insight_cdece16dd6da4944a3b38987b58abc0c` 约 29 秒完成 1 采集、2 Evidence、1 Report，页面有进程数字和树，结论为“本次观测窗口未确认故障”，Report 仍为 `INSUFFICIENT_EVIDENCE`。健康三依赖正常。回滚先用本发布 `private/rollback.compose.json` 恢复 Worker/Web，再原子指回 `20260923T112700Z`；保留卷、历史 Evidence 和旧发布。详见 [发布记录](../reports/architecture/service-exam-release-20260923.md)。

## 2026-09-23 服务体检式页面首版发布

平台 `/opt/mini-drop-current` → `/opt/mini-drop-releases/20260923T112700Z`，仅 Web 镜像更新；Worker/Analyzer/Chroma 保持 `20260923T101442Z`，AGI-saber 保持 `20260923T105939Z`。入口为“选择服务 → 检查当前状态/描述异常 → 体检报告与默认排查树”；完整报告、技术数据和路线按需展开。云端健康三依赖正常，历史真实案例浏览器无 JS/HTTP 错误。回滚用本发布 `private/rollback.compose.json` 恢复前一 Web 镜像，再原子指回 `20260923T112200Z`；不能删除卷、历史发布或 Evidence。验收与具体口径见 [发布记录](../reports/architecture/service-exam-release-20260923.md)。

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
