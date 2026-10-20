# Python Saber 接入与验收

当前AgentScope增量：API12/runtime2下新调查11/修复9，完整网页76.4秒，四个真实SDK阶段、fresh profile/test、原2+冻结6/384例/6组对照通过，CPU中位1.5587倍，仅限登记组件基准；原仓库不自动发布。详见[发布记录](releases/20261020-agentscope.md)与[接入分工](AGENTSCOPE_INTEGRATION.md)。以下保留先前版本的采集与接入验收。
更新：2026-10-20。当前 API `20261020.10`、Web `20261020.10`、Agent/Helper `20261020.3` 及最终三角色AppArmor策略下，已从公网页面新建调查9，在88.0秒内完成采集、自动复测/再次诊断、真实源码补丁和严格页面呈现验收。原2+冻结6项测试、384例一致性和6/6组CPU对照通过；6组加速比的中位数为1.353364×。见[最终验收](evidence/completion-20261020/saber-final-acceptance.json)。早期后端和页面错误仍单独保留如下。

## 接入内容

源项目来自桌面 `AGI-Core项目/AGI-saber-python/final`。快照只包含真实的 `internal/rag/fts5_index.py` 和原 `tests/test_fts5_index.py`，登记项目为 `saber-python`，preset 为 `saber-fts5-v1`。原仓库保持不变，没有读取 `.env`、真实 `data`、用户知识库或运行数据库。

这是 Saber 的 SQLite FTS5 关键词检索组件接入。冻结 adapter 用 importlib 按精确文件路径加载该真实模块，避开会加载外部引擎依赖的 `rag/__init__.py`。本轮没有启动整个 Saber 应用，不代表其 Agent/RAG 引擎、身份系统、前端或全部功能已上线验收。

登记包版本为 `9245ce09fa2e2adceafebb669ee4605f94a2bb05153d5844d24888fb61d4f402`；原模块 SHA-256 为 `c0b47884db29620a743250f37f0b8305a2c5c23ede897cd914dce5b5382357df`。包版本与单文件 SHA 含义不同；原测试和模块的精确文件清单由登记回执锁定。

## 调查与验证流程

