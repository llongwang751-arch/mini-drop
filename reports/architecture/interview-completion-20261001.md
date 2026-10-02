# 面试交付五个重点补齐：2026-10-01

本轮五个重点已经交付。统一发布20261001T160532Z / f8562dfb，精确CI13/13和真实浏览器通过。

统一版本已发布`20261001T160532Z`，后端与Web都来自同一Git源码`f8562dfb6e5ee94f1e228d2515f4f4a1cd6115ff`。[CI36888942005](https://github.com/llongwang751-arch/mini-drop/actions/runs/36888942005)成功13/13；PR测试merge7cfa9e7与发布提交Git tree一致，证据已核对。Python1451通过/16登记跳过、Web309通过、真实PG14通过零跳过、真实Chromium7项回归通过；Chroma独立零跳过及Go race/安全/真实镜像与连续I/O专项通过。之前的CI浏览器失败、业务两次失败和原8/18评分保留。

Worker/Analyzer各208份源码、Web58份容器内和公网文件SHA一致；13容器健康，另10容器ID保持，21故障inactive。部署前后Office保持紧邻基线PID2670585/NRestarts=0、agent-workspace-20261001T160559Z；本轮更早Office由外部工作独立更新，不能声称整轮不变。API二进制SHA保持。回滚配置`/opt/mini-drop-releases/20261001T160532Z/private/rollback.compose.json`，旧发布与数据保留。

真实浏览器验证工程14/21/路径4/21/反证5、18新+3旧范围、五个体检状态、业务find_longest_match报告、原CPU/HTTP/I/O路径及浏览器前进后退、4工程案例/9份下载SHA与1440/1024/768/375宽度，无JS/HTTP错误。上线后另一个真实Go正常检查验证trace/span关联标签在可信重绑后保留，2份原始下载SHA一致；标签为明确测试值，不冒充真实请求因果。三次隔离业务进程均已退出。全部证据及发布回执见[补齐交付](interview-completion-20261001.md)与[归档清单](../quality/interview-completion-20261001/manifest.json)。



| 重点 | 实际结果 | 可审查证据 |
|---|---|---|
| 当前状态检查 | 正常、真实CPU异常、故障撤销恢复、采样取消后无法判断、重新采样正常，五个独立会话；8份原始下载SHA一致，无根因报告 | `reports/quality/interview-completion-20261001/go-*` |
| 业务修复闭环 | 隔离真实HTTP/SQLite FTS5，三窗共1080次、成功/质量100%；同96候选/4RPS/360次，P95 68.377→2.696ms | `business-fix-r3/validated-comparison.json`及原始三窗/source/6份产物；前两次失败保持 |
| 数据库可靠性 | 新增真实PG并发收束、事务回滚、提交前/后进程退出、新进程重试、取消竞争、未完成采样门禁6项；CI36882851972新旧共14通过零跳过 | tests/test_health_check_postgres.py；CI真实PG job110438703399 |
| 可复现交付 | 视觉源码与功能统一Git；修正浏览器旧视口断言/端口冲突；请求trace/span在重绑时保留，身份权限仍需重验 | 精确CI13/13及发布回执已归档；旧trace2失败/修复后通过，Chromium旧布局失败保留 |
| 性能扩展 | 18类独立新实验，79份原始下载SHA一致，18类撤销/恢复/收束完成；连同此前3条记录，工程判断14/21、具体路径4/21、有效反证5条 | performance-manifest.json、performance-18.json、18个原始case及生成工程索引 |

工程14项包括4项具体路径、5项有界支持与5项有效反证。内存注入在诊断窗前已进入保留平台期，增长假设被反驳不代表无内存压力，更不能称为识别内存泄漏。Java GC观测要求分配Profile和同任务独立GC计数器；锁等待Profile只描述采到的等待，不提供锁持有者或代码级因果证明。Python支持要求重算具备源码位置的函数份额和独立Linux CPU计数，均不改原报告因果标志。

原实验记录按当时评分为8/18。评分补齐Java分配/锁等待合同、修正跨信号域反证以及Python源码Profile形状后，对同一不可变新证据重评为11/18，再合并先前三项为14/21。原始8/18不改写，生成索引来自版本化源合同；混合批次不能称一次完整21类新准确率实验。旧严格因果0/21和已接受小时1/120窗超限原报告保持。

七类不足及下一步分别是：

| 案例 | 真实失败原因 | 后续需要的最小能力 |
|---|---|---|
| Python源码热点 | Profile已完成，但规划为泛化文字，UNSUPPORTED_PLAN；后续OS采样被预算收束预留拒绝 | 新假设使用注册的有界源码合同，并预留完整工具/收束时间 |
| C++ CPU | 计划文字50%而表达式0.5，CPU谓词与工程路径域不完整；不把0.5当50 | 明确单位、注册perf路径合同及独立OS数值 |
| C++锁竞争 | 已采CPU Profile与系统指标，但“显著/同窗增长”的锁判据不可执行 | 真锁等待时长/次数或off-CPU观测及数值门槛 |
| Python、Java、C++文件I/O | 系统和eBPF任务完成，但计划所需同步操作耗时/新增次数缺测 | 每语言应用同步操作计数及耗时，或可信目标块I/O关联；不能以缺测补0 |
| 邻居干扰 | 泛化Python热点假设不可执行，且未取得跨进程竞争关联 | 明确目标与邻居、同资源竞争窗口、独立对照；仅主机负载不足以归因 |

业务链是人工提出假设→平台批准取证→工程修复→固定负载复测。慢请求trace_id写入原请求上下文；已知绑定替换丢字段缺陷已修复，本轮取证发生在旧平台，关联以原始query及请求记录为准，发布后另验新字段保留。Profile主要find_longest_match，独立CPU反证被保留，完整CPU主张不成立；报告没有被强制刷过。修复源含缓存及连接处理，重排耗时单独测量支持计算优化，不能把所有HTTP收益只归于缓存。此业务为EXTRACTIVE_LOCAL，不是Office或在线LLM测评。

本轮Office由其他工作独立更新，任务未改其进程或发布；不能声称整轮Office PID不变。后续部署以邻近发布快照比较。所有隔离业务进程已经退出，21故障inactive，平台13容器健康。没有删除旧问题、证据、数据库卷或对象存储。

复核：

```powershell
python scripts/build_engineering_diagnosis.py --check
python scripts/evaluate_same_load_business_fix.py reports/quality/interview-completion-20261001/business-fix-r3
python -m pytest tests/test_engineering_diagnosis.py tests/test_engineering_java_profiles.py tests/test_same_load_business_fix.py tests/test_diagnosis_trace_correlation.py -q
# 真实PG：专用测试库、RUN_POSTGRES_TESTS=1；参照CI环境，必须零跳过
python -m pytest tests/test_health_check_postgres.py tests/test_drop_insight_report_effects_postgres.py tests/test_outbox_postgres.py -q
```

固定现场记录为不可变证据。重新注入必须选择新目录，保持目标、源码及负载一致，并独立保存结果；不能覆盖本轮文件。
