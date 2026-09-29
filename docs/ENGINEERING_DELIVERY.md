# 测试开发、后端与 Agent 开发：工程交付入口

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

CI `36594980233` 的Python独立CPU对照出现相同起止总量却得到负CPU（约-1.3e-16%）的确定性浮点运算缺陷。计算改为直接使用报告记录的起止总量差；不将负数钳制为零，不放宽窗口门槛。新增4项在旧源码全部失败，修后此组20项通过；原失败CI产物和前后JUnit见[原始包](../reports/quality/focused-delivery-20260929/cpu-roundoff-regression.zip)。Linux新实跑以随后CI结果为准。
