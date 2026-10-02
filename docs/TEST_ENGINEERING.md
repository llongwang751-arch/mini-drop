# Mini-Drop 测试开发与质量工程

## 2026-10-02 分类边界与公共知识最新交付

线上已发布 `20261002T120042Z`，应用源码 `cfea6744fc2395d5392440b2404ee275ada0a69f`；Worker/Analyzer各222个源码SHA和19篇公共知识对应的文件SHA通过，13个容器健康、11个容器未替换，Web/native/API/办公服务状态及数据保留。新Chroma快照44个chunk READY，旧39个chunk仍READY且未改。

精确源码主CI14/14：Python 2163通过/16登记跳过，Web 330、真实PostgreSQL 14零跳过、Chromium固定数据8；Chroma专项通过。干净Linux核心栈87/87和10个实际阶段通过。

原明确纯描述问题的新真实会话返回NORMAL，来源SERVER_REQUEST_INTENT、诊断chat model_invocations=0；信息分支不创建PG检查点，不生成Task/Tool/Evidence/Report或健康事实。上游服务仍进行HYBRID查询embedding，不能宣称全链路AI调用为0。拒绝会话实际返回REFUSED，但原smoke因读取旧模型事件没有承诺的planner_kind字段报KeyError；独立只读核验确认输出和来源，原FAILED不改。缺测会话一次OpenAITimeoutError未产生输出，0重试且无新增采集任务。浏览器实际呈现2/3张规划卡、24次布局检查通过；这三次现场尝试独立于32题实评。NORMAL只描述当前请求范围，真实体检仍需观测。

32新题通过当前公共生产schema的JSON DTO适配器执行离线规划实评，未运行LangGraph Agent、未派发采集任务或注入故障；维护者出题、独立代码复算，非第三方盲测。32新题零重试真实chat：HTTP成功30/32、超时2，结构29/32，四态分类28/32，下一工具29/32。NORMAL与INSUFFICIENT_EVIDENCE各8/8、REFUSED7/8、INVESTIGATE5/8；四个未通过项为2超时、1拒绝误判NORMAL、1调查计划含不可执行判据，被DTO门禁拒绝。BM25 Recall@3 0.90625/MRR 0.93750，无答案误召回2/16；HYBRID Recall@3 0.84375/MRR 0.78125，无答案误召回1/16，实际后端{'BM25_ENTITY_CHROMA_RRF': 15, 'BM25_ENTITY_CHROMA_RRF_RERANK': 17}。原始错误/超时保留分母，服务器0诊断chat不计模型准确率；合成规划不是实际因果根因成绩。

公共请求意图规则不按题ID实现；双重否定、数字性能观测、明确症状、未完调查及真实动作请求不能走信息收束。流程描述和动作授权分别判断。Java线程CPU、同宿主块设备争用、同步写反证由public catalog与官方来源支持；未知主体、否定、运行时与候选主能力准入保持。旧模型题/真值/分数与失败CI不改，新语料不回填旧模型成绩。

本次只更换两个Python后端的源码覆盖层；预部署磁盘实际测量按1GiB保留空间、8倍两服务覆盖层/发布文件/压缩包、16MiB新索引上界及精确旧发布拷贝计算，不删除镜像或数据。评测只读发布来源接口使用manifest.json；新发布按旧兼容路径建立指向source-manifest-20260930.json的相对软链接，canonical原字节未改。容量、来源兼容与旧快照保留均有独立回执。

剩余改进优先级：供应商超时的稳定收束、模型拒绝边界和可执行计划表达、知识漏召回及无答案误召回。按本次公开失败类别改进通用规则后，使用新未曝光题验收；不能重试原题或改真值宣称新成绩。继续扩大有官方能力声明、能取得真实观测的知识覆盖。检索命中不能当Evidence，无答案不能当健康；当前项目已具备测开、后端和Agent面试演示链路，用户本人仍须读源码并彩排。

[当前固定SHA交付](CURRENT_DELIVERY.md)，[本次交付报告](../reports/architecture/planning-boundary-v3-20261002.md)。

Vitest 固定最多两个并发 worker，保留既有 5 秒超时与全部断言。首次本机默认并发和第二次 CI 的既有页面超时均保留；降低同时构建 jsdom 的资源争用，不把重跑失败报告改成成功。新评测在真实首轮之前固定跨解释器的 AST 结构摘要与 math.fsum 浮点均值；原始字节、非空语法节点、逐题判断仍严格校验。

## 2026-10-02 四态规划与无答案检索已发布

当前已部署 `20261002T103825Z`，应用源码 `73b4b18ad83553a5012a0025dfb78bb21ff8dc7f`。本次仅更换 Diagnosis Worker 和 Analyzer；Web 保留 a6a36260/20261002T095001Z 的运行镜像，完整 Web Git tree 与最终源码相等，58 个公网资源 SHA 一致；Worker/Analyzer 各 219 个源码文件逐一核对，13 服务健康，其余 11 个容器及紧邻部署前 Office/API/Native/CPP、环境和挂载保持。故障广场 21 场景均 inactive，展示工程判断 21/21、路径 6/21、反证 8 条；没有重跑故障或一小时实验。

