# 服务体检式诊断页面发布（2026-09-23）

导师会议纪要要求结论先行、证据链清晰、树状可视化和少用术语。本次将已接入服务作为普通用户入口，保留“不填写故障也能检查当前状态”和选中真实业务请求的能力。结果页把目标绑定、证据采集、报告生成压成三步进度；先显示本次窗口判断与进程 CPU/RSS/线程/FD，再默认展示服务端持久化的父子排查树。评分、工具原始记录、Skill 路线、Agent 过程和逐轮调查记录按需展开。采集、审批、报告门禁和原业务埋点未改；历史业务请求与后续进程采样仍是两个时间窗。

Web 全量 43 个文件、196 项测试通过；`npm run build:check` 通过。发布包 SHA-256 为 `734adc58e63ddb5f5f2e5beb4ba1cbb1bfdc671a5e223033ead64889bf75a693`。云端先滚动 Web 到 `20260923T112200Z` 做实拍检查，随后为收紧节点信息和把树提前而发布最终 `20260923T112700Z`；最终 `/opt/mini-drop-current` 指向后者，Web healthy。Worker、Analyzer、Chroma 沿用 `20260923T101442Z`，AGI-saber 沿用 `20260923T105939Z`。公网健康接口三依赖 healthy，已接入服务为 `OBSERVED`，真实业务请求状态 `AVAILABLE`；历史真实诊断 `insight_31065996e81542658cd1800f2bdb00a4` 的浏览器验证通过且无 JS/HTTP 错误，实拍见本地 `output/local-sre-20260919/browser-1790162782469/real-local.png`。该报告仍为 `INSUFFICIENT_EVIDENCE`，页面准确显示“本次观测窗口未确认故障”。

本轮是 Web 交互改版，没有重新注入故障或重新跑 AGI-saber 性能修复对照；不能将历史正常窗口、页面可达或单个真实案例当作故障诊断准确率。若需回滚 Web，使用 `/opt/mini-drop-releases/20260923T112700Z/private/rollback.compose.json` 恢复 `20260923T112200Z` 镜像，并将 current 指针原子指回该发布。旧发布、数据库卷、对象存储和 Evidence 均保留。
