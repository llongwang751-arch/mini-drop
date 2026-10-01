# 测试开发、后端与 Agent 开发：工程交付入口

## 2026-10-01 当前演示补齐

入口仍为 https://120.24.187.205/ai-diagnosis，具体线上提交及发布状态先看PROJECT_CONTEXT顶部。新增21类工程索引为判断14/21、路径4/21、反证5条（18类新实验+3类先前记录），7类不足公开保留，旧因果0/21单列。不要把工程判断、具体定位与因果根因混为同一个成功率。

面试用5分钟完成一条主线：先展示正常、异常、无法判断三个体检结果，再看真实采样产物和目标寿命，最后展示同负载修复记录。正常仅说明本次已检查范围；异常可以进入深入调查，采集不足可以重新采集，后续新会话须重绑当前进程，不复用旧PID权限。管理服务后续按钮已有实浏览器记录，本轮隔离Go三态的后续恢复/重采是独立新会话。

可直接打开[正常体检](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2:insight_8d7d9d15496e42f18be26711329658b3)、[CPU异常体检](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2:insight_7785e3d08c3942e7b82f654c7eaf5e83)、[采集不足](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2:insight_9655a7bce3e74b58af7ff18e7695be68)、[真实业务采样报告](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2:insight_1c11b8b7314d4108be44811fd8ded2ca)。这是历史现场证据，当前状态要再次检查。

业务讲稿：固定问题与96候选的HTTP知识检索服务，排序计算中采到difflib.find_longest_match；人工修复缓存与连接处理，三窗各360请求，同一进程/输入/4RPS，上限8并发，P95从68.38ms降至2.70ms，质量100%。6份采样下载SHA一致。它是独立真实SQLite业务样例，不是Office/LLM效果评测，也不是AI自动因果根因。面试官可按[原始修复记录](../reports/quality/interview-completion-20261001/business-fix-r3/validated-comparison.json)重算。

测开重点讲测试预言、三窗对照、原始证据校验、负向篡改用例与PG进程中断；后端重点讲行锁、原子事件与状态、取消竞争、trace/span在可信重绑时保留；Agent重点讲执行合同、预算/审批、取证及反证，不能用采样占比替代CPU百分比。14项真实PG通过零跳过，其他测试与最终CI见补齐交付。不要在现场重跑一小时或承诺21类全部定位。

复核命令：`python scripts/build_engineering_diagnosis.py --check`；`python scripts/evaluate_same_load_business_fix.py reports/quality/interview-completion-20261001/business-fix-r3`；`python -m pytest tests/test_same_load_business_fix.py tests/test_diagnosis_trace_correlation.py tests/test_engineering_java_profiles.py -q`。真实PG需要专用测试库及RUN_POSTGRES_TESTS=1，CI负责实际运行；不要把本机跳过算通过。样例复现源为scripts/same_load_rag_fix_fixture.py及原始记录中的source目录，用隔离一次性进程，stdin依次发送baseline/before/after，关闭stdin退出；平台取证仍需可信Agent绑定和批准。


## 2026-10-01 最终演示版本复核

