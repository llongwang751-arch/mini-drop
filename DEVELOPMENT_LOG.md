## 2026-10-21 · 八项 Agent 扩展与资源补证衔接

- 八项扩展已实现并逐步上线：结构化事实、动态补证、多源信号及一跳拓扑、通用Python项目、审阅方法库、持久检查点与预算容错、重复评测、生成回归及补丁。真实修复10已完成；固定评测API5的4/24与API7调试复测24/24全部保留，程序接受率不代表独立语义准确率。
- API9候选修复实际资源摘要E1无法支撑追加观测的问题：只接受绑定当前授权任务、严格来源/单位/采样数/峰值校验的RESOURCE测量；当前采集器重复请求经完整授权和参数校验后返回提示，不生成重复计划。真实隔离PG17及十次迁移29项回归、全API vet、gofmt与diff检查通过，另经独立只读审查。未扩大预算或更改原437字实景目标。
- 当前线上仍为API8/runtime4/Web6/Analyzer0.2.5、SQL10；API9部署与新一轮真实闭环待追加收据。调查18根诊断91成功、资源子诊断92失败仍保留，不将根诊断成功记作完整闭环。task102经正常HTTP在原job run3恢复成功，run1/2失败及RAW摘要不改写。详见[交付矩阵](docs/DELIVERY_MATRIX.md)与[本轮发布说明](docs/releases/20261021-agent-expansion.md)。

## 2026-10-21 · 诊断Skill、个人案例与固定评测

- 最终已部署功能提交387144ad25bf：API/AgentScope runtime/Web均20261021.2、SDK2.0.10、SQL9。Linux候选镜像SDK37/37、0skip通过后只替换API/runtime，其他25个容器保持；最终只读检查27运行健康、7监控up、HTTPS readiness200、无进行中诊断。执行器源码/服务、数据卷与旧命名镜像保留，源文件按清单增量同步，不声称整仓库每个文件都部署。
- 真实DeepSeek完成两种固定导入输入的off/experience对照：任务90诊断34/35、任务91诊断36/37均完成；启用经验各加载对应开发种子case1/2。另将既有Saber真实受控修复9的当前有效回执保存为case3，核对调查11/task86/诊断29与源码版本，不重跑采集、补丁或新基准。种子为合成验收操作，不冒充独立人工语义评审。
- 定性Agent复审保留了实际解释错误：诊断37称“唯一叶帧热点”，实际70%/30%两个叶帧；诊断35部分复测参数归因于E7，但工具实际仅返回手动导入建议。当前引用校验不保证语义正确，根因/误报/拒答准确率仍为null，不用两组对照推断经验收益。四次真实调用、两次经验命中与程序边界通过不等于四次结论正确。见本轮agent-review.json、evaluation-live.json及[发布说明](docs/releases/20261021-diagnostic-knowledge.md)。
- 功能提交387144a的GitHub push/master CI于2026-10-21 06:05 UTC最终观测到7/7全部通过，包含API race/vet/漏洞检查、Core、Web、SDK、契约及Analyzer/维护真实PG/S3集成（run39551510896）。后续仅文档与脱敏收据收尾，运行版本仍绑定明确功能提交，不把CI通过扩大为语义准确率或长期稳定性验收。

- 首次部署API20261021.1/runtime20261021.1/Web20261021.2及SQL009已完成：LinuxSDK34/34、27容器健康、7监控up，旧镜像/卷与执行器源码保留。公网两宽四Skill及两份真实保存的合成案例检查通过，8个JS/CSS与index哈希一致。首次真实DeepSeek对照#33因首轮提出超过2工具被执行前拒绝（轨迹仅E1），保留失败记录；补丁改为整批不执行并在原模型预算内固定反馈重选，新增SDK3项回归，Windows28通过/9Linux跳过。没有提高预算或截断工具批次。
- GitHub首版CI的Go race测试已通过，govulncheck发现旧Go1.26.9标准库GO-2026-6809；按官方修复版本升级工具链、Docker和CI至1.26.10，补构建阶段的固定评测输入。可达漏洞扫描0项（另保留1条未调用require模块提示），补丁重建/部署及真实模型复验另行记录。
- Go1.26.10已重新通过全API真实隔离PG256项（真实DeepSeek单项仍跳过、由线上单独验证）；SDK补丁的Windows37项中28通过/9项Linux专用跳过。首次Go重验的测试启动器因旧包装进程超时未能结束，已清理仅本轮所有的临时Job并改为直接运行已验证工具链，最终111.11秒完成，不将启动器超时说成产品断言失败。

- 在真实AgentScope诊断链路增加版本化方法和个人经验：四类内置Skill按当前采集类型/热点/质量匹配，历史候选按本人、采集器、Agent、项目/版本、单位和质量筛选，最多50候选/3返回。默认experience，可切skills/off；SQL009与输入摘要保留旧记录兼容。新增知识页、实际知识引用、人工纠正和关联受控修复保存/撤销，不把历史模型叙述或自由备注送回模型。
- 历史只能作先验。Go强制Skill必要工具和本轮测量引用，拒绝模型自报knowledge；固定首次知识快照，防重复读取掩盖撤销。提交事务固定来源任务、案例和授权行，再核当前attempt/analysis/来源与有效期。独立审查发现的锁等待后撤权竞态与执行器长超时已补回归，describe限3秒、知识/列表限8秒。外部登记版本只读核验与PG不是跨系统原子提交。
- 实跑全API隔离PostgreSQL17、全部九次迁移：256项通过（含子例）、1项真实模型opt-in跳过；Go vet通过。Web143单测、知识UI9场景/5次axe零违规、生产构建与Prettier通过。官方SDK本地34项中25通过/9项Linux特有跳过；新工具实际经SDK mock transport回调，不访问真实模型。HTTP80路由/97schema、18TaskKinds/4RPC及V1负控通过。
- 固定合成集22例，19/19合法方法路由、22/22输入边界和209/209报告防线，评分器16用例通过；这些不代表模型根因正确率。新增4个可真实导入的dev种子/holdout输入及同输入off/experience评分协议，缺独立语义评审或定价时指标留空。CI加入知识UI与评分器回归。证据见docs/evidence/diagnostic-knowledge-20261021；后续真实部署和DeepSeek对照追加在本节开头，首次失败保留。

## 2026-10-21 · 全工程中文注释与源码阅读路线

- 按用户“详细但不逐行”的要求，为522个手写源码/配置文件翻译英文解释并补模块职责和关键流程说明：采集Agent与AgentScope区别、任务下发/回传/恢复、Outbox、attempt/lease栅栏、授权、ReAct工具闭环、复测与隔离修复、Worker大制品及运维边界。另新增docs/CODE_READING_GUIDE.md，并在README/文档索引加入入口。技术标识、运行字符串、机器指令、许可证、生成文件及历史证据保留。
- 核验：Go101份AST（不含注释/位置）等价；Python168份去docstring后AST等价且py_compile通过；Web/验证脚本122份非注释token、编译JS、CSS结构及机器指令一致；其余132份C++/Java/Proto词法、SQL语句、YAML节点/自定义标签及配置非注释行一致。独立复核纠正两处namespace句意误判，并明确helper未ACK缓存也有TTL，避免注释夸大恢复保证。
- 实跑Web137、元数据脚本9、Go API124通过/42跳过（顶层58/27，未配置真实PG/模型，使用nomsgpack）、基准5及vet、两个Go负载仅build/vet；MinGW以C++2a草案模式/Werror构建领域测试，20检查通过。契约18类型/4RPC、V1兼容负控、HTTP76路由/91schema、Bash17份语法与PowerShell语法通过。Windows没有运行完整Linux C++20构建；Docker引擎未启动，不将有限领域测试扩大为全量构建通过。
- 本轮不重建或重启线上服务、不发起采集/模型请求，前端四个发布资源SHA仍与上一版相同。注释改变源码字节，原登记业务快照、镜像及旧收据仍对应各自冻结版本；不改写旧SHA来伪造一致。GitHub推送后的CI状态单独观测，不预写成功。

## 2026-10-21 · 云蓝与冷灰配色（已上线）

- 根据用户继续更换配色的要求，将全局画布、侧栏、共享按钮、登录图与Guide统一为云蓝/冷灰：背景#f5f7fb、白面板、正文#17263c、强调#1d5fd1。移除旧橄榄主题的局部覆盖，成功绿/错误红和火焰图数据编码保留；布局、字体、尺寸及业务交互逻辑保持。
- 实际手机登录检查发现原唯一H1随story隐藏：表单标题改H1、story改H2，对应CSS选择器同步，视觉保持；首次axe发现与截图私有归档。最终生产构建及Prettier通过，既有Guide15及workspace-shell5共20项回归20/20、0skip，加登录两宽共八次axe扫描零违规。主要按钮白字5.82:1、hover7.57:1，辅助文字在画布4.73:1；已查看最终截图并完成独立颜色复审。真实部署后验收另记，不复用上版性能分数冒充本版测量。
- 功能04330c7已推送master，并实际部署drop-web:20261021.1至82.157.81.79，镜像d9861628680f。仅Web替换，其他26个身份/镜像保持；27运行健康、7监控up、readiness200、执行器active。旧Web11镜像、13个旧哈希资源和卷保留。公网1440/375真实GET-only验收2/2通过，实际主色正确，四阶段/FAQ/手机抽屉与焦点正常，五个静态文件SHA一致。见 [本次发布](docs/releases/20261021-palette.md)；本轮证据与历史字节分开，不宣称实际运行了新采集或模型。

## 2026-10-20 · 使用指南视觉与交互重构（Web11 已上线）

- 根据用户截图重做 `/guide`：编辑式标题、证据闭环图、四阶段切换、现象索引、原生 FAQ 与按权限显示的开始入口。沿用现有品牌、导航和实际能力边界，未改后端或 Agent；路由按需加载，主 JS 从原 493.45KB 降至 483.07KB，新增页面 JS 27.36KB。
- 三轮视觉复审修复节点/图标溢出、中文字体回退、手机首屏占比、正文大小和标题孤行。四宽度×四阶段布局实测下方位置变化 0px、无裁切；六宽度 320–1440px 检查通过。明确导入夹具的 Guide 15/15、原 SSE/制品 Worker/请求生命周期回归 16/16，均 0skip；axe 四次扫描 0 违规。原轮询测试在路由切换后需恢复虚拟时钟以等待 React lazy Suspense，再暂停检查，保留原取消/去重断言。
- 最终 Web137 单测、生产构建、Prettier 通过。本地 Lighthouse13.5.0 的 gzip 构建/导入认证夹具：桌面性能99、手机91、可访问性及最佳实践均100，不代表公网 SLA。独立八维视觉审查未发现阻止发布的实质问题；见 [设计记录](design-system/drop/pages/guide.md) 与 [最终证据](docs/evidence/guide-20261020/final/summary.json)。
- CI 的 preview 步骤显式启用原 fixture opt-in，避免以跳过冒充回归；完整默认 preview 已实际48/48通过、0skip、0fail。
- 上线完成：功能提交a898c9d已推送GitHub，Web11镜像18e1a20a3356已切换，仅Web容器替换，其余26个身份/镜像不变，全部27运行健康，7监控up、readiness200、执行器active；旧Web10镜像、5个旧哈希资源和全部卷保留。公网1440/375真实认证GET-only检查四阶段/FAQ/无横溢/无pageerror通过，未执行采集或模型。GitHub新运行的Web job已成功，其他job以观测为准，不预写整套通过。见 [本次发布](docs/releases/20261020-guide.md)。

## 2026-10-20 · 15类采集器与非CRM现场演示

- 响应用户“不止CRM”的要求，部署7个标记为controlled-synthetic-demo的Go/Java/Python/C++ I/O及3种原生分配器负载；真实采集，不导入profile。标准库pprof、async-profiler、HPROF、py-spy、memray、内核syscalls/VFS/block、缺页/mmap、gperftools/jemalloc、资源及固定脚本共15类/20场景通过，所有通过项RAW下载大小和SHA-256一致。
- 公网页面新建CPU任务45，19个结果/制品视图无页面脚本错误/水平溢出。Go CPU23→32、heap24→36、Java CPU25→41完成真实DeepSeek、确认后自动复测/再次诊断。Java从公网页面自然语言选JAVA_CPU的调查7，任务46/47、verification10，78.8秒通过；因果状态仍not_tested，不能宣称源码自动修复适用于任意Go/Java仓库。
- 初次无事件21/22、tmpfs block34失败保留；改受控I/O目标至2GiB有界spool磁盘，block48通过。缺页35实际3952事件，原驱动错误要求字节权重>0，补验事件数与RAW哈希通过；原失败断言保留。malloc38 TRACE_ATTACH_UNAVAILABLE、BOLT43 RUNNER_FAILED仍未通过，不猜测具体BOLT原因。
- 实际新检查：Go构建、javac、C++ -Wall/-Wextra/-Werror编译和Compose config通过；未改应用二进制，沿用API9/Web7/SQL8。24个运行容器无OOM/unhealthy、7个监控up、HTTPS readiness200。演示profile纳入部署配置，旧命名镜像及卷保留。测试工具多次登录触发原10次/15分钟限制，未弱化限流；后续内部验收复用受保护会话。
- 详见docs/DEMO_SCENARIOS.md及docs/evidence/demo-scenarios-20261020。此次没有重新运行全量单测、axe、生命周期/授权/恢复套件；原CRM源码及业务发布保持原边界。

# Drop 开发、验证与上线日志

## 2026-10-20 · 实时面试演示 20261020.9

- 按用户要求从公网页面重新彩排。发现源码工具按版本/补丁跨任务复用测量，改成由Go提供code_repair执行身份：新任务必定重新测量，同任务重复/恢复复用自己的receipt，显式cache_hit；模型不能自选身份。新增跨任务/同任务/非法范围回归3项执行器测试通过。
- 第一次浏览器彩排调查#5采集/诊断正常，复测诊断12被AI_INVALID_REPORT拒绝，流程如实失败并保留录像/状态。新增最后一轮报告无效时最多1次无工具格式纠错，仍受原150秒总预算、引用/字段/长度校验；纠错继续无效不采纳。数学不等号作为纯文本允许，HTML标记仍拒绝；新增纠错成功/失败预算回归。
- Go全包及vet通过，隔离PG47项0失败/skip（真实DeepSeek导入fixture另标），Web构建与8个UI fixture通过，执行器3项通过。API9/Web7命名镜像已部署，旧镜像/卷不删，SQL仍8。CI配置已包含相关回归，未声称远端CI已运行。
- 新建调查#6，通过真实网页选择CRM、输入问题/授权、生成与确认计划，79.7秒完成任务15/16、自动诊断与源码修复5。profile/test_patch均cache_hit=false，实际新沙箱测量；原回归及384次类型/值一致，6/6组CPU对照达门槛，中位加速4.4301倍（仅合成配色函数）。保留实跑录像和步骤，不以历史调查冒充实时结果。
- 面试步骤与边界见docs/INTERVIEW_DEMO.md，证据docs/evidence/interview-20261020。原CRM未自动发布；模型/网络与测量会波动，不承诺每次80秒或固定提速。


## 2026-10-20 · Agent V2 + 真实Python CRM · 20261020.7

- 按用户指定接入E:/crm/AiNativeCrm-python；读取其AGENTS，未读真实.env/客户数据，原CRM源码与TS版保持不改。首个隔离场景为品牌配色预览，快照408文件、源码版本20eb8592d7a6c9b959113417de45ef99ec2c6b2125b9c635b9c15cd574cf12a5。
- SQL007/008增加调查、自动复测和源码修复；自然语言计划读取实际能力/schema，参数拒绝回填并可修正，审阅确认后采集。任务11→诊断9→自动复测12→新诊断10/compare_profiles→源码修复4真实完成。采集987样本仍标不足描述门槛，不把重复采样称作因果。
- 源码Agent真实cProfile、SHA/行号定位、模型补丁、原CRM回归+冻结类型/可变对象隔离/身份/限流测试、384次严格Python类型和值对比、6组交替CPU对照、补丁后profile和最终模型报告。模型对纯转换/亮度函数加有界缓存；CPU时间中位加速4.3343倍，6/6组超过门槛，仅代表合成业务函数基准和优化组合。
- 人工复核拦截一次把list改tuple的模型优化：旧JSON等价比较漏掉类型变化，成功状态审计撤销并保留旧证据。增加严格类型digest/冻结Python契约；旧补丁实际重跑拒绝。注释无逻辑变化的负控通过功能/384输出，却以1.0540倍、3/6组被性能门槛拒绝。未选择性删除失败历史。
- 实际联调修复同文件多段补丁协议、限制性systemd umask导致沙箱目录不可遍历、无效参数误占实际测试预算；为同文件上下文重叠/源SHA、可变类型、准入锁序、管理员降权后历史源码报告隔离补回归。API只有Unix socket，生成代码独立无网络无凭据容器，不共享Agent命名空间。
- 新增浏览器与纯Python回归已纳入CI配置；本地执行通过，未声称远端GitHub Actions已运行。
- 实际检查：Go全包/vet，隔离PG44通过0失败0skip（真实DeepSeek明确导入fixture另标）；Web133单测、构建、8个UI fixture；公网8视图×1440/375共16项axe0/脚本错误0/溢出0；纯补丁边界2、测量类型2、原CRM上冻结契约3通过。公开路由/schema检查76/90/18通过。本轮未修改C++，沿用其之前Werror+16CTest证据。
- 已部署腾讯云82.157.81.79：API20261020.7、Web20261020.6、SQL8，17运行容器、7监控up、HTTPS/Grafana/Tempo200。原命名镜像/数据卷保留；迁移前PG快照，新写入后PG/S3在线备份和隔离恢复成功，包含新增表。root登记工具已在实际服务器复现执行。
- 公开方案参考Datadog、Grafana、Dynatrace及ReAct；实现后补读PerfAgent论文作为方向对照，未宣称采用其代码或复现实验。详见docs/AGENT_V2_RESEARCH.md、DIAGNOSIS_AGENT_V2.md、DELIVERY_MATRIX.md和evidence/agent-v2-20261020。
- 边界：源码/补丁在隔离副本，不自动发布原CRM；首个preset不等于任意仓库或全CRM。证据引用/行号/测试门槛不能保证模型每句解释正确，也不承诺生产P95/P99或每一行独立因果。新镜像统一CVE扫描/签名、全Runner复验、拓扑RCA及长期HA仍未完成。

