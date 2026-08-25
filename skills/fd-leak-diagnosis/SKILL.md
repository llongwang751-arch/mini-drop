---
name: fd-leak-diagnosis
description: 当文件描述符持续增长或出现 too many open files 时，定位未关闭的文件、Socket 和管道。
---

# 文件描述符泄漏诊断

## 目标
识别 FD 是否持续泄漏、主要资源类型、持有路径和修复后的恢复情况。

## 输入契约
- 必填：`agent_id`、`pid`、`environment`、`start_time`、`end_time`。
- 可选：进程 FD 上限、请求量和发布版本。

## 适用与停止条件
- 适用：FD 数持续增长、连接建立失败或出现 `too many open files`。
- 只有瞬时峰值、连接池扩容后稳定时不判泄漏。

## 取证顺序
1. 连续采集 FD 数与上限占比，计算增长斜率。
2. 按普通文件、Socket、管道和匿名句柄分类聚合。
3. 找出增长最快的目标及持有进程，检查 open/close 是否成对。
4. 关联调用路径或连接生命周期，停止故障后复测。

## 结论门槛
- FD 数连续增长且某类资源占比同步上升。
- 存在未关闭路径、连接生命周期或资源所有者证据。
- 修复后增长停止或资源被释放；否则保持疑似。

## 必需证据与反证
- 支持证据：FD 趋势、类型分布、资源目标、调用路径、恢复窗。
- 反证：连接池稳定、流量增长解释充分、统计包含已关闭句柄。

## 安全边界
只读取 `/proc` 和注册遥测，不自动关闭业务 FD，不修改进程资源上限。

## 输出契约
输出 `root_cause`、`resource_type`、`confidence`、`evidence_refs`、`counter_evidence_refs`、`limitations`、`next_action`。

## 回归门禁
覆盖文件、Socket、管道泄漏及连接池预热反例；证据污染或 PID 复用时必须拒答。
