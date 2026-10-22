# 全量交付与验收矩阵

## 测试深度补强（2026-10-23，已发布，最终CI通过）

实现与边界见[本轮说明](JD_QUALITY_DEPTH.md)、[CRM数据库验收](CRM_DATABASE_QUALITY.md)及[摘要发布](../scripts/quality/reports/README.md)。已实际通过真实模型新场景生成、Memory独立oracle/变异、真实PG/RLS/本地issuer，以及完整PROC_CPU网页/采集/分析与3类恢复。第004轮真实1800.011651秒/60成功窗口，合计65任务/260制品，绑定窗口修复前API；第005轮当前API23.2核心链路与3故障通过，持续预算为零。取证修复后43项真PG/HTTP回归0skip通过，原六轮因果独立复测已完成2/6，细节见下。最终CI通过，不能把旧字节持续窗口或方向匹配当成新版本语义验收。

最终功能提交 `2d413e4a4abd` 两个工作流共10项跨attempt成功（基线7、JD3）。bounded前端job首次因依赖下载超外层时限而取消，原取消保留且仅此job重跑；protocol/memory为原成功物理执行的复用，不能说首次attempt全绿或全部重跑。普通未插桩PROC_CPU整链通过5成功任务/20份制品SHA、3类恢复与页面取消重试，持续预算为零；Linux Fuzz实际600011次。见[最终CI收据](evidence/jd-depth-20261023/ci/github-ci.json)。

结果页面只展示已完成的审阅执行记录，不提供任意测试命令或HTTP写入；Go当前会话重验管理员角色。独立人工语义评分仍需真人提交；结构匹配、受控干预和模板生成不代替人审。生产数据卷、旧命名镜像与原项目登记快照保持。

功能提交 `2d413e4a4abd` 的12份工具/门禁/契约Git blob已逐SHA同步线上源码目录，原权限、29容器身份/镜像/卷保持，零重启/SQL/模型。收尾[只读核验](evidence/jd-depth-20261023/release/final-readonly.json)为29容器健康、真实API就绪、7监控up、14条记录与Git blob全等，见[同步收据](evidence/jd-depth-20261023/release/source-tools-sync-2d413e4a4abd.json)。文档及工具源码同步不冒作重新构建应用或新的30分钟/模型验收。

文档提交 `7bd70394efa7` 的81份公开文档/证据已逐SHA同步服务器，29容器及原权限保持，见[文档同步收据](evidence/jd-depth-20261023/release/source-docs-sync-7bd70394efa7.json)。随后仅归档该收据与文档收尾并再次同步；独立质量发布目录的14条记录保持，功能CI仍绑定2d提交。

历史bd3c045对应CI重新核对为8/10：先前9/10误沿用上一SHA结果，现更正；浏览器和OpenAPI字段覆盖两项失败。其后窗口3字段的生成器及正负控已修，88路由/126schema通过。r3/r4发布失败及独立恢复证据保存在[发布历史](evidence/jd-depth-20261023/release/)。r5已上线API23.2/Web23.1，内部API ready、公网API JSON、13条初始记录及7监控通过，其他27容器和卷保持；追加新因果失败后共14条，原13条SHA不变、29容器不变。真实公网两宽、14条摘要、匿名401、历史失败与JSON导出已通过[浏览器验收](evidence/jd-depth-20261023/release/public-browser.json)。

093a05d的新CI契约及另8项成功，完整平台门禁仍失败：受控Agent未注册，接口200/列表0；8服务运行无OOM、清理成功。补owned Core固定事件与TLS握手投影后继续定位，未把服务器隔离环境已通过代替GitHub的失败。

6aeb122同SHA CI仍9/10：固定日志确认26次注册失败、Core就绪、Agent初始化完成，代理全空，直接mTLS校验通过。临时诊断构建仅改QA副本日志以取有界gRPC状态，原/补丁/插桩SHA另证，acceptance_eligible=false；21项负控通过。最终仍必须用普通未插桩镜像重新通过，不把诊断轮算验收。