## 2026-10-20 · 腾讯云诊断Agent上线 20261020.3

- 用户明确将目标改为82.157.81.79；beij.pem以ubuntu身份认证、sudo成功。旧120.24.187.205认证前EOF属于旧目标历史，不再阻塞本次部署。新机4核8GB/Ubuntu22.04，独立txdrop项目及/opt/drop-current。
- 实读用户导师原文，检索Grafana MCP、Polar Signals、PerfAdvisor等一手资料，选择Go持久化有界工具循环。增加热点rank的调用者/被调用者工具，共5工具；PROC_CPU线程tick边界明确。未引入外部任意SQL/shell或冒称腾讯内部Agent实现。
- 新机C++ Release/Werror+16 CTest通过；官方async-profiler4.1固定SHA离线构建，坏包/超限2回归通过。API/Web/Analyzer/Ops/Core/工具及网关独立构建并保留命名镜像。Docker build --cpus不支持和GitHub下载失败的历史日志保留；改为CPU配额和验证过的离线输入。MinIO镜像层已完成但Docker附加referrers请求失败，skopeo从现有腾讯镜像源复制同版本后成功加载。
- 真实首轮模型报告人工审阅发现：已知samples被称单位未知、上下文省略标记不充分。先以3类边界测试复现，再细分遍历/祖先/子调用/出现位置截断，约束单位与复测漂移解释，发布API20261020.3。最新Go全包/vet及隔离PG34无失败/跳过，实际DeepSeek导入fixture联调通过；JSON合法不保证语义正确。
- 真实腾讯云任务1 PROC_CPU/任务2 PERF_CPU采集、分析和RAW大小/SHA下载通过；API3诊断2使用任务2同一真实profile，五工具全部实际调用；人工确认复测任务4在原采集链路成功，相同键幂等返回同任务，对比页真实可用。
- Web133单测/构建、5个诊断fixture浏览器流程；公网12页面×1440/375共24检查，axe/页面错误/横向溢出均0，真实报告和对比截图已审阅。HTTP契约65路由/82 Schema/18类型通过；可选spec-validator未安装不计入通过。
- Let's Encrypt短期IP证书真实签发、外网受信任HTTPS/Secure Cookie通过；PUBLIC_URL已切HTTPS。Tencent Agent身份下90天内部叶实际轮换，CA不变；修正轮换审计使用参数化agent_id，修正监控证书路径为当前发布目录。
- 16运行容器、7监控目标up、Grafana/Tempo200；4组运维service实际成功且timer active。helper固定2GiB盘已挂载，启动前systemd依赖已安装，未冒称已做实际重启。最终SSE/在线备份/隔离恢复证据独立记录，异地备份未配置。
- 旧drop-drop数据库/源码先归档，8旧容器及/home/ubuntu/drop源码已删除，drop_drop-pg卷及命名镜像保留，未执行down -v。密钥、登录信息仅在忽略文件/服务器secrets；发布包不包含任何实际secret。
- 当前与历史矩阵分开，新机未重跑所有16类Runner/20场景、全部追踪/告警故障、完整容量/ARM/长期运行及最终统一漏洞扫描；不把旧证据扩大为本机新通过。完整发布/设计/边界和回执已更新。

## 2026-10-20 · 候选开发历史（迁移前状态，已由下节发布记录更新）

- 用户新指令覆盖此前不展示AI的要求；使用其提供的DeepSeek配置，密钥写忽略目录，不进源码、日志、浏览器或归档。实际models接口返回deepseek-flash/deepseek-v4-pro，选用deepseek-flash。
- 基于现有单次建议新增独立diagnosis_runs（SQL006）：异步排队、每用户3活跃上限、Idempotency-Key、4轮/8工具/150秒预算、180秒租约与最多3次崩溃恢复。绑定task/attempt、analysis job/run、完整结果哈希；状态与事件原子提交。
- 新增摘要、热点/本地枚举语义线索、采集质量、复测草案工具。未知工具/参数及虚构证据报告拒绝；只发送问题与脱敏统计。原符号在平台内授权显示。复测独立确认，锁来源任务，复用TaskKind和目标身份校验，任务/来源事件/审计同事务。
- React新增智能诊断标签，支持自定义问题、证据引用、报告、历史、取消、本地模式与复测对比；诊断模块按需加载。按ui-ux-pro-max审查表单状态、键盘标签、手机布局和axe。
- Go全包测试与vet通过（普通测试无配置时PG/live用例会跳过，真实验收另跑）；隔离PG新回归及真实DeepSeek联调通过，来源明确导入样本。5个浏览器流程通过，含桌面/手机axe；Web133单测和构建通过。新增HTTP契约65实际路由/82 Schema校验通过。可选OpenAPI spec-validator未安装，未计入通过。
- 回归中确认复测fixture首次使用错误SQL列parameters，修为真实params后通过；失败记录保留在.local下。真实模型初稿曾把未知导入权重称耗时，人工审阅后强化单位边界提示，再次实际联调通过；结构校验不等于保证语义无幻觉。
- 新增20261020.1 Compose覆盖和运行镜像构建定义，保留Core5、Analyzer、生产数据卷和旧命名镜像。当前SSH在认证之前EOF，80端口亦连接后无HTTP响应，本机无法进入部署主机；已告知用户恢复SSH。未声称新功能已在服务器上线，旧版生产现状也未能重新验收。

## 2026-10-19 · Java现场采集资源预算/命名空间修复发布 20261019.5

- 补跑最终16种Agent现场采集时，Core3的JAVA_CPU任务#10明确失败；同一UID10001、capless JVM直接asprof采集成功。复现平台限制后，RLIMIT_NPROC32/64均因fork资源不足失败，256成功，确认该限制按共享主机UID计数，混入其他容器线程。失败不计作通过。
- 候选修复改为50ms核算工具独占进程组的task数量（含线程），扫描最多32768个PID；超限或核算失败终止该组，返回RUNNER_PROCESS_LIMIT。Agent/helper的容器pids_limit64/96继续提供硬上限，不把轮询称瞬时每任务硬隔离。新增同UID无关进程、工具子进程/线程超限及非法预算回归。
- 候选Core4严格Release/Werror构建和16CTest通过，额外UID10001/cap0预算回归通过；该额外测试首次tmpfs不能执行嵌套fixture，改为明确exec的独立测试tmpfs后同一二进制通过，生产tmpfs未变、失败日志保留。新Core/Agent4命名镜像已冻结，源码SHA256 dafb8b05c637a2fdc33c7b843b3e09ad69bf37cf0b20df5f75da4424de9722b1。Core4真实JAVA_CPU任务#11已通过全链；随后JAVA_HEAP任务#12明确失败，未发布Core4。JDK17工具在共享PID而独立mount namespace时错误寻找自身/tmp；原生asprof jattach诊断在相同受控JVM生成7,148,477字节HPROF成功。
- Core5改用固定SHA的asprof原生jattach执行GC.heap_dump，live/all语义保持，拒绝诊断命令路径注入；16CTest、额外UID10001预算回归、新argv/路径回归通过。TaskKind仅JAVA_HEAP的runner/tool_requirements两字段更新，其他参数/格式/Proto不变；实际比对rc4内置catalog已确认，API以同一只读真源文件协调，程序镜像复用，Analyzer不读取该catalog。HTTP61路由/75Schema/18目录契约、共享契约与实际go domain测试通过。当时线上仍Core/Agent3，候选验收进行中；最终发布结果见本节末。

- 最终Core5的16种Agent/20真实场景全链全部通过，RAW大小/hash与目标恢复逐项验证；跨UIDPython内存正负控、60秒Agent基线、最终Linux本地开发/HTTP目录、umask0077非root读权限均通过。负载Go/IO最初在noexec spool不能启动，仅修正自有fixture执行tmpfs并核对exe SHA，失败记录保留。
- 已发布Core/Agent5并协调API只读catalog，实际仅4服务容器变更，另12个不变，16容器与全部原数据卷/命名镜像保留。公网PROC_CPU #34 / PERF_CPU #35通过，发布前后备份/真实恢复最终181对象；当前浏览器和7监控目标通过。完整证据见[20261019.5发布](docs/releases/20261019.5-platform.md)，供应链、完整容量/ARM/物理HA边界仍保留。

## 2026-10-19 · 平台升级与统一发布 20261019.3

