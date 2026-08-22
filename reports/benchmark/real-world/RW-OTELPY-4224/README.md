# RW-OTELPY-4224 真实上游 PR 回放

- 上游：OpenTelemetry Python PR #4224
- 基线提交：`679297f5ebd37510b6c9e086fc27837935d57e81`
- 修复提交：`84c6b0a419226328b6884b43a61cfd7a8fa3b3bb`
- 环境：Ubuntu 24.04.4 / Linux 6.8 / Python 3.12.3
- 每轮创建并关闭 250 组 MeterProvider、PeriodicExportingMetricReader、Exporter，强制 GC 后统计弱引用存活数。
- 基线和修复各重复 3 次。

## 结果

基线三轮均残留 250 个 Reader 和 250 个 Exporter；修复后三轮 Reader、Exporter、Provider 均为 0。该案例满足“故障可复现、修复可验证、重复三次稳定”的入库条件。

`report.json` 是机器可读摘要，`base.ndjson` 与 `fix.ndjson` 是逐轮原始输出，`otel_gc_harness.py` 是两侧完全相同的中立测试程序。