0cc95f6插桩诊断轮为8/10且不可作为最终验收：26次注册失败均为gRPC14，Core内部错误0、真实API ready200、TLS校验通过。原10秒Fuzz预算另失败，保留原轮而不自动重试；新增固定200000次/目标及120秒硬限、有界反例导出，Windows600000次和20项捕获/既有回归通过，不能代替Linux最终CI。独立[薄镜像来源](evidence/jd-depth-20261023/ci/thin-qa-build-001.json)及[实际动态库对照](evidence/jd-depth-20261023/ci/thin-library-comparison-001.json)只证明版本差异，尚不单独证明故障原因。

91aaa00诊断轮同SHA9/10：默认27次DNS失败，单改native后约1秒注册，恢复默认再45秒仍15次DNS失败；两腿Core始终ready、进程运行/无OOM、零任务和其他设置不变，证明策略差异，不归因为具体c-ares安全包。最终普通workflow已恢复原C++镜像，QA Agent明确使用系统解析器并实际核验；System27项和protocol20项通过，后续普通2d验收结果见本节首部。[历史LinuxFuzz收据](evidence/jd-depth-20261023/ci/fuzz-91aaa00-execution.json)记录实际600028次，不把三组20万次预算冒作精确执行数。

修复后唯一v2-r2已完成原六轮：12诊断终态成功、43模型/50工具；独立干预6/6，故障方向2/6，综合2通过4失败。恢复Top3 6/6不能作误报率0，因为六份正常报告也均含CPU假设。精确根因/正常误报/拒答三个独立人审指标仍null；六个故障轮未主动查业务信号，暴露策略缺口。实际Go工具窗collector_execution与context_queried=false已被正常run125调用验证；29生产、源码及环境后验与清理通过。见[独立原始分数及边界](evidence/jd-depth-20261023/causal/v2-r2/README.md)，不覆盖旧失败或合并分母。

## JD质量补强（2026-10-23）

新增[质量工具包](../scripts/quality/README.md)和[岗位映射](JD_QUALITY_LAB.md)，平台API9/runtime4/Web6/Analyzer0.2.5/SQL10保持。此次是测试工具、分层门禁与实际证据增量，没有增加生产权限或替换业务数据。

功能提交b4875c17已推送GitHub master并同步82.157.81.79；124份提交内文件SHA核对通过，29个生产容器/镜像/卷及三个登记快照/执行器broker保持，readiness200、7监控up，见[源码部署](evidence/jd-quality-20261023/deployment.json)。新增工具通过CLI和CI使用，没有新增前端页面。精确功能提交的新增JD工作流3/3及既有quality工作流7/7已终态成功，见[CI收据](evidence/jd-quality-20261023/github-ci.json)。

| 范围 | 已实际执行 | 边界 |
| --- | --- | --- |
| CRM业务缺陷发现 | 本机及服务器各47基线/47 sham通过、3/3合成变异被杀死；每轮265实际ASGI请求 | 真实联系人路由/服务/MemoryRepository；合成身份，不覆盖TCP/TLS/CRM PG/RLS |
| AI测试计划 | 原生AgentScope2.0.10+DeepSeek实际1模型/1工具，随后执行同一47场景计划 | 从审阅目录选择，不能改oracle、URL、身份或任意代码 |
| HTTP安全 | 隔离PG真实HTTP矩阵42含子例通过/0skip，41拒绝场景逐个检查18表 | 不等同外部DAST、全部漏洞扫描或浏览器Cookie行为 |
| 状态与协议 | 真PG随机4seed×96动作+竞争回归10通过；3个Go Fuzz共119104执行 | 有界固定种子/时间；未知输入仍可能有缺陷 |
| 内存安全及采样分析 | Linux ASan/UBSan/泄漏/Werror的6个CTest；实际Runner→MinIO→Analyzer成功，3个制品SHA验证 | 不是Go HTTP→Agent gRPC→浏览器全平台单条E2E |
| 性能基线 | Core独立身份10/25/50，850心跳0失败；9采样窗口+阶梯+30秒持续测量 | 资源受限、合成与短时；不称容量上限、无扰动或长期长稳 |
| Agent新评测 | 7实际诊断通过、1非法输入拒绝；受控标签Top3匹配7/7 | 作者可见，独立人工语义、正常误报及拒答准确率仍null |
| 持续防退化 | 新增3-job workflow与既有7-job workflow均通过；关键函数覆盖门禁、失败收据和严格解析负控 | 绑定功能提交b4875c17，CI不包含付费模型或外部CRM源码；不扩大为生产长稳认证 |