并发案例源码入口已指向实际修改的skill_evolution.py；最终Web为 `20260930T181400Z` / `5bbeb4c`，55份文件SHA通过，其余12容器保持、13容器健康。精确源码CI [36757023388](https://github.com/llongwang751-arch/mini-drop/actions/runs/36757023388)成功13/13。再次实浏览器核验4项案例、9份下载SHA、4种宽度和旧21类停止入口全部通过，旧根因0/21与停用状态保持。修正仅涉及源码链接元数据，不修改测试、阈值或原始成绩。

默认演示是4项工程缺陷修复回归，AI自动根因NOT_EVALUATED。Python1260通过/10登记跳过、Web240通过、真实PG8通过零跳过；用户接受一小时1/120窗超限用于面试，原严格FAILED保留，不再重跑。详见[工程案例](ENGINEERING_CASES.md)。

## 2026-10-01 工程缺陷演示已部署

默认演示已改为4个有旧代码失败和修复后回归证据的工程缺陷，旧21类保留历史入口及停止控制。新Web已部署 `20260930T180319Z` / `dbdf265`，55份dist文件SHA一致，其余12容器未重建；Worker/Analyzer各184文件、API二进制和Office3集成模块保持并复核，13容器及API3依赖健康，21故障inactive、原根因0/21。

本地Python1260通过/10登记跳过，Web240通过；CI [36755582449](https://github.com/llongwang751-arch/mini-drop/actions/runs/36755582449)成功13/13，真实PG8通过零跳过，测试merge2ace4b4。生产依赖审计0漏洞；实浏览器4种宽度通过、9份下载SHA一致、无JS/HTTP错误，历史停止入口可用。生成合同与缺文件/篡改/跳过拒绝回归均通过。

新案例是工程缺陷修复回归，AI自动根因NOT_EVALUATED，不能算旧21类通过。用户接受一小时1/120窗超限用于面试，停止重复发压；原严格FAILED及200ms门槛保留。当前演示、复现及原始证据见[工程缺陷主线](ENGINEERING_CASES.md)。

## 2026-10-01 新演示主线

默认展示[4个工程缺陷](ENGINEERING_CASES.md)：计时、缓存、PG并发与通知生命周期。21类转为历史实验，不继续作为面试版本阻塞条件，模型自动根因成绩不重写。用户接受小时1/120窗超限用于面试，停止重复发压，原严格报告保留。发布状态以PROJECT_CONTEXT为准。

## 2026-10-01 最终交付状态

Worker/Analyzer 20260930T144209Z/dc50c47，Web 20260930T164946Z/7403815（Axios1.20.0），Office observer-20260930T145902Z/f37f44e，API继续f9b143a。Office源码CI36733279485及测量源码52881ec的CI36742160900均成功13/13；后者Python1249/10登记跳过、PG8/0、Web228/44。单隧道独立新双机小时实验FAILED：3600秒持续，19,950次计划请求，60RPS P95 68.366ms，持续总体P95 101.374ms，120窗中1窗不达标；资源PASSED、独立原始重算VERIFIED、远端清理确认。实发19,950、未发0；持续成功率100.0000%，质量率100.0000%。

单隧道完整小时1/120窗失败、首次3578秒中断均原样保留；4路短测通过但新长测1102.2秒中断并有9次超时及6/36窗超限，不能算小时完成。默认恢复1路、4路显式选择；未改变200ms门槛或原失败成绩。

Office两组原+2000ms三段均通过，公开浏览器通过；一次明确准备请求不计入三段，首次模型调用仍可19秒。浏览器诊断3工具完成、1门禁拒绝，9份下载SHA通过，结果证据不足。历史21因果0/21保持；当前步骤、完整证据与边界见[性能闭环](../reports/architecture/performance-fix-20260930.md)及[项目上下文](PROJECT_CONTEXT.md)。

最新Web/兼容源码CI36746799170成功13/13，Python1249/10登记跳过、PG8/0、Web228/44；前一b05ec31的CI因生产Axios审计失败保留，升级未绕过门禁。最后仅更新Web，其余12容器不变；逐文件核对、13容器健康及真实报告/树页面通过。

以下带时间与版本的内容是此前过程记录，当前状态以上述最终复验为准。

## 2026-09-30 当前版本的演示入口

当前发布`20260930T115734Z`/`f9b143a`，CI13/13成功；Python1217通过/10登记跳过、真实PG8通过零跳过、Web226通过。Go依赖升级及源码/二进制漏洞扫描、lint均已阻断，不能将全仓Trivy报告模式或未做的容器OS扫描说成全部安全通过。

先用[本次收尾报告](../reports/architecture/security-cancel-delivery-20260930.md)展示11项旧代码反例、真实PG竞争、权限拒绝和运行中取消。云端“停止诊断”按钮先确认，随后显示只读；已经形成的证据保留，故障注入仍需单独停止并恢复。

再打开[本次正常业务案例](https://120.24.187.205/ai-diagnosis?case=insight_2a97fc90606d4379821e4c9ddaf75ac6)：同一request_id关联问答、2条证据和报告，说明后续采样与原请求不是同一时间窗；报告“本次观测窗口未确认故障／证据不足”是正常演示结果。备用[报告与探索树短录屏](../reports/quality/security-cancel-20260930/browser-rehearsal/interview-report-tree.webm)只录制报告页面与树；完整业务请求、关联和产物校验以API原始证据为准。

下文旧“三段验收”页面仍可操作，但本次额外彩排的检索延迟差未达到+2000ms，失败原始数据已保留，现场不能承诺三段性能对比一定通过，更不能把故障撤销称为代码修复。完整21场景根因0/21与小时延迟失败也照实解释。

## 新增验收客户端问题闭环

[本轮传输复盘](../reports/architecture/acceptance-transport-20260930.md)可演示测试基础设施的失败如何影响后台实验：系统代理读取超时→客户端安全撤销→后台采到正常期零GC/无样本。对同接口比较代理与直连，核对服务端日志及采样时间后，只修改显式传输选项和错误定位；不修造样本、不放宽评分。旧代码9项失败，修复后96项相关用例与全量1201项通过；新云端批次取证链、恢复和清理通过，但有界观测仍不计因果根因。这适合解释测试预言、实验时窗和验收器自身的可测试性。


## 新增三个可演示的缺陷闭环（2026-09-30）

[实例范围、事务竞争与Java报告范围](../reports/architecture/instance-scope-fix-20260930.md)已部署：跨Agent重复进程名不再由模型猜选；并发Skill首插入用诊断父行锁保护；真实热点和GC计数器仍可成为VERIFIED观测，但报告、建议和评分不将其冒充因果根因。每项均有旧代码负向复现、修复回归、CI和新版本证据。Python1192通过/8登记跳过，PG6通过零跳过，CI13/13成功。可按失败证据→状态/身份或事务机制→回归→部署复验讲解，保留原失败成绩。

## 2026-09-30 最新交付

后端及Web新代码已部署并复核；完整21场景严格根因0/21、已验证观测4/21，撤销/清理21/21；一小时请求19,950全成功，但60RPS与9个持续窗口延迟超限，整体未通过。Web观测范围误标及HTTP409已修复，224项测试与CI13/13通过，云端同会话复验通过。详情和能力边界见[本次部署验收](../reports/architecture/deployment-validation-20260930.md)。下方带日期记录保留当时状态。

本页收敛项目的展示范围：用三个真实问题说明测试设计、后端状态管理与受约束 Agent 的实现。当前是实验与工程验证平台，不能描述为已具备所有故障的自动根因定位或自动修复能力。当前版本、测试与部署记录优先看 PROJECT_CONTEXT 顶部。

## 三条可以检查的实现主线

| 方向 | 具体问题与最终行为 | 源码与行为回归 | 需要能解释的问题 |
|---|---|---|---|
| 测试开发 | 计划槽位恰落在6秒边界时，浮点时钟相减得到5.999999999999993，造成31/29错分；改用index/rate保存计划偏移，实际延迟仍从原计划时间计算 | `scripts/run_load_endurance.py`、`tests/test_load_endurance.py` | 如何区分发压器错误、服务SLO失败和报告错误？为什么不能重算旧成绩掩盖失败？ |
| 后端开发 | 不可执行模型候选在报告后续效果中可能反复失败重试；现在原文与拒绝槽位幂等留痕，无可执行候选则正常终止，旧报告保持不可变 | `server/app/drop_insight/service.py`、`tests/test_cpu_contract_persistence.py`、`tests/test_drop_insight_report_effects_postgres.py` | 唯一约束、effect-key、行锁、租约和fencing各解决什么？SQLite流程回归为什么不能替代PostgreSQL竞争测试？ |
| Agent 开发 | 自由文本强百分比、跨窗稳定或因果断言不能由较弱Profile关键词冒充支持；新Python/Go观察计划共用可执行合同，报告前补采沿用原假设，报告显式标明非因果观察 | `server/app/drop_insight/cpu_criteria.py`、`hypothesis_predicate.py`、`tests/test_cpu_observation_registry.py`、`tests/test_cpu_control_worker_flow.py` | 模型生成计划与工具能证明的范围如何对齐？知识为什么不是证据？COUNTER、CONTROL与SUPPORT分别是什么？ |

最终验收还检查结构化 `claim_scope`：`BOUNDED_OBSERVATION` 即使是VERIFIED、引用完整且包含准确函数名，也不能获得根因通过。未知或格式错误的范围和非布尔因果标记同样拒绝。无范围字段的历史报告按旧规则兼容，原报告不回写；因此不能把新旧口径混成准确率趋势。

## 本地复现入口

沿用项目环境安装 `python -m pip install -e ".[dev]"`，在仓库根目录执行：

```powershell
# 风险快速门禁：本轮175项通过、零跳过
python scripts/run_quality_gate.py --profile smoke

# 全量Python、合同一致性与关键行/分支覆盖率
python scripts/run_quality_gate.py --profile python

# 独立HTTP样例的业务正确性与相同负载对照
python scripts/run_quality_gate.py --profile business
```

每次生成新的输出目录，打开命令末尾的report.html。不要复用旧目录或把历史日志当本次执行结果。这些入口不触发云端故障；smoke不是压力测试，business不是生产业务容量验证。

本轮本机全量1170 passed / 7 expected skips，快速门禁175 passed。7项是PostgreSQL和Chroma环境依赖，不能计作本机执行通过，需查远程专项CI。最初完整回归的9项失败来自两处旧SimpleNamespace测试替身没有status；补齐真实会话字段后恢复通过，未放松终态保护或修改断言预期。关键报告结论与修复验证模块行/分支覆盖率保持100%。

[原始回归、首次失败及修复证据包](../reports/quality/focused-delivery-20260929/regression-evidence.zip)保留JUnit、日志、覆盖率、前后复现和文件哈希。[同一输入的汇总器前后对照](../reports/quality/focused-delivery-20260929/scope-before-after.json)明确是合成负向样本：旧版误计根因，新版拒绝；它不是云端根因成绩。浮点历史复现文件的“尚未应用”字段保留其当时状态，当前修复由新源码和新回归证明。

## 15分钟讲解顺序

1. 先说明输入、输出和失败标准：请求成功、证据可信、观测成立、因果根因、同负载修复是不同结论。
2. 用浮点分桶的原始边界值讲最小复现，再打开固定时钟回归，解释为何选独立的index/rate预期。
3. 用报告效果回归讲重复调用、终态与不可变记录；再指出真实PostgreSQL并发专项的证据边界。
4. 用25%样本不能证明超过50%的反例讲Agent计划约束；展示观察报告为何不能计根因。
5. 最后展示质量报告中的失败、跳过、覆盖率门槛和远程CI，而不是只报测试数量。

求职表述应落在自己能解释并复现的实现上。测开强调风险、用例与验收可信度；后端强调任务状态、事务并发、幂等及接口边界；Agent强调工具编排、来源验证、预算与失败处理。同一个项目可以选择不同主线，不需要再增加框架来匹配岗位名称。

## 已知边界与暂缓项

- 最新云端仍为 `2400190`；本页新候选未部署，新旧版本不能混用。
- 21场景批次已由用户停止：完成9项，严格0/9、原门禁根因字段1/9、恢复清理9/9，其余未完成。本轮不重启这项实验。
- 一小时双机实验已完成，19,950次均成功且固定质量检查通过，但恢复及5/120持续窗口超限，整体FAILED。短测通过不能替代它。[完整测量](DISTRIBUTED_LOAD.md)
- 完整多租户范围过滤、跨服务/SQL追踪、诊断取消产品入口、全故障域独立对照、生产容量与容灾验证仍属后续工作。
- 不引入任意Shell、自动修改生产代码或通用自修改Agent；这些不属于本轮交付目标。

恢复后优先修复有独立复现证据的缺陷。本轮交付标准是代码回归与依赖专项验证通过、失败证据可追溯、说明与实际能力一致，不以21场景全绿作为完成条件。

## Linux CI新增缺陷复盘

CI `36594980233` 的Python独立CPU对照出现相同起止总量却得到负CPU（约-1.3e-16%）的确定性浮点运算缺陷。计算改为直接使用报告记录的起止总量差；不将负数钳制为零，不放宽窗口门槛。新增4项在旧源码全部失败，修后此组20项通过；原失败CI产物和前后JUnit见[原始包](../reports/quality/focused-delivery-20260929/cpu-roundoff-regression.zip)。[CI 36595478139](https://github.com/llongwang751-arch/mini-drop/actions/runs/36595478139) **13/13作业通过**，测试代码 `9d0231dbf4cb6c4beca346230967b0b139be3c29`，测试merge `be98350e1568d71e0411f3eea7d13ecb20d75f2e`。Python1174 passed / 7 expected skips；PostgreSQL、Chroma、Go race、Chromium、业务重复验收及Linux独立热点均由专项实跑。Trivy/部分lint仍为report-only，通过不表示所有安全规则阻断发布。 [CI原始清单](../reports/quality/focused-delivery-20260929/ci-run.json)。代码已提交推送；云端发布仍为2400190，本轮未部署。
