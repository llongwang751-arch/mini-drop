# 重复进程名的目标实例修复（2026-09-30）

完整21场景验收发现3项诊断选错Agent。Java GC场景在Control注入，自动范围却选择Worker2；两台主机都报告真实Java进程，进程名不能唯一确定用户要诊断的实例。历史失败证据保留在原campaign，不回写成绩。

## 行为与边界

故障入口现在从运维配置`MINI_DROP_FAULT_LAB_AGENT_ID`传递`target.agent_id`；缺少配置时在注入前拒绝。该字段只是范围筛选，不携带实验室快照PID，也不授予直接采样权。通用进程发现读取会话中请求的Agent，并拒绝调用方以另一个Agent覆盖。指定实例不可用或快照过期时不能跨主机回退。

显式进程名在筛选后仍匹配多个候选时保持NEEDS_CLARIFICATION，不交给模型或能力分数猜选。没有显式进程名的普通自然语言自动发现继续沿用原行为。最终选择仍须通过已有不可变Agent快照、boot/start ticks/namespace/executable、版本与时间校验；替换快照时只重试受限竞态，不退回旧PID。

此修复解决实例选择，不能证明Java GC根因、提高所有故障准确率或完成同负载修复验证。受控故障的四种运行时目前由同一个运维登记Agent负责；跨主机实验室分配仍需显式部署配置设计。

## 可复现验证

新增12项负向用例在旧生产代码上全部失败，原30项通过。修复后自动范围、故障入口和管理服务52项通过；增加快照替换竞态后自动范围27项通过。全量本机原收集1186通过/7项依赖跳过；新增竞态专项通过，最终完整提交由CI重新验证。

测试使用实际数据库/Repository登记两台不同Agent与可信快照，覆盖正确绑定、缺失实例、过期快照、同机同名歧义、冲突筛选和绑定瞬间快照替换。全部失败日志、修复后日志和JUnit保存在`reports/quality/instance-scope-20260930/`。

代码e8e32d9发布为20260930T091326Z，Worker/Analyzer各183文件SHA通过，其余11容器未重建。CI36694384788全部13作业成功，Python1190通过/7预期依赖跳过。原误选Java GC、锁竞争和文件I/O三项新批次COMPLETED：目标选择正确3/3，注入/撤销恢复/清理/会话收束各3/3，lineage1/3，严格根因0/3。完整21成绩没有被替换或拼接。原始报告为`reports/ai-diagnosis/fault-plaza-instance-scope-3-deployed-20260930.json`。

## 复验发现的Skill激活竞争

Java GC规划HTTP500源于`uq_diagnostic_skill_activation_diagnosis_skill`冲突：后台和HTTP两条规划路径均先读空再插入。会话仍有三条完成的采集任务并正常收束，不把接口错误改成通过。候选锁定诊断父行后读取现有激活，保护不存在的首次记录和复用trace的读改写；锁已有activation不能保护首插入，所以不采用该办法。没有数据库迁移、唯一约束放宽或泛化异常吞掉。

使用当前PostgreSQL镜像的独立tmpfs测试容器，专用mini_drop_test库及随机测试schema；生产库和卷未接入。旧代码真实触发相同UniqueViolation，初版修复测试仍失败并保留，正确事务范围修复后1项竞争测试通过：两个请求都成功，只留一条记录、两轮trace均保留。临时容器与SSH隧道已清理。21项Skill/Worker回归通过；带覆盖率完整质量门禁1190通过/8项已登记依赖跳过、无覆盖率违约，PG专项已另行实跑。候选待CI、部署与独立新会话验收。证据见同目录`postgres-race-*`、`skill-race-quality.zip`及`worker-rpc-errors.jsonl`。
