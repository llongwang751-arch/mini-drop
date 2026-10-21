# 全量交付与验收矩阵

## 八项 Agent 扩展（2026-10-21，已部署，真实场景验收中）

API7独立调试复测24/24报告通过，原API5的4/24原样保留；同输入/同顺序，off与experience各12/12。第二批属于已见调试，不是语义准确率或经验收益证明，独立人工评审仍为空。见 [两批完整对比](evidence/agent-expansion-20261021/evaluation24-api7-debug-retest/acceptance-comparison.json)。现场task102另发现上游空栈格式，API8/Analyzer0.2.4候选已保权重并补质量标记，232项Go与20项解析回归通过，等待实际Linux发布和恢复验收。

API5固定24轮实际仅4/24报告通过，off/experience各2/12，20个失败全部保留；这不是语义准确率，人工标签仍为空。见 [逐次结果](evidence/agent-expansion-20261021/evaluation24/trial-receipt.json)与[评分输出](evidence/agent-expansion-20261021/evaluation24/evaluation-live.json)。API6/Web5候选针对真实失败补资源事实、假设纠错及窄屏布局，128项Go定向回归、全API vet、部署5项和Web7项通过，待新闭环及独立debug_retest，不覆盖原实验。

API20261021.5候选增加报告专用安全纠错提示，66项Go定向回归、全API vet及SDK新增3项通过；原错误来源仍拒绝，预算保持，尚待真实模型复验。收据见 [安全反馈回归](evidence/agent-expansion-20261021/report-feedback-go.json)。

当前API20261021.7/runtime20261021.4/Web20261021.5、SQL10：Linux SDK56/56、0skip，29运行健康/7监控up；本次只更新API，其余28身份/镜像/卷和broker/登记快照保持。176项广Go回归、全APIvet通过；Web5公网两宽24事实组件边界、E锚点、键盘、axe及32静态/index SHA通过。见 [当前部署](evidence/agent-expansion-20261021/deployment-r7.json)、[真实公网事实页](evidence/agent-expansion-20261021/public-facts-api6-web5/summary.json)。此前21.4的布局检查没有覆盖实际TaskDetail长事实，375裁剪失败与Web5修复证据分别保存；多处模型错误的预算失败也保留，真实补证闭环继续验收。

已部署功能670da84：API/runtime/Web20261021.3、SDK2.0.10、SQL10，29运行健康、7监控up、HTTPS readiness200；Linux SDK44/44通过。应用三容器替换、其余24身份/镜像不变，新增双服务遥测；旧卷/命名镜像、CRM/Saber完整登记快照保持。真实DeepSeek通用Python修复10已在32.36秒完成，新增回归红绿/原回归/正负对照通过，8.2122×仅合成购物车函数，不代表生产延迟。见 [部署](evidence/agent-expansion-20261021/deployment.json)与[真实模型修复](evidence/agent-expansion-20261021/live-generic-acceptance.json)。跨信号、追加补证和24次真实重复试验正在验收。

代码已实现事实协议2、动态补证、四类业务信号/登记拓扑、通用Python登记、审阅方法库、持久化恢复/预算/重试、可复现重复评测和生成回归/补丁包，详见 [实现与边界](AGENT_EXPANSION.md)。本地整体PG384通过/1真实模型skip、Go vet通过；Web153单测、相关浏览器33/33与两宽页面检查通过；通用Python在Linux Docker实际红绿+正负性能对照通过，双服务Linux实际HTTP7通过。见 [本轮本地收据](evidence/agent-expansion-20261021/local-checks.json)，实际平台发布与模型结果单列于上段。

| 模块 | 已有实际证据 | 不能扩大解释为 |
| --- | --- | --- |
| 结构化事实/权限/恢复 | 精确字段值单位与程序描述、三独立SDK进程、384项整体PG含租约/撤权/锁序 | 任意模型解释和业务因果正确 |
| 补证状态机 | 最多2分支/20分钟、明确确认、普通任务与再次诊断、取消/过期可信回收 | 无限制自主采集或不同单位直接比较 |
| 跨服务信号 | 固定连接器与来源时间窗、原始日志投影/别名不换绑、真实双服务HTTP/SQLite场景 | 已覆盖所有生产遥测产品、多跳自动RCA或字段级多租户 |
| 通用项目与回归 | Python manifest+固定镜像、真实基线失败/候选通过与冻结对照、SHA绑定patch | 任意语言任意仓库或自动发布原业务代码 |
| 方法库与评测 | 草稿/发布/退役与版本授权、固定6例/52防线、24次成对试验计划 | 个人案例跨团队共享、自动学习或已获得独立人工准确率 |

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
