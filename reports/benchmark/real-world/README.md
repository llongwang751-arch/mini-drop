# 真实业务测试集执行状态

更新时间：2026-08-21（Asia/Shanghai）

## 本阶段已验证

- 7 个真实 PR 候选案例的 manifest、公开题面和私有 Oracle 一一对应。
- 公开执行计划不包含根因 ID、PR URL、base/fix SHA 等答案字段。
- 机制运行按 run 原子持久化；有效终态可在进程重启后恢复，遗留 `RUNNING` 会转为 `INTERRUPTED`。
- `MECHANISM_REPRO` 执行完成统一标记为 `COMPLETED + UNSCORED`，`passed=null`，不进入正式评分分母。
- 证据由服务端生成并绑定 run/case；评分器只认可哈希有效且引用可解析的结构化证据。
- 本地机械回归：本次发布前验证的 Python 非保护文件套件 851 passed，保护文件安全选择 26 passed，Go 全套通过，Web 39 tests 通过且生产构建成功。

## 当前真实运行状态

| 项目 | 状态 | 说明 |
|---|---|---|
| 真实 PR base/fix replay | 0/7 | 尚未完成固定提交的完整上游 A/B replay |
| 页面机制适配器 | 4/7 可执行 | 只验证机制与恢复路径，全部不具备正式评分资格 |
| OTel 正式 18-run | NOT_EXECUTED | 本阶段未启动 |
| RCAEval / HolmesGPT 等对照 | NOT_EXECUTED | 尚未在同一输入、遥测与预算下执行 |

## 验证边界

本阶段没有部署云端、注入故障或重新执行云端验收。上述 Pytest、Vitest 和 Web build 仅用于本地机械回归，不替代三节点云端正式验收。

完整上游 replay 仍为 0；没有同题运行就不生成通过率、对比排名或产品结论。
