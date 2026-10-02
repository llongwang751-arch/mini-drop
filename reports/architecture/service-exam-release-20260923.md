# 服务体检式诊断页面发布（2026-09-23）

导师会议纪要要求结论先行、证据链清晰、树状可视化和少用术语。本次将已接入服务作为普通用户入口，保留“不填写故障也能检查当前状态”和选中真实业务请求的能力。结果页把目标绑定、证据采集、报告生成压成三步进度；先显示本次窗口判断与进程 CPU/RSS/线程/FD，再默认展示服务端持久化的父子排查树。评分、工具原始记录、Skill 路线、Agent 过程和逐轮调查记录按需展开。采集、审批、报告门禁和原业务埋点未改；历史业务请求与后续进程采样仍是两个时间窗。

Web 全量 43 个文件、196 项测试通过；`npm run build:check` 通过。发布包 SHA-256 为 `734adc58e63ddb5f5f2e5beb4ba1cbb1bfdc671a5e223033ead64889bf75a693`。云端先滚动 Web 到 `20260923T112200Z` 做实拍检查，随后为收紧节点信息和把树提前而发布最终 `20260923T112700Z`；最终 `/opt/mini-drop-current` 指向后者，Web healthy。Worker、Analyzer、Chroma 沿用 `20260923T101442Z`，AGI-saber 沿用 `20260923T105939Z`。公网健康接口三依赖 healthy，已接入服务为 `OBSERVED`，真实业务请求状态 `AVAILABLE`；历史真实诊断 `insight_31065996e81542658cd1800f2bdb00a4` 的浏览器验证通过且无 JS/HTTP 错误，实拍见本地 `output/local-sre-20260919/browser-1790162782469/real-local.png`。该报告仍为 `INSUFFICIENT_EVIDENCE`，页面准确显示“本次观测窗口未确认故障”。

本轮是 Web 交互改版，没有重新注入故障或重新跑 AGI-saber 性能修复对照；不能将历史正常窗口、页面可达或单个真实案例当作故障诊断准确率。若需回滚 Web，使用 `/opt/mini-drop-releases/20260923T112700Z/private/rollback.compose.json` 恢复 `20260923T112200Z` 镜像，并将 current 指针原子指回该发布。旧发布、数据库卷、对象存储和 Evidence 均保留。

## 最终链路修复与复测

首版 Web 发布后的公网实测发现真正的阻断点：点击 AGI 办公助手后台“检查当前状态”会创建并绑定目标进程，但规划器把没有异常症状的正常检查当成信息不完整的故障单，记录 `planner.needs_clarification`，不下发采集。失败会话 `insight_cf9c16e0c4e24ea59d33c3883d412bf3` 之后因预算到期为 `CANCELLED`，0 Tool Call、0 Evidence、0 Report；保留该记录，不能将其算作通过。

服务入口现显式传 `health_check=true`。该意图随 `diagnosis.created` 持久化，Worker 用一次 `collect_sys_metrics` 建立当前窗口基线，不再要求用户虚构 CPU/延迟异常，也不调用故障根因模型或 Skill 路线。单次体检的预算为 120 秒、1 个工具、1 轮；具体故障仍使用原有多轮循证路径。报告 Evidence Gate 未放宽。最终发布包 SHA-256 为 `4e549a5eb08d95a0901893ed2345f4752b4b95d189a4e7b59f3e515f5515769c`，云端 `/opt/mini-drop-current` → `20260923T114300Z`，Diagnosis Worker 与 Web 使用本发布镜像，Analyzer/Chroma 保持 `20260923T101442Z`，AGI-saber 保持 `20260923T105939Z`。

最终从公网真实浏览器点击“选择服务 → AGI 办公助手后台 → 检查当前状态”，新会话 `insight_cdece16dd6da4944a3b38987b58abc0c` 约 29 秒完成：目标 PID 3102610、1 次 Tool Call、2 条 Evidence、1 份 Report，页面显示 CPU 0.3%、RSS 102.8 MiB、线程 5、FD 10、默认排查树和“本次观测窗口未确认故障”。浏览器无 JS/HTTP 错误，截图见本地 `output/cloud-sre-20260923T112700Z/browser-flow/normal-flow.png`。Report 状态仍为 `INSUFFICIENT_EVIDENCE`，不说明服务永久健康。Python 关联回归 55 项通过，Web 定向 6 项通过；Web 完整 196 项与构建门禁在最终发布前通过。公网三依赖 healthy，服务为 `OBSERVED`，请求观测为 `AVAILABLE`。

最终回滚使用 `/opt/mini-drop-releases/20260923T114300Z/private/rollback.compose.json` 恢复上一 Worker/Web 镜像，再将 current 指针原子指回 `20260923T112700Z`。上一发布没有健康检查专用规划，因此回滚会重新出现正常检查阻断；旧发布、数据库卷和 Evidence 必须继续保留。本轮没有修复 AGI-saber 的性能问题，也没有执行同负载修复复测或生产准确率评估。

最终代码重新执行完整回归：Python 616 passed、7 skipped；Web 43 个文件、196 项全部通过；Web 生产构建和 bundle 检查通过。