工具与模型/采样/CI的结果分开记录；测试框架负控不算真实模型调用，合成变异不算生产漏洞。历史失败、第一次门禁汇总失败以及修复后通过的记录均保留。

## 八项 Agent 扩展（2026-10-21，已上线并通过实景闭环）

当前 API20261021.9、AgentScope runtime20261021.4、Web20261021.6、Analyzer20261021.2（0.2.5）、SDK2.0.10、SQL10。29运行健康、7监控up；最后仅替换API，其他28身份、镜像、卷及broker/三项目登记快照保持。见[当前部署](evidence/agent-expansion-20261021/deployment-r10.json)与[实现及边界](AGENT_EXPANSION.md)。

实际验证分开记录：早期整体真实PG392通过/1真实模型opt-in跳过；Go增量232通过/0skip、全APIvet通过；API9资源补证候选另完成真实隔离PG17回归29通过/0skip及全APIvet。Linux SDK56/56、解析器20/20，新增真实PG→worker JSON→child及20相邻解析共21/21。Web153单测、原相关33浏览器及新7浏览器回归通过；Web6公网32静态/index/14字体SHA、两宽事实、键盘、axe均通过。次数对应各自来源版本，不把早期全量检查说成每次增量都重跑。

真实模型修复10完成通用Python源码定位、代码/新增测试补丁、基线失败/候选通过、冻结回归、输出类型和值及6组性能正负对照。32.36秒、中位8.2122×仅为合成购物车函数，不代表生产延迟。见[真实修复](evidence/agent-expansion-20261021/live-generic-acceptance.json)。原task102通过正常HTTP重新分析，在原job新run3中成功解析380权重/未知1，run1/2失败归档及原文件SHA保持；见[真实恢复](evidence/agent-expansion-20261021/analysis-recovery-102.json)。

API9的真实双服务调查19已完成：Python CPU任务109/诊断93成功，实际查询四类业务信号，取得本轮SQL日志、时长与一跳拓扑；changes明确返回窗口内无匹配变更，不填充变更记录。明确确认followup3后，RESOURCE任务110采集及分析成功，子诊断94成功，实际10个资源样本、E1资源事实的值/单位和程序摘要校验通过。调查18的子诊断92等历史失败保持原样；补证不能跨单位作为可比复测，也没有据此宣称根因确认。见[真实业务闭环](evidence/agent-expansion-20261021/live-business-acceptance.json)。

固定评估先后保留两批：API5的4/24和API7调试复测24/24（off/experience均12/12）。第二批已见调试数据、同输入/同顺序且全新run，不是独立盲测；不填独立人工语义准确率，不据此证明经验收益。见[完整对比](evidence/agent-expansion-20261021/evaluation24-api7-debug-retest/acceptance-comparison.json)。

| 模块 | 交付及实际证据 | 验收边界 |
| --- | --- | --- |
| 结构化事实 | 协议2精确路径、值、单位、E来源，程序描述与有界多错误反馈 | 不保证自由解释或因果正确 |
| 动态补证 | 调查19→明确确认followup3→资源任务110→诊断94成功；最多2分支/20分钟 | 异类补证不作为可比复测，根因确认另需受控干预 |
| 跨服务信号 | 固定metrics/logs/traces/changes与一跳登记拓扑；真实HTTP/SQLite信号 | 登记关系不是根因，多跳RCA未验收 |
| 通用项目接入 | 管理员Python manifest/固定镜像/冻结源码，实际项目修复10 | 不是任意语言或任意上传执行 |
| 方法与经验库 | 个人/团队/项目版本、审阅发布/退役/有效期、授权及PG竞态回归 | 共享审阅方法，个人案例不自动跨团队共享 |
| 恢复与容错 | 持久检查点/工具回执/累计预算、固定重试与熔断、真实分析恢复 | pending状态不盲重放，非分布式HA保证 |
| 可复现评测 | 两批24次全结果、4场景/12对、空独立人工模板与摘要绑定 | 程序接受率不能替代语义准确率 |
| 回归及补丁包 | 新增独立测试红绿、原回归与正负性能对照、SHA绑定diff下载 | 隔离快照验证，不自动发布业务仓库 |

历史失败、SQL来源别名修复、未知栈处理、两次发布保护回滚及各版收据保存在[开发日志](../DEVELOPMENT_LOG.md)和本轮证据目录；不改写旧报告或截图为成功。