精确源码 [主 CI 36996499159](https://github.com/llongwang751-arch/mini-drop/actions/runs/36996499159) 实际 14/14，Python 2019 通过/16 登记跳过，Web 330、真实 PostgreSQL 14 零跳过、Chromium 固定数据 8；Chroma 独立作业通过。[干净核心 CI 36996499257](https://github.com/llongwang751-arch/mini-drop/actions/runs/36996499257) 87 项门禁零跳过、10 阶段通过。原失败和原断言保持，专项数量不重复加到主套件。

规划输出已统一为 INVESTIGATE / NORMAL / INSUFFICIENT_EVIDENCE / REFUSED。合法非调查结果空假设、空工具，持久化 planner.output_recorded，不新建采集任务；NORMAL 只是输入或规划范围内未提出异常，真实体检仍必须使用采集后的健康判据。NO_RELEVANT_KNOWLEDGE 表示未找到相关知识，不表示业务正常。提示先选四态，只有仍有可验证异常与可执行动作的 INVESTIGATE 才必须扩展假设和数值证伪；目标、权限、预算、数值、采集失败与证据门禁不降低。检查点使用 diagnosis-agent-v7-four-state-prompts:<diagnosis_id>，scope-agent-v1:<diagnosis_id>；旧 v6 和原 ID 检查点保留，SQL 业务 ID 不改变。

新冻结 24 题首轮仍为 df0d3ef0 上的 21 响应/3 超时，结构、判断和下一工具均 21/24，零重试；首轮 BM25 Recall@3 0.96875、无答案误召回 5/8；HYBRID Recall@3 0.90625、误召回 1/8。a6a36260 的同题已曝光检索回归中，两路无答案误召回均 0/8，两路 Recall@3 均 0.875，BM25 MRR@3 0.90625、HYBRID MRR@3 0.875；主体准入减少误召回也损失相关内容覆盖。这不是新的盲测。最终 73b4b18a 的知识语料与三个检索实现文件与 a6a36260 Git tree 相同，由发布 manifest 证明来源等价，没有重复消耗 chat 或检索实评预算。

v7 提示部署后的同一 NORMAL 问题新会话补验 1 次，结果未通过；原始状态 `FAILED`，预期 NORMAL，实际 `INSUFFICIENT_EVIDENCE`，实际持久采集任务 0。本次返回合法 INSUFFICIENT_EVIDENCE 并直接 finish：只有进程身份、没有性能基线或时间窗口，且用户要求不采集。没有供应商超时、检索循环、非法计划或语义重试；合法停止合同已实证，但该问题的 NORMAL 标签未通过。当前浏览器实际呈现 2/3 张规划结果卡、3 个无答案通知，24 次四视口布局检查通过，TLS 证书校验开启，无 console/network 错误，本次没有证据下载。缺测与拒绝卡的产生源码是 df0d3ef0；新 NORMAL 会话的产生源码是 73b4b18a，逐例来源分开保存，不宣称三个状态都在最终版本重新实跑。

后续最有价值的是补公共知识能力与同义表达的覆盖，以新的未曝光问题重新冻结评估；供应商超时单列为可用性问题，不能把超时强制变成 NORMAL 或刷重试成功率。求职准备继续阅读真实源码、讲清测试判据和失败取舍，并完成本人五分钟彩排。

本次线上浏览器与旧 41 份下载分别计数；本次下载数为 0。详细事实由 [CURRENT_DELIVERY](CURRENT_DELIVERY.md) 的固定 SHA 合同生成，见 [本轮交付](../reports/architecture/planning-retrieval-v2-20261002.md)。

## 历史快照：2026-10-02 首次求职材料口径

当前定位是面向 Linux 多语言服务的性能与可靠性测试平台：体检、受控故障、采集分析、受约束 Agent 调查和工程回归形成可审计链路。展示成绩为 **工程诊断判断 21/21、异常路径定位 6/21、有效反证 8 类**；21 条结果分为 6 条定位、8 条反证、7 条有证据支持的候选判断。来源是 7 类新真机窗口与 14 条此前记录的工程重评，不能称为本轮重跑 21 类或 21 类因果根因全部定位。4 个工程缺陷修复案例另列，不能加到上述 21 类中。

已部署基线为源码 `de094fff754f2d8cb7139dd99e31c7a30d5fb754`、发布 `20261002T045234Z`。[CI 36966142203](https://github.com/llongwang751-arch/mini-drop/actions/runs/36966142203)实际完成 14/14 作业：Python 1682 项通过、16 项登记跳过；Web 317 项通过，真实 PostgreSQL 专项 14 项零跳过，Chromium 固定数据回归 8 项。16 项跳过仍保留在普通套件中，由 PostgreSQL/Chroma 独立作业实际覆盖，不能凑成“零跳过”或把专项重复加到主套件数量。真实浏览器另完成 41 份下载 SHA 核验和 48 次四视口布局检查，它与固定数据浏览器回归分别统计。

已完成的一小时双机实验实发 19,950 请求，成功率与固定质量检查均为 100%；120 个 30 秒窗口中 1 个延迟超限。用户接受它用于求职演示；原严格报告仍为 FAILED，200ms 门槛不改，不能写成所有窗口达标或生产容量证明。证据见[双机测量](DISTRIBUTED_LOAD.md)。

正常体检仅表示已检查范围正常；无法采到必要观测时为无法判断；REFUTED 是测量反驳指定异常假设，不等于整个业务正常。三种同步 I/O 在 tmpfs 中测得低延迟，不叫磁盘瓶颈；CPP I/O 后续 perf 预算拒绝仍保留，不能说七类完整预期工具链均通过。旧专项当前成绩和入口已移除，历史原始报告与 SHA 保持；工程判断、因果验证与同负载修复分别陈述。

干净 Linux 核心平台复刻已按 [独立 CI 36975451670](https://github.com/llongwang751-arch/mini-drop/actions/runs/36975451670) 的真实回执完成：从精确 Git 源码构建七个独立镜像，以全新项目、私有 PKI、数据库和对象卷验证迁移、8 项服务健康、Agent 新心跳、真实系统采集、S3 上传/下载 SHA、独立 Analyzer 与持久化身份绑定，最后只清理本次容器和网络。私有目录已删除，卷保留给临时 Runner 回收；未执行全局 prune。它验证核心平台与一个空闲 Python 目标，不包含 Office、Go/Java/C++ 工作负载部署、perf/BPF、外部模型、操作系统安装、离线安装或小时压测。首次失败及修复回执保留，详见[干净平台原始报告](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/report.json)与[复刻范围](REPLICATION.md)。

24 题冻结新题的模型规划/检索评估流程已 **COMPLETED，效果存在缺口**：22/24 次响应成功、2 次超时且零重试；完整结构 15/24、分类 15/24、合成窗口判断 11/24、下一工具符合冻结预期 13/24。BM25 recall@3 为 75.83%、MRR@3 为 0.80；无答案题仍召回 3/4。只有 22 次 usage 已知，成本未知。题目由项目维护者预先冻结，不是第三方出题；评估不执行真实工具、故障或平台 Agent 闭环，不能计为现场诊断/因果准确率。原失败、评分规则及 50 份来源/请求/响应 SHA 保持，clean Git 独立复算相同，见[新题评估与失败解释](HELDOUT_EVALUATION.md)。

本轮已验证的源码候选为 `34b74e7b15c81a883e8a7dc2698d8ba7a356dcbf`，[主 CI 36975451853](https://github.com/llongwang751-arch/mini-drop/actions/runs/36975451853) 实际 14/14 作业成功：Python 1827 通过/16 登记跳过、Web 317、真实 PostgreSQL 14 零跳过、Chromium 固定数据 8；跳过和专项仍分别统计，见[精确 CI 主报告](../reports/quality/interview-release-20261002/main-ci-34b74e7b/summary.json)。这些是候选源码与新环境证据，线上演示应用仍为 `de094fff` / `20261002T045234Z`，没有把候选提交冒称新生产部署。

剩余优先事项是本人彩排、理解源码并说明真实参与边界，以及在新的冻结版本中改进正常/缺测/拒绝结果的结构合同、无答案检索与保守返回策略；不能修改本轮题目、规则或原成绩来追求通过。收尾状态和已发布事实优先查[当前交付事实](CURRENT_DELIVERY.md)的源合同生成页；文档与讲稿准备不表示用户已掌握或完成彩排。原始回执见[当前成绩交付](../reports/architecture/engineering-score-only-20261002.md)、[归档清单](../reports/quality/engineering-score-only-20261002/manifest.json)与[项目上下文](PROJECT_CONTEXT.md)。下文带旧日期、版本和测试数的过程段落保留其当时状态，不能当作当前待办或当前成绩。

### 测开怎样说明测试结论

| 验证层 | 已有结果 | 能证明什么 |
|---|---|---|
| 契约、单元与状态机 | Python/Web/Go/Native 回归，生成物漂移检查和负向用例 | 当前实现对明确输入和规则的行为；不是全语言统一覆盖率 |
| 真实依赖 | PostgreSQL 14 项零跳过，Chroma 独立实际作业 | 数据库竞争/恢复与检索依赖的对应场景；普通套件 16 skip 本身不算通过 |
| 固定数据浏览器 | Chromium 8 项 | 页面行为与回归断言；不替代线上模型、性能或真实故障 |
| 实际部署浏览器 | 41 份证据下载 SHA、48 次布局检查 | 线上既有报告可读、下载与来源一致，四种宽度及导航通过；没有新注入或新建诊断 |
| 工程诊断实验 | 21 判断、6 路径、8 反证，7 新+14 此前 | 指定合同、目标和窗口下判断有效；不是自动因果成功率 |
| 工程缺陷修复 | 4 项，9 份证据下载 | 旧实现能失败、修复后回归符合独立预期；与 21 类性能统计分列 |

风险选择优先于测试数量：接口 200 但质量失败、缺字段与真实零、窗口拼接、计数重置、PID 复用、SHA 篡改、取消后迟到提交都要有拒绝用例。核心平台新环境复刻已通过，保留集评估已完成；模型效果仍有结构和无答案检索缺口，不能将流程完成写为全部效果通过。现有合成/固定数据测试仍保留其标签。

## 历史记录：2026-10-01 最终交付状态

Worker/Analyzer 20260930T144209Z/dc50c47，Web 20260930T164946Z/7403815（Axios1.20.0），Office observer-20260930T145902Z/f37f44e，API继续f9b143a。Office源码CI36733279485及测量源码52881ec的CI36742160900均成功13/13；后者Python1249/10登记跳过、PG8/0、Web228/44。单隧道独立新双机小时实验FAILED：3600秒持续，19,950次计划请求，60RPS P95 68.366ms，持续总体P95 101.374ms，120窗中1窗不达标；资源PASSED、独立原始重算VERIFIED、远端清理确认。实发19,950、未发0；持续成功率100.0000%，质量率100.0000%。

单隧道完整小时1/120窗失败、首次3578秒中断均原样保留；4路短测通过但新长测1102.2秒中断并有9次超时及6/36窗超限，不能算小时完成。默认恢复1路、4路显式选择；未改变200ms门槛或原失败成绩。

Office两组原+2000ms三段均通过，公开浏览器通过；一次明确准备请求不计入三段，首次模型调用仍可19秒。浏览器诊断3工具完成、1门禁拒绝，9份下载SHA通过，结果证据不足。历史专项原始记录保留；当前步骤、完整证据与边界见[性能闭环](../reports/architecture/performance-fix-20260930.md)及[项目上下文](PROJECT_CONTEXT.md)。

最新Web/兼容源码CI36746799170成功13/13，Python1249/10登记跳过、PG8/0、Web228/44；前一b05ec31的CI因生产Axios审计失败保留，升级未绕过门禁。最后仅更新Web，其余12容器不变；逐文件核对、13容器健康及真实报告/树页面通过。

以下带时间与版本的内容是此前过程记录，当前状态以上述最终复验为准。

## 历史记录：2026-09-30 当前版本的演示入口

当前发布`20260930T115734Z`/`f9b143a`，CI13/13成功；Python1217通过/10登记跳过、真实PG8通过零跳过、Web226通过。Go依赖升级及源码/二进制漏洞扫描、lint均已阻断，不能将全仓Trivy报告模式或未做的容器OS扫描说成全部安全通过。

先用[本次收尾报告](../reports/architecture/security-cancel-delivery-20260930.md)展示11项旧代码反例、真实PG竞争、权限拒绝和运行中取消。云端“停止诊断”按钮先确认，随后显示只读；已经形成的证据保留，故障注入仍需单独停止并恢复。

再打开[本次正常业务案例](https://120.24.187.205/ai-diagnosis?case=insight_2a97fc90606d4379821e4c9ddaf75ac6)：同一request_id关联问答、2条证据和报告，说明后续采样与原请求不是同一时间窗；报告“本次观测窗口未确认故障／证据不足”是正常演示结果。备用[报告与探索树短录屏](../reports/quality/security-cancel-20260930/browser-rehearsal/interview-report-tree.webm)只录制报告页面与树；完整业务请求、关联和产物校验以API原始证据为准。

下文旧“三段验收”页面仍可操作，但本次额外彩排的检索延迟差未达到+2000ms，失败原始数据已保留，现场不能承诺三段性能对比一定通过，更不能把故障撤销称为代码修复。原始诊断与小时延迟失败按当时条件解释，不作为当前工程成绩。

## 历史记录：2026-09-30 测试客户端的路由与时间线缺陷

[传输复盘](../reports/architecture/acceptance-transport-20260930.md)用代理/直连成对读取、历史API日志和任务创建时间区分服务阻塞与客户端超时。超时后的安全撤销会影响仍在运行的后台采样，因此故障后零样本不能当故障期诊断失败。显式直连保留TLS验证，不自动重试不确定POST；9项旧代码失败、96项相关回归通过，全量1201通过/8登记跳过。新冻结部署GC批次COMPLETED，取证链/恢复/清理/收束通过，观测VERIFIED但严格根因0/1。原失败批次和小时失败成绩保留。


## 历史记录：2026-09-30 最新交付

后端及Web新代码已部署并复核；该历史批次原始报告保留，撤销/清理21/21；一小时请求19,950全成功，但60RPS与9个持续窗口延迟超限，整体未通过。Web观测范围误标及HTTP409已修复，224项测试与CI13/13通过，云端同会话复验通过。详情和能力边界见[本次部署验收](../reports/architecture/deployment-validation-20260930.md)。下方带日期记录保留当时状态。

## 历史记录：2026-09-29—30 聚焦测开、后端与 Agent 开发的交付（代码及CI已验证，未部署）

用户已恢复有明确范围的工程收尾。本轮完成可执行Python/Go观察合同、不可执行候选幂等拒绝与正常收束、报告观测范围、最终验收拒绝弱观察冒充根因、浮点分桶和双机报告展示修复；不扩展诊断故障域，不重启已停止的21场景或小时实验。后续入口为[工程交付](ENGINEERING_DELIVERY.md)。

本机全量1170 passed / 7 expected skips，关键覆盖率无违约；快速门禁175 passed、零跳过。最初9项失败定位为两处旧测试替身缺status字段，补齐字段后恢复通过，终态保护未放松。原始失败、最终JUnit/覆盖率与前后负向复现已归档到 `reports/quality/focused-delivery-20260929/`。[CI 36595478139](https://github.com/llongwang751-arch/mini-drop/actions/runs/36595478139) **13/13作业通过**，测试代码 `9d0231dbf4cb6c4beca346230967b0b139be3c29`，测试merge `be98350e1568d71e0411f3eea7d13ecb20d75f2e`。Python1174 passed / 7 expected skips；PostgreSQL、Chroma、Go race、Chromium、业务重复验收及Linux独立热点均由专项实跑。Trivy/部分lint仍为report-only，通过不表示所有安全规则阻断发布。 首次CI `36594980233` 的CPU控制浮点误差失败保留；新增4项反例在旧版失败，按记录端点计算后修复，未钳制负值或放松阈值。后续文档/证据归档提交不与已测试源码混淆。

新报告 `verification.claim_scope=BOUNDED_OBSERVATION`、`causal_root_cause_verified=false`，明确Profile和OS分别窗口；即使VERIFIED与函数词汇命中，也不能计入新根因成绩。最终汇总器补丁已应用；未知/非法scope、非布尔因果标记同样拒绝，历史无scope记录保持兼容且不改写。新口径不与旧分数拼接。

云端仍保持 `20260928T142312Z` / `2400190`，本轮候选未部署。9/21中断批次保持STOPPED_BY_USER、严格0/9、原根因字段1/9，所有故障已停用；一小时原测量FAILED保留。学习课程正文属于用户改动，单独保留；仅由生成器维护文件索引。

## 历史记录：2026-09-28 部署前回归与独立测量入口

本轮最终本机 Python 质量门禁执行 **1000 passed / 7 skipped**，状态 `PASSED_WITH_SKIPS`，关键模块覆盖率无违约，测量期间源码未变化（`source_unchanged=true`）。本机报告为 [`output/quality/deployment-python-final-20260928/report.json`](../output/quality/deployment-python-final-20260928/report.json)。这是部署前回归结果，不代表远程 CI 已完成，也不代表云端严格根因或一小时稳定性已通过。真实部署、远端测试和复验成绩继续以 PROJECT_CONTEXT 的实际执行记录为准。

新增 `tests/test_process_cpu_control.py` 的 63 项回归覆盖真实 Analyzer 计数到谓词与 Evidence 门禁：数值阈值两侧及边界、中英文明确判据、复合/无关判据拒绝、缺失与非有限计数、分数 PID、PID 复用、采样时钟倒退、名义循环秒数与真实耗时差异、缺少可选应用快照、错误 SHA/Analyzer/目标/样本量，以及 CPU 采样窗口越出任务区间。来源负测先确认完整基线可通过，再破坏单项条件，避免本来无效的样本使测试假通过。CPU CONTROL 单独不能验证函数根因；相关域约束见 [AI 诊断](AI_DIAGNOSIS.md#2026-09-28-进程-cpu-独立对照本地验证待云端复验)。

新增 [双机负载与一小时资源观测](DISTRIBUTED_LOAD.md) 执行入口，分开发压端和一次性目标进程，校验两端身份、测量源码字节及远端资源记录。SSH 隧道与网络耗时计入端到端延迟；执行器及其测试通过只说明链路具备可验证的入口，小时级指标必须来自完成的实测。严格故障验收继续保留每轮独立 Campaign、失败和清理记录，不用重跑成功覆盖旧失败。


本文维护测试工程的执行入口与能力边界，评估日期为 2026-09-26。架构及云端状态仍以 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) 为准；业务测量标准维护在 [BUSINESS_ACCEPTANCE.md](BUSINESS_ACCEPTANCE.md)，真机故障标准维护在 [FAULT_PLAZA_ACCEPTANCE.md](FAULT_PLAZA_ACCEPTANCE.md)。

2026-09-27：已建立 [草稿 PR #1](https://github.com/llongwang751-arch/mini-drop/pull/1)，真实 PostgreSQL、Go race、Chromium、业务三轮重复与 Chroma 37 项零跳过已通过。第二轮 Python 暴露 Worker 首轮评估受机器启动时间影响，已修复并补确定性边界回归；[第三轮 CI](https://github.com/llongwang751-arch/mini-drop/actions/runs/36297143120) **11/11 作业全部通过**，Python **696 passed / 7 skipped**（依赖相关跳过由专项实际执行覆盖），PR head `bed58b0`。新增 `retrieval` profile（安装 `.[dev,retrieval]`，临时 Chroma + 本地测试 embedding），关键覆盖率文件缺失时拒绝通过。实际外部检索源的独立回归与 HTTP 复验见 [本轮复盘](../reports/architecture/test-engineering-ci-20260927.md)。下方“待 PR 触发”保留历史批次时间，最新状态以此处和项目上下文为准。

## 1. 项目评估与求职定位

作为个人工程项目，Mini-Drop 的系统深度较好：真实多语言服务、异步任务、采集、分析、鉴权、数据库状态和业务请求能串成完整链路。它的突出价值是失败之后仍能拿到可解释的证据，以及明确区分“执行成功”“证据有效”“根因验证”“业务恢复”。这已经具备性能测试和可靠性测试的应用场景。

2026-09-26评审时最影响测开展示的，是测试入口分散、重要环境依赖测试可能跳过，以及功能广度大于持续验证的深度；统一质量入口和真实依赖专项现已交付，仍需通过新环境/保留集验证推广范围。此前演示主要从 AI 调查开始，面试官容易只看到平台开发。建议保留已有平台，把求职叙述聚焦为：**面向多语言服务的性能与可靠性测试平台，使用自动化回归、受控故障和证据采集辅助缺陷定位。** AI 是辅助调查模块，测试是否通过由明确的判据决定。

这是一种基于现有实现的展示角度，不表示本项目已经成为成熟的通用压测工具、混沌工程系统或生产自动修复平台。

## 2. 已有优势与实证

| 能力 | 实际实现与证据 | 面试时讲清什么 |
| --- | --- | --- |
| 测试分层 | Python pytest、React/Vitest、Go testing、Agent CTest、生成合同检查 | 各层发现什么问题，Mock 验证不到什么 |
| 接口与协议 | `scripts/check_openapi_routes.py`、任务/状态/错误码生成器 | 变更源合同、生成多语言代码、检查漂移 |
| 并发正确性 | `test_drop_insight_report_effects_postgres.py`、`test_outbox_postgres.py`、Go `idempotency_postgres_test.go` | Event/Barrier 控制交错，检查租约接管、串行化与幂等冲突 |
| 证据与负向测试 | `test_drop_insight_policy_evidence.py`、`test_artifact_integrity.py` | 错目标、低质量、未知样本、损坏 hash 和采集失败必须拒绝 |
| 业务性能验收 | `run_business_acceptance.py` 与 `business_test_plan.json` | 相同负载、固定问题、正常/异常/变更后窗口；检查正确性和尾延迟 |
| 故障实验 | 21 个隔离白名单场景、有限时长、finally 清理 | 注入生效、撤销恢复、AI 根因与代码修复是四件不同的事 |
| 可审计失败 | 原始 Artifact、请求数据、版本、哈希、失败报告保留 | 不用重跑成功覆盖失败，不把缺失数字补成零 |

改动前清点为 Python 80 个测试文件、Web 43 个测试文件、Go API 9 个测试文件、原生 Agent 5 个测试源文件。函数定义数和参数化后的执行数不同，这些均不是覆盖率。

本次改动前重新执行 Python：**625 passed / 7 skipped**；`server + analyzer` 行覆盖率 **70.60%**，不是分支覆盖率，也不是整个四语言项目的覆盖率。7 个跳过项是 5 个 PostgreSQL 用例与 2 个可选 Chroma 用例。日志/JUnit/覆盖率位于 `output/sdet-review-20260926/` 及 `output/sdet-baseline-20260926.log`；后续改动结果另存，不覆盖基线。

改动后实测：Python **650 passed / 7 skipped**、Web **203 passed**、Go **73 个测试事件通过 / 1 skipped**（包含子测试），高风险 smoke **82 passed**；3 个 HTTP 场景符合各自预期。完整版本、报告位置与未执行范围见 [本轮验证记录](../reports/architecture/test-engineering-review-20260926.md)。

同日第三轮增量后（见 3.4–3.7）：Python **693 passed / 7 skipped**（累计新增 43 项测试），完整 `python` profile 门禁 `PASSED_WITH_SKIPS`，最新报告 `output/quality/qa-python-round3-20260926/`。

## 3. 本次落地的改进

### 3.1 风险驱动的统一质量入口

源合同是 `contracts/quality_plan.json`，执行器是 `scripts/run_quality_gate.py`。它复用现有测试，不再维护一份独立的成功数字。

首次安装沿用现有环境准备：Python 使用 `python -m pip install -e ".[dev]"`，Web 使用 `npm --prefix web ci`，Go 按 `apiserver/go.mod` 准备。执行器自身不安装依赖、不启动 Docker、不访问部署环境。

```powershell
# 默认执行 smoke，输出目录自动带 UTC 时间与随机 ID
python scripts/run_quality_gate.py

# 列出/校验风险合同，不运行测试
python scripts/run_quality_gate.py --list

# 全部 Python 测试与覆盖率记录
python scripts/run_quality_gate.py --profile python

# 本地跨语言回归
python scripts/run_quality_gate.py --profile local

# 真实本机 HTTP 三窗性能实验
python scripts/run_quality_gate.py --profile business
```

| 配置 | 执行内容 | 不在该配置内的验证 |
| --- | --- | --- |
| `smoke` | 6 项合同/计划检查；状态机、预算、证据、产物、Analyzer、业务验收与测试基础设施的高风险回归 | 全量功能、真实数据库与浏览器 |
| `python` | 合同检查；全部 pytest；行覆盖率 JSON | Go/Web/原生运行；缺少可选依赖时的真实检索 |
| `local` | `python` + React/Vitest + Go API + Go 故障目标编译 | Go race、原生 Linux、PostgreSQL 专项、浏览器 E2E、生产发布 |
| `business` | 固定测试计划下 3 条 HTTP 场景，每条正常/异常/变更后三个窗口 | 外部原办公助手实验、真实 LLM、生产流量、容量极限 |

每次生成新的 `output/quality/<run-id>/report.html` 和 `report.json`，包括 profile、源码 commit、脏工作树标志、源码摘要、计划 hash、开始/结束时间、每个套件状态、风险映射、失败用例、跳过原因、原始日志、JUnit 与文件 hash。Go 数量包括 testing 框架报告的父测试及子测试，不与 pytest 数字相加来制造“总覆盖率”。`go-demo` 目前仅验证编译，不能因退出码为零称其存在行为测试。

报告没有远程资源依赖，可以直接打开 HTML。风险区显示本次执行了哪些套件，不表示该风险已穷尽；没有执行的风险显示 `NOT_RUN`。报告摘要由 JSON 生成，不手工改 HTML 中的结果。

运行结束会再次核对源码与测试计划摘要；执行中发生源码变化会标为 `FAILED / SOURCE_CHANGED_DURING_RUN`，需在稳定工作树重跑。开发依赖也明确声明测试直接使用的 `jsonschema` 和 `PyYAML`，避免本机已有包掩盖干净 CI 的收集错误。

判定规则：

- `PASSED`：选定配置的所有执行项通过且没有跳过；不是整个平台发布许可。
- `PASSED_WITH_SKIPS`：实际用例通过，但存在合同明确允许的环境跳过，报告保留原因。它不能证明被跳过能力正确。
- `FAILED`：失败命令、超时、缺失/损坏报告、未知跳过、零测试或全部跳过。Go 报告出现重复终态或已启动用例没有终态，也拒绝通过。
- 中途报告保持 `RUNNING`；进程被外部强制终止时不会留下伪造的完成状态。

执行器不会自动重试失败；输出目录已存在就拒绝启动，防止覆盖证据。覆盖率目前是观测项，没有凭空设一个百分比作为硬门槛，也没有启用差异覆盖率或分支覆盖门槛。

### 3.2 PostgreSQL 并发进入独立 CI

原 CI 普通 pytest 没有配置数据库，所以关键数据库竞争用例会跳过。本次新增 PostgreSQL 16 服务容器，数据库名称固定为 `mini_drop_ci_test`；Python/Go fixture 继续在各自随机 schema 内创建和清理数据。

专项必须执行 5 个 Python 用例和 1 个 Go 真实幂等竞争用例。校验器检查指定用例、JUnit、Go 测试与包级 PASS、运行元数据，拒绝缺失、失败及 skip。普通 Go job 也增加 `-race -count=1`。无论成功失败，保存日志、JUnit、Go JSON、数据库日志和 commit/run 信息。

Native Agent 使用 `ctest --no-tests=error`；Control 当前没有注册测试，因此工作流明确标记为只构建，避免零测试被称作通过。Python CI 复用统一质量入口并上传报告。

历史批次 `e4ff878` 推送时 PostgreSQL 和 Linux race 尚未实跑；本机 Windows race 曾因 `0xc0000139` 退出。2026-09-27 已经通过 PR 实际执行：PostgreSQL 5+1 零跳过、Linux Go race 通过，见本页首段与本轮复盘。

### 3.3 两个真实验收缺陷

| 缺陷 | 最小失败输入 | 旧行为 | 修复后 |
| --- | --- | --- | --- |
| 缺失阶段被补零 | 30 请求中只有 1 条记录 retrieval=123ms | 其余 29 条被当作 0，阶段 P95=0 | P95=123ms，`stage_sample_counts.retrieval=1`；没有记录的阶段不造数据 |
| 低质量降级误通过 | 30/30 HTTP 成功且响应更快，但均为降级、质量检查全失败 | `DEGRADED_AVAILABLE` | `REJECTED`；降级也须达到质量门槛且不低于正常基线 |

修复位置为 `server/app/drop_insight/business_acceptance.py`，回归在 `tests/test_business_acceptance.py`。真零值、全部阶段缺失、质量低于基线、旧报告兼容及篡改拒绝都纳入测试。原有成功率、负载可比性、总请求延迟、trace 去重等判据继续保留。

新增阶段计数字段会影响旧实际 RAG 报告的精确比较，因此投影器只兼容该新增字段的缺失，继续核对旧指标与结论。已经用仓库历史实际 RAG 报告只读验证；没有回写旧 JSON，也不把有错误旧指标的报告“迁移”为新通过结果。前端保留原阶段数值接口，新样本数目前在机器报告中可见。

### 3.4 重复性能回归，与它暴露的一个场景缺陷

`scripts/run_business_acceptance.py` 新增 `--repeat N`（1..10）：每一轮都是独立 campaign（新服务、新窗口、新端口），逐轮落盘为 `campaign-rXX-cases/`，聚合报告写入 `repeat_summary`（`mini-drop.business-repeat-summary.v1`，标注 `ALL_RUNS_RECORDED; NOT_BEST_OF`）：逐场景 outcomes、`outcome_stable`，以及 baseline/fault/after 三个窗口的 P95 min/mean/max/stdev。`--require-outcomes` 语义为所有轮次都必须达标。质量门禁新增 `stability` profile（`business-stability` 套件，`--repeat 3`）；CI business 作业新增独立重复步骤（见 3.5 的“待 PR 实跑”边界）。

**真实发现**：首次 `--repeat 2` 时 RAG-03 两轮均为 `REJECTED`（预期 `DEGRADED_AVAILABLE`），且稳定复现。根因是场景设计缺陷：旧基线不含任何依赖耗时（P95≈30ms），恢复阈值=基线×1.3≈39ms，而降级路径固定多出 10ms 超时等待（P95≈43ms）——机器越快越必然 REJECTED，此前通过只是因为当时基线绝对值更慢。修复：RAG-03 基线锚定为 `Settings(dependency_latency_ms=10)`，与处置后的超时常数一致，比值随机器缩放；修复后两轮均 `DEGRADED_AVAILABLE`，after/baseline≈1.02。两次 REJECTED 的原始证据保留在 `output/qa-business-repeat-20260926/campaign.json`，未删除；`tests/test_business_repeat.py::test_degradation_scenario_baseline_shares_the_dependency_constant` 防止场景回退。测试计划中 RAG-03 的 requirement 已同步。

### 3.5 真实 Chromium 回归进门禁与 CI

`scripts/verify_frontend_workbench.mjs` 三处更新：`MINI_DROP_ACCEPTANCE_CHROME` 优先，否则自动探测 `ms-playwright` 下最新 `chromium-*`（兼容 chrome-win64 / chrome-win / chrome-linux 布局，CI Linux 与本机共用一条路径）；新增 `--output` 参数供门禁隔离证据目录；断言随 9 月 23 日体检改版更新——结论摘要改为“首屏可见”并记录实测位置（当前 672px），排查树为默认视图后改为断言真实父子树画布渲染、全屏弹窗画布占满视口。当前 7 项检查、0 个浏览器异常（`output/qa-browser-20260926/browser8/result.json`）。

`contracts/quality_plan.json` 新增 `web-browser` 套件（风险映射 ui-regression）与 `browser` profile，并把 `web-browser` 加入 `local`（需要先 `npm --prefix web run build`）；CI 新增独立 `web-browser` 作业（npm ci → 构建 → playwright chromium → 脚本）。与 3.2 相同的边界：**工作流已推送、本地已全绿，但 CI 尚未通过 PR 实跑，不能称 CI 已通过。**

### 3.6 关键模块分支覆盖率门槛（先基线，后约束）

`python-all` 开启 `--cov-branch`，全库口径从纯行覆盖 70.60% 变为：语句 70.61%、分支 55.65%、行+分支合并 66.84%——引用时必须写清口径，不能混用。`critical_coverage` 按模块设下限（2026-09-26 本地基线向下取整，约束新增未测分支而非追全库 100%）：

| 模块 | 基线（行+分支） | 下限 |
| --- | --- | --- |
| `business_acceptance.py` | 94.69% | 94 |
| `event_store.py` | 95.70% | 95 |
| `hypothesis_predicate.py` | 85.39% | 85 |
| `report_conclusion.py` | 69.40% | 69 |
| `fix_verification.py` | 13.64% | 13 |

低于下限、或模块从未被执行（`observed=false` 记 0）都会判 `FAILED`。门禁证据：`output/quality/qa-python-final-20260926/report.json` 的 `critical_coverage`（GATED，0 breaches）。

### 3.7 补测 fix_verification、共享 fixture 与 property-based 测试

- **`fix_verification.py`：13.64% → 100%（分支含行），下限钉到 100。** `tests/test_fix_verification.py` 用 18 项行为测试覆盖修复复测闭环：核心不变量是“修复前后任一任务缺 TopN 数据只能 `REJECTED`，不能把数据缺失当作热点消失”；另有相对下降阈值边界（恰好 1−0.3 判 VERIFIED、略高判 REJECTED）、非 dict 行与字符串占比的容忍、请求窗口重叠的字符串/naive/aware 时间语义，以及 sqlite 引擎上 `verify_diagnosis_fix` 持久化、缺失数据留痕、新序优先与 limit。`quality_plan.json` 中该模块下限从 13 上调到 100：此后新增分支必须带测试才能进门禁。
- **`tests/conftest.py` 收拢共享 fixture。** `postgres_sessions` 与 `NOW` 从 `test_drop_insight_report_effects_postgres.py` 移入 conftest；原模块保留 re-export 作为历史导入点，`test_outbox_postgres.py` 改从 conftest 导入 `NOW`。本地验证 skip 行为与收集数不变；PG 用例的真实执行仍只能由 CI 作业证明。
- **hypothesis 进入 dev 依赖，property-based 测试落地。** `tests/test_business_acceptance_properties.py` 用 6 个 property（合计约 500+ 随机样例）锁定业务验收的统计与判定三角形：P50/P95 必须是观测样本且符合 nearest-rank 重算（oracle 独立于实现）、阶段统计恰好覆盖观测样本（缺失不补、真零保留）、after 窗口任意失败→`REJECTED`、任意降级→`DEGRADED_AVAILABLE`、全健康→`IMPROVEMENT_VERIFIED`、跨窗口复用 trace 或任一负载字段漂移→`INCOMPARABLE`。
- 全量 **693 passed / 7 skipped**；门禁 `output/quality/qa-python-round3-20260926/` 为 `PASSED_WITH_SKIPS`、`critical_coverage` 0 breaches（fix_verification 100.0% 达标）。

## 4. 与开源工具对比

以下是官方机制对照，未在同机、同负载下比较性能，不能据此给出领先排名。

| 参照项目 | 可借鉴的能力 | Mini-Drop 的现状 | 实际取舍 |
| --- | --- | --- | --- |
| Grafana k6 | 开放/封闭负载模型、场景与阈值，阈值失败返回非零退出码 | 已有固定到达率、客户端排队计入延迟、可比性及质量门禁；当前只有小型固定 HTTP 案例 | 需要通用协议与阶梯负载时接 k6，不重写完整压测器 |
| Chaos Mesh | 串并行/条件故障工作流、连续状态检查与自动终止 | 白名单场景、限制时长、finally 清理已存在；通用业务探针、组合实验与运行中止损不足 | 先完善当前 Compose 故障实验协议；无需为对标立即迁 K8s |
| Grafana Pyroscope | 连续 Profile 查询、并排对照和差分火焰图 | 已有窗口化连续采集和多语言产物；跨版本比较、统一历史检索不如成熟工具完整 | 为测试 run、工作负载和 commit 关联 Profile；热点占比不能替代业务性能验证 |

官方来源：[k6 阈值](https://grafana.com/docs/k6/latest/using-k6/thresholds/)、[k6 负载模型](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)、[Chaos Mesh 工作流](https://chaos-mesh.org/docs/create-chaos-mesh-workflow/)、[状态检查](https://chaos-mesh.org/docs/status-check-in-workflow/)、[Pyroscope Profile 对比](https://grafana.com/docs/pyroscope/latest/view-and-analyze-profile-data/pyroscope-ui/)。

本项目有辨识度的部分是测试失败后的证据链和受约束诊断。成熟开源项目在通用负载、实验编排、查询体验和生态上更完整，不适合声称本项目已达到这些工具的生产成熟度。

## 5. 本轮完成情况与后续优化

| 优先级 | 问题与动作 | 完成标准 |
| --- | --- | --- |
| 已实跑 | PostgreSQL 并发与 Linux race | 原2026-09-27批次PG 5+1；最新真实PG专项14项零跳过及Go race，按各自CI回执分列 |
| 已实跑 | web-browser 与 business 重复步骤 | 真实 Chromium 与 3 次重复实验已在 Linux CI 通过，保留 artifact |
| 已实跑 | Chroma 集成作业 | 安装 retrieval，37 项通过且零跳过，使用隔离持久化索引与本地测试 embedding |
| 已复验 | RAG JSON 解码历史修复 | 新增独立排序/隔离/新鲜度回归、cProfile 和同负载 HTTP 对照；非生产/真实模型指标 |
| 范围保留 / P1 | 因果与工程判断分离 | 工程21/21、6路径、8反证已验收；独立因果干预与同负载代码修复按域另验，不把有界观测改成因果，不作为通用诊断平台承诺 |
| P2 | 拆分仍约 8,500 行的 `drop_insight/service.py` | 以状态、取证、报告等真实边界拆分，保持行为回归，不把“拆文件”本身当收益 |
| P2 | 扩大关键模块清单并逐步上调下限 | 新增预算、幂等相关模块入 `critical_coverage`（report_conclusion 已在 9 月 28 日提升到 100%，后续选取未纳入的预算/幂等模块）；随债务清偿上调既有下限 |

重复性能回归（多次独立运行、记录 P95 波动）已在 3.4 完成；`fix_verification.py` 补测、conftest 收拢与 hypothesis property-based 测试已在 3.7 完成。

还未解决的边界：本机已增加 30 分钟持续请求与资源增长筛查，小时级双机soak已完成，最新固定单路窗口1/120超限且用户接受演示，原严格FAILED保留（更早批次另有5/120超限），完整泄漏诊断与生产容量极限仍未验证；受限资源账号目前采取明确拒绝，并未实现完整多租户资源过滤；全仓Trivy仍为report-only，Go源码/二进制漏洞检查与golangci-lint已于2026-09-30阻断，不能称全部安全规则阻断发布。多语言架构便于展示跨栈能力，也增加依赖和维护成本，下一步应优先闭环测试，不再扩展技术栈。

## 6. 测开面试演示路线

建议选择能够自己逐行解释的一条主线：

1. **讲风险**：例如降级接口 200 但答案质量下降，为什么只断言状态码不够。
2. **讲用例设计**：正常、异常、边界、稀疏数据、质量低于基线；指出旧代码会怎样误判。
3. **运行 `smoke` 并打开报告**：展示失败/跳过规则、风险映射、源版本与原始日志，不只展示绿色通过数。
4. **运行 `business`**：解释同负载三窗、固定问题与引用校验；第三案例持续依赖慢，回退只能判降级。
5. **展示一次取证链**：已有服务请求→进程→Profile/指标→报告；说明 AI 提议不构成测试真值。
6. **讲质量落地**：用例如何进入 CI，真实数据库竞争与 SQLite/Mock 有什么差别，为什么允许的 skip 仍不代表执行成功。
7. **讲重复运行暴露的场景缺陷**：`--repeat 2` 让 RAG-03 在快机器上稳定翻成 REJECTED（阈值 1.3×基线，降级路径固定多 10ms），修复是把基线锚定到同一依赖常数——用 `output/qa-business-repeat-20260926/` 里失败与修复后的两份 campaign 证据讲“性能验收的阈值必须随机器缩放”。

可使用的项目介绍：

> 我围绕多语言服务开发了性能与可靠性验证能力，设计接口合同、任务状态、并发幂等和证据门禁回归，并通过受控故障和同负载 HTTP 实验验证业务变化。测试失败后关联进程采样辅助排查，判定逻辑独立于 AI。我还修复了缺失阶段被补零、低质量降级被误通过的问题，并为质量入口加入防止跳过和缺失报告假绿的检查。

这些描述只有在你确实理解并能解释对应实现后才适合用于个人贡献陈述。可引用真实执行结果；不要写“根因准确率95.2%”“21/21自动修复”“节省80%排障时间”或“生产高可用”等未证实成绩。旧专项原始验收保持归档，不作为当前工程成绩；当前按21/6/8解释，故障撤销也不是修复代码。

## 7. 同一进程的阶梯负载与持续运行

新增 `endurance` profile：复用独立 HTTP 样例进程执行短阶梯、恢复、持续请求，接入 Linux CI；本地默认脚本另外运行 600 秒持续阶段。27 项回归覆盖未发送请求分母、超时尾延迟、引用错误、时间/样本边界、连续容量档位、窗口退化、报告失败留痕和有界调度。方法、命令、计数口径及限制见 [业务验收](BUSINESS_ACCEPTANCE.md#阶梯负载与持续运行2026-09-27)。[本轮实测](../reports/architecture/load-endurance-20260927.md)已完成：12/12 CI 作业通过，Python 723 passed / 7 skipped；600 秒持续阶段 3000 请求成功/引用检查均 100%，P95 39.85ms、20 窗均达标。40 请求每秒达标、80 档延迟超标，降载恢复通过；总共 5250 条原始请求。仍不把本机 10 分钟样例当成生产小时级长稳或泄漏验收。

## 8. 目标资源、独立对照与报告复核

持续验收现在观察自己创建的目标进程，而非压测器自身内存；资源样本校验 PID/create_time，缺失、采样断档和增长超预算均不能自动通过。`verify_load_report.py` 从原始请求、资源 JSONL 重算阶段/窗口判定；`render_load_report.py` 只渲染通过完整性复核的证据，自包含图表保留 INVALID、未发出槽位及缺失值。

隔离 Linux `hotspot-controls` 实际执行 Python/Go demo，使用 OS CPU 累计时间和函数采样做正常/故障/撤销对照；三窗同进程，快照与 pprof 留存。它为下一步云端独立取证提供可运行的方法，但没有提升历史 AI 根因成绩，也不声称完成同负载代码修复。资源实测和最新作业结果以 PROJECT_CONTEXT 顶部与原始报告为准。

项目 README 已提供本机短回归与图表命令，面试讲解顺序见 [演示手册](INTERVIEW_DEMO_GUIDE.md#测开岗位先演示这一条2026-09-27)。


本轮实际结果与原始证据已归档到 [资源与热点对照实测](../reports/architecture/resource-controls-20260927.md)：30 分钟持续及资源筛查通过，但原整轮因 80 RPS 发压饱和仍 INVALID；独立容量复测最高已测通过 60 RPS、70 RPS 延迟超标。代码 `3b6809c` 的远程 CI 13/13 作业通过，Python 780 passed / 7 skipped。Python/Go 对照仅为 CONTROL_VERIFIED，不回写云端历史专项原始报告，也不算新增因果验证。


## 9. 结论可信度负向测试（2026-09-28）

本轮从剩余 69.4% 的 report_conclusion 覆盖缺口切入，先复现 Java 替换函数却沿用包装帧占比、非法数值导致崩溃/误判、缺失 GC/数据库计数生成反证等问题，保存修复前 24 项失败。新增语义与边界测试验证真实零值和缺失必须区分、GC 活动与分配因果关系分开、异常百分比不能支持/反驳、数据库延迟字段兼容与缺失、反证不能被 VERIFIED 标题覆盖。

report_conclusion 的行+分支覆盖达到 100%，quality_plan 下限由 69 上调为 100；既有 100% fix_verification 门槛保持。覆盖率证明这些路径执行过，正确性仍由独立预期断言与修复前失败证据支撑。最后执行数量与 CI 以 PROJECT_CONTEXT 顶部为准。

最终 [CI 与缺陷证据](../reports/architecture/conclusion-integrity-20260928.md)：代码 `6fb7124` 的 13/13 作业通过，Python 865 passed / 7 skipped，report_conclusion 与 fix_verification 行+分支均 100%。
