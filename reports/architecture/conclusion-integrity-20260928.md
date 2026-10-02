# 诊断结论可信度修复与测开回归

本轮从报告结论模块的覆盖缺口切入，发现的是结果正确性问题，而不只是缺少测试数量。先运行负向用例得到 24 项失败，再修复数值处理与展示逻辑，最后通过统一门禁和 Linux CI 验证。没有修改云端部署或历史诊断报告。

## 真实输入与前后行为

| 输入 / 风险 | 修复前 | 修复后 |
| --- | --- | --- |
| Java Lambda 包装帧 99%，业务帧 24% | 换成业务函数名后仍显示 99% | 函数名和占比来自同一帧，显示 24%；缺占比则不展示数字 |
| TopN 百分比为字符串 `NaN` | 仍可能 SUPPORT；报告渲染执行 `int(NaN)` 崩溃 | 判据 NEUTRAL；渲染不输出非法比例，不崩溃 |
| 只有 JVM GC schema，没有计数或窗口 | 自动填 0，形成“未观察到 GC”的 COUNTER | 缺失不是零，NEUTRAL，不产生反证覆盖 |
| 只有数据库锁 schema，没有会话计数 | 自动填 0，形成“无锁等待”的 COUNTER | NEUTRAL，不把观测失败写成不存在问题 |
| GC 有活动，但分配增量为 0 | 没满足 CONTROL 条件便描述成无 GC 活动 | NEUTRAL，保留“观察到活动但未证实所提关系”的区分 |
| 数据库提供 `lock_wait_ms=1840.5` | 只读 `max_wait_ms`，报告成 0ms | 两个历史字段兼容，读到 1840.5ms；缺失为 null，冲突不晋级 |

[机器可读前后对照](../business-acceptance/conclusion-integrity-20260928/before-after.json) 是用同一组内存输入执行历史 `2775aac` 与修复后 `6fb7124` 的叶子函数生成，记录版本和源码哈希。它验证确定性判定和渲染，不冒充线上事故复现或真实 GC 压测。

## 判据与回归设计

数值先验证再判断，不把 `False` 当 0 或 `True` 当 1。百分比有限且在 0..100；计数是非负整数，不能将 1.5 次截断为 1 次；负数增量可能表示重置，也不能截成零。现有数字字符串继续兼容。

TopN 中任一命名行比例无效时，既不能确认该函数主导，也不能用它证明不主导。GC 独立对照要求完整计数和正时间窗：真实零活动仍可作 COUNTER，有活动且分配为正可作 CONTROL，缺失或因果关系不完整则 NEUTRAL。数据库有效计数仍能支持锁等待；可选时长缺失不阻止已观察计数的陈述，也不伪造时长和边数。

报告测试还覆盖：反证存在时 VERIFIED 标记不能强行变成最终根因；无具体发现时不重复假设充当结论；未知样本数不显示已知数量；Python/Go/Java 不同 profile 的观察边界；Java 分配类型去重和长度限制；多个候选证据的选择。测试通过 service 的重导出入口及实际叶子函数执行，未用 Mock 伪造被测返回值。

## 验证证据

- 修复前：新增负向测试实际得到 **24 failed / 40 passed**，原始日志保留。
- 修复后针对性回归：报告、谓词、证据质量三组 **135 passed**。
- 本地全量第一轮：**858 passed / 7 skipped**；随后新增 7 个窗口、混合无效行、缺失等待时长边界，最终远程 CI 为 **865 passed / 7 skipped**，相对上一轮增加 85 个实际执行用例。
- `report_conclusion.py` **行+分支合并覆盖 100%**，`quality_plan.json` 下限从 69 上调到 100；`fix_verification.py` 原有 100% 门槛保留。覆盖率不能单独证明断言正确，因此同时保留修复前失败和前后对照。

[CI 36429192084](https://github.com/llongwang751-arch/mini-drop/actions/runs/36429192084) **13/13 作业全部通过**，测试 PR head `6fb7124098746fe9f6d51d769b42c086db3f1594`、merge `6ef3f51a7a12f118a613c3b397cea9d1951b1306`。Python 865 passed / 7 skipped；7 项依赖场景由 PostgreSQL/Chroma 专项执行，不是把跳过算通过。report_conclusion 与 fix_verification 的行+分支均 100%，全部关键模块无门槛违约。

[CI 清单](../business-acceptance/conclusion-integrity-20260928/ci.json)、[Python 机器报告](../business-acceptance/conclusion-integrity-20260928/ci-python-report.json)、[CI JUnit/日志/覆盖率](../business-acceptance/conclusion-integrity-20260928/ci-python-evidence.zip) 已保存；逐一核对其 SHA 与 CI 原始清单一致。最后只做文档和证据归档，不冒充新归档提交的 CI 已完成。

修复前日志、本地第一轮 JUnit、完整日志、覆盖率与质量报告在 [回归证据包](../business-acceptance/conclusion-integrity-20260928/regression-evidence.zip)。该包没有覆盖之前的任何输出目录。

复现针对性测试：

```powershell
python -m pytest tests/test_report_conclusion.py tests/test_hypothesis_predicate.py tests/test_drop_insight_policy_evidence.py -q
python scripts/run_quality_gate.py --profile python --output output/quality/conclusion-new
```

## 当前完成程度

| 方向 | 当前状态 | 剩余边界 |
| --- | --- | --- |
| 自动化回归与质量门禁 | 可运行、可追溯，含负向测试与关键模块覆盖下限 | 全项目覆盖尚未到 100%，安全报告型规则不等于阻断规则 |
| 真实依赖专项 | PostgreSQL 并发、Go race、Chroma、Chromium 已在隔离 CI 实跑 | 不等于完整生产多租户验收 |
| 性能与资源测试 | 已有 30 分钟请求与资源筛查、阶梯/恢复、原始报告重算和图表 | 独立压测机、小时级实验和完整泄漏诊断仍未完成 |
| 受控热点与定位 | Python/Go OS CPU 与函数采样三窗对照已实跑 | 不等于云端 Agent/AI 的严格根因闭环 |
| 结论可信度 | 本轮补齐非法数值、缺失反证及错帧占比防回归 | 历史报告不改写；只有未来运行新版本才获得修复 |
| 求职展示 | 有测试计划、真实缺陷、失败日志、修复、防回归和 CI 证据 | 个人贡献陈述仍须能独立讲清实现、取舍和限制 |

云端严格根因历史成绩仍为 1/21。本轮提升的是测试与判定的可信度，没有凭本地测试提高线上成绩。学习教程中用户正在修改的课程正文未纳入本轮提交，只通过原生成器更新了源码文件索引。
