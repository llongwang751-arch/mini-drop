# 受控故障干预与再次诊断

本实验使用既有 `integrations/observability-demo`，不增加SQL执行、shell、任意地址或生产代码修改能力。固定三条路径：纯Python计算、四次SQLite笛卡尔积、十二次SQLite聚合加50ms网关deadline。第三项是**组合干预**，不能称只调整deadline已隔离单一原因。

每轮 `normal-before独立观测 → fault实际采集与诊断 → 撤销故障客户端 → normal洗脱62秒 → 同参数复采与新诊断`。默认三种各两轮，seed固定顺序，共12份诊断；`--repetitions 1`得到独立预登记的3轮/6份诊断，可先做首次接线验证。失败不覆盖、不挑优重试；1重复与2重复计划的来源和分数必须分开。

洗脱是必要的：Go业务工具按实际attempt执行起止各扩展60秒，立即复采会把旧故障放进正常诊断的观察窗。控制器不改此契约，也不清空服务遥测。每轮通常1–2分钟，完整两重复约10–15分钟；不要把有界阶段当作长期稳定性测试。

`model_input.json`只包含同一中性问题、采集和只读诊断参数；控制器的故障选择、oracle和评分阈值不进入模型请求。普通Go API重新执行目标/会话/能力/参数校验。采样始终为同一目录进程的6秒49Hz PYTHON_CPU；前后参数摘要仅排除Go自动注入的 `traceparent`，所有采集身份、预算、选项仍参与比较。API与demo时间窗、PID/UID/角色、环境SHA、执行计数及失败完整保留。

## 执行

在能访问现有内部demo服务的受控环境运行。生产执行由管理员串行安排，不与其他负载实验混跑。除 `--execute` 外默认零模型调用。

```sh
python3 scripts/quality/causal/run.py --repetitions 1 --output /private/causal-plan-001
python3 scripts/quality/causal/run.py --execute --repetitions 1 \
  --config /private/causal-config.json --environment /private/environment.json \
  --credentials /private/credentials.json --output /private/causal-first-001
```

config只允许下面字段；`agent_id`和`telemetry_service_id`须为当前已授权登记，不猜PID。目标PID由实际catalog健康接口取得，查询前后重核身份。

```json
{"api_origin":"https://82.157.81.79","gateway_origin":"http://observability-gateway:8130","catalog_origin":"http://observability-catalog:8131","agent_id":"REPLACE_WITH_CURRENT_AGENT_ID","telemetry_service_id":"gateway"}
```

API严格TLS，demo仅允许上面两个固定内部hostname/port；本机独立HTTP夹具需另加 `--allow-loopback`。不允许外部域名、凭据URL、重定向、自由SQL或任意query。凭据文件只含username/password，放受保护目录；不要把凭据放进命令行或提交Git。

environment是管理员在运行前从实际容器/登记文件取得的冻结清单，结构如下。值必须是实际摘要，不能用标签/latest或示例补齐。

```json
{"schema_version":1,"images":{"api":"sha256:<64hex>","agent":"sha256:<64hex>","helper":"sha256:<64hex>","analyzer":"sha256:<64hex>","runtime":"sha256:<64hex>","observability_demo":"sha256:<64hex>"},"sources":{"observability_server":"<64hex>","observability_config":"<64hex>","task_kinds":"<64hex>"}}
```

源码清单绑定本仓库实际demo server.py；镜像/配置SHA是来源冻结，不能冒充远端可信执行证明。运行环境须保持目录布局，除causal目录还复用agent客户端、evaluation模块、Analyzer五个标准库模块及demo server.py，不需新的第三方框架。

预算为逻辑模型调用累计60、工具调用80；申请下一诊断前预留当前协议最坏9/14，失败缺少运行元数据按最坏数计费。不是provider底层HTTP重试次数的报价。HTTP有超时/输出/次数限制，负载单线程闭环、每阶段最多120秒/160请求，整轮控制器最多1800秒。已知活动任务/诊断通过普通API取消；创建返回未知ID时明确DO_NOT_REPLAY，不猜ID、不盲重试。每阶段写私有追加journal，最终仍保存失败capture；公开使用score输出。

## 指标与人审接口

`controlled_intervention_verified`由独立客户端和服务端指标、实际HTTP状态、固定SQL事件、真实CPU符号采样以及同参数干预前后变化判断。空窗、缺失、单位错误、旧故障残留、目标变化、非真实采样均不算通过。P95与响应错误率必须来自当轮实际数据；阈值是预注册的本演示判据，机器噪声可能导致失败，不能调阈值后挑选同批最优结果。

`fault_supported_direction_top3`与`normal_after_direction_top3`仅使用现有受约束 `hypotheses.kind` 的精确枚举，方向命中不等于精确SQL、deadline或业务根因准确。`exact_root_cause_accuracy`、语义误报/拒答继续保持null，直到独立真人提供有效评审。评分器不从自由解释猜词、不让另一个模型自评，不复制Go事实标量数学校验。

```sh
python3 scripts/quality/causal/experiment.py score --input /private/run/capture.private.json --output /private/run/score-new.json
python3 scripts/quality/causal/experiment.py human-template --input /private/run/capture.private.json --output /private/run/human-review-new.json
python3 scripts/quality/causal/experiment.py human-score --input /private/run/capture.private.json --reviews /private/reviewed.json --output /private/review-score.json
```

`human-review.schema.json`供报告页面渲染/提交校验，含真实task/run身份、分析与报告SHA、真人身份声明、独立性与三个未填语义标签。它不是已经存在的新HTTP保存接口：产品页面须由已认证的人提交，并由后端按同一结构/来源权限复核落库。模板生成不增加已审人数，AI类型、重复报告、改过的SHA、没有客观干预却声称精确根因正确均会拒绝。工具不能验证审阅者现实身份；未遮蔽oracle不得声称盲评。

```sh
python3 -m unittest discover -s scripts/quality/causal -p 'test_*.py' -v
```

单测中的诊断DTO为明确导入控制；本机真实HTTP用例实际执行CPU/SQLite/deadline和恢复，但没有平台采样/模型时不称完整链路验收。真实模型、真实采集和完整计划由另一次管理员执行的私有capture及公开分数证明。