- 用户选择tx-drop继续完整平台，吸收drop-drop本地流程与视觉；明确取消企业登录、AI模型、异地备份展示，保留后端接口与规则反馈。此前指南未完成验收继续记录，没有以UI完成替代。
- 生产[https://120.24.187.205](https://120.24.187.205)已更新Core/Agent/Web3、Ops1；复用逐文件匹配的API/Analyzer rc4，16容器。原PG/S3卷、旧目录和全部命名镜像保留，未运行down -v。
- 本地CLI8回归、Linux真实首次/重复启动及PROC闭环通过；Web133单测/构建/33浏览器、6真实stage E2E通过；公网11路由×1440/375、匿名登录、真实perf gzip Worker、axe范围0违规。
- 当前Core严格Release/Werror及16CTest通过。BPF退出先停生产者，最终Core3九个真实helper场景全部通过，含alloc与Python恢复；增加跨UIDPython内存kill(pid,0)权限预检、15秒冷启动版本界限。真实正负控及最终Agent→helper→S3→Analyzer任务#7通过，RAW hash/size正确、目标恢复CPU继续增长/TracerPid0。CAP_KILL仅helper的Agent容器PID namespace，Agent cap0。
- 真实Analyzer最终提交SQL故障产生partial、完整result409，桌面/手机真实浏览器确认；真实CE:gzip存储元数据及原字节下载/Worker通过。BOLT固定toy经实际工具优化，6次交替输出/退出码一致，不执行用户ELF。
- Ops metrics遇不可读/链接/超限状态文件仍安全提供其余指标，新增3项Linux真实HTTP/FS回归；Linux离线合计42项通过。stage四服务Tempo links、真实DropServiceDown触发/恢复、Alertmanager合成运输336.485秒成功；生产7目标up，Grafana/Tempo及预置dashboard通过。Grafana128MiB无OOM但持续内存回收导致未就绪，调整768MiB预算后实测通过。
- Secret bootstrap重复写只读派生文件及链接目录权限问题修复并实际capless回归；13项TLS发布/回滚检查及公网真实信任通过。125旧对象维护转换SSE-S3，升级前后真实备份恢复及全部对象hash通过，最终167对象。KMS独立备份及PEM公钥RSA-OAEP-SHA256加密恢复副本实际解密回验；私钥/明文KMS不进应用。续期/备份/监控timer已安装且service实际成功。
- 最终公网PROC_CPU #32 / PERF_CPU #33实际COLLECTED/SUCCESS、RAW校验及历史结果可读。首次Agent镜像240秒构建超时保留，后续有界续建成功。其余失败诊断保留，不写成成功。
- 修复重建容器后Nginx保留旧上游地址；真实stage API换IP负控502、刷新后401，生产未变，本地up自动刷新亦实跑通过。内部证书stage正控/真实API错配回滚及两次PERF通过，生产换叶后上述真实proc/perf通过，CA不变、仅4服务ID改变；4组timer/service已验证。
- 首次隔离证书调用未固定Compose项目目录，3个测试服务临时挂载生产配置中的凭据文件而未能就绪。已停止/移除这些测试容器，保留卷，固定脚本目录并在轮换前后验证stage Secret来源后重验通过；生产未变、凭据值未写日志。首次内存目标缺memray模块与最终dev验证器stdout解析问题分别修正，原失败日志保留。
- 完整版本、镜像、证据和回退见[发布说明](docs/releases/20261019.3-platform.md)。Analyzer57Critical/329High、Ops依赖61High匹配未处置完；最终全部语言/原生现场工具覆盖、容量/ARM/物理HA仍见剩余矩阵，不声明全部指南完成或整体安全门禁通过。

以下按日期保留原始过程，各项仅适用当时的源码/镜像。

> 历史快照（2026-10-18 17:35 CST）：按用户“先上线部署吧，后面再说”完成有限发布 `20261018.1-limited`，公网为 API rc4 / Analyzer rc4 / Web rc7 / 内部网关 rc2，Core/Agent 与数据服务保留原容器；SQL schema 5。真实采集、导入、RAW 校验、375/1440 生产浏览器及旧结果兼容检查通过，8 个容器运行，配置已持久化。完整指南尚未全部验收；高级采集/helper、完整 stage、HTTPS/加密/运维与安全处置等后续继续。Analyzer 仍有 57 Critical / 329 High 扫描匹配，不能将上线视为安全门禁通过。详见 `docs/releases/20261018.1-limited.md`、`docs/DELIVERY_MATRIX.md` 和 `docs/CAPABILITY_GAPS.md`。后文按日期保留历史过程。

## 2026-10-18 · 按用户指示先有限上线

- 发布范围冻结为 API `20261018.1-rc4`、Analyzer `20261018.1-rc4`、Web `20261018.1-rc7`、内部 S3 网关 `20261018.1-rc2`。沿用 Core/Agent `20261016.1`；不部署尚未验收的 helper、高级采集、新 Ops、生产加密迁移或整套监控。未完成项继续保留，不删减用户全量要求。
- 升级前重新执行在线备份和真实隔离恢复：`/opt/drop-backups/drop-20261018T090826Z-b79c30`，schema 1、115 对象/1,510,006 字节，PG dump SHA256 `daa37076a433fe53d420d0d319fead5c58fcefd1514ea0f7264aba68a28c570d`；这是同机恢复保护，不宣称本轮新增定时异地复制。原有 23 份成功分析结果结构兼容检查通过。
- 单一发布队列顺序替换四个应用服务；迁移至 schema 5，重新初始化有限数据库运行权限。PostgreSQL、MinIO 后端、Core Server、Agent 四个容器 ID 前后严格相同；没有删除卷、旧任务或旧命名镜像。
- 首次切换的健康端点通过，但新增公网脚本检查实际发现 rc6 的 assets 目录为 0700，导致资源 404。立即实际回滚四服务，数据和采集端未重建；schema 5 保留，旧应用恢复正常。根因为打包 umask 077 影响新目录，原本的文件字节/单测不能覆盖容器目录权限问题。
- Web rc7 从精确 rc6 镜像作权限修复，目录 0755 / 文件 0644，业务 bundle 14 份字节全部不变；两个公用构建 recipe 同步修复，103/105 份其他源码未变。实际 UID 101、capless、只读、无网络容器逐一 HTTP 读取 14 份资源并匹配 SHA256；验证容器曾缺少生产已有的 cache tmpfs，补齐验证 profile 后通过，失败历史保留。
- rc7 新 SBOM/Grype 实际生成并扫描：0 Critical / 0 High / 3 Medium；API rc4、网关 rc2 也为 0 Critical / 0 High，但 Analyzer rc4 仍 57 Critical / 329 High，未上线 Ops rc4 仍 61 High。没有忽略规则、整批误报认定或全套安全通过结论。
- R2 实际重新部署并完成公网健康、入口脚本 SHA256、匿名任务接口 401 检查。真实任务 26 为旧 Agent 的受控 PROC_CPU 实采，117 cpu_ticks；任务 27 明确标记 IMPORTED 来源，100 samples。两项均 COLLECTED / SUCCESS，RAW 实际下载并核验 size/SHA256。只新增两项验收任务，未删除旧任务。
- 实际生产 Chromium 浏览器验收 4.78 秒通过：375/1440 两尺寸的概览、任务、节点、新建、详情共 10 组页面，20 张脱敏截图；新导入结果的 gzip 制品经 Worker 加载并显示火焰图。旧任务 1 的真实 `/result` 200 且分析面板可见；不截图旧详情，不读取旧 RAW。pageerror、5xx、外联、额外写操作、旧 RAW 读取均为 0，没有文档横向溢出。验收脚本曾因 UUID 身份与标题装饰句点断言失败，只修正脚本并补回归，未把这些失败归为产品故障；实际失败历史保留。
- 17:35:18 CST 完成 `.env` 持久化，默认 `COMPOSE_FILE=compose.yml:compose.perf.yml:compose.limited-release-r2.yml`，并生成 release proof。发布后只读核验为 8 个运行容器、schema 5、25 tasks / 31 attempts / 125 artifacts；四个保留容器身份不变，约 2.37 GB 可用内存。该快照不等于持续容量/SLA 证明。
- 根代理另实际目检生产概览 1440 视口和详情 375 全页截图，确认布局、留白、状态层级与页面边界没有明显阻塞问题；未将奖项品质目标写成已获奖或全量审计通过。完整发布范围、证据与回滚步骤写入 `docs/releases/20261018.1-limited.md`，三份交付文档同步更新。
- 最终实际完成证据一致性检查：8 份发布元数据、20 张截图 SHA、运行镜像/overlay/任务身份/schema 对照均通过；发布说明本地链接及8份相关文件的行尾空白检查通过，`git diff --check` 通过。证据索引保存为 `docs/evidence/releases/20261018.1-limited/evidence-manifest.json`，不将该 checksum 索引称为签名。


## 2026-10-16 · 初始化

- 需求来源：[Drop 性能诊断系统架构与复刻指南](./drop系统复刻指南.md)，共2776行。工作区原无业务代码、无Git仓库。
- 已确认 SSH 严格主机校验和 BatchMode认证可用。目标Ubuntu24.04，2vCPU，3499MiB内存、4095MiB swap、约30GiB空闲；未运行现有业务容器，80/443空闲。
- 保留 C++ / Go / Python / React 四模块与 PostgreSQL / MinIO / gRPC / SSE 架构。受服务器资源约束单副本、Analyzer并发1、编译并行1、显式容器内存预算。
- 使用 `ui-ux-pro-max` 生成设计建议，再按诊断工作台调整设计；grill-me与tdd未在已安装skill目录找到，采用需求审查和测试先行方式协助开发，不宣称加载不存在的skill。
- 契约先行：Proto、TaskKind真源、SQL migration、HTTP契约已建立。多代理分工核心/API/Analyzer，主线程负责Web、集成、部署、质量与交付。
- 实际支持范围以运行验证为准；线程级procfs与导入样本分别明确标识，不伪装内核函数级采样。
- 已查阅[gRPC C++官方文档](https://grpc.io/docs/languages/cpp/basics/)、[PostgreSQL锁语义](https://www.postgresql.org/docs/current/sql-select.html)、[MinIO Go SDK](https://github.com/minio/minio-go/blob/master/docs/API.md)。

## 需求审查与阶段验收

1. 契约、数据库与安全边界 → 多模块可独立构建。
2. 注册/心跳、真实采样、对象上传、异步分析 → 一个真实任务完整闭环。
3. 幂等、取消重试、租约与重启恢复 → 故障注入可解释。
4. 登录、角色/owner范围、审计与计划任务 → 权限回归通过。
5. React工作台、结果交互、响应式、键盘与减弱动效 → 浏览器验收。
6. Compose上线、备份/回滚/runbook → 公网与SSH双向验证。

所有未交付的扩展采样器、企业SSO、HA、多架构镜像和合规供应链措施必须在最终范围矩阵中如实标记，不以界面占位冒充实现。

## 2026-10-16 · 模块开发与测试

- **C++ 核心**：先写 argv/参数/状态迁移边界测试，再实现 libpq 事务、四类 gRPC service、证书CN身份校验、DB队列、Agent受控负载、perf/procfs Runner、上传与本地恢复journal。Ubuntu24.04 warnings-as-errors 构建通过，CTest 3 suites、34项检查通过。procfs按配置频率真实读取线程CPU ticks；perf仅真实probe成功才宣告能力。
- **Go API**：HTTP / service / repository / storage / gRPC分层；bcrypt会话、CSRF、角色/owner、任务幂等、Outbox、SSE、分钟计划、审计。真实PG17.11验证并发幂等只产生一组记录、owner越权拒绝、取消/重试隔离、计划去重、用户并发配额、导入中断超时和概览状态计数。
- **Python Analyzer**：黄金folded样本先红后绿；校验UTF-8、SHA256、size、节点/深度/行数、gzip展开上限；保留递归并正确区分self/inclusive。lease owner + current attempt + lease expiry三重条件提交，结果上传失败不得写success。真实PG+MinIO共28项测试通过，包含实际SIGKILL后另一Worker等待15秒租约自然到期接手、attempt=2并生成四个结果对象。
- **竞态审查**：发现旧CANCEL Outbox可能取消新retry attempt，Proto追加TaskReference.attempt_id；API和Server均按当前attempt fencing。旧heartbeat/progress/Notify不得续租或覆盖新任务，SQL锁后重新读取latest attempt。
- **React Web**：Geist/IBM Plex Mono字体自托管，炭黑/陶橙工作台；概览、任务、节点、创建、详情、计划、成员和审计；图表只使用真实聚合。火焰图D3布局在Worker；搜索/聚焦/重置/展开/键盘，移动抽屉与reduced-motion。TypeScript生产构建通过，HTTP契约与双状态终态12项单测通过。
- **前端回归修复**：丢失响应重试保留逻辑提交幂等键与默认名称；切换kind清除不兼容目标；节点请求显示错误；viewer隐藏写操作；导入失败引导重新导入；下载避免异步弹窗阻断。公网IP为HTTP非secure context，UUID改用crypto.getRandomValues，避免randomUUID不可用导致页面错误。

## 2026-10-16 · 依赖、安全与部署准备

- 服务器原配置Docker镜像代理拉取旧PG阻塞，改从Docker官方ECR公共镜像获取，实际容器输出PostgreSQL17.11；固定该补丁版本。
- npm首次缓存目录发生EEXIST，切工作区独立cache和官方registry；扫描发现旧Vitest/tinypool漏洞，升级Vitest5.0.5后npm audit全依赖0项漏洞。
- Go漏洞扫描推动升级Go1.26.9及Gin/gRPC/pgx等依赖；应用可达与导入包漏洞0，模块图中未导入功能的上游未修复项如实保留于API日志。
- 按[NGINX安全公告](https://nginx.org/en/security_advisories.html)将公网与存储网关固定到1.30.6。修复location覆盖add_header导致主HTML缺安全头的问题，安全头与cache policy统一server层配置。
- 查阅MinIO官方2026安全公告，社区版本没有对应补丁发行；按[unsigned-trailer缓解](https://github.com/minio/minio/security/advisories/GHSA-hv4r-mvr4-25vw)和[复制头缓解](https://github.com/minio/minio/security/advisories/GHSA-3rh2-v3gr-35p9)增加内部S3网关、请求头白名单、STS/管理入口封禁，并把真实backend置于独立internal网络。8项真实网关回归通过，正常签名/预签名与SHA元数据均兼容。此项是官方workaround和隔离缓解，不宣称二进制漏洞全部修复。
- Secret在服务器生成，源目录0700；每个容器只挂自身证书与必要Secret；CA签名私钥独立保存、不挂运行容器。bootstrap可重跑且检查CA证书/私钥公钥一致性。
- Compose设置容器CPU/内存预算、只读文件系统、cap_drop、no-new-privileges、日志轮转，PG/存储/gRPC无宿主端口；MinIO私有bucket初始化已实际成功。
- 用户已选择“先用公网IP上线”，PUBLIC_URL为http://120.24.187.205。
- 已提供CI工作流、OpenAPI3.1（30操作）、单一契约静态校验、在线备份/隔离恢复脚本。公网浏览器、mTLS probe、备份恢复与最终发布结果在后续条目补充。

## 2026-10-16 · 公网闭环与运维验收

- 公网六项Playwright E2E已实际全通过（26.1s）：匿名边界/登录布局与减弱动效；导入时模拟丢失HTTP响应后同幂等键重放只创建一任务；真实火焰图/签名下载；真实PROC_CPU采集；取消立即重试不误伤新attempt；375/768/1024/1440宽度/导航/键盘；CSRF与viewer/owner任务及制品隔离。
- 首轮发现入场opacity动画使瞬间文字对比度不够，改为保持文字不透明的位移；另修测试进程重启后的fixture依赖，不能把空task ID请求列表的200当越权。复测后critical/serious问题为0，继续消除moderate地标命名、移动菜单焦点与离屏Tab停靠问题。
- perf隔离探测证明云主机可用：未改宿主perf_event_paranoid=4；非root Docker有效cap为空；容器UID0+仅CAP_PERFMON+默认seccomp只放开perf_event_open可采到真实函数栈。已部署明确的compose.perf.yml、专用0700卷、受控workload，未启用privileged/hostPID/SYS_ADMIN/SYS_PTRACE。
- **真实perf任务 #8**：5秒99Hz，COLLECTED/SUCCESS，189 samples、22 nodes，树包含main→benchmark→aggregate→transform；RAW 704B，SHA256 `200f6f0a193de435c344e90ee8fd78ef3dd338c102d978cd7aa1bbabc1b5f469`，结果四制品ready，13个持久事件。
- 真实mTLS RPC probe在perf部署后再次通过：证书角色隔离、旧cancel/heartbeat/progress/Notify不能覆盖新attempt、当前取消成功；测试fixture已清理。
- 分钟计划真实触发并生成任务 #12，COLLECTED/SUCCESS，唯一record=1，耗时66.757s；计划 #1 验收结束后已暂停，保留历史记录。
- 在线备份与真实隔离恢复通过：`/opt/drop-backups/drop-20261016T120557Z-b94b1b`；快照9 tasks/11 attempts/101 events/45 artifacts，45个对象共1,458,001B逐一匹配hash/size，并完成隔离S3桶实际上传回读。验证DB和bucket已清理，生产未停机。
- API低负载读基线：本机Nginx、10并发/100请求、0错误；P50 31.773ms、P95 181.412ms、P99 221.376ms。仅为该主机短基线，不作为公网SLA或持续容量承诺。
- 大图真实导入基线：任务 #9 为10k nodes、#10为49k nodes，均实际上传、分析与公网加载。合成数据已在任务名明确标识，不冒充现场采集。

## 2026-10-16 · 优化与正式发布 20261016.1

- 完整49k节点仍在Worker布局和全局搜索；SVG按可视层裁剪并保留3行缓冲，矩形固定25px高、单一Tab入口和方向键导航。49k可交互2,655.3→1,468.8ms、搜索769.0→145.4ms、实际矩形49,000→8,402；10k可交互1,063.7→860.3ms、搜索190.1→58.8ms。逐项验证屏外匹配、定位、滚动、重置、展开和键盘，零JS/HTTP错误。单次本机浏览器测量不代表SLA；完整口径见 `docs/evidence/flame-performance.md`。
- 最终Web单元16/16通过、TypeScript/Vite生产构建与Prettier通过；npm官方registry审计0漏洞。开发机默认镜像不支持audit端点，显式切官方registry完成扫描，没有把镜像404当通过。
- 迁移owner与运行角色分离：owner `drop` 只供migration/permission-init；API、C++ Server、Analyzer共用 `drop_app`，无superuser/createDB/createROLE/replication/bypassRLS，只有表DML和序列使用，无schema CREATE和TEMP。生产实际连接已核对。
- 第一次切换暴露SQL的默认btrim不去除Secret末尾LF，造成SCRAM认证失败；修为显式去除空格/TAB/CR/LF，重跑权限初始化恢复。隔离fixture补初始LF和轮换CRLF，16项权限/认证/防泄漏检查全部通过（6.63s），包括拒绝文件读取、DDL、TRUNCATE、setval、权限提升与密码轮换。
- MinIO既有卷经明确的一次性权限初始化后改为UID10001、CapEff=0、只读根目录和临时目录；在独立fixture先通过45B与7MiB multipart真实往返再切换生产。Agent专用perf覆盖配置保留；实际仅CAP_PERFMON，宿主perf_event_paranoid=4未改。
- 发布镜像 `drop-{core,api,analyzer,web}:20261016.1`；API使用完整Dockerfile从源码构建验证的镜像。服务器 `.env` 固定双Compose配置，避免后续普通up意外丢失perf。数据库/制品与历史任务跨完整容器重建保留，所有运行服务恢复正常。
- 最终公网Playwright **6/6通过，23.3s**；导入丢失响应幂等、实际PROC_CPU、取消立即重试、签名制品下载、CSRF/owner/viewer隔离均在新数据库角色与非root存储上通过。375/768/1024/1440无页面溢出，移动抽屉inert与焦点循环/Escape通过。
- axe改为断言全部violations为空，修复导航地标重复与登录品牌区缺header；登录/总览/结果三页报告均为 `[]`。此前快捷键测试在session hydration前按n造成假失败，改为等待真实页面就绪，未放宽产品登录边界。桌面/移动截图已重拍，证据保存在 `docs/evidence/`。
- 当前入口为用户指定的公网IP HTTP；可信公网TLS、企业SSO、完整资源组RBAC、HA及扩展语言/堆分析能力仍按交付矩阵列明，不能据此宣称腾讯内部全量能力或企业规模SLA。
- **发布后真实perf任务 #19**：新Agent PID7、5秒99Hz，COLLECTED/SUCCESS，190样本、10节点；RAW及4结果逐一下载通过SHA256/size核验，RAW权重与火焰树权重一致，13条持久事件齐全。截图等待fonts.ready及所有有限动画结束，零JS异常；详见 `docs/evidence/perf-live-final.json`。
- 真实SSE首连收到事件189后主动断流，携带Last-Event-ID=189续读190/191；与持久事件after游标查询完全一致、无重放，连接设有界timeout并主动关闭。客户端/数据库时钟分开记录，不以跨机器时间差推断延迟。
- **最终在线备份及隔离恢复**：`/opt/drop-backups/drop-20261016T122640Z-c5ae53`，快照17 tasks/21 attempts/199 events/85 artifacts，85对象共1,487,885B全部hash/size一致，真实隔离S3往返成功。测试库/桶已清理，生产容器未重启。该备份位于同一主机，未配置独立故障域副本。
- 最后资源快照：宿主可用内存约2.2GiB、swap未使用、磁盘剩余21GiB；Docker资源限制与健康状态见 `docs/evidence/release-20261016.1.json`。此为时点快照，不代表峰值或容量压测。
- 源码交付使用Git索引及归档，排除 `.claude`、`.local`、Secret、依赖缓存和构建目录；174个交付文件已检查凭据匹配和危险文件路径，未发现泄漏。服务器保留源码、命名发布镜像和最新备份；运行配置和凭据不进入源码包。

## 2026-10-16 · 前端重设计 20261016.2

- 用户指出旧版视觉不满意，选择“浅色精密仪表台”。继续使用ui-ux-pro-max的交互/可访问性规则；对通用设计搜索建议按诊断产品用途取舍。独立代理先审查截图与源码，再分别实现概览/图表、壳层/登录/任务表、详情与能力差距，主线程整合视觉系统与部署。
- 排版：中文标题28–39px，登录44–66px，主要正文/任务名14px，辅助信息12px、紧凑元数据11px。采用自托管Geist/IBM Plex Mono，删除原6–9px碎字；移除过多英文眉题及垂直品牌字。
- 留白与层级：四个等权统计卡改为指标横带，最近诊断进入主栏首屏；右栏仅节点健康、实际队列和制品存储，24小时趋势移至记录之后。去掉概览底部营销卡与每行重复图标。详情强调火焰图，终态流程压成细条，采集/分析状态仍明确分开。
- 色彩与原创识别：浅色画布#f4f6f8、白面板、墨色导航#182a34、蓝操作#3158d9；暖色仅留给真实火焰图，绿红用于状态。登录SVG以层叠切面构成Drop标志，不伪装采样图。辅助灰初版对比4.39未达标，改#5b6c79并重新扫描。
- 动效与微交互：180–240ms色彩/边框/菜单过渡，按压反馈；取消数据区反复入场动画。保留骨架、错误与等待反馈。24小时真实柱图支持悬停/触屏及左右/Home/End键，只有一个Tab入口；按钮、菜单、表单的选中/展开状态补齐ARIA。减弱动效继续生效。
- 响应式：手机任务记录重新排版，名称/状态/类型/时间/目标分区，修复桌面列宽继承造成的窄列换行。SVG最小画布由600改1000，避免1000宽viewBox把函数文字横向压窄，仍仅容器内横向滚动；Worker、全树搜索与可视行裁剪不变。手机详情刷新按钮和工具栏不再额外占整行。
- 独立第二轮发现812×375横屏及200%文字放大时侧栏底部不可达、表格类型与状态重叠35px；修为侧栏内部滚动、子项不压缩、类型与徽标自然换行，手机名称留白随文字缩放。保留修复前坐标，限定5项复测全部通过；20组响应式检查、4页200%文字检查、柱图键盘与抽屉焦点/Escape均留证，零JS/HTTP异常。
- CSS拆出workspace、task-table、detail三个模块，移除115条不再使用的旧概览选择器；保留基础组件与响应式层。Node生产构建、TypeScript、16项Web单测及Prettier通过。
- 已发布 `drop-web:20261016.2`；新增WEB_RELEASE_TAG支持独立前端版本，后端RELEASE_TAG仍为20261016.1。发布前后核对7个后端/采集/分析/数据库/存储容器ID和StartedAt完全不变，Web健康通过；原前端镜像保留可回滚。
- 新版公网6项Playwright **全部通过，29.6秒**，覆盖真实导入/采集/取消重试/下载、权限、响应式和键盘。首次启动发生Windows测试worker本机原生退出、测试尚未运行；再次执行全部通过，未跳过或放宽断言。
- 新版公网13组主要页面/宽度复核：总览、任务、详情、新建、节点、计划、成员、指南与登录的axe报告全部为空，无页面溢出、无JS/HTTP异常；截图保留在 `docs/evidence/redesign/`，旧版对照图使用before前缀保留。本地早期Vite开发模式曾因海量模块请求耗尽浏览器资源，改用真正生产构建预览完成验证，未把开发模式失败归因于已发布包。
- 全量能力回答已明确为“没有完全复刻”，新增 `docs/CAPABILITY_GAPS.md` 的15项源码对照。除企业SSO/资源组RBAC、语言/堆/eBPF扩展、复合任务、HA和完整可观测/供应链外，特别补清分钟间隔不等于Cron/时区语义，受控workload不等于任意业务/宿主/容器采样。前端改版未改变这些后端能力边界。
- 大图原脚本暴露展开/收起的真实视窗缺陷：初始位于底部，展开后浏览器夹紧scrollTop，收起保留该值使root被正确裁剪但失去用户上下文。修复为切换前记录是否接近底部，在layout effect恢复底部锚点；非底部视图不强制跳动。Worker与采样数据不变，未靠给测试额外滚动掩盖问题。
- 该视窗修复单独发布为 `drop-web:20261016.3`，保留已发布的.2及最初.1镜像。最终版本16项单测、构建、格式通过；公网 **6/6 E2E再次全部通过，26.8秒**。最终发布快照见 `docs/evidence/redesign/ui-release-20261016.3.json`，其余7服务仍未重建。
- 最终.3公网13组axe/宽度复核再次全绿；10k/49k原大图脚本与原断言全部通过（未增加滚动规避）。49k交互1,703ms、搜索89.1ms、完整49k树/首屏8,402矩形保留，零JS/HTTP异常。附加锚点检查：底部2357→展开2138→收起2357、距底始终0；中间1179→1179→1179。8维自检及截图入口汇总于 `docs/UI_REVIEW.md`。

## 2026-10-16 全量复刻继续开发（进行中）

用户明确要求指南全部能力，本轮以完整指南为验收范围；上一轮单机核心范围不再作为完成标准。既有线上服务保持运行，新增功能在隔离构建和测试通过后发布，保留数据和旧镜像。

- 分工：C++/协议补齐Runner、目标解析与能力探测；Go/数据库补齐通用导入、DAG、Cron时区、OIDC、资源组/共享、AI和反馈；Python补齐原生格式解析、堆对象图/支配树、I/O与内存；主代理补齐动态表单、结果视图、企业管理与运维部署。
- 已冻结V2协议和18类TaskKind元数据（含API编排类型）；原protobuf编号保留。详见contracts/V2.md、api/HTTP_FULL.md和analyzer/section-examples.json。后者仅为字段示例，不作为真实采样证据。
- Web测试先行：Schema表单校验与二进制无损base64编码3项，首次因模块不存在失败，实现后3项通过。新增数据类型视图、通用创建与共享反馈界面正在联调，尚未算作线上验收。
- 能力完成须同时记录实现、真实测试和环境验收。OIDC提供方、外部模型、跨节点HA、异地备份等依赖的真实环境证据不以本地fixture替代。

### 全量联调阶段补充（2026-10-16 23:00，进行中）

- Go 全量 API 已冻结可联调镜像，真实 PostgreSQL 17.11 的 repository/service/httpapi 回归通过；OpenAPI 逐一覆盖 60 个实际操作、73 个 Schema，18 个 TaskKind 参数 Schema 与真源一致。
- Analyzer 首轮最终测试 63/63、83.658 秒、零 skip；17 类任务与额外 JFR 原生格式、HPROF SIGKILL/checkpoint 恢复实际通过。Core 的 13 类真实 Runner 产物再次交给 Analyzer 交叉解码，13/13 通过。
- 对照指南 7.8/7.9，又补堆分析阶段事件及资源网络、上下文切换、采集器自身开销和受权限约束的 dmesg；该增补需重新冻结并重跑测试，当前不把旧镜像测试结果当成新代码验收。
- 新建隔离 Compose 项目 dropfullstage，使用独立数据库、对象存储、CA 和 Secret，Web 只绑定服务器回环 18080；线上旧服务保持运行。
- 维护模块真实 PG/S3 集成 5 项通过，包含过期删除、缺失/哈希隔离、孤立对象、未完成分片上传与重复执行。MinIO 社区版不接受分片生命周期 XML，改为有界签名 API 显式清理，并保留过期策略。
- 审计表追加权限脚本实际联调发现 search_path 下未限定 schema 的错误，已改 public.audit_logs；独立无网络 PG 权限回归 20 项通过，验证可追加、禁止 UPDATE/DELETE/TRUNCATE。
- Web 构建与 27 项单测通过；浏览器首轮发现空状态标题层级和测试 fixture/字段断言问题，正在修复重跑，不记为已通过。新增采样方式分类和中文参数标签。
- Prometheus 18 条规则、Alertmanager、OTel 配置校验通过；Tempo 3.1 配置已修正并真实启动。监控完整告警/追踪链与外部 HTTPS 仍待验收。

### 全量验收进展与资源争用事件（2026-10-16 深夜，进行中）

- Analyzer 在提高火焰树上限前的冻结版本，真实 Linux PostgreSQL/S3 回归为 70/70、零 skip；该报告不能代表最新 100,000 节点版本。新上限含虚拟根，100,001 明确拒绝，本机边界回归通过；新 72 项 Linux 回归与 512 MiB 三档规模基线待串行执行。
- Web 28 项单测通过，包含恰好 100,000 节点接受和超界拒绝；最新 TypeScript/Vite 生产构建通过。浏览器已通过通用导入、反馈及 8 类原生格式详情和全部制品 SHA/大小校验；后续共享、Cron、DAG、全页可访问性仍需重跑，不把早期线上版本的 E2E 当成本轮验收。
- 百万任务基线改为本地独立 PostgreSQL 17.11，不再占用服务器。Windows Job Object 限制合计 512 MiB、一 CPU；最小索引第二轮 45.324 秒，峰值 126.77 MiB。真实执行 004 迁移两遍，100 万任务和 1 万软删除记录保留，schema_version=4，索引 valid/ready。首页 SQL 424.254→0.019 ms、offset 20,000 的 SQL 983.550→6.065 ms；这些是该环境的 EXPLAIN 时点数据，不等于公网吞吐。24 次创建、60 次查询、20 路 SSE 全部成功。证据：api/benchmarks/million-task-minimal-index-windows-2026-10-16.json。
- 004 仅新增有效任务的 created_at/id 稳定倒序部分索引，额外占用约 29.91 MiB；未加入候选方案中无必要的多组大索引。最终 API 镜像需复制新迁移并重新绑定 SBOM/镜像摘要，运行二进制保持既有冻结 SHA。
- 同一小规格服务器并行执行构建、隔离集成和压测后，观察到 I/O pressure 约 91%、负载约 7，随后 SSH 卡在 banner 阶段；生产静态健康端点仍可返回，但真实 API 出现超时，不能据静态 200 宣称生产健康。已停止提交新的远端负载，后续验证改为串行队列。
- 已准备仅匹配完整 Core 私有编译容器 ID 的 cgroup SIGTERM 脚本；用户已打开阿里云云助手，等待执行回执。该命令不重启 Docker、不删除容器或数据卷；尚未确认 SSH/API 恢复。恢复后需先检查并释放本轮独立测试资源，再继续验收。
- 对照静态加密要求，已编写 MinIO SSE-S3 主密钥文件配置和独立正反向验证脚本；尚未运行成功，生产未启用，既有对象也尚未迁移，不记为已验收。

### 逐章审计发现的功能遗漏（继续实现，不缩减范围）

- 复核指南 4.5、5.3.2、7.6、7.11～7.12、8.3～8.8，发现此前矩阵把部分“代码未实现”误归入仅待验收：任务批量操作/对比，火焰图高级筛选/联动，采集优先级，上传失败后的原文件恢复，安全日志制品，perf 原生线程/时间/DSO/丢样保留，总临时盘预算，默认 HTTP 超时与请求取消，已选 Agent 的类型过滤。已追加到剩余实现项并分工，不能称这些功能已经完成。
- API Client 已增加覆盖连接和响应正文的 20 秒默认超时、外部取消信号合并、定时器/监听清理；4 项离线回归实际通过。通用导入可显式放宽至 60 秒，重试继续使用原幂等键。
- 优先级先写范围测试，因 CreateTask 未定义 Priority 编译失败；随后添加 -10..10 参数/持久化/Proto32与005迁移，正向与真实数据库验证继续进行。Proto 既有字段编号保持。
- 新增代码将改变 API 与 Analyzer 的最终源码/二进制指纹；此前镜像和 SBOM 保留为历史证据，不能直接套用于最终发布。

### 2026-10-17 连接恢复及串行验证

- 用户通过云助手执行精确编译容器 cgroup 的 SIGTERM 脚本，返回匹配进程 0；这只能证明该进程当时已不存在，不能据此认定脚本终止了负载。随后 SSH 实际恢复，未重启宿主或 Docker。
- 已用明确项目名 dropfullstage 停止本轮全部隔离联调服务，保留容器、卷、旧发布镜像。线上 drop 项目未重建。复查生产 API /auth/me 和任务查询均 200、约 0.2 秒，内部 /health/ready 为 ready；宿主 load≈0.43、可用内存≈2334MiB、swap=0、I/O PSI avg10≈1%。这些为恢复后的时点快照，不代表峰值容量。
- 后续远端验证改为 Core→Analyzer→全栈→供应链单队列，本地浏览器/Go数据库回归独立进行。资源状态异常时不再提交额外构建与压测。
- 优先级真实数据库回归先红：Outbox 原 FIFO 仍先下发低优先级。修复为取消优先、priority DESC、created_at/id FIFO；repository/service/httpapi 三套真实 PostgreSQL17.11 回归全部通过、零 skip、14.189 秒，Windows Job 合计512MiB/一CPU、峰值约97MiB。证据 api/benchmarks/integration-priority-windows-2026-10-17.json，保留相应 red 报告，不冒充 Linux 运行证据。
- 任务批量操作和真实树对比代码已补；新增11单测及显式 imported-browser-fixtures 的375/1440、axe、确认/部分失败/精确字符串ID检查通过。该浏览器检查不替代真实全栈。高级火焰图新增线程/系统栈/自身聚合/排序配色/调用关系与TopN联动；10项相关单测和最新构建通过，完整浏览器回归仍待执行。
- 最新 Web 完整49项单测和 TypeScript/Vite 生产构建通过；高级火焰图本地导入fixture的浏览器线程/系统栈/自身视图/配色/TopN/调用边跳转均通过，375/1440 axe零违规、无横溢、无JS异常，最终一轮6.4秒。首次因预览进程已结束连接失败，改用Playwright管理前台服务；首次精确label选择器等待失败改用浏览器实际combobox可访问名称，没有放宽功能断言。显式标注 fixture，不作为现场采样证据。
- 目检后将未配置模型的辅助卡移至主图与规则证据之后，防止遮蔽主诊断首屏；修复调用关系按钮的原生浏览器外观。导入的语言原生文件现在同样显示“数据导入”和未知采集时长，避免误写Agent采集及0秒。新增perf证据折叠区保留线程、原纳秒时钟和unknown丢样，不把未知显示成0。

### 2026-10-17 全量验收继续

- 最新安全LOG/优先级/当前attempt授权增补，repository/service/httpapi三套真实PostgreSQL17.11回归通过，零skip、17.358秒、Windows私有Job峰值104.09MiB；报告integration-log-grants-windows-2026-10-17.json。覆盖取消/重试、终态120秒LOG、旧attempt/软删除、presign并发写回fencing以及旧RAW basename升级，不把fixture当生产证据。
- Core当前快照Linux Release以-Wall/-Wextra/-Wpedantic/-Werror编译通过，CTest10/10；独立双Server mTLS RPC验证身份隔离、attempt fencing、取消/心跳、LOG与优先派发，旧无锁Server负控实际触发并发超派断言，修复后通过。Agent2→Server1及Agent1→Server2均真实PROC_CPU上传、DB大小/SHA核验，2/2通过，包含Server故障切换和V1协商。后续终态grant白名单加固及其增量回归仍在做。
- Analyzer0.2.2新增原生perf元数据和下载/临时盘聚合预算已冻结；本机53项离线回归中48通过、5项依赖POSIX跳过。新的91项Linux真实PG/S3回归及100k/512MiB基线待Core队列交接，旧0.2.1的70项不代表该版本。
- 本机实际Chromium原生Worker三档规模基线10k/50k/100k均通过，全部节点保留，初始SVG仅23帧；离屏全树搜索、深80层聚焦/重置、展开和键盘、100k的1440/375无横溢。报告附源码/bundle SHA与环境，明确仅导入fixture、不代表Analyzer/网络/服务器SLA。
- 又发现配色/排序会把已聚焦子树重置：新增浏览器断言先红（预期2节点、实际7），修为保留当前root和调用关系；同一root重布局保留中段scrollTop/底部锚点。49单测、TypeScript/Vite构建、explorer和三档规模浏览器共4项通过、17.7秒。此次100k Worker首次往返161.8ms、结果fetch至双RAF可交互743.6ms、全树搜索1.7ms；主renderer JS堆100ms抽样峰值129.21MiB，明确不含Worker且不是OS RSS。
- 第一次独立MinIO加密fixture失败：内容哈希正确，但自动加密返回算法不是要求AES256；未触碰生产存量。已补显式bucket AES256规则，新增整bucket存量迁移dry-run/停写确认/条件复制/前后流式哈希及legacy/wrong-key恢复脚本，离线测试通过；真实私有重跑和生产迁移待执行，不能记为静态加密已交付。
- API优先级和LOG修改已改变运行代码，旧SBOM所绑定的二进制不再是最终版本；发布须重新编译Linux API、携带001～005迁移并重新绑定镜像/SBOM。更新RUNBOOK和供应链说明，防止误用仅COPY迁移覆盖层。
- 最新API已在本机用Go1.26.9分别交叉编译静态linux/amd64和arm64，全部66份Go/module/Proto/catalog/SQL文件编译前后SHA一致；`go test -tags=nomsgpack ./...`通过。编译步骤没有运行Linux二进制；真实PG证据另绑定，未冒称最终镜像已构建。见api/benchmarks/release-cross-build-2026-10-17.json。
- 告警实测发现三处漏报：Analyzer完成计数少_total且failure状态写成failed、过期租约恢复少_total、ServiceDown未包含Agent。官方Prometheus3.15.0 Windows发布包经官方SHA256清单核验，promtool时序回归先红（3个预期告警未触发），修复后18规则语法及5场景12断言通过，含恢复/低失败率/证书过期。此为合成序列单测，真实抓取/Alertmanager/审计闭环仍待执行。
- 存量加密工具新增37项离线、8项真实PG半边回归全部通过，0skip；PG17.11使用全部5份迁移、Windows Job512MiB/1CPU，实测SHARE锁、维护互斥、活动任务拒绝、3秒锁超时和释放后恢复。S3部分尚为Fake，已短暂交接唯一远端队列执行真正MinIO legacy/AES256/wrong-key恢复，未动生产。
- HTTPS安装脚本在umask077下创建runtime目录会阻止非root网关读取配置，已补0755；Certbot签发/续期辅助容器加256MiB/.25CPU/64PID预算。6份发布/备份/证书/监控脚本逐一bash -n通过，完整Compose叠加config --quiet通过；TLS实际签发与上线尚待执行。
- outbox最后一次RPC失败存在真实竞争：旧快照会把已被Core接受的QUEUED、用户CANCELED或重试后的新CREATED改成FAILED。新增3场景真实PG回归全部先红，再改为RPC返回后按Task→最新Attempt加锁重读，仍为同attempt/CREATED才原子失败，否则退役旧outbox；不在RPC期间持Task锁，避免Core自身状态事务死锁。重编repository/service/httpapi后三套零skip全绿，12.428秒、峰值159,756,288B；保留red报告。最新linux/amd64和arm64 API已重编，67份来源SHA编译前后相同，先前二进制不再作最终。
- 私有上传故障注入4/4实际通过：503→429→200、403只请求一次/保留RAW、Agent重启只上传且RUNNER_ACCEPTED仅一次、迟到LOG第4次补传，RAW/LOG实际字节均与DB SHA/size匹配。新perf_script和perf.data已真实采样交Analyzer；高级BOLT/BPF首次暴露工具PATH及bpftrace表达式错误，按具体错误修复后重验，不能把首次失败改写为通过。
- Server可观测遗漏已补：最新私有OTLP receiver真实收到drop-server的queue.task/dispatch.task，验证持久化task/request、原traceparent的异步links、新traceId及安全属性；对应快照严格编译和10CTest再次通过。全栈Tempo查询及Alertmanager审计投递仍待执行。
- 真正MinIO加密fixture已实测legacy→AES256和新写，整体16.340秒；wrong-key精确错误码匹配失败使脚本停止，原密钥恢复未执行。保留该失败快照，按精确release源码仅补kms密码学错误码并做红绿回归，generic500/网络错误不当作成功拒绝。实际资源label计数容器/网络/卷均为0，生产未改；新版完整重跑待串行队列。
- 备份和恢复脚本改为从部署目录读取已有完整COMPOSE_FILE，防止仅基础文件丢失当前覆盖配置；各自bash -n通过，真实新版备份恢复仍待执行。
- 复核指南4.4发现终态重复取消原返回INVALID_STATE，不满足幂等要求。新增真实PG的6并发取消与4种终态重复请求，先红后改为授权后返回当前终态、不重复写事件/outbox/audit；同轮补最低schema5启动门禁（移除5的真实PG负控先红），防止新priority运行代码误接受仅schema2的旧库。最新三套PG回归零skip、13.689秒、峰值112,652,288B；新的两架构静态API及仅8文件/约11MiB发布context已准备，未构建镜像/未部署。
- 最终Web68项单测/10文件、生产构建和新增E2E严格TS检查通过；主线程对最终index-DoEWqUpR统一重跑10项本机浏览器回归，27.7秒全绿。覆盖高级图及10k/50k/100k、20/40秒轮询退避/成功恢复、隐藏暂停、离页原生Abort、SSE精确int64游标/1-2-4秒重连/403停止。controlled-clock与visibility模拟在报告中明确，真实fetch/读流/Abort和Worker未替换。所有源码哈希与最终bundle绑定，未冒充全栈。
- 真实全栈批量/对比用例已补，仅创建2个独立import任务，完整双树与权重差、确认删除和逐ID404；现只做编译/list检查，尚未访问stage。Web增加与API一致的预编译产物Dockerfile.runtime，约280KiB无secret构建context已准备，实际镜像构建待串行队列。

### 2026-10-17 02:51 用户要求暂停

- 已按“先停下”中止全部代理的开发/验证，完成仅用于停止的只读确认。远端私有Core任务/容器0，各代理本机preview0，4178/4186无监听，未发现任务stage隧道仍运行。生产未动，文件/卷/命名镜像保留。
- 暂停前API新增采集MANIFEST授权和终态撤销；新MANIFEST使用manifest/，新BINARY采集/导入使用binary/，与各90天生命周期一致，避免raw/14天提前删除。真实PG先红后绿，最新三套回归13.777秒零skip，两架构API重新编译。首轮采集manifest测试还暴露重复Unmarshal复用map残留字段的fixture问题，清空目标后继续保留原严格断言。
- Ops新增实际SDK生命周期XML回归，binary/90天缺失先红后修复；已加入binary/孤儿清理范围，保留活动引用/新对象不删除。28项离线回归通过，未部署新规则。
- 最后逐条审计仍发现采集独立manifest、ETag、离线事件、跨Server入队上限和CPU affinity遗漏。Core已合入部分实现，但截至暂停尚未重新编译验收；两份RPC fixture增补仍待写。不可将先前10CTest/4恢复绿套于本轮改动。
- 安全Markdown已用于规则、模型、流式和复合建议，禁自动图片/危险协议/原始HTML。视觉复查发现全局flex会挤压正文，先几何回归复现后修复；最新79单测、build及375/1440真实浏览器/axe通过，解析器动态分块后主入口471.41KB。尚未统一重跑全站preview，旧Web构建context不代表此版本。
- BPF已有部分子类实采证据，alloc在完整受控PERFMON+BPF+SYS_PTRACE组合仍EACCES；未扩大SYS_ADMIN/privileged/hostPID权限。BOLT和block修正待复验。Analyzer91/512MiB、新perf交叉、完整SSE、全栈、运维和生产发布均尚未完成。
- 完整停止点及恢复顺序写入docs/PAUSE_STATUS.md；本轮没有宣称全量完成，公网仍旧版。

### 2026-10-18 按用户要求恢复全量开发与验收

- 用户明确要求“继续吧”，已解除暂停。先核对停止点和当前源码；远端负责人确认仅8个生产容器、可用内存约2326MiB、swap0、I/O PSI0、磁盘约11GiB可用，为时点快照。保持Core→Analyzer→全栈/运维→供应链单队列，本机Web/Go检查独立进行。
- 最新Web统一复测79/79单测、TypeScript/Vite构建、spec/config类型检查、Prettier和依赖树通过。受Playwright管理的preview11/11、零skip/flaky、42.94秒，覆盖真实Worker10k/50k/100k、375/1440、轮询取消/退避、SSE精确游标/隐藏暂停、安全Markdown/axe；该轮不是实际后端全站验收。重建bundle保持index-CXjL4q3j.js，统一冻结报告正在整理。
- API在未改运行源码的前提下重新核对并封装8文件运行context，约11MiB；linux/amd64二进制SHA仍为7dcae4788480b2db5c68aea5ac7badb1dfd462d7cf2709310c854d4fc26ef69a。HTTP契约60操作/73Schema/18类型及Proto既有字段兼容/负控再次通过；尚未构建最终镜像或切换生产。
- Core开始补真实schema4拒绝、双Server队列32上限、manifest事务/回放/授权与offline事件去重fixture，再以0.5CPU/768MiB及读8MB/s写4MB/s预算串行Linux编译。Analyzer先在本机准备，25运行文件冻结未变，尚未接手远端队列。
- 备份审查补齐对象自定义metadata及编码/语言/下载名/缓存内容头，恢复只允许这类有界头，拒绝鉴权/复制/密钥头、注入、重复及超限；兼容旧备份。压缩对象按原字节流备份，不进行HTTP自动解码。6项离线回归与丢弃metadata负控、Git Bash语法和内嵌Python编译实际通过；默认Windows bash指向不可用WSL首次未执行成功，已改明确Git Bash，不计为通过。真实随机S3桶及新版完整备份恢复尚待串行全栈阶段，证据docs/evidence/full/backup-metadata-offline.json。
- 再审指南11.3确认“Agent专用用户/独立最小root helper”仍是缺口；明确安排Core当前验收交队列后在本机落实，不能用UID0+capdrop代替该要求。最终源码与镜像须待此项完成后再次冻结。
- Core pre-helper冻结实际通过Linux Werror/11CTest、schema4拒绝/最低5、双Server31+2仅1ACK且最终32队列、mTLS/manifest回放与原子回滚、offline事件去重和OTLP安全links；两种独立负控分别复现33队列和双派发。实际Agent标签覆盖缺陷已修，6项上传恢复（含manifest重试/重启字节不变）及N/N-1双向2项、Host/Docker目标7项通过。真实BOLT/syscalls/vfs/block/pagefault/mmap六项采样通过，alloc受只读tracefs限制稳定拒绝，advanced整体不能算全绿。core/evidence/resume-pre-helper-20261018.json记录各批指纹；独立helper不在该证据内。
- 实际urllib3 gzip响应又复现了维护器和Analyzer/加密迁移器的透明解压，可能把合法压缩对象误判SHA。三处改为读取存储原始字节；维护器2项真实HTTPResponse离线红绿通过，Analyzer另增真实S3 gzip轮转测试。迁移器保留四项系统metadata，对SDK不能原样写回的Expires写前明确拒绝。最新Analyzer25运行文件重新冻结为36f726fdf62ee5262160ed8ea95fe8b959f61ba0e5274e02ddc44e60b92db8d9，不能套先前SHA。
- 新增只读取证器的重启/OOM、已知镜像引用、seccomp摘要和项目网络白名单；当前16项脱敏回归通过。Helper部署边界4项、备份metadata6项、维护器原始字节2项和Ops测试资源所有权4项，共32项离线检查通过、0skip；4份Bash及两份内嵌Python语法通过，Ruff检查通过。新helper Compose/systemd/K8s配置没有运行时验收，不能仅据配置声称最小权限已交付。
- Ops孤立测试runner先用Fake-Docker复现4处碰撞/清理边界失败，修为随机prefix、启动前拒复用、只清本轮label资源、保留失败码、只读/无swap/明确就绪门禁。新增CI覆盖统一preview、HTTP契约、Ops孤立PG/S3、部署纯回归及Prometheus规则时序；仅YAML解析已执行，未声称远端CI已运行。新helper K8s严格1.34.1 Schema21/21，零错误/零skip，见docs/evidence/kubernetes-schema-helper-2026-10-18.json。

### 2026-10-18 中断后继续收尾

- 用户要求“继续吧，刚刚中断了”，从现场文件与远端进程恢复，不重启开发。只读确认生产仍8容器，可用内存约2360MiB/swap0/I/O PSI0（时点值）；没有遗留私有测试容器。唯一远端队列先交Analyzer，Core/Web仅本机开发。
- 取回中断前已完成的Analyzer92项真实PG17.11/S3：92pass、0skip、57.822秒，安装运行时与新SHA一致。真实perf_script通过，但新perf.data暴露工具兼容失败；保留失败日志并继续定位，未将92项通过冒称所有实采输入通过。
- 取回Web新增压缩制品链实际94单测和21/21统一浏览器通过、0skip/flaky、38.93秒。原94之前“91项”为过时计数，以JSON实际结果为准；目检后的少量留白修正仍需对应新bundle复测/冻结。官方npm registry实际audit252依赖、各级漏洞0，默认镜像audit404未算成功。

### 2026-10-18 第二次中断后继续

- 继续保持远端单队列。Analyzer 最终 Linux PostgreSQL17.11/S3 回归为92/92、零skip、58.459秒；新版官方 perf6.12.95 解码真实perf_script91样本与独立perf.data93样本均通过。512MiB整容器100k节点峰值159076352字节、OOM0，100001节点明确拒绝。最终镜像326427a3996a…；此证据对应增加统一配置/日志前的冻结版本，后续运行源码变化须重新验证。
- Web 最终94单测、21浏览器回归零skip/flaky通过，安全下载辅助另有6项真实loopback回归。只使用冻结的runtime context构建drop-web:20261018.1-rc1，镜像43511ce389c8…、UID101；单任务256MiB、无swap、0.5CPU。直接流式导出到开发机后生成实际CycloneDX1.7 SBOM（1282组件），未在服务器额外保存镜像tar，也未切换线上。
- helper第三轮严格Linux编译和12项CTest通过；7组真实Linux UID/UDS/stream/replay/cancel/ACK隔离测试通过，profiler工具为明确标记的imported shim，不记为真实采样。helper最终实际PERF、延迟能力注册、掉线重注册与PG/PUT恢复仍待验。
- 实际准备独立stage的2GiB ext4采集文件系统，root仅允许/opt/drop-stage-full/runtime；两次执行保留marker inode和内容。cache 0:0/0700与incoming 0:10001/1770同设备，挂载nosuid/nodev/noexec。UID10001实际超额posix_fallocate返回ENOSPC28；元数据后的容量2040373248字节。未增加helper SYS_ADMIN，也未修改生产文件系统。证据docs/evidence/full/helper-spool-stage-2026-10-18.json。
- 部署纯回归本轮46项及真实urllib3响应2项均通过；首次把Ops测试从错误cwd导入产生ModuleNotFoundError，改为其实际工作目录后2项通过，未修改运行源码。更新Kubernetes19+2资源后，严格1.34.1 Schema21/21、零错误/skip。小规格overlay去除没有HTTPS overlay时会产生无镜像gateway的配置错误。
- 逐章核对确认指南10.4配置优先级和11.6统一日志字段仍有实现缺口，已安排各语言原生入口及回归，不能仅归为“待验收”。将保留原环境变量和Secret_FILE，增加CLI>环境>配置文件>默认、脱敏有效配置与稳定hash；因此API/Core/Analyzer/Ops最终冻结与镜像仍需重做。
- 一次过宽的本地JSON检索输出了旧管理员会话Cookie；立即停止宽泛检索。只向原站/auth/me发送该旧会话且不打印敏感内容，实际返回401，确认服务端已失效。后续测试使用新的独立会话，禁止扫描凭据目录正文。
- 生产最新schema1的23任务/115制品已于本轮在线备份，115对象共1510006字节实际上传/下载到独立验证桶并恢复到独立数据库，5.76秒通过，Content-Type/metadata和全部SHA验证；验证资源清理成功，生产服务未重启、schema未变。备份目录/机器证据见preupgrade-backup/restore-2026-10-18.json。首次SSH stdin被docker client消耗未产生完整证明，已显式隔离stdin并以完整成功证明作门禁，不把首次零退出误记验收。
- 将该份3.4MB完整备份通过SSH流式拉至开发机独立E盘，仅持久化AES-256-GCM密文及私有目录内的恢复密钥；实际认证解密hash一致，ACL检查只有当前用户与SYSTEM。首次身份命令因本地化输出编码失败且未转移数据；改用原始字节提取SID授权，新增两项安全回归，签名/ACL共8项通过。此次人工异地主机副本不等于定时复制或托管vault，生产迁移后还需新备份。

### 2026-10-18 配置日志与浅图优化收尾

- API使用原生Go实现CLI `--config`/`--set`、CLI>环境>JSON>默认、26键allowlist与原有Secret_FILE行为；拒绝未知键、重复键、null、控制符、大文件及CLI/JSON内联秘密。只打开最终优先级获胜的Secret引用；Linux非阻塞打开并检查常规文件，兼容投影Secret的常规文件符号链接。有效配置和稳定SHA256只包含安全投影，秘密值及其可猜摘要不进入公开hash。
- API启动和HTTP日志统一12公共字段，未知上下文留空，固定错误码及允许列表附加字段；不记录Cookie、Authorization、查询串、原始错误或Profile。实际负控先复现低优先级失效FILE被读取及JSON null被当空字符串，再修正并通过。最终13包/54项单测、go vet、gofmt、3套重新编译的真实PG回归0skip（16.873秒）及实际schema5 migration入口两次通过；无DB单测环境另有18项skip，未混入真实PG结果。Windows实际入口验证12字段和CLI秘密拒绝。
- API重新冻结79文件并交叉构建linux/amd64、linux/arm64，amd64二进制SHA d883250ba83fa64655a10c8f4f3c83fdc75541b9aeec5cb1caa7a572cd9f0c76。根代理已实际构建API rc2镜像6e87244…，执行Linux配置/FIFO/投影Secret链接6项全过；本轮Ops rc2另有42项真实PG/S3全过。后端完整stage与生产切换尚未完成。
- 根据1440/375实截图，火焰图浅树最小高度220产生明显空白；新增renderableRows/tinyFrames随Worker遍历计算，SVG最小高度收紧到104，并说明细帧仍保留、可搜索定位。完整100k未裁剪。先对旧bundle运行高度负控实得220并保留截图，再验证新bundle高度不超过120及搜索末端隐藏叶帧成功；深树滚动/键盘仍使用实际可绘制行数。
- Web rc2最终96项单测、6项真实loopback安全E2E辅助、21项本机原生Chromium检查全部通过，0skip/0flaky，浏览器50.909435秒；TypeScript、spec类型、Prettier和生产构建通过。375/1440截图逐张目检及axe零违规，无横向溢出；这些接口明确为导入fixture，不能冒作真实后端全站验收。冻结入口index-DL4Gc6cu.js，source manifest 397a0ad0…d668、全部bundle manifest fcc5bd71…0ed3。
- Web rc2部署包仅包含Dockerfile.runtime/nginx/dist，332801字节、SHA0741948c378d4e05713af684ff5478ce7448b90d3ed92e40b59183708e619976；原rc1包及证据原字节保留。根代理已实际构建drop-web:20261018.1-rc2，镜像3b7e61ce6c5e2b4a833e0a3d75e001bb9a1b11a29676b6a7cbd82402c83f4892、运行UID101；旧1282组件SBOM只对应rc1，rc2供应链结果另行记录。
- 运行源码与产物保持冻结；等待Core最终strict14和真实语言目标/helper验收后由根代理启动stage，再执行实际全站浏览器。真实stage显式`--trace off --reporter line`、不录HAR/video，下载通过限额/精确大小/哈希与固定错误码辅助；尚未启动此次实际stage浏览器。公网仍backend20261016.1/web20261016.3，未声明全量完成。
- 随后取得实际rc2镜像报告runtime-images-rc2-2026-10-18.json与web-runtime-image-rc2-2026-10-18.json，绑定API6e87244a…、Opsd6453ecb…、Web3b7e61ce…及各context SHA；API Linux6项与Ops隔离42项（1.092秒）均有日志指纹，生产未改。Analyzer0.2.3最终报告也已落盘：26文件08656189…、镜像55ae36b4…，Linux真实PG17.11/S3为101/101、零skip、59.168秒，重新解码实际Core RAW script91/data93各自匹配。其解析/流水线/资源预算未变；此前0.2.2的512MiB规模基线没有重跑，不改标为0.2.3实测。
- Stage执行前只读审查发现：E2E Node fetch对保留Content-Encoding:gzip的对象会透明解压，API PresignGetFor也尚未签identity覆盖，浏览器原始下载存在同类问题。先用真实loopback的压缩对象/编码头令旧辅助实际失败，再改node:http/https原始有界流，7项回归全过，相关spec TypeScript/Prettier通过；无重定向、无Cookie/referrer、精确大小/总超时和固定脱敏错误仍保留。原rc2运行冻结保存在web-rc2-runtime-source-freeze.json，统一冻结新增E2E-only来源eae93dc6…1f20；全部dist与332801字节context逐字节未变，不重建Web、不冒报重跑96/21。
- 根代理授权修复产品链路：API短URL将response-content-encoding=identity纳入SigV4；新回归先红后绿，独立HMAC验证签名有效、删除override使签名无效，37秒TTL/原对象路径/同源代理映射不变。仅storage.go/test2文件变化，配置/授权/生命周期/SQL未改；实际MinIO的存CE:gzip→identity短URL→自动解压客户端仍拿存储SHA，留待stage实测。
- API rc3另存.local/release-api-20261018-rc3，未覆盖rc2。13包55单测通过、无DB环境18项skip、vet/gofmt和新Windows实际入口12字段/秘密拒绝通过；此前真实PG3套保留原范围，本次没有重跑或重标。新linux/amd64 SHAee66a9b486cf3c29499a21f549fcdced13482c2cbd1991805ceddef9b2bf906b，arm64 SHA2735a739…324c；8文件context11273914字节，SHA9621c3f4d874ac03f4f6addff145195af640f0359e5de4849a7a8f4858113a58。第一次本机冻结器只从stderr检查日志而实际JSON在stdout，检查器修正后重跑通过，API运行代码无额外变化。

- 新增服务器本机SBOM采集器scripts/release/host_sbom.py，20项纯fake-host/文件系统回归、Ruff检查/格式及Python编译通过，未访问远端或启动Docker/Syft/浏览器。限制六组件精确引用与预期image ID，串行docker:sha256扫描、前后tag指纹复核、私有工作目录/资源门禁/固定错误码/拒已有结果；记录兼容既有离线签名器并标source=docker-daemon，不伪造archive摘要。官方Syft1.54.1会将镜像Labels放入CycloneDX metadata，故发布前仅重建安全来源metadata，组件/依赖原样保留并按最终SBOM计算SHA。真实Linux六镜像扫描、Schema及签名仍待根代理执行，证据host-sbom-offline-2026-10-18.json。


### 2026-10-18 继续：发布前静态门禁

- 本机重新执行部署边界回归46项、共享TaskKind/Proto兼容/HTTP契约3项检查，全过；记录为 `docs/evidence/full/deployment-offline-final-2026-10-18.json`。本报告不重标以前的Ops真实S3或Linux采集结果。
- 最新Kubernetes只读subPath Secret配置重新通过官方kubeconform严格K8s1.34.1 Schema：21/21、0错误、0跳过；实际集群尚未提供，未声称HA演练通过。
- 新helper真实6类恢复已完成，语言/BPF高级采集仍在串行验证，最终Core镜像未冻结；公网仍是旧版本。

- TLS部署脚本补齐只重建指定服务的--no-build/--pull never，公网签发/续期共用.acme.lock及public-state.sh事务：新pair写受控bundle，current原子切换，edge配置原子替换，nginx检查/重载或HTTPS探测失败恢复旧pair/config；回滚自身失败保留两份恢复状态，成功退出不误回滚，未变pair不累积副本。两个edge模板error日志限crit、access关闭；过期监控改为验证后读取current。12项实际Git Bash+假命令事务回归、Ruff/编译与5份Bash语法/LF检查通过，零skip；Windows只有ln/readlink用标记文件模拟，无原生symlink/真实Docker/OpenSSL/HTTPS验收声明。新操作顺序见deploy/tls/README.md，证据tls-publication-offline-2026-10-18.json。实际内部轮换仍需独立Agent新心跳及经helper的PERF实采，不能用PROC_CPU替代；未访问远端，API/Web运行产物未改。


### 2026-10-18 继续：供应链检查准备与真实缺陷

- 开发机校验并运行官方immutable Grype0.120.1（archive与官方checksums/API digest一致）；实际更新10月17日漏洞库并记录SHA。试扫历史Web rc1得到zlib的1项可修High与BusyBox的3项Medium匹配，没有把它写成当前rc2或生产扫描结果。最终六镜像与可利用性/修复评估继续，未宣称扫描门禁通过。
- 官方Stereoscope provider会先暂存daemon tar再解包层，因此原SBOM门禁的单份镜像空间预留不足。新增1GiB镜像/2GiB空闲的负控实际先失败，修为2×image Size+独立1GiB后，21项纯边界回归及Ruff通过。只有准备证据，没有访问ECS或清理生产/旧镜像。
- 内存采集发现GDB已可ptrace但O_RDWR打开目标0600内存文件会因最小DAC策略失败。Core正以官方LLDB后端验证相同权限及512MiB限制；若通过将同步共享tool声明，并重新封装API候选，旧rc3产物保持。

- Web生产依赖审计以node直接执行npm-cli.js、结构化捕获真实exit0（不沿用PowerShell末端退出码），官方registry返回117prod/0漏洞、无error/stderr，package.json和lock SHA前后相同；结果web-runtime-dependency-audit-2026-10-18.json只覆盖构建浏览器资产的生产依赖图，不代表Nginx OS包。另通过有界公开HTTP实际取得DockerHub1.30.6-alpine的官方index/amd64 manifest digest及10月14日更新时间，Alpine v3.24/main SecDB确认zlib CVE-2026-85091 fixed1.3.2-r1；原web工具访问失败没有算成功。只读证据与条件性修补顺序分别存nginx-upstream-metadata-2026-10-18.json/nginx-patch-plan-2026-10-18.md，历史rc1匹配没有冒作rc2或官方当前镜像结论。未pull、未访问ECS、未改冻结Dockerfile/dist/context，也未自动新增平台镜像。


### 2026-10-18 继续：当前候选OS依赖确认与修补准备

- Core队列负责人在当前Web rc2镜像内以24MiB/no-swap、UID65534、capless、只读根、network none执行apk只读查询，确认zlib1.3.2-r0。官方Alpine v3.24 SecDB明确CVE-2026-85091修于1.3.2-r1；当前官方Nginx tag index digest也已读取，未凭日期宣称镜像已修。
- 已授权本地Web两个Dockerfile最小升级zlib，保持原JS/dist字节；将封新rc3，不覆盖rc2。存储网关和HTTPS入口将使用独立受补丁的平台Nginx镜像，六应用组件之外另记平台SBOM/签名。实际构建/新SBOM/High是否消失仍待重队列。
- host SBOM只增加明确drop-nginx白名单，22项纯边界全部通过；发布只读取证器新增同一发布镜像白名单后16项全部通过。未把这批纯测试当镜像扫描或上线。

- 完成.local/stage_idle_agent.py准备及9项纯fixture（含带空格/括号comm的stat列偏移、单核CPU计算/PID重用/UID、只读SQL及脱敏），Ruff/编译通过；固定dropfullstage Agent/PG labels+IDs和tini直接child，60秒主进程/受控子进程分开、Docker/cgroup/host PSI另列。没有执行远端采样，prepared报告不含任何伪造RSS/CPU值。
- Core实际只读确认Web rc2 zlib1.3.2-r0后，按明确授权仅在Web两Dockerfile最终stage插入USERroot/apk upgrade --no-cache zlib，保留最终USER101；精确移除两行能复原旧recipe哈希，全部92输入及14个bundle核验，业务源码和dist原字节未变。另准备无业务COPY的deploy/Dockerfile.nginx，供已授权第7基础设施镜像drop-nginx、两个网关共用；未修改Compose/CI。
- 独立Web rc3 context332838字节，SHAa60dd66a2062e812e8fa48ff471e27b5de1a9cb7841859bcb85684f794f8aa19；独立平台context251字节，SHA81fbbcbdbb0234dabd8958e2ec5e4982f98954e5b2f04818b46c3b4f754cb228。逐件tar成员/字节比对通过，旧rc2包0741948c…原样保留；新source13e52866…，dist仍fcc5bd71…。新冻结web-basepatch-rc3-freeze.json明确96/21/7仅沿用未变JS证据，没有重跑或冒报OS修复。尚未build或执行apk，实际包版本/SBOM/Grype/UID101/网关与stage浏览器仍待根队列；本地stage执行器已切为新rc3冻结校验，未启动浏览器。


### 2026-10-18 继续：Alpine仓库连通与独立候选

- 平台Nginx rc1实际apk构建在240秒墙钟限制结束且没有新tag，临时RUN容器已由根代理确认不存在；不把超时或候选recipe记为构建通过。根代理在同原Nginx镜像的32MiB/no-swap/capless/UID65534/只读容器实际读取Aliyun v3.24 main x86_64 APKINDEX，HTTPS200、528376字节、约1.8秒、exit0。该验证只证明仓库连通，不证明安装或修复。
- 三份Dockerfile统一只用sed将dl-cdn.alpinelinux.org替为mirrors.aliyun.com，原分支路径、包签名校验、apk upgrade --no-cache zlib与最终USER101均保留；精确逆换能重建前次recipe SHA。新Web rc4 context332913字节/SHA97e8c08093b0cd95217109e73aaab3a8c9ac5c06a612f0e6bdd12d301fd08d22，平台rc2单文件context314字节/SHAad57616d4db3d3deb4591cf6c6bc641ac2dde1c4959a0b15ff1ebdde9bc64602，逐成员和内容核验通过。
- web-mirror-rc4-freeze.json source manifest c88d2fe98e23c68bee64865e38399cca3268658da50b06084829b72afbce419a，全部14个dist仍fcc5bd71…0ed3，沿用96/21/7时明确未重跑JS。旧Web rc2/rc3及平台rc1 archive指纹重新核验不变；本机stage launcher只更新优先校验最新freeze，Python编译通过，尚未启动browser。新镜像实际构建、安装版本、SBOM/Grype/UID与真实stage行为继续待根队列。


### 2026-10-18 继续：实际依赖扫描与镜像修补

- 原平台Nginx CDN构建超时后，确认中间RUN已退出；改用已验证连通的Aliyun同分支镜像源且保留APK签名检查，以256MiB/no-swap/0.25CPU串行完成两个新镜像。drop-nginx:20261018.1-rc2 为 f76c617fd59867fba9b4d322c1cd4a041d2253cf440f4816f7cad95a3c4a6cee，drop-web:20261018.1-rc4 为 09823c12f5b2fc80c87400f0065ffb7b1395f130b324c0b347b5d2544b74812a；最终USER101，两者在实际24MiB/no-swap/UID65534/capless/只读/network-none容器均查得zlib1.3.2-r1。证据web-platform-runtime-images-2026-10-18.json；未修改前端dist、未重复冒报96/21/7，也未因此宣称完整扫描或公网验收通过。
- 对Ops rc2实际执行受systemd约束的Syft1.54.1扫描，生成3381组件SBOM，SHA af65807579972ab5f4cb9d55d9121c3da77532a22226392616b7717490872919。扫描限制实测MemoryMax384MiB、swap0、CPUQuota50%、无外网、磁盘读8MiB/s/写4MiB/s；Docker daemon导出在该cgroup之外，未把子进程限额冒作daemon上限。供应链README已明确此边界及实际50%设置。
- 使用官方校验的Grype0.120.1和已更新的10月17日库，本机离线扫描上述精确SBOM，实际434条匹配：Critical17/High156/Medium147/Low27/Negligible87，exit2、发布门禁失败。原始报告与摘要位于docs/evidence/supplychain/security，SHA a20246a28f998fc9b750c17bbb9314e81d8d027f9664daf57f15c2183e8cea91。正在逐包核对Python运行时、urllib3和系统库并准备候选；尚未忽略匹配、改阈值或替换生产，旧42项功能回归保留原适用范围。
- Core恢复GDB catalog后，其JSON与API rc3语义相同，但LF/CRLF令字节SHA不同；已协调从rc3归档恢复原字节，最终采用c4a8757ea8628c2756df736221816ace667dbb92c7aed30d15571b08f13da243，避免不必要的API重封。stage准备脚本改回APIrc3并钉死Web/网关实际ID，仅本机编译校验、未执行远端配置或启动。Core正在唯一重队列验证真实GDB ptrace launcher，未将本机准备记为实采成功。


### 2026-10-18 继续：隔离浏览器顺序与UI只读复核

- 本机复核web-platform-runtime-images报告与两个context、92源文件及14bundle全部一致；实际构建/包版本由根代理报告，未重标为Grype或stage已通过。API最终仍rc3，Ops/Analyzer安全评估未接受，startupproof绝不推断扫描通过。
- 现有stage_ce_gzip只完成唯一导入对象元数据/原bytes检查，browser_ce_gzip才验证fresh grant、identity头、浏览器gzip magic/精确size/SHA及真实Worker。新增仅.local联合入口与9项纯metadata/假subprocess回归，实际preflight、Ruff/格式/编译和Node语法通过；Windows CRLF proof长度问题先红后绿改O_BINARY。入口固定loopback，CE→12项Full/System→synthetic10k/50k/100k遇错即停，不回显原异常；没有启动browser/SSH/服务/隧道，说明见stage-browser-execution-plan-2026-10-18.md。
- 指南§8只读复核：完整事件之外的概览是4阶段；失败结构字段/partial分析视图与最后失败事件仍画check存在真实展示限制。Markdown为react-markdown skipHtml及元素/URL白名单，未使用指南指名DOMPurify。限制记录到web-guideline-readonly-review和剩余矩阵；未改冻结runtime，未声称获奖认证或真实全量验收。


### 2026-10-18 继续：本机资源、独立候选与提前分诊

- 本机已有WSL2及Docker Desktop但engine未运行；尝试后台启动后，宿主空闲虚拟内存下降至约90MiB且出现Python MemoryError/工具宿主退出。未启动任何测试容器；立即执行docker desktop stop --timeout 30，实际exit0，Desktop/backend/vmmemWSL进程均消失，空闲物理约3.06GiB/虚拟3.30GiB恢复。本轮不再启动本机engine，不终止其他用户程序；ECS重任务继续单一队列。
- Compose新增ANALYZER_RELEASE_TAG/OPS_RELEASE_TAG独立候选覆盖，均回退RELEASE_TAG；三个基础/维护覆盖文件对应改动。本机Docker Compose5.5.0在干净临时env下实际通过统一标签与独立标签两组config合并，覆盖全部profiles并核验12项服务镜像、Agent/helper最小权限及loopback18080；未连接Docker daemon/未启动容器。服务器2.40.3实际合并另待stage。随后46项部署边界与3项共享契约重新执行全过、6.607秒。
- SBOM采集器仅再允许固定drop-core-full-tools-base:v2用于工具底座提前分诊，其他工具版本/registry前缀拒绝；报告显式diagnostic_only/release_component=false，不代替最终Agent或六应用签名位。新增负控先以SBOM_IMAGE_REJECTED实际失败，再修改后24项纯回归全过、0.094秒及Ruff通过，证据host-sbom-triage-offline-2026-10-18.json。尚未启动远端分诊扫描。
- 公开源码/证据预检查采用白名单及git -z正确保留中文根指南，未选入.local/.claude/Secret/runtime树；1076文件约36.5MB，2个已知live口令精确比对、私钥/API key模式与真实X-Amz-Signature查询匹配均零。初版模式误匹配task-后UUID中的sk-，按token边界修正并确认没有任何live值匹配；没有打印匹配值或私有配置。这是预检查，尚未创建最终源码归档/commit/签名。

### 2026-10-18 继续：前端rc5与发布阻断项核对

- Web rc5实际110项单测、23项本机原生Chromium检查（39.187秒，0skip/0flaky）、7项下载辅助回归、TypeScript/生产构建/Prettier通过；98源码与14bundle已冻结，运行context349343字节、SHA `69fb37481433425c5a23ab6fc6a601c9bf978a8e875774ffc4e24fe990f85126`。补齐七阶段进度、当前attempt失败结构字段及可读RAW/LOG/BINARY、失败/取消图标，不能以旧attempt制品兜底；失败后制品不冒作partial分析JSON。Markdown使用remark AST与URL规则后再由DOMPurify3.4.17清洗最终HTML，原始HTML禁用。官方npm生产依赖100项、0漏洞、exit0；这不包含容器OS。根代理目检375/1440最终失败页、Markdown页，卡片已修复桌面24px/手机16px边距；23浏览器接口均为显式导入fixture，真实整站stage尚待执行。
- 新helper的r14 Release/Werror实际16/16 CTest、6类真实上传恢复已通过；高级采集7/9快照之外，Python内存GDB符号加载和alloc/free仍在修复。r15三种真实对照均出现target executable I/O error，目标存活无OOM；r16固定受信PID的proc root/exe/symbols只读正控制已能解析四种Python表达式，但完整memray采集尚待实际验证，不扩大为SYS_ADMIN/privileged绕过。
- Ops/Analyzer安全修补候选准备采用官方digest校验的Python3.12.15-slim-trixie、urllib3 2.8.0、纯psycopg3.3.6加系统libpq，避免旧binary wheel自带旧OpenSSL。实际GNU checksum合包检查已过；Ops新42项+五阶段SSE、Analyzer新101项/规模/工具链与整镜像安全扫描均尚未执行。APT修复最低版本已核对官方索引，不把recipe准备写成安装成功。
- 工具基镜像 `drop-core-full-tools-base:v2` 的384MiB/no-swap/.5CPU诊断扫描实际被cgroup OOM终止，unit `drop-sbom-23518490d6.service`日志确认OOM而非超时。07:54 UTC实际只读查到root0600旧锁及root0700单个`.drop-sbom-*`目录，无活动scanner service；只取stat元数据、未删除目录。旧Ops3381组件实际包含3256文件、117library、7application和1OS，组件数不等于软件包数。后续准备将扫描内存上限明确改为768MiB并保留宿主余量、单队列、无Swap及I/O限制；锁的内核崩溃恢复回归仍需完成，未删cataloger以压低发现数量。
- 公网07:47 UTC `/healthz`200及内部ready通过，单次外部响应约0.039秒，不是SLA。生产仍旧版，没有迁移、重启或新版本切换。根代理已同步当前交付矩阵和未完成清单，未给出缺少依据的完成百分比。

### 2026-10-18 继续：补齐失败后部分分析结果与新版回归

- 逐项核对§8.6确认：旧版只有RAW/LOG/BINARY下载，解析完成但后续规则/构建/上传失败时，没有本次partial分析结果。现以 `analysis_jobs.result` 保存独立 `completeness=partial` 快照，无SQL迁移或Proto变动。只保留已校验解析的摘要/Top20、16KiB/160B UTF8名称/安全数值；不复制整棵火焰树、原工具报告或工作目录。所有写入复用当前attempt、owner/run与lease的前后栅栏，重试claim同事务清空，失败保留、成功覆盖；解析子进程尚未成功回传时不伪造partial。
- 新 `GET /tasks/:id/partial-result` 仅查询当前COLLECTED+FAILED/latest collection attempt/failed job/run/latest ready RAW id+digest的匹配快照；`/result`仍仅成功，Task详情另给 `analysis_identity` 供浏览器拒绝旧run。API严格结构校验后用PureJSON避免合法C++符号的HTML转义膨胀；标准envelope另预留1024字节，页面只作文本显示。共享Schema与显式导入fixture已冻结，HTTP契约实际61路由/75 Schema/18种类检查通过。
- API首次新增unit实际因DecodePartialResult尚未实现而编译失败，随后正负边界通过。真实PG前两轮失败分别为测试夹具相对路径和新attempt缺raw_object_key；两份red报告保留，修正fixture后repository/service/httpapi三套实际0skip、4.301/3.667/2.808秒通过。覆盖权限隐藏/read-share撤销、旧run/RAW/digest、新attempt、取消/删除、未终态、坏结构和20×159B含&<>的真实JSON HTTP响应。新rc4重新跑13包76单测（另19项无DBskip）、vet/gofmt、原生12字段入口及双架构编译；85文件前后SHA一致，amd64 `48c0c64c…e4a`、arm64 `5dd58495…e0b`，context `8e53cfe6…c658`。新govulncheck actual exit0、called/imported均0，保留module-only GO-2026-5932；并非所有依赖零通告。
- Web rc6实际131项单测、28项统一原生Chromium（49.25秒、0skip/0flaky、单worker/关闭trace）及7项下载辅助通过；新增独立有界partial请求、当前version/identity绑定、离页Abort及重试/重新分析/取消清理，失败不回退旧成功缓存。105源码/契约、14bundle在浏览器前后SHA一致，352553字节context `fd7621bb…1942`；npm官方生产依赖100项0漏洞。原focus截图保留；另补默认页顶/真实viewport截图，确认fullPage滚动导致的sticky捕获伪影不作为产品改动。此冻结阶段新API/Web尚未构建，后续实际构建见下一节；没有切生产。
- Analyzer新增partial构造/worker/事务回归：本机23项相关测试与22项真实PG均0skip，后者独立Windows17.11、单CPU/512MiB Job、峰71,729,152字节/22.88秒。新rc4冻结27运行文件，runtime context `9913fbb6…7e73`；121项Linux PG/S3、后阶段上传失败保留partial、规模与工具链正在独立远端验证，尚未宣绿。不得把23/22本机结果冒作121项Linux结果。
- Ops rc3实际因官方固定基镜像APT签名文件为`.pgp`而不是recipe断言的`.gpg`在构建阶段失败；保留原包/现场。只校正此断言的新rc4 `e8d95a64…c7d7`实际构建并通过42项PG/S3和五阶段SSE、UID10001/系统libpq/Python3.12.15/pure psycopg3.3.6探测，生产ID前后不变。新768MiB/no-swap/.5CPU采集器在低压后实际生成2835组件SBOM，原锁文件无需删除；真实Linux33项flock/SIGKILL/继承FD回归0skip通过。Grype同一10月17日漏洞库得到0Critical/61High/63Medium/11Low/68Negligible，exit2；旧17/156和新0/61各按原镜像保留，不把待核对High忽略或宣称门禁通过。
- Core r17仅对alloc加已验证PID的`-p`：先用新断言对旧r16库实跑红code8，再修后Release/Werror16/16绿2.65秒。实际alloc仍64.516秒超时137，helper/target无OOM；最小multi-uprobe正控仍有命中。Python内存仍未成功；GDB诊断停在目标函数调用检查阶段，具体阻塞点仍在定位，强制结束debugger后toy退出是失败证据，不作为业务取消/恢复通过。公网仍backend20261016.1/web20261016.3，无本轮生产切换。

### 2026-10-18 继续：当前交付状态与最终候选构建

- 回答用户进度：主要功能代码、前端改版及分模块回归已有实际证据，但没有完成指南全部能力验收，不能称为全量交付或已升级公网。采集剩余不止两处超时，还包括最终helper下Java CPU/alloc/lock/HPROF、原生GPERF CPU/heap及JEPROF的实采，以及恢复、RPC/N-N1与最终双镜像冻结。整套stage/监控与恢复、最终安全审计、HTTPS和生产发布均未完成；外部企业身份源/模型/定时异地备份与真实多节点/ARM环境另列边界，不默认为删减。
- API rc4实际串行构建为`sha256:94ad66ca5cd6a9a866c84c35916626b9cfe41e765c3cd648abb04970777abc47`，linux/amd64、USER drop、固定context `8e53cfe6…c658`；Web rc6实际构建为`sha256:8f3ead04322e631f44f8a7c010bd8437a3acb5b1a72560791a197dbdfbcb8053`，固定context `fd7621bb…1942`。旧镜像保留、生产非空运行容器ID前后不变；安全证明分别在`api-runtime-image-rc4-2026-10-18.json`与`web-runtime-image-rc6-2026-10-18.json`。候选镜像构建不代替stage验收或上线。
- Analyzer rc4镜像`b9e6b659…f136`已经构建，但原校验在构建后I/O压力门禁code24停止，尚未进入121业务回归；旧现场保留。本机新resume1包127文件/25279770字节的严格成员、两份GNU校验和、本机guard四项负控通过；在新私有目录复用精确image/context，不重复apt构建，保持门禁阈值，仅作每2秒、最多60秒的被动降压等待。尚未把准备状态写成Linux通过。
- Docker29.1.3实际同一工具底座inspect.Size为690937462字节、image ls约2.65GB，旧双倍inspect预算会低估解包空间。参考官方moby#53426，采集器改为固定Unix socket images/json接口，严格有界响应、精确ID和整数字节，只保留ID/大小；预算为content+disk+1GiB。Windows38项通过、5项Linux专属明确skip，Linux43/0skip入口已准备，尚未执行；既有33项Linux锁回归按旧源码保留。未知旧工作目录、失败扫描证据均未删除。
- 08:38 UTC公网healthz200/内部ready通过，外部响应约0.076秒、8个旧版生产容器；仅一次只读探测，不作SLA或新版验收。用户状态清单同步至DELIVERY_MATRIX与CAPABILITY_GAPS。
- 随后Core独占队列r18实际Release/Werror构建及16/16 CTest（3.65秒）、Linux6项仪表化回归通过，但两种真实采集仍失败。alloc的perf/raw对照各有172alloc+172free/344栈块，20秒后仍不退出，需TERM再KILL；无OOM且CPU约0.18/0.13秒，不能称为符号解析单点原因或恢复成功。memray的访问检查、断点/bt、dlopen/dlerror/符号加载均过，libthread_db启用，挂在SPAWN_CLIENT_BEGIN无END；5秒direct-call-timeout未使之返回，最终结束owned GDB后toy退出，恢复失败。safe摘要见`core/evidence/helper-advanced-r18s.json`与`helper-advanced-r18d.json`，原诊断私有保留。完成后实际只余8个旧版生产容器，队列归还。
- 新磁盘预算/锁版本在原生Linux128MiB/no-swap/.25CPU、capless/NNP/无网络下实际43/43、0skip通过，0.713052秒，证明`host-sbom-disk-size-linux-2026-10-18.json`；只运行文件系统/锁和fake Docker/HTTP回归，没有执行Syft/Docker。原33项历史证明未覆盖。随后根开始独占Analyzer resume1，不并行远端重任务。
- Analyzer resume1实际完成runtime/tools探测与121/121真实PG/S3，64.033秒、0skip/0fail、UID10001/Python3.12.15/purepq，绑定同一`b9e6b659…f136`镜像；原integration JSON SHA `0ed3f468f8adaedca44842fd3381e91011dcda235f461c8b2610c5ca9087558d`。后续`verify_historical_perf_inputs`失败是copy_perf.py固定ROOT仍指向rc4而非resume1，进入复制前断言拒绝；历史perf script/data的size/SHA/manifest实际只读核对正确，未回显RAW。perf/18格式/512MiB scale/compiler/BOLT尚未跑，不把121成功扩大为整套成功；准备新私有resume2修驱动并按精确SHA复用本轮同镜像已过报告，避免重复业务回归，原目录不变。
- 新采集器首次API扫描被构建后残余I/O压力门禁拒绝，无OOM/timeout；08:52:47 UTC实际只读宿主MemAvailable2363166720B、磁盘5804077056B、memory PSI avg10=0/0、IO avg10=0.16/0.14后，重试串行采集成功。API rc4 SBOM902组件/SHA `7c502ed7…b139`，Web rc6 1282/SHA `8f330234…1c3e`，平台Nginx rc2 1282/SHA `7e726c85…a142`；每次记录inspect packed和image-list整数字节、768MiB/noSwap/.5CPU真实limits，旧扫描/失败receipt保留。
- 同一官方Grype0.120.1与10月17日离线库实际扫描上述三个精确SBOM：均0Critical/0High、exit0；各3Medium，API另1Unknown，原始报告与summary在`docs/evidence/supplychain/security`。这是三个镜像的门禁通过，不证明Ops61High已处理或最终7镜像整套通过。随后开始独占Analyzer rc4 SBOM，无其它远端重任务。
- Analyzer rc4实际SBOM扫描完成：6368组件，SHA `90833691…ed33`，packed358721901B/image-list1301674349B；实际768MiB/noSwap/.5CPU、I/O限额报告保留。扫描中08:56:27 UTC宿主仍余2086289408B内存/4528496640B磁盘，memory PSI some/full avg10=0.74/0.56、IO41.43/35.02；没有叠加重任务，后续公网healthz一次200/0.032008秒。清理仅自己临时目录，未知旧目录/成品保留。
- 同一Grype库扫描该精确Analyzer SBOM得到57Critical/329High/269Medium/24Low/750Negligible/273Unknown，exit2，原报告SHA `ff031e33…dbb3`。Critical分组为linux-perf46、libcurl6、系统Python3.13五包同CVE；初步核对三个官方公告并记录在`analyzer-rc4-initial-triage.md`，不把源码包映射、离线工具前提或TLS客户端条件直接当全量不可达证明。API/Web/Nginx三项通过与Analyzer/Ops两项未通过分别列明。
- 已成功只读导出本轮同镜像已过runtime/tools/121阶段证明：`analyzer-security-rc4-completed-stages-resume1-2026-10-18.json`，SHA `4b6246ba…d44e`，明确PARTIAL_VALIDATION及剩余pending，不伪造整套通过。resume2新包128文件/25289495B校验后实际执行，在最后`case_bolt`退出1；原现场不改，代理仅限新私有目录读取安全计数/失败分类，未重新构建或重复已过121测试。此时没有生产迁移/切换。
- resume2实际只读核对：perf双输入通过script56920B/91sample、data16032B/93sample，报告SHA `d16023cd…95fe`；18格式均success（`e908838a…22e0`）；整cgroup512MiB/noSwap的10k/50k/100k为0.660/1.416/2.776秒、memory.peak158076928B、100001拒INPUT_LIMIT_EXCEEDED，scale SHA `e1203816…b268`，三者State Exit0/noOOM。固定Core工具底座编译fresh ELF28856B/SHA `4ac4d1cb…eac7`及compiler inventory成功，非业务性能/SLA。BOLT精确失败在benchmark_bolt_prebuilt.py:70的llvm-bolt instrument、子工具exit1；原驱动capture_output却未持久化CalledProcessError.stderr，不能推断缺库/ELF兼容原因，且未保存失败State不能冒称noOOM。实际SBOM已列两个libbolt_rt档案，故未盲加依赖；独立同ELF/镜像诊断包仅准备中。没有fdata、优化或三组等价通过结论。
# 2026-10-19 · 以 tx-drop 继续完整平台并吸收开发/视觉设计（进行中）

用户确认选择 tx-drop，吸收 drop-drop 的本地开发流程与视觉设计，要求最后部署到既有服务器，并允许清理旧应用与临时构建。完整指南、历史未完成项继续有效；本轮跟踪清单见 `docs/CONTINUATION_20261019.md`。

- 新增标准库本地启动器 `scripts/dev.py` / PowerShell 入口：独立 Compose project、独立 Secret 与卷、仅127.0.0.1端口，拒绝生产/外部卷及越界宿主挂载；7项隔离回归实际通过。Docker Desktop当前未启动，本机尚未跑完整容器链，后续使用服务器独立环境验证。
- Web采用暖白/墨色/橄榄绿、自托管Manrope/Bricolage字体；桌面导航可收起并持久化，移动抽屉保持完整文字；环境标识区分本地开发与自托管。133项前端单测通过，3项新浏览器检查通过（桌面持久化、移动菜单、修饰键及axe）；原有完整浏览器套件正在重跑。曾因测试标题末尾句点与品牌可访问名称不含可见文字而失败，均已修正，不把失败算通过。
- Core BPF新增退出前停止生产事件的持续门，避免异步stack输出不断进入导致收尾挂起；当前源码已在独立Linux容器Release/Werror编译并安装，严格CTest细节与真实高级采集仍在收集。生产容器尚未切换。
- 私钥附件未被远端接受；沿用本机既有授权SSH身份成功连接，不改变服务器认证配置。凭据不进入源码、镜像或公开证据。

# 2026-10-20 · Saber 接入、BOLT 与 malloc 真实补验

- 桌面 Python Saber 的真实 FTS5 模块及原2项测试冻结接入 `saber-python`。只复制明确文件，不读取环境配置、真实知识库或业务数据库；原项目不修改。新增受限 preset、SQL/tenant AST 冻结、6项冻结回归、384例严格类型/值比较、6组交替 CPU 对照和未优化负控；不是整个 Saber 服务上线。
- API10 / Web8 / Agent1 下，真实调查8、任务55→56、源码修复6的后台链路75.9秒完成。原2+冻结6回归通过、384例一致、6/6达门槛，组件查询 CPU 中位加速1.504786倍。首次页面错误回退显示旧CRM记录，原driver只检查heading而漏检；错误截图、录像和原结论保留并标记失败。Web9改为按明确调查/修复ID加载，缺少记录不回退其他ID；12项本地UI fixture及真实调查8→修复6桌面/手机只读呈现通过。这两阶段不能合称Web9从零75.9秒通过。
- 当前 Go 源码全包测试及vet通过；fresh隔离 PostgreSQL17 的110项（37顶层+73子项）0失败/0skip，30.25秒。实际模型fixture与协议/存储fixture明确区分。执行器4项边界回归通过，Web9生产构建及Prettier通过。证据位于 `docs/evidence/completion-20261020/`。
- 平台实跑新增取消后幂等重试任务50、串行DAG任务51→52、真实一分钟调度触发任务53，各自RAW大小/SHA通过；测试调度已停用。另有5个匿名访问面401；没有在生产冒称完整viewer角色矩阵通过。该阶段在Core1完成，与后续Core3采集证据分开。
- C++最终Core3按warnings-as-errors构建、18/18 CTest通过。第一构建因漏打包target-policy fixture而17/18失败，补齐真实fixture后通过，没有弱化测试。BOLT旧候选顺序错误由同一导入profile的完整IPC实际拒绝；修正后最终Helper完整IPC8/8通过，native实采另行验证。
- Agent/Helper `20261020.3`已部署到用户确认的新目标82.157.81.79；API10/Web9、SQL8、其余应用和数据容器ID保持，10个共享Agent命名空间的受控负载重新绑定。AppArmor专用策略及单个uprobe_events写挂载已安装；无SYS_ADMIN、无全局docker-default修改，数据卷和旧命名镜像保留。主机没有重启演练。
- BOLT真实任务57完成采样→fdata→分析→优化ELF，145样本/3节点、9制品逐一大小/SHA校验，固定自有ELF6组交替输出/退出码一致。CPU比0.9965，未观察到CPU收益；wall比不作业务加速结论。新实现验证原始perf MMAP2 PID/dev/inode及真实ELF SHA/build-id，只对本次native采样补缺，不覆盖冲突或关闭身份匹配。43/54失败保留。
- malloc最终候选真实隔离3/3通过：时长窗口96alloc+96free；9事件预算精确5alloc+4free；取消0.644秒返回TASK_CANCELED。实际用户栈、目标继续运行、RAW大小/SHA、短别名及全局探针集合恢复均通过。生产任务58进一步得到100alloc+100free、200条非空栈；PERF_CPU59和PYTHON_CPU60通过。
- 新策略下memray任务61实际失败，内核审计定位为目标docker-default拒收来自专用Helper的SIGSTOP。保留61失败，继续最小策略修复和完整回归；不把前三项成功扩大成全部通过。Saber最终Core3从零验收、最终备份恢复及当前部署收据将在完成后追加。BOLT no-LBR界面的分支零值语义也正在纠正。

## 2026-10-20 · 最终组合版本闭环与交付

- 最初target-only AppArmor修复虽让Helper可调试，却遗漏Agent docker-default发起侧的身份读取peer；实际只读预检暴露，静态parser/审查没有冒作功能通过。随后三角色显式矩阵在独立容器实际验证：Agent身份read可用、USR1拒绝、目标反向read拒绝、Helper STOP/CONT/QUIT和真实PTRACE_ATTACH/DETACH恢复，所有临时对象回收。只对Agent命名空间内10受控目标启用，原UID/cap/只读/资源限制不变。最终策略主机持久enforce，无SYS_ADMIN/privileged/unconfined或全局docker-default改动，API10/Web10/数据容器ID保持。
- 最终策略下生产任务62–69的8项及70–81的12项全部通过，合计16类Runner/20场景；RAW逐项大小/SHA正确，目标继续运行、无Tracer/残留采集子进程。memray62退化修复通过；malloc63为100/100和200非空栈。66采集实际成功、200fault/0bytes合法，保留checker误判并补验同任务，没有伪造新采样。最终malloc独立3项97/97、事件5/4、取消0.644秒及alias/probe集合回收通过。
- 最终BOLT81真实161样本，9制品校验和6组固定输入输出/退出码一致，CPU比0.9942无收益。Web10保留实测0，将无分支测量/未知mode显示为未测量，4个SSR fixture、构建、真实双视口/制品/RAW再验通过；原错误截图仍在。
- 最终Saber网页新调查9、任务82→83、源码修复7，88.0秒完成；原2+冻结6测试、384例严格输出和6组fresh CPU对照通过，中位1.3534倍、6/6达门槛。严格driver核对关联ID/项目/实际summary/scope/CPU及桌面/手机、无刷新或人工挑记录、无页面错误，录屏保存。原Saber两源文件SHA未变。结论仅受控FTS5查询基准，原业务源码未自动发布。
- 最终HTTPS ready200、26运行容器无OOM/unhealthy、7监控up、SQL8、Agent在线16能力、broker active。最终备份drop-20261020T080453Z-eee69b已完成真实隔离PG与S3恢复；数据卷、历史失败和命名镜像保留。没有把这些证据扩大为镜像统一安全门禁/长期HA/ARM/全Saber或生产P95通过。当前索引见 `docs/releases/20261020-saber-collectors.md`，源码/证据增量封包及SHA另存交付收据。

## 2026-10-20 · AgentScope 原生 ReAct 最终上线

- 官方AgentScope2.0.10 Agent/Toolkit/DeepSeekChatModel接管规划、诊断和源码阶段；Go保留domain、授权、attempt/lease、预算、schema和最终验收。新增私有双UDS、SO_PEERCRED、独立临时capability；capless UID10002、无DB/S3/Docker/代码executor挂载，512MiB/0.5CPU。
- 首次真实初始化因setgid被内核清除而拒绝启动，旧API健康保留；仅补临时init supplementary10003并实际验证2750。fresh PG发现schema required需JSON类型归一，修复后117项0skip通过。
- 首次网页调查10/修复8完整失败保留，不能把补丁test通过当作Agent报告成功。旧runtime数字重编码在真实SDK/Go双UDS新增负控中复现；新runtime保留Go accepted原始JSON token，指数/负零/长小数/24KiB严格结果回传通过。原Go严格验收不放宽，安全错误原因分离且不写异常文本。
- API12/runtime2已部署82.157.81.79，只重建API/新框架，其他25实例、数据卷、旧images保留。SQL8，27运行容器/7监控up。新调查11→86/87→29/30→verification14→repair9：75.6秒成功、双视口录像共76.4秒；4unique实际SDK run、13模型/17工具回调；fresh profile/test、原2+冻结6、384例、6/6、CPU1.558688倍，仅Saber受控FTS5基准，原源码未发布。
- 最终全Go测试/vet/Linux构建、fresh PG117、Linux SDK33（0skip）、双UID互通8、peer2、executor12、真实Docker取消约0.58秒/SDK取消约0.53秒、Compose5与28负控通过。未修改或重新采样全部C++ Runner，旧Core3/16Runner证据另列。
- runner RSS空闲107.0MiB、实跑后115.0MiB；94distributions，严格hash锁；memory.peak不可用，不宣称峰值或优于LangGraph。最终备份drop-20261020T110823Z-1595d4真实隔离PG/S3恢复579/579通过。见docs/releases/20261020-agentscope.md。

## 2026-10-20 · GitHub 完整项目发布准备

- 用户明确授权替换 mini-drop master 过时项目文件，保留旧提交历史，使用独立目录准备完整 tx-drop 快照和普通快进推送；原开发工作区不reset/clean。
- 新增官方AgentScope Linux CI及严格依赖锁安装、Compose版本固定和完整覆盖回归；手动release矩阵增加runtime第8镜像。CI不自动部署生产，不把配置文件存在当作安全门禁通过。
- OpenAPI生成源补齐实际Go报告agent_runtime有界只读schema及源码执行器取消说明；生成后76路由/91schema、任务契约、Proto兼容及负控通过，未修改生产Go运行时。
- CRM/Saber导出器要求显式--source/--output，输出须源码树外且为空、拒绝symlink祖先。4项临时合成fixture回归通过，不读取真实业务配置、不修改业务源码。Web137单测、构建/Prettier、启动器8及增量打包5项实际通过。
- 更新当前项目上下文、文档索引和公开范围；候选源码逐文件校验，排除密钥、登录态、现场RAW、缓存及录像Git blob；显式toy解析fixture保留。录像另作Release附件。推送、远端CI及服务器源码绑定以实际后续收据为准，本记录未预宣称通过。

## 2026-10-20 · GitHub 发布与服务器绑定完成

- 完整源码提交12fb28804f7d已普通快进推送master，父提交609ea8a保留。REST核对1548文件和tree0777cf40，旧apiserver/server/native不在当前tree。1819候选凭据扫描零命中，20个toy样本SHA/大小通过。
- 服务器逐文件校验同步1548源码，绑定上述提交；27容器ID/镜像前后相同、ready200、7监控up、executor active、生产.env保留，未重建或重启服务。成功/首次失败录像已上传非草稿Release，GitHub digest与本机SHA一致；收据在docs/evidence/github-publication-20261020。
- 首次远端CI的web、SDK、contracts通过；API在测试执行前因Actions健康命令单引号拆词失败，修正为双引号完整参数。maintenance因Compose2.40.3拒绝负数字符串而提前退出，实际负控改合法1GiB超512MiB预算，同时保留归一对象-1直接策略断言，未改权限fence。修后本机Compose5项/28真实overlay+1直接负控通过；新远端结果以Actions为准。

## 2026-10-20 · CI 官方存储依赖恢复

- 第二提交31cb6b3已发布并同步1551源码SHA，27生产容器/镜像保持，ready200/7监控up。服务器Linux Compose2.40.3也实际5/5通过。GitHub该提交API race/vet/govulncheck/gofmt、Web、SDK、契约及Core构建均通过。
- maintenance后续失败是旧MinIO官方容器仓库拒绝匿名拉取；Quay精确tag匿名token后manifest仍401，未换第三方镜像或绕过隔离测试。新增仅供依赖重建的Dockerfile：官方同版本GitHub release的amd64/arm64 binary、固定官方SHA、LICENSE及源版本标识，有界下载。CI先构建本地fixture tag再运行原PG/S3集成；新开发环境可同样预构建。未更新生产MinIO镜像、配置或卷。
- 生产storage-backend只读核对/usr/bin/minio SHA为eef6581f6509f43ece007a6f2eb4c5e3ce41498c8956e919a7ac7b4b170fa431，与官方amd64 release SHA相同；基础镜像和容器image ID不冒称相同。见docs/evidence/github-publication-20261020/minio-binary-proof.json和deploy/minio说明。最终CI结果仍以Actions为准。

## 2026-10-20 · 发布最终验收

- 功能源码797e9ec7e581已推送master并同步服务器1555文件SHA；本机已关联origin/master，切到codex/published-tx-drop，原main/52fb787历史保留。所有原工作文件字节保持，工作区干净，未checkout/reset/clean。
- GitHub精确push/master run39418233749于12:10 UTC核验7/7 job全成功：API race/vet/govulncheck/gofmt、Web单测/构建/审计/浏览器、Core严格构建、AgentScope Linux、契约、维护以及Analyzer原PG/S3隔离集成均通过；两处MinIO官方binary fixture实际构建成功。旧失败日志保留，旧31cb被取代的运行已取消以释放runner。
- 最终12:09:55 UTC线上ready200、27运行容器且无OOM/unhealthy、7监控up，API12/runtime2/Web10/Agent3及原存储镜像保持。公开CI和readiness收据补入docs/evidence/github-publication-20261020，并存Release附件；收尾提交仅更新文档/JSON，使用[skip ci]避免再次构建相同功能源码，不把文档提交冒作另一轮7项执行。
## 2026-10-21 · 八项 Agent 扩展（已部署，真实场景验收中）

- API8/Analyzer0.2.4实际Linux56 SDK和20解析通过；首次监控抓取未刷新触发回滚，核对29健康/7up后用同不可变镜像重发并等抓取6/7→7/7，未改卷。真实task102重分析run2仍失败，定位为Repository原SQL别名attempt与整数列同名，to_jsonb解析成整数使可信runner_metadata成为NULL；离线parser正例此前未覆盖该真实存储边界。改名collection_attempt，保留来源gate；Analyzer0.2.5实际隔离PG→JSON→child正负控和原20解析合计21通过/0skip。Web6仅更正旧预算文案，Prettier/build/既有7浏览器通过，API8/runtime4保持。Analyzer2/Web6待发布与原run3恢复/新现场闭环。

- API7同四输入/同24顺序的新命名空间debug复测完成24/24（off、experience各12/12），原API5的4/24及20失败完全保留。此批已见调试数据，不是独立盲测；人工语义标签与准确率、费用仍为空，不能据此证明经验提升。两个观察端点的29容器身份完全一致，不冒称连续审计。见evaluation24-api7-debug-retest。
- 新现场调查17/task102在诊断前暴露py-spy实际文件的整条空栈：1508字节、380总权重，其中1未知；原通用parser严格拒绝。新增仅可信当前attempt py-spy/PYTHON_CPU来源的适配，用明确未知占位保留全部权重、记录固定质量计数，通用collapsed、伪装导入、坏权重/控制符仍拒。Analyzer0.2.4的20相邻单测0skip与原真实文件离线replay通过；原文件/失败状态未改。Go新增质量来源/单位/计数及未知热点防线，232项广回归0skip和全APIvet通过。API8/Analyzer20261021.1候选待Linux与真实恢复/新闭环验收。

- API6/Web5已部署，29运行健康、7监控up、原27容器和全部卷/登记/broker保持；56项SDK中的原53项已在runtime4 Linux镜像通过，新增3项随后本机通过待新Linux执行。Web5公网两宽实际24事实、E锚点、键盘、axe和32静态/index SHA通过，旧窄屏失败截图单独保存。新调查16/task101/诊断66因四个独立报告错误逐个修正耗尽预算，原失败保留，未启动API6的24轮复测。
- API7候选汇总至多6项/3500UTF8字节的固定安全反馈，按facts/verification/hypotheses轮转，任一错误仍拒整份，不采纳部分报告；原SDK4096界限及模型/工具预算不变。176项广Go回归0skip、全APIvet、格式检查通过；SDK56项本机47通过/9平台skip。实际66缩减fixture证明部分修复仍拒、全部修正才通过；独立源码审查无阻塞。待API7部署、真实闭环和新调试复测。

- API5固定24轮已全部保留：4success/20failed，off与experience各2/12；失败均为模型报告校验后的预算耗尽，不是150秒超时。首4检查点定位mixed_signals缺少必要提示及事实来源缺失；本批语义准确率、人审覆盖与费用不补造。调查15/task95/诊断40首次真实v2成功，追加资源task96采集和分析成功、诊断41因不受证据支持的变更假设失败。另发现资源聚合未进入E1和TaskDetail窄屏表格被裁剪。原失败保留，未作成功重标。
- API6/Web5候选补资源有限聚合、真实样本与峰值摘要、固定路径/单位和多核CPU独立界限；补假设前提/引用及标量count错误的安全反馈，并明确只选择有关事实。128项Go定向回归0skip、全API vet、部署合并5项测试通过；Web长事实两宽组件边界、键盘与严格CSP等7项浏览器回归通过。新复测采用不同实验/幂等空间，明确为已见失败后的debug_retest，不冒充未见holdout。

- 诊断39实际错误缩减为明确导入fixture，补充报告专用白名单反馈：定位分区、索引和真实E12的预期/实际工具，禁止原始错误、任意路径和值回显。66项Go定向回归（20顶层、46子例）全部通过、0skip；全API go vet、gofmt、diff检查通过。SDK新增3项后Windows44通过/9Linux专用跳过，原5/新9模型预算及格式轮零工具执行保持。API20261021.5候选准备发布，尚未把新真实模型或24轮评测写为完成；运行时/Web保持20261021.4。

- 21.4已实际切换：功能修正提交4b54e4f，Linux镜像SDK50/50、0skip；API/runtime/Web4、SQL10、29健康、7监控up、readiness200。只换应用三容器，其余26身份/镜像/卷及broker PID、三项目冻结登记保持。公网1440/375真实HTTPS只读初验：10截图、0axe/溢出/页面错误/console、32静态文件含14font及index SHA一致，真实repair10两份下载SHA相同，长diff键盘滚动通过。Facts新真实报告验收单独等待，未用失败诊断冒充成功。
- 第三次相同437字问题调查14/task94/诊断39：真实四信号、一跳上游指标、拓扑、补证草案E12均完成，24个事实值/路径与工具一致；但模型将E12追加采集草案写成tool_plan的同类复测来源，Go按契约拒绝，最终9模型轮预算耗尽。失败报告未采纳、补证未stage/确认/执行，诊断39和真实流量记录保留。进一步补充程序生成的安全字段纠错反馈，维持原来源白名单与预算；未把该次写成完整闭环成功。
- 21.3公网/真实场景暴露的问题未隐藏：首个新增遥测目标任务92被默认AppArmor角色阻止读/proc/PID/exe，补充既有受控peer角色后仅重建两个新demo，其余27身份/镜像/卷保持，下一任务93实际采集分析成功。诊断38取得真实指标、SQL日志、Trace及登记拓扑后，第5轮provider输出在SDK工具预校验失败；被拒参数当时未持久化，不能事后猜具体字段或宣称final facts成功。
- 准备21.4修复：仅诊断阶段对通过硬名称/ID/严格JSON边界的Schema参数错误整批不执行并反馈已知字段，原预算内重选；失败原文/未知值不入检查点，不产生测量事实。SDK50项在Windows41通过/9 Linux专用skip，完整API真PG第三轮392通过/1真实模型skip，三独立SDK进程含本版源码；Go vet通过。复测报告列表复制出口补齐基线/候选业务信号授权，真实PG18项通过，nil配置关闭含信号记录并兼容旧无信号记录。
- Web21.4候选修复真实长diff键盘焦点及内联字体与严格CSP冲突：具名可聚焦region，Vite小字体输出同源woff2；不放宽CSP。focused6场景、实际严格CSP头、两宽方向键与build通过。AppArmor部署5顶层及29项负控通过；CI旧预算/格式问题修正。以上候选尚待新镜像Linux测试及切换，随后继续真实跨信号与24-trial验收。
- 功能670da84已推GitHub并实际上线82.157.81.79：API/runtime/Web20261021.3、SQL10、SDK2.0.10，Linux镜像SDK44/44、0skip通过；只替换应用三容器，其他24身份/镜像保持，新增2受控遥测服务。29运行健康、7监控up、HTTPS readiness200。迁移前PG备份、旧命名镜像/数据卷及CRM/Saber所有manifest和冻结源码哈希保留；broker升级后新增generic登记，未发布原业务源码。
- 新通用Python项目真实DeepSeek/AgentScope修复10在32.36秒完成：4模型/5工具，新增test_sku_whitespace.py真实基线断言失败/候选通过、原回归/类型和值/6组CPU与负控通过，6/6改善，中位8.2122×仅合成购物车函数。补丁SHA、测试名和来源版本见本轮live-generic-acceptance.json。首次验收client把projects误读为items的失败保留，尚未创建修复；修正client后原输入实际执行成功。
- GitHub首版maintenance因部署测试残留旧5/7预算断言失败；仅更新测试为9/14及最终格式轮规则，同时保留历史低预算兼容边界。实际部署配置测试5/5、SDK四项旧新预算/恢复回归通过；主运行源码不因此改变，远端CI重验另记，不提前称全绿。
- 本地页面验收收尾：Web153单测、完整相关浏览器33/33（0skip/0flaky）、新增页面最终12场景、1440/375两宽axe零违规与无文档横向溢出、合法patch下载SHA及篡改拒绝、生产构建/格式通过。已查看新截图；补证操作需明确勾选，成功根调查仍可停止未完成分支。事实出处沿实际复测关系读取对应诊断轨迹，拒绝把候选E3链接到基线或规划E3；无权限时显示纯文本。旧截图字节已恢复，后续截图输出改到当轮私有目录。
- 在原 AgentScope/Go 边界新增事实协议2、需审阅的动态补证分支、四类业务信号与登记一跳拓扑、通用Python manifest、个人/团队/项目方法库、持久化检查点与累计预算/重试熔断、重复评测及独立人工标签导入、生成回归测试与SHA补丁包。实现和使用边界见 [AGENT_EXPANSION](docs/AGENT_EXPANSION.md)。SQL010是兼容增量，旧协议1报告和CRM/Saber旧preset不改写。
- 整体真PG17、Go1.26.10、全10迁移实际151顶层+233子例=384通过、0失败；唯一skip为未启用的真实DeepSeek opt-in。覆盖三独立SDK进程恢复、v2精确数值、HTTP/CSRF/审批、版本与申请时钟、撤权/过期、追加分支取消幂等和锁序。首轮旧fixture/时钟断言失败保留，修正后完整重跑；未放宽协议。Go vet实际通过。
- Linux独立新镜像验证通用Python：真实broker创建无网络/无凭据容器，基线红/候选绿、冻结原回归、输出类型/值和6组正负CPU对照通过，中位8.6406×仅合成购物车函数。另新双服务镜像7项真实HTTP测试通过，本机两独立进程28请求验证慢SQL、CPU、错误和恢复。此阶段仅构建新命名镜像，没有切换现有27容器或修改既有业务登记快照；不算真实模型验收。
- 固定事实集6/6方法路由、52/52防线及Python24项通过；24-trial真实API重复试验在此本地验收阶段仅完成准备，未执行的准确率不填值。当时服务器仍为上一版20261021.2/SQL9；实际部署、真实DeepSeek和公网结果按发生时间追加于本节开头。
