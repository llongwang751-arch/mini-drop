# 诊断 Agent 墙钟预算与证据缺口改进

## 问题与改动

上一版 LATS 会话 `insight_7f96b792aaba448ab690db15428d73f8` 在第三个探针期间达到 300 秒，被取消。初始 UNDERSTANDING 阶段消耗约 114 秒，不能把该状态的全部时间认定为范围选择单次调用；采集资源预算没有计算模型与队列已经消耗的墙钟时间。

本批不提高会话总时长。范围选择阶段最多 25 秒，单轮规划最多 60 秒；主模型每次请求最多 45 秒且受该阶段剩余时间限制。最终计划输出上限 1000 tokens，提示以简短条件描述并在剩余不足 25 秒时直接提交计划。RAG 查询、计划语义纠错不重置截止时间。摘要调用绕过主模型包装器，因此单独按剩余时间绑定至多 10 秒 timeout，并使用中间件副本避免并发修改缓存模型。

工具准入与审批后实际执行均检查剩余时间能否覆盖探针时长及 30 秒分析/报告余量。无足够时间时释放预留、停止扩展，不产生假根因；已有未验证报告按现有终结规则保留。HTTP timeout 是请求阶段约束，不是硬实时 watchdog；队列、数据库、流式网络拖延仍由会话过期扫描兜底。

Verifier 显式输出未覆盖支持条件索引、证伪条件索引和独立对照缺口。工作记忆将原始验证结果供下一轮读取，提示先解释补哪个缺口再选允许工具。此次尚未实现确定性的缺口→工具排序，也没有改变 LATS 单步真实工具的执行语义。

## 验证

- 本地相关测试共 156 passed、2 skipped。覆盖晚到审批不创建 Task、阶段内请求共享剩余时间、摘要绑定与并发副本、预算终结不升级为已验证、原有轮次与 LATS 行为。
- 两个跳过项为本机 Chroma SDK 相关测试；云端使用实际 Chroma/Embedding/Reranker 的检索门禁。
- 部署源包 309 文件，SHA-256 `04dc890b9844b4453ff3cf0ab21afd468ee20a2f5e8af8426bc17e16338c1a85`。
- 本批仅构建 Diagnosis Worker 增量层，包含 service、claim_verifier 与 deadlines；无数据库迁移。发布目录 `20260919T141100Z`，前版 `20260919T133900Z`。
- 首轮云端 `insight_8e3110d5bef740798e6dfbf9f03e19c6`，约 180 秒完成，3/3 工具成功，全部任务有效产物、准入证据、真实混合检索、故障清理均通过。但三轮模型均 OpenAITimeoutError，全部规则兜底，因此完整验收 `passed=false`，不能称作 Agent 成功。报告仍为两份 PARTIAL_WITHOUT_COUNTER、一份 INSUFFICIENT_EVIDENCE，没有 VERIFIED。
- 首轮采用单轮 45 秒/单请求 30 秒，知识查询后最终计划剩余时间不足；修订至单轮 60 秒/单请求 45 秒并缩短输出，仍保留总 300 秒与采集/报告余量。
- 修订源包 SHA-256 `7cec5a0c203a859099801bef5244b2fc220690f908d55285d82a649d5b196825`，发布目录 `20260919T141800Z`；首轮镜像及失败记录保留。

## 修订版真实回归结果

会话 `insight_106b58e1477b48919b61199881240f12` 在 **191.12 秒**内完成三轮，未触发会话过期。三轮均有 MODEL / MODEL_REPLAN 假设。py-spy、系统指标、perf 三项工具均 COMPLETED，13 份产物、13 条 Evidence，所有任务产物完整性、准入证据存在、实际三路混合检索与故障清理检查通过，完整链路 `passed=true`。最终 lats.search_terminated 的 BUDGET_EXHAUSTED 对应该实验最多三轮的探索边界，不是 300 秒墙钟过期。

报告仍为两份 PARTIAL_WITHOUT_COUNTER、一份 INSUFFICIENT_EVIDENCE，**零 VERIFIED**。单场景单次成功只能说明该版完整链路可运行，不能证明稳定性、根因准确率或 LATS 优于 ReAct。本批没有重跑 ReAct 对照。

原始证据：本地 `output/cloud-sre-20260919/lats-deadline-r2.json`；云端本发布目录 `lats-deadline-r2.json`。首轮失败证据为本地 `lats-deadline.json` 和云端 141100Z 同名文件。发布后 HTTPS healthz 的 control_plane、database、diagnostic_ai 均 healthy，故障注入已停止。

下一优先级仍是 Python 采样的休眠线程/线程命名空间口径、同负载独立对照、证据缺口与工具能力的确定性匹配。工作记忆的 verification 目前保留完整报告验证对象，第二轮报告该对象可超过 20 KB，需进一步拆出有界条件索引投影、按需读取 claim 引用；此项尚未实现。完整节点内多步 ReAct、故障盲测和可靠修复复测闭环也仍未完成。
