# Mini-Drop 采集器分析与 AI 证据契约

更新时间：2026-07-29  
契约版本：`1.0.0`

## 1. 为什么需要契约

Agent 上传的文件不都等于“可以支撑结论的证据”。例如：

- `perf.data`、`ebpf_raw` 是原始采集文件；
- 火焰图、TopN、IO 延迟分布是分析结果；
- 文件存在不代表目标、时间、样本量和采集质量都可信；
- 未经 Analyzer 验证的文件不能直接交给 AI 下结论。

因此系统用版本化契约连接三层：

```text
Collector 输出 -> Analyzer 校验/转换 -> Evidence Adapter 质量判定
```

## 2. 当前八类契约

| Collector | Analyzer Type | 必要产物 | 可作为分析结果的产物 |
|---|---|---|---|
| `perf_cpu` | `collector.perf_cpu` | raw 或火焰图/TopN | flamegraph JSON/SVG、TopN、建议 |
| `ebpf_io` | `collector.ebpf_io` | `ebpf_metrics` | IO 延迟分布 |
| `pyspy` | `collector.pyspy` | `flamegraph_svg` | Python 火焰图 |
| `continuous_perf` | `collector.continuous_perf` | `continuous_summary` | 时间窗摘要、火焰图、TopN |
| `java_async` | `collector.java_async` | `java_flamegraph_html` | Java 火焰图 |
| `go_pprof` | `collector.go_pprof` | `pprof_raw` | pprof 原始数据、SVG |
| `memory_smaps` | `collector.memory_smaps` | `memory_json` | 内存趋势 |
| `sys_metrics` | `collector.sys_metrics` | `sys_metrics` | `sys_metrics.v2` 指标 |

契约外产物、缺少必要产物都会让 AnalysisJob 重试；持续失败后进入死信，不会把父任务伪装成成功。

## 3. AI 证据门禁

导入 Drop Insight 前检查：

1. Task 与 TaskAttempt 已完成；
2. Artifact 属于对应 Collector 的分析结果类型；
3. Artifact ID 出现在成功 AnalysisJob 的输出引用中；
4. 诊断目标 Agent/PID 匹配；
5. 时间窗口重叠；
6. 不是 degraded 采集；
7. 样本数量达到采集器自己的阈值。

分类结果：

- `ACCEPT_SUPPORT`：可以支撑结论；
- `ACCEPT_LIMITED`：可以展示和引用，但不能提高结论置信度；
- `REJECT`：原始文件、契约不匹配、未验证、空样本、目标或时间不匹配。

旧数据仅在没有 AnalysisJob 且 Artifact 明确携带历史
`analyzer_version` 时按兼容模式导入；新任务必须走 AnalysisJob。

## 4. 可观测性

`/api/metrics` 暴露：

- `mini_drop_analysis_jobs_by_status`：从数据库实时汇总，跨 Server/Worker 进程可靠；
- `mini_drop_analysis_jobs_total`：当前进程观察到的生命周期事件；
- `mini_drop_analysis_job_duration_seconds`：Worker 本地处理耗时；
- `mini_drop_ai_evidence_decisions_total`：AI 证据接受、受限、拒绝计数。

## 5. 扩展新采集器

新增 Collector 时必须同时完成：

1. 在 Agent 注册 Collector；
2. 在 `artifact_contracts.py` 声明输入、必要产物、分析产物和版本；
3. 在 Evidence Adapter 声明最低样本阈值；
4. 增加正常产物与错误产物契约测试；
5. 在真实 Linux 环境验证一次成功和一次异常路径。