最终[公网两宽页面复核](evidence/agent-expansion-20261021/public-api9-root19-resource110/summary.json)以实际新任务109/110检查10项并查看8张截图，证据分别绑定诊断93/94；没有重跑采集或模型。最新API9提交[CI观测](evidence/agent-expansion-20261021/github-ci-8fab193.json)为6项通过、Analyzer仍在运行（11:18:10 UTC），不是全绿；前版21aa30d/4f35e9b已各7/7通过。

最终[只读健康检查](evidence/agent-expansion-20261021/final-health.json)于11:19:09 UTC通过：29运行、配置的healthcheck均健康、无OOM，7监控up、严格TLS readiness200；活动采集/分析/诊断/修复/调查/验证/补证与待确认补证均为0。三个登记项目冻结文件校验通过，执行器运行且源码对应发布，演示负载已停止。

## 诊断Skill、个人经验与固定评测（2026-10-21）

实现四类版本化方法、按当前测量匹配、本人案例保存/检索/撤销、知识模式对照和知识页；人工纠正与真实隔离修复资格分开。来源、当前权限、轮次/分析、项目版本、有效期和终态并发栅栏已覆盖；案例不包含自由历史指令，历史引用不能单独支撑本轮假设。外部源码登记版本与PG不具备跨系统原子性，当前也没有向量检索、跨团队共享或模型自主学习。见[实现说明](DIAGNOSTIC_KNOWLEDGE.md)。

实际验收：Go1.26.10全API真实隔离PG256通过/真实模型1项跳过，Go vet；Web143单测、知识UI9场景/5次axe零违规及build/Prettier；SDK在Windows28通过/9项Linux跳过，最终Linux镜像37/37、0skip；HTTP80/97、共享18/4与V1负控；固定22例及评分器16项。Skill路由19/19、输入22/22、报告防线209/209不能称为模型准确率。见[本地证据](evidence/diagnostic-knowledge-20261021/local-checks.json)与[真实PG](evidence/diagnostic-knowledge-20261021/postgres-acceptance.json)。

**已上线**：API/runtime/Web20261021.2、SDK2.0.10、SQL9，27运行健康、7监控up、readiness200。原预算内超批反馈重选及Go1.26.10安全补丁已部署；首轮真实模型失败保留。两组固定合成导入的off/experience共4次真实DeepSeek调用均完成，启用时2/2加载对应开发种子；既有Saber受控修复回执另通过真实保存/来源核验，不代表新基准。见[部署](evidence/diagnostic-knowledge-20261021/deployment.json)、[真实调用](evidence/diagnostic-knowledge-20261021/live-acceptance.json)、[修复案例](evidence/diagnostic-knowledge-20261021/verified-case.json)、[最终健康](evidence/diagnostic-knowledge-20261021/final-health.json)与[发布说明](releases/20261021-diagnostic-knowledge.md)。

模型解释仍有已观察到的错误：#37称“唯一叶帧热点”与70%/30%两叶帧矛盾，#35部分复测参数的工具归因未受支持。定性[Agent复审](evidence/diagnostic-knowledge-20261021/agent-review.json)不冒充独立人工标注；[语义准确率仍为空](evidence/diagnostic-knowledge-20261021/evaluation-live.json)，两组可见输入不能证明经验提高准确率。公网两宽无横向溢出/页面异常、四次axe零违规，保留桌面既有workspace-switch标签的1类incomplete。GitHub精确功能提交387144a的push/master CI最终7/7全部通过，包含Analyzer/维护真实PG/S3集成，见[CI收据](evidence/diagnostic-knowledge-20261021/github-ci.json)。

## 中文注释与阅读路线（2026-10-21）

522个手写源码/配置文件已补中文说明，覆盖C++采集/控制面、Go API、Python Analyzer/AgentScope/隔离执行器、React/Worker及构建运维；新增 [中文阅读路线](CODE_READING_GUIDE.md)。注释解释职责、数据来源、设计原因与失败边界，保留标识符/运行字符串/机器指令/许可证/生成文件/历史数据。

