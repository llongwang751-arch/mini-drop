# 云端部署、严格根因复验与双机长测

## 发布与可回滚范围

2026-09-28 发布 `20260928T140102Z`，源码 head `1d010cc5f3ad55ba5fdcadea3a18728afab0d805`。Diagnosis Worker 与 Analyzer 分别以自己的原有镜像构建源码覆盖层，候选导入通过后才替换；健康检查通过后切换 current。其他 11 个运行容器 ID 未改变；数据库、对象存储及办公助手数据保留。

[部署元数据](../business-acceptance/deployment-20260928/deployment.json) 记录两服务前后镜像、旧发布和回滚路径；[源码清单](../business-acceptance/deployment-20260928/manifest.json) 按字节记录 182 个覆盖文件。[源码与部署执行器归档](../business-acceptance/deployment-20260928/deployment-source-evidence.zip) 不含私有 Compose 或凭据。候选健康和未变容器检查均纳入回滚范围；恢复时逐服务尝试，即使一项失败也不阻止另一项恢复，并将 current 指向旧目录。未实际制造上线故障演练回滚，不把代码路径审查称作完整灾备验收。

回滚文件保留在新发布 `private/rollback.compose.json`，只在服务器使用。恢复两个服务、检查健康后，current 指针恢复到 `20260923T163300Z`。部署不执行 down、remove-orphans、删除卷或清理镜像。

## 自动化验证与缺陷证据

[CI 36432783520](https://github.com/llongwang751-arch/mini-drop/actions/runs/36432783520) 13/13 作业通过，Python 1000 passed / 7 expected skips；对应测试 merge `cec151cb360577affc9a4bc3afd67187dbed369d`。PostgreSQL、Chroma、Go race、浏览器与短负载分别执行。最终本机 Python 门禁同为 1000 passed / 7 expected skips，关键覆盖率无违约，执行期间源码指纹一致。

[本机回归原始包](../business-acceptance/deployment-20260928/local-regression-evidence.zip) 同时保存最初的 986 passed / 7 skipped / 2 failed 与最终报告。两个真实子进程失败未再现，根因尚未定位；只增强 stderr/HTTP 失败详情，不宣称已根治，未增加自动重试掩盖失败。

本轮新增 63 项进程 CPU 判据回归，覆盖完整 /proc 计数、真实时间端点、进程身份、有限整数、显式单域阈值与不同窗口拒绝。CPU 系统计数可以构成独立对照，但不能单独证明某函数导致性能问题。严格故障恢复还要求基线、故障、恢复六次快照的宿主进程与容器生命期一致，不把进程重启误认为恢复。

## 历史成绩口径

[历史逐场景矩阵](../business-acceptance/deployment-20260928/historical-matrix.md) 从 20260920 原始报告重新核对，21 份 case 的规范化 SHA 均验证：lineage 10/21、严格通过 1/21、撤销后恢复与清理 21/21。以前文档“链路没有失败项”不准确，已修正文档；原报告保持不变。9 月 10 日页面旧索引的 lineage 12/21 来自其他批次，不能混用。

本轮新成绩只使用新的独立 campaign，失败也保留；故障撤销不是同负载代码修复。新完整结果和双机一小时数据仍在执行，本文尚不宣称新根因分数或长稳通过。

## 第一轮真实 Pilot（失败保留）

[新部署 Python/Go pilot](../ai-diagnosis/fault-plaza-strict-pilot-20260928.json) COMPLETED，严格 **0/2**；lineage、同进程六次身份观测、撤销后恢复、清理和会话收敛均 **2/2**。Python 实际观察 `source_hot_function` 33.3% 聚合栈占比；Go `main.runCPUFault` 93.8%、925 样本。函数定位有结果，但报告仍为 PARTIAL_WITHOUT_COUNTER，因此不算根因验证通过。

Python 的真实 `/proc` 计数从 1106032 增至 1107270、Hz100、14.007 秒，算出 88.384379% 单核利用率，15 个样本，身份和任务时间包含校验通过。它没有回答初始模型写出的“Python 栈均匀或系统库主导”反证；后续采集又绑定新建系统基线假设，无法成为原 CPU 假设的 CONTROL。问题在可执行计划与证据归属，不能靠放宽 CPU 来源或根因门禁解决。

修复方向是在计划准入阶段要求 CPU 升高类假设具有可执行独立反证，对未满足的模型方案明确反馈；仅在原假设已声明精确 CPU 阈值且有真实支持时，沿现有策略、预算、截止时间与幂等路径对原假设补采。旧报告和假设不会重写；效果须由新发布的新 campaign 判断。


实际报告契约要求每个假设唯一且不可变，补采必须在首次报告之前。新增 worker 流程测试使用真实谓词、Evidence Gate 和报告持久化，仅模拟远端采集边界，验证补采等待、拒绝/失败收束、低 CPU 反证与旧报告保护。同假设两次取证仍只算一轮，不能冒充完成严格协议的三轮要求。模型计划准入的真实前后证据保留原 proposal，不自动删除、替换旧反证。


## 二次发布与完整复验

当前版本为 `20260928T142312Z`，源码 `240019025473b3e85f1e7828251afcbdc0f96284`；首次发布保留为回滚目标。两服务健康，各183个文件逐字节匹配清单，其他11个运行容器不变，API三依赖healthy。[二次部署元数据](../business-acceptance/deployment-20260928/runtime-v2-deployment.json)、[运行源码核对](../business-acceptance/deployment-20260928/runtime-v2-runtime-source-verification.json)、[精确源码包](../business-acceptance/deployment-20260928/runtime-v2-source-evidence.zip) 均已归档。

[CI 36435478810](https://github.com/llongwang751-arch/mini-drop/actions/runs/36435478810) 13/13通过，Python1071 passed / 7 expected skips，critical coverage无违约；测试merge `eeeda1c92627a4533f34b59478039e9157ed7368`，执行前后源码哈希一致。[CI清单](../business-acceptance/deployment-20260928/ci-followup-run.json) 与原始JUnit/日志/覆盖率分别保留，不覆盖首版CI。新CPU合同模块23条语句和10个分支均覆盖；覆盖率本身不代表模型规划一定通过实测。

完整21场景从2026-09-28T14:30:46Z开始在此冻结版本执行，正常根因失败继续采集，只有清理或会话收敛不安全才中止。完成前不预填新分数。

## 双机小时实验结果

完整实验与原始证据见[双机负载](../../docs/DISTRIBUTED_LOAD.md)。19,950/19,950请求成功且固定质量检查通过，资源增长与完整性检查通过；恢复P95 267.92ms、持续120窗中5窗超限，因此整体FAILED。持续总体P95 143.88ms不能覆盖失败窗口。测量源码冻结到退出后再修浮点分桶，旧报告未重算；公网/隧道/未插桩耗时仍未完成单独归因。
