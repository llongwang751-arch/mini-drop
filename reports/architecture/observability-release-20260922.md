# 诊断可观测摘要与业务指标发布（2026-09-22）

云端平台当前指向 `/opt/mini-drop-releases/20260922T100300Z`，其中 Web 为该标签镜像，Diagnosis Worker、Analyzer 和 Chroma 沿用本批 `20260922T094352Z` Python 镜像。办公助手已发布 `/opt/agi-office/releases/20260922T094352Z`。先发布的 `094352Z` 平台目录、再上一版 `20260920T185032Z`、办公助手上一版 `20260913T092221Z`、数据库、对象存储和历史证据均保留。

页面新增低密度观测摘要，默认显示窗口判断、目标进程身份、CPU、RSS、线程和文件描述符；主机指标、探针、Evidence 准入、业务指标及故障注入说明按需展开。结束窗口存在系统指标但没有支持根因的可信 Evidence 时，页面显示“本次观测窗口未确认故障”；这不修改 Report 门禁，也不表示持续健康。

办公助手 ASGI 入口累计 HTTP 请求、5xx、处理中请求、耗时及近期 P95，原子写入进程私有临时目录。记录中没有 URL、正文、响应或凭据。Native Agent 读取目标进程根目录中的有界快照，Analyzer 校验 PID 并只复制白名单数值。窗口内存在请求且平均耗时达到 500ms、近期 P95 达到 1000ms 或失败率达到 5% 时，才形成 HTTP 服务退化信号。

验证结果：Python 定向 8 项通过；Web 42 个文件、190 项测试通过；生产构建与 bundle 门禁通过。云端四项容器健康，公网 `/api/healthz` 返回 Control、PostgreSQL、Diagnosis Worker 全部 healthy；浏览器打开公网工作台和实际会话均无 JS/HTTP 错误。真实办公助手请求使进程快照请求计数增加，真实诊断 `insight_cf586d3ed6a742deb6bcc9756c4c8a2f` 的 `SYS_METRICS_SYS_METRICS` Evidence 含 `application_metrics_analysis.v1`、PID 3137797 和 `identity_verified=true`。该正常采样窗口业务请求增量为 0，准确保留为 0，没有补造流量。

终态平台源码包 SHA-256 为 `dc4deef173fe4a3e93ca16ddb873a8522376926a50729bc8d7c98feb0f16fc95`。首次终态截图发现页面误选同任务的 `SYS_METRICS_MANIFEST`，因此数字显示“未采集”；随后收紧为只选 `SYS_METRICS_SYS_METRICS` 主证据并新增 manifest 回归样本，重新发布 Web。最终公网截图显示进程 CPU 0.3%、RSS 85.4MiB、线程 3、FD 9。平台回滚执行本发布目录中的 `scripts/release_sre_cloud.py rollback`；办公助手回滚需把 `/opt/agi-office/current` 原子指回上一发布并重启 `agi-office-backend.service`。任何回滚都不删除卷或历史证据。
