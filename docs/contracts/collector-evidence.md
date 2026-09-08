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

## 2. 当前八类任务采集契约与一个 C++ opt-in 桥接器

| Collector | Analyzer Type | 必要产物 | 可作为分析结果的产物 |
|---|---|---|---|
| `perf_cpu` | `collector.perf_cpu` | raw 或火焰图/TopN | flamegraph JSON/SVG、TopN、调用图、建议 |
| `ebpf_io` | `collector.ebpf_io` | `ebpf_metrics` | IO 延迟分布 |
| `pyspy` | `collector.pyspy` | `flamegraph_svg` | Python 火焰图 |
| `continuous_perf` | `collector.continuous_perf` | Bundle、raw 或 summary | 时间窗摘要、窗口火焰图、TopN、调用图 |
| `java_async` | `collector.java_async` | `java_flamegraph_html` | Java 火焰图 |
| `go_pprof` | `collector.go_pprof` | `pprof_raw` | pprof 原始数据、SVG |
| `memory_smaps` | `collector.memory_smaps` | `memory_json` | 内存趋势 |
| `sys_metrics` | `collector.sys_metrics` | `sys_metrics` | `sys_metrics.v2` 指标 |

`native/gperftools_bridge` 是受限 C/C++ 环境的独立 opt-in 工具，还不是一个可由普通 TaskKind 自动选择的完整 Analyzer 契约。它要求应用链接/预加载 libprofiler、设置 `CPUPROFILE` 和 `CPUPROFILESIGNAL`，并由同 UID 非 root 身份触发。完成 Linux 容器验收和 gperftools profile 到统一火焰图的转换前，不能把“桥接器存在”表述为端到端 C++ 采集已完成。

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

Go `/api/metrics` 使用 Prometheus 文本格式暴露当前 API 进程的请求总数、5xx 总数和在途请求数。
AnalysisJob 状态、任务事件、Evidence 判定和 Outbox 状态仍需从 PostgreSQL、审计接口与结构化日志
核对；跨组件业务指标和 OpenTelemetry Trace 仍是后续缺口。

## 5. 扩展新采集器

新增 Collector 时必须同时完成：

1. 在 Agent 注册 Collector；
2. 在 `artifact_contracts.py` 声明输入、必要产物、分析产物和版本；
3. 在 Evidence Adapter 声明最低样本阈值；
4. 增加正常产物与错误产物契约测试；
5. 在真实 Linux 环境验证一次成功和一次异常路径。
