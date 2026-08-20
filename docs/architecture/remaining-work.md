# Mini-Drop 剩余工作

更新时间：2026-08-10

本文只记录尚未关闭的事项。已实现并通过自动测试或当前 Docker 环境端到端验证的能力，不再列为缺口。

## 当前环境已经关闭的关键问题

1. **跨语言采集矩阵**：CPU/perf、Python/py-spy、Continuous Profiling、Java/async-profiler、Go pprof、eBPF I/O、内存趋势、系统指标 8 类采集任务均已完成一次统一端到端验证。
2. **状态机闭环**：任务可经过 `PENDING → RUNNING → UPLOADING → ANALYZING → DONE/FAILED`，状态迁移、原因和 TaskAttempt 均持久化。
3. **分析产物可信性**：原始产物与 Analyzer 生成的火焰图、TopN、建议等产物均计算 SHA-256，并通过完整性校验后标记为 `VERIFIED`。
4. **Python 采集**：增加稳定的 Python 热点演示目标，py-spy 能生成非空 speedscope、火焰图与 TopN。
5. **Java 采集**：Agent 镜像集成 async-profiler，解决容器挂载命名空间导致的输出文件不可见问题，可生成 Java HTML 火焰图。
6. **eBPF 采集**：适配当前 bpftrace 版本和 WSL2 内核类型声明，I/O 探针可生成非空统计和原始事件产物。
7. **性能样本计数**：perf 分析不再把硬件 event period 当成样本权重，AI 证据质量使用真实采样数。
8. **AI 循证闭环**：诊断假设能够绑定已完成的 TaskAttempt，导入经过 Analyzer 与哈希校验的证据，并生成带 `evidence_refs`、覆盖率、置信度和结论状态的报告。
9. **统一 AI 入口**：旧的 Drop Insight、集群诊断和历史入口已收敛到 `/ai-diagnosis`，避免多个相似页面重复表达同一流程。
10. **工程回归**：Python、Go、C++、React 测试及 Web 生产构建均已通过。
11. **真实 Web 故障实验 10/10**：统一测试集全部支持从 Web 一键注入、三段快照、任务取证、循证诊断、隐藏 Oracle 对比与 finally 恢复。新增噪声邻居使用独立同宿主机 peer 进程及 boot ID 证明共享宿主机；容量场景记录到达/完成速率、拒绝、队列和延迟；队列场景记录生产/消费速率和 lag。Task ID 只有在 TaskAttempt、`VERIFIED` Artifact 和 Analyzer Job 链路可追溯时才通过证据门禁。

## 已关闭：统一测试集 Web Campaign

`T1-NOISY-001`、`T1-LOAD-001`、`T1-QUEUE-001` 已于 2026-08-10 完成当前 Docker 环境端到端验证。正式 10 类测试已全部从离线定义升级为可注入、可采集、可诊断、可恢复实验。锁等待继续作为额外扩展场景，不替代正式编号。

## P0：需要外部原生 Linux 环境完成的最终验收

### 1. Ubuntu 22.04 原生矩阵

在一台干净的 Ubuntu 22.04 主机上执行完整采集矩阵，重点保留：

- perf 与 eBPF 所需的内核版本、capability、`perf_event_paranoid` 等环境信息；
- Java、Go、Python、C++ 真实目标进程的任务 ID；
- TaskAttempt、采集日志、Artifact 哈希和 Analyzer Job；
- eBPF 现场制造 I/O 或调度抖动前后的可视化差异。

当前 Docker Desktop/WSL2 已通过功能联调，但它不等价于独立 Ubuntu 生产主机最终验收。

## 已关闭：完整 90 次公开基准 Campaign

2026-08-10 已完成 10 类故障场景 × 3 种诊断策略 × 3 次重复，共 90 个唯一执行 ID。84 次 Campaign 完整完成，6 次内存故障确认失败被如实保留；不存在重复、计划外或缺失记录。正式报告、90 份原始 Campaign、提交记录和 SHA-256 清单位于 `reports/benchmark/official-90/`。

## P1：后续生产运维强化

1. 压测环境记录 Agent 心跳 P99、任务吞吐、SSE 连接数、Analyzer 峰值内存和大火焰图首屏时间。
2. 在部署环境配置 MinIO Lifecycle，并定期运行孤儿 Artifact 与哈希不一致巡检。
3. 需要真实组织管理时，引入持久化用户、用户组、密钥轮换和权限管理后台；当前 Principal 与资源范围校验足够完成演示和隔离验证。
4. 进行长时间 Continuous Profiling 稳定性、容量和存储成本测试。

## P2：后续交付能力

1. Kubernetes/Helm 或等价部署清单。
2. 多架构镜像、SBOM、依赖漏洞扫描和镜像签名。
3. Dashboard、告警规则、证书轮换和灰度回滚 Runbook。

## 验收口径

后续描述完成度时必须区分三个层级：

1. 代码已经实现并有自动测试；
2. 当前 Docker Desktop/WSL2 环境已完成端到端验证；
3. 原生 Ubuntu 或公开基准已完成现场实测并留存证据。

目前剩余的 P0 项只有第三层的原生 Ubuntu 真机验收，不是当前代码链路缺失。
