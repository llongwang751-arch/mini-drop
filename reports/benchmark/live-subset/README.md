# OTel Orchestrator-Backed Live Subset

状态：**READY TO RUN / NOT YET CLOUD-VERIFIED**（截至 2026-08-20）。

这是独立于历史 `official-90` 的真实 OTel 诊断子集，不覆盖或改写 `../official-90/`。

## 固定协议

- 案例：`T1-CPU-001`、`T1-MEM-001`
- 策略：`CONSTRAINED_HYBRID`、`DECISION_TREE`、`EXPLORATORY`
- 重复：每个案例和策略 3 次
- 总执行：18 个唯一 execution ID
- 故障窗口：6 个，每个 case/repetition 一个
- Oracle：仅在终态诊断冻结后由 evaluator 读取，runner 输入不包含标准答案

CPU 使用 Ad Java 的真实 gRPC 负载和 CPU 故障开关；MEM 使用 Email 的 HTTP 负载和 retained-memory 故障开关。两类窗口都要求 baseline task、incident-only 信号、受限时长、清理和恢复检查。MEM 还要求 Email 重启并确认 host PID 已变化。

## 执行

在云端原生 Linux 节点执行，目标 Agent 必须 `ONLINE`，并显式批准 R2：

```bash
python scripts/run_live_subset.py \
  --otel-root external/opentelemetry-demo \
  --base-url https://<control-host> \
  --agent-id <online-agent-id> \
  --approve-r2 \
  --cpu-project-name <cpu-compose-project> \
  --cpu-compose-file deploy/otel-demo/compose.common.yaml \
  --cpu-compose-file deploy/otel-demo/compose.t1-cpu-001.yaml \
  --memory-project-name <memory-compose-project> \
  --memory-compose-file deploy/otel-demo/compose.common.yaml \
  --memory-compose-file deploy/otel-demo/compose.t1-mem-001.yaml \
  --output-dir reports/benchmark/live-subset
```

`--finalize-only` 只校验已有结果，不会补跑缺失窗口。部分窗口、重复策略或失败窗口不得静默覆盖；完整窗口才允许续跑跳过。

## 发布门禁

最终报告只在以下条件全部满足时生成：

- 18 个 execution ID 完整、唯一、无计划外记录；
- 6 个 raw manifest 均存在，且每个 manifest 覆盖对应窗口的三种策略；
- 每个 manifest 标记 `publication.published=true`；
- 没有 `fixture_failure`，`cleanup.errors` 为空；
- diagnosis detail 保留真实 Orchestrator 终态和 `evidence_refs`；
- evaluator 保持 Oracle isolation。

`INSUFFICIENT_EVIDENCE` 是允许的真实诊断结果，不会被重写为根因命中。窗口状态 `PUBLISHED`、`FIXTURE_FAILED`、`SKIPPED` 的含义见 [operations guide](../../../docs/guides/operations.md#14-otel-orchestrator-backed-live-subset)。

## 预期产物

正式运行后，目录应包含：

- `run-plan.json`：18-run 计划和 fingerprint；
- `campaign-windows/*-manifest.json`：6 个窗口原始 manifest；
- `submissions.json`：18 个 submission；
- `evaluation-report.json` 和 `evaluation-report.html`：最终评分报告；
- `run-result.json`：完整性和窗口状态摘要。

当前仓库只记录协议和执行入口，尚无正式云端 submission、manifest 或评分结果。