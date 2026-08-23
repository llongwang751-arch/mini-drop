# 真实业务测试集执行状态

更新时间：2026-08-22（Asia/Shanghai）

## 本阶段已验证

- 7 个真实 PR 候选案例的 manifest、公开题面和私有 Oracle 一一对应。
- 公开执行计划不包含根因 ID、PR URL、base/fix SHA 等答案字段。
- 机制运行按 run 原子持久化；有效终态可在进程重启后恢复，遗留 `RUNNING` 会转为 `INTERRUPTED`。
- `MECHANISM_REPRO` 执行完成统一标记为 `COMPLETED + UNSCORED`，`passed=null`，不进入正式评分分母。
- 证据由服务端生成并绑定 run/case；评分器只认可哈希有效且引用可解析的结构化证据。
- 历史发布快照中的本地机械回归：Python 非保护文件套件 851 passed，保护文件安全选择 26 passed，Go 全套通过，Web 39 tests 通过且生产构建成功；这些不是当前改动后的复验结果。

## 当前真实运行状态

| 项目 | 状态 | 说明 |
|---|---|---|
| 候选案例 | 7 | 公开题面与私有 Oracle 一一对应 |
| 页面机制适配器 | 4/7 | `MECHANISM_REPRO + UNSCORED`，只验证机制与恢复路径 |
| 历史稳定上游 A/B 回放归档 | 1/7 | OTel Python #4224 固定 base/fix 各重复 3 次；这是已保存的历史证据，本次未重新执行 |
| 正式 Admission / 评分 | 0/7 | 尚无 `real-world-admission-v1` 正式准入记录，不生成准确率、排名或产品结论 |
| RCAEval / HolmesGPT 等同条件 RCA 对照 | 0 个有效诊断结果 | HolmesGPT 受供应商 401 阻塞；其余仍受环境或资源限制 |

## 验证边界

本阶段没有部署云端、注入故障或重新执行云端验收。上述历史 Pytest、Vitest 和 Web build 结果仅说明当时的本地机械回归，不是当前改动后的验证，也不替代三节点云端正式验收。

OTel 归档证明固定 base/fix 在归档环境中得到稳定观测，但缺少正式准入所需的可信运行信封、来源树与依赖承诺、不可变镜像摘要、执行时间戳、baseline/incident/verification 三角色证据，以及 Oracle 前冻结诊断。因此当前仍为 0/7 正式 Admission / 评分；没有正式同题运行就不生成通过率、对比排名或产品结论。