Go101、Python168、Web122及其余132份的AST/非注释token/解析结构对照通过；计数包含1份未修改但一并对照的Web配置。Web137、纯元数据9、API124pass/42skip、基准5、领域20检查及Go vet/格式/语法/契约检查实际通过，具体命令和边界见 [本轮验收记录](evidence/chinese-comments-20261021/verification.json)。缺少真实PG与模型配置的用例如实跳过；完整Linux C++20构建没有在本机执行。此为源码可读性增量，线上仍使用API12/runtime2/Web20261021.1/AgentHelper3/SQL8，不重启服务或重新宣称现场采集/模型验收。

## 云蓝与冷灰配色（2026-10-21 已上线）

按用户反馈统一指南及共用侧栏/按钮/登录图的配色，布局和业务行为保持；同时修复发现的移动登录H1缺失，视觉不变。新构建、Prettier、既有UI20/20（0skip）及桌面/手机登录检查通过，合计八次axe扫描零违规；主按钮白字5.82:1、辅文在背景4.73:1。成功/错误状态与火焰图编码保留，36份历史证据SHA不变。功能04330c7已推送master，实际仅更新Web至20261021.1，其他26个容器身份/镜像保持，27容器健康、7监控up、readiness200。公网GET-only两宽2/2通过、5个静态文件SHA相同。不把上一版Lighthouse或现场采集记录算作本轮重跑。见 [发布说明](releases/20261021-palette.md)、[回归](evidence/palette-20261021/regression.json)、[登录](evidence/palette-20261021/login-visual.json)、[公网](evidence/palette-20261021/production/summary.json) 与 [部署](evidence/palette-20261021/deployment.json)。

## 使用指南增量（Web11 已上线，2026-10-20）

`/guide` 已重构为与 Drop 品牌一致的交互式技术手册，包含采集→分析→诊断→验证、场景索引、结果边界和按权限显示的操作入口。六宽度 UI15/15、原导航回归16/16、Web137、构建及 Prettier 实际通过；四宽度流程切换位移0px、axe四次0违规。Lighthouse本地导入夹具桌面99、手机91，可访问性/最佳实践100；不作为生产响应时间或真实采集/模型执行证据。见 [设计与八维复审](../design-system/drop/pages/guide.md)、[最终页面验收](evidence/guide-20261020/final/summary.json)、[导航回归](evidence/guide-20261020/final/navigation-regression/summary.json)、[性能范围](evidence/guide-20261020/final/lighthouse.json)。

CI原fixture opt-in已显式启用，本机完整默认preview实际48/48通过、0skip、0fail。**上线完成**：功能提交a898c9d已推送master；实际只替换82.157.81.79的Web11，其余26容器身份及镜像保持，27运行健康、7监控up、HTTPS readiness200，API12、AgentScope runtime2、Agent/Helper3、SQL8及原镜像/数据卷保留。[部署收据](evidence/guide-20261020/final/deployment.json) · [真实认证公网GET检查](evidence/guide-20261020/final/production/summary.json) · [发布说明](releases/20261020-guide.md)。GitHub新运行Web job已成功，完整状态见本次CI观测，未将仍在执行的job预写为成功。

## GitHub 源码发布补充（2026-10-20）

用户已明确授权替换 `mini-drop` 的过时项目文件，使用完整 tx-drop 快照并保留原提交历史。新增官方 AgentScope runtime CI、8镜像手动发布矩阵、当前项目上下文和文档索引；生成 OpenAPI 补齐实际报告 `agent_runtime` 的只读有界 schema，76路由/91schema及Proto兼容检查通过，未修改生产运行时。CRM/Saber 导出改为必填源码/输出路径，4项合成目录边界回归通过；Web137、构建/Prettier、启动器8、增量打包5实际通过。GitHub Actions 状态以推送后实际结果为准，未将其预写为通过。

公开范围排除密钥、登录态、数据库、现场RAW和录像Git blob，保留显式toy格式fixture及脱敏验收摘要；录像通过Release附件提供。见[发布范围](GITHUB_PUBLICATION.md)。生产API12/runtime2及原数据卷/命名镜像保持，后续源码同步不重建容器。

发布收据：源码12fb28804f7d已普通快进推送master，旧目录从当前tree移除；服务器1548文件校验、27容器身份保持、ready200/7监控up通过；成功/失败录像均已上传且GitHub digest匹配。[GitHub验真](evidence/github-publication-20261020/publication.json) · [服务器绑定](evidence/github-publication-20261020/server-sync.json) · [录像附件验真](evidence/github-publication-20261020/release.json)。首次Actions暴露健康命令引号及Compose负数fixture可移植性问题，后续提交修正；这段记录不宣称新远端整套已通过。