1. 在[智能性能调查](https://82.157.81.79/investigations)选择“Python Saber · 知识库 FTS5 查询”，绑定当前 Agent/PID/UID/start_time/source revision。模型按节点真实能力与参数 schema 生成采集计划，用户核对后确认。本轮既有实跑选择 `PYTHON_CPU`、10秒、99Hz、`native=false`。
2. py-spy 实际采集，制品上传并由 Analyzer 解析。DeepSeek 调用热点、调用上下文和采集质量等证据工具，随后自动复测、再次诊断与比较。重复采样用于复现，不独立确认根因。
3. 绑定源码 Agent 测量真实组件的 cProfile、读取相应版本与行号，模型自己生成补丁。仅允许修改 `fts_tokenize` 和 `build_fts_query`；SQL类、其他顶层节点、原测试和 harness 冻结。没有预置优化补丁。
4. root 管理的固定 broker 在无网络、无凭据、非root、只读根文件系统且有CPU/内存/进程/时间限制的沙箱中应用补丁。原 Saber 项目和生产业务源码不会被自动修改或发布。
5. 先执行原2项测试和额外冻结6项，再完成6组新进程交替CPU对照。384例比较递归精确 Python 类型和值，包含实际 MATCH 字符串、绑定user范围、结果pg_id/content和BM25 score。
6. 功能通过、输出一致，且CPU中位加速至少1.10×、至少5/6组超过1.05×，才标记 `validated_in_controlled_saber_benchmark`。这是受控实用验收，不是正式统计显著性检验，不代表任意输入或生产P95/P99收益。

测量key包含当前 repair 的执行范围。同一 repair 的恢复可以复用同一步；新 repair 必须重新测量。既有实跑的 profile/test_patch 均为 `cache_hit=false`。

## 负载与边界

harness 有64条合成查询：8条边界输入，56条约1K至8K上限的长查询；内存SQLite中有64条合成知识块、多个合成用户范围，执行真实FTS5 MATCH/BM25。建索引、启动整个应用和网络请求不计入query CPU计时。首次验收的candidate profile实际最大查询长度为6315字符，8192是生成上界。

冻结回归覆盖中英/Unicode/casefold、重复词、空值、引号和保留符号、64个去重词元次序、tenant隔离和删除、返回类型、真实SQL结果及score精确一致，以及仅两函数可变、SQL类AST冻结。这些有限测试不能保证模型解释或所有输入都正确。

本地原样candidate负控完成384例一致性检查，但未达到CPU门槛，`accepted=false`。它证明功能通过不会自动被写成性能优化成功；其本地环境与线上实际测量分别记录。

## 已有实跑及页面修复

| 阶段 | 实际结果 |
|---|---|
| API10 / Web8 / Agent1 / Helper1，调查8 / 任务55→56 / repair6 | 从网页新建，后端完整闭环75.9秒；原2+冻结6项测试通过，384例一致，6/6组达门槛，CPU中位加速1.504786×；profile/test_patch fresh |
| Web8源码页 | 失败：列表缓存没有repair6，点击查看补丁后错误回退显示旧CRM5。原driver只核对后端API和heading而漏检，不能记作完整UI通过 |
| Web9修复与本地回归 | 明确ID的调查和源码记录使用各自详情API；缺少、加载和报错时不回退其他ID，新调查确认路由保持其ID；源码scope使用实际validation值。原9项及新增3项fixture共12项通过 |
| Web9实际只读呈现 | 从现有调查8点击linked6，无刷新或手工选修复，自动显示Saber/repair6/实际summary/scope/1.50×（API精确值1.504786）；桌面1440、手机375通过，脚本错误0、无横向溢出；未重跑模型或补丁掩盖错误 |

首次呈现错误的 summary 已标记 `backend_pipeline_passed=true`、`source_render_verified=false`、`passed=false`。原driver的 `passed=true` 另存 `original-driver-summary.json`，原错截图和录像保留。组合回执分别引用后端实跑与修复后呈现，不能表述为“Web9在75.9秒内从零完成了完整UI演示”。

证据：

- [当前版本从零完整验收](evidence/completion-20261020/saber-final-acceptance.json)。
- [早期分阶段组合验收](evidence/completion-20261020/saber-combined-acceptance.json)。
- [首次后端与呈现审计](evidence/completion-20261020/saber-live-2026-10-20T07-05-19-055Z/summary.json)、[原driver回执](evidence/completion-20261020/saber-live-2026-10-20T07-05-19-055Z/original-driver-summary.json)、[实际工具和补丁](evidence/completion-20261020/saber-live-2026-10-20T07-05-19-055Z/detail.json)。
- [Web9桌面/手机真实只读核验](evidence/completion-20261020/saber-ui-web9-2026-10-20T07-22-25-365Z/summary.json)。
- [Web9部署](evidence/completion-20261020/web9-deployment.json)、[当前Web10部署](evidence/completion-20261020/web10-deployment.json)。Web10另修BOLT未测量计数显示，未以此冒充新的Saber实跑。
- [当前Go源码隔离PG17验收](evidence/completion-20261020/postgres-acceptance.json)：110项（37顶层+73子项）通过、0skip。一个真实DeepSeek测试使用明确导入fixture，其他协议、存储和Agent数据是fixture，不等同生产故障注入。
- [12项本地UI fixture](evidence/completion-20261020/ui-fixtures.json)，不等同真实模型或采集。

## 现场操作

进入[智能性能调查](https://82.157.81.79/investigations)，选择已接入的Saber按钮，勾选统计发送/采集授权和登记源码的隔离修复授权，再生成并审阅计划。目标PID来自当前登记信息，不能复制旧记录中的PID。

示例问题：

> 调查Python Saber知识库长查询的CPU热点，选择Python CPU调用栈采集10秒99Hz并关闭native扩展，检查热点、调用上下文和采集质量；自动复测、再次诊断。继续定位绑定版本源码，在隔离副本生成最小补丁，保持查询语义、输出类型与用户隔离，执行原功能回归、严格输出比较和交替CPU对照，只依据实际证据判断。

确认后可查看原采集、复测、再次诊断和关联源码记录。结束时核对项目为Saber、源码记录ID与调查关联一致、实际summary/scope和性能门槛；不能只看“定位与修复结论”标题。当前严格driver会检查这些字段并保留每次失败。

## 最终版本现场验收

2026-10-20，在最终三角色AppArmor策略、API10/Web10/AgentHelper3下，复用既有私有登录态，从公网页面创建全新[调查9](https://82.157.81.79/investigations)。目标由登记项目重新发现为PID82、UID10001、start_time_ticks9040395，未复制旧调查的PID23821，也没有预置补丁。

| 步骤 | 当前实跑结果 |
|---|---|
| 模型采集计划与页面确认 | 8.2秒生成PYTHON_CPU/10秒/99Hz/native=false，8.4秒从页面确认 |
| 真实采集与自动闭环 | [原任务82](https://82.157.81.79/tasks/82)→诊断25→[复测任务83](https://82.157.81.79/tasks/83)→再次诊断26，verification12成功 |
| 真实源码定位 | repair7，实际cProfile定位fts5_index.py的build_fts_query行35及genexpr行42，SQL类和其余顶层源码冻结 |
| 新测量 | profile_business_flow和test_patch均cache_hit=false，job key与本次repair绑定 |
| 功能和性能 | 原2项、冻结6项测试通过；384例严格类型/值一致，6组交替对照全部达门槛；6组CPU加速比中位数1.3533639366425003× |
| 正确页面展示 | 自动从调查9进入Saber/repair7，无刷新或手工选repair；实际summary/scope、ID与CPU结果均校验，桌面1440及手机375截图目检通过，页面错误0、无横向溢出 |
| 全程 | 88.0秒，录屏和全部步骤独立保存；原仓未修改，原Saber应用未自动发布 |

复测比较仍保留 `root_cause_status=not_tested`，因为重复采样本身没有代码干预；源码补丁在独立受控benchmark通过后才给出 `validated_in_controlled_saber_benchmark`。本次candidate profile实际最大查询长度为6313字符，每64例测量执行62次真实SQLite查询。性能结论仅限该合成查询负载和补丁组合，不代表全Saber或生产P95/P99。

证据：[最终回执](evidence/completion-20261020/saber-final-acceptance.json)、[原始现场步骤](evidence/completion-20261020/saber-live-2026-10-20T08-01-13-655Z/summary.json)、[真实工具/补丁/对照](evidence/completion-20261020/saber-live-2026-10-20T08-01-13-655Z/detail.json)、[桌面结果](evidence/completion-20261020/saber-live-2026-10-20T08-01-13-655Z/source-result.png)、[手机结果](evidence/completion-20261020/saber-live-2026-10-20T08-01-13-655Z/source-result-mobile.png)、[全程录像](evidence/completion-20261020/saber-live-2026-10-20T08-01-13-655Z/rehearsal.webm)。