31cb6b3已同步1551源码，Linux Compose2.40.3五项通过；远端API race/vet/漏洞检查、Web、SDK、契约、Core构建通过。MinIO上游容器拉取失败另修复为[官方同版本binary+固定SHA构建](../deploy/minio/README.md)，CI继续执行原PG/S3集成，不放宽测试；[生产binary只读SHA](evidence/github-publication-20261020/minio-binary-proof.json)与官方相同。生产存储镜像和数据未替换，新CI状态见Actions。

**最终功能提交797e9ec7e581验收完成**：[push/master CI run39418233749](https://github.com/llongwang751-arch/mini-drop/actions/runs/39418233749) **7/7全部通过**，包含两个官方MinIO fixture构建、原Analyzer/Ops真实PG/S3集成、API race/静态及漏洞检查、Core、Web、SDK与契约。服务器1555源码已绑定；最终ready200/27容器健康/7监控up。[CI收据](evidence/github-publication-20261020/github-ci-797e9ec.json) · [最终健康](evidence/github-publication-20261020/final-readiness-797e9ec.json)。后续收尾提交仅文档/JSON，跳过重复CI；上述7项对应明确功能提交，不扩大为最终镜像统一安全扫描或长期稳定性验收。

历史AgentScope基线（2026-10-20）：当时为API12 / AgentScope runtime2（SDK2.0.10）/ Web20261021.1 / AgentHelper3 / SQL8，27运行容器、7监控up；调查11/修复9完整网页76.4秒通过，原2+冻结6、384例、6/6对照、CPU1.5587倍。Go117、SDK33、双UID互通8、executor12及Compose5/28负控通过。详见[AgentScope发布](releases/20261020-agentscope.md)与[接入](AGENTSCOPE_INTEGRATION.md)。后面的API10表格保留为采集器组合的历史验收；当前版本见文首诊断知识模块。

历史组合部署：[https://82.157.81.79](https://82.157.81.79)，API10 / Web10 / Agent与Helper3 / SQL8，26运行容器、7监控目标up。用户点名的BOLT和malloc已通过；Saber真实FTS5组件已接入并完成最终网页全流程。详见[本次发布](releases/20261020-saber-collectors.md)。

| 当前增量范围 | 已执行证据 | 实际边界 |
|---|---|---|
| 所有实时Runner | 任务62–81，16类/20场景，采集→上传→分析→RAW大小/SHA通过 | 受控合成负载，工具/内核/目标配置依赖保留；原VFS/block历史证据另存 |
| malloc/free与memray | 任务63真100/100及200非空栈，memray62通过；3项最终隔离、peer正负控及sysfs拒绝通过 | 非mmap替代；SIGKILL后自动清残留未保证 |
| BOLT | 任务81，161样本、9制品、6组ELF输出一致；无分支数据UI明确未测量 | CPU比0.9942未观察提升，无LBR/ARM实机或自动业务二进制发布声明 |
| Saber源码与自动修复 | 调查9 / 修复7，88.0秒；2+6回归、384例、6组fresh对照，CPU中位1.3534倍 | 真实FTS5模块+合成输入，不是全部Saber上线；补丁在无网络隔离副本 |
| 回归与权限恢复 | C++18、IPC8、PG110(0skip)、执行器4、UI12+计数4通过；Agent10目标身份及目标恢复通过 | IPC/PG/UI fixture明确标记，不冒作生产故障注入或完整角色现场矩阵 |
| 平台与运维 | 任务50取消重试、DAG51→52、调度53；最终备份和真实隔离恢复；HTTPS200 | 单机同机恢复，无异地/HA或主机重启承诺 |

证据索引：[多场景](DEMO_SCENARIOS.md) · [Saber](SABER_INTEGRATION.md) · [现场演示](INTERVIEW_DEMO.md) · [剩余边界](CAPABILITY_GAPS.md)。历史失败/误判均保留，最终值以新记录为准。

## Agent V2早期基线（历史记录）

下表及其后原始验收属于API7/Web6/Agent1阶段。它们不覆盖上面的最终组合版本，也不限制新接入的Saber；当前状态以上表及本次发布收据为准。

| 范围 | 当前实现与实际检查 | 验收边界 |
| --- | --- | --- |
| 平台分层及契约 | C++/Go/Python/React独立；18 TaskKind、76 HTTP路由、90 Schema检查通过；SQL007/008真实迁移 | 18类型含导入/复合；不是18个现场采样器 |
| 从零自然语言采集 | 持久化会话、目标身份绑定、能力/schema目录、模型计划审阅确认；真实CRM任务11选择PYTHON_CPU、10s/99Hz | 专用端口/模板等缺失时需要配置，不捏造配置；不能任意shell或替换目标 |
| 真实采集与工具诊断 | CRM任务11得到987样本、258节点；DeepSeek诊断9实际检查热点/质量/多条调用链和复测草案 | 采样权重不是请求延迟；此前PROC_CPU/PERF_CPU现场证据仍有效 |
| 自动复测与再次诊断 | 确认后自动任务12、诊断10、compare_profiles，调查4关联源码修复4并完成 | 原采样987不足描述门槛1000，保留insufficient_evidence；重复采样不确认因果 |
| 源码定位及自动隔离修复 | 真实Python CRM408文件固定版本；cProfile实际行31/96/97等、文件SHA校验；模型补丁在隔离副本执行，原回归及冻结类型/隔离/身份/限流检查通过 | 首个preset为品牌配色；不代表全部CRM或任意仓库；没有自动发布原业务仓库 |
| 优化效果及负控 | 384次类型/值一致，6组交替CPU中位加速4.3343×、6/6达门槛，补丁后profile；错误list→tuple补丁拒绝，注释补丁比1.0540×、3/6，拒绝 | 合成业务函数的优化组合；不代表生产P95/P99，不是每行独立因果或正式显著性检验 |
| 权限/并发/恢复 | 隔离PG44项通过、0失败/跳过，含真实DeepSeek导入fixture；生命周期、幂等、取消、租约/重分析、自动复测、源码定位及降权历史报告隔离 | 协议mock与真实CRM/model证据分开，不能保证所有模型解释正确 |
| Web | 133单测、生产构建、8个浏览器fixture通过；公网8视图×桌面/手机16检查，axe0/脚本错误0/横向溢出0，截图已审阅 | 浏览器fixture不等于真实模型；真实结果另有公网记录 |
| 执行隔离 | API仅Unix socket；root登记源码/镜像，独立无网络/无凭据/只读/UID10001沙箱，512MiB/1CPU/64进程/240s硬限；运行身份不发模型 | 不是任意代码执行服务；取消限制后续写入，已启动计算在硬限内收尾 |
| 构建与部署 | 命名API7/Web6/CRM runner4及原镜像保留；schema8、运行镜像ID/二进制SHA、HTTPS、7个监控up见部署凭据 | 本轮未改C++，既有Werror+16CTest证据保留；未全重跑16类Runner |
| 备份与旧部署 | 迁移前PG快照；新写入后在线PG/S3备份与隔离恢复；旧drop-drop源码/容器已归档删除，数据卷及命名镜像保留 | 同机备份，未配置异地目的地；从未执行down -v |
| 剩余生产验收 | 既有受信任IP TLS、Secure Cookie、4组运维timer及有界helper spool继续使用 | 新镜像未统一CVE扫描/签名；没有跨信号拓扑RCA、全CRM覆盖、长期容量/HA/重启演练 |

证据：[真实闭环](evidence/agent-v2-20261020/live-crm-agent.json)、[摘要](evidence/agent-v2-20261020/summary.json)、[负控](evidence/agent-v2-20261020/negative-controls.json)、[PG](evidence/agent-v2-20261020/postgres-acceptance.json)、[公网](evidence/agent-v2-20261020/public/browser-acceptance.json)、[部署](evidence/agent-v2-20261020/deployment-proof.json)。见[实现及演示流程](DIAGNOSIS_AGENT_V2.md)、[公开产品/论文比较](AGENT_V2_RESEARCH.md)、[剩余边界](CAPABILITY_GAPS.md)。

[上版矩阵](history/DELIVERY_MATRIX_20261020.3.md)及[迁移前矩阵](history/DELIVERY_MATRIX_20261019.5.md)作为历史保留，不冒充其全部场景已在新版本重跑。旧120.24.187.205不再是演示目标。
