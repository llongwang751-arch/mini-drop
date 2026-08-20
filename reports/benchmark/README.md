# Mini-Drop 统一诊断测试结果

最新正式评测：2026-08-10

## 正式实验协议

- 测试集：10 个故障场景。
- 策略：`CONSTRAINED_HYBRID`、`DECISION_TREE`、`EXPLORATORY`。
- 重复：每个场景、每种策略运行 3 次，共 `10 × 3 × 3 = 90` 次。
- 执行方式：每次均通过正式 Web API 启动真实、有界、可恢复的故障 Campaign。
- 证据链：保存基线、故障、恢复快照及隐藏 Oracle 比对结果。
- 评分隔离：诊断输入不包含标准答案，Oracle 仅由离线评分器读取。
- 质量门禁：缺失或被拒绝的产物不计作证据；失败任务不声明根因。

## 结果摘要

- 计划执行：90
- 唯一执行：90
- 原始 Campaign：90
- Campaign 完成：84
- Campaign 真实失败：6
- 重复/计划外/缺失执行：0 / 0 / 0
- 虚构失败 ID：0
- 完整性门禁：PASS

| 策略 | 次数 | 平均分 | 根因精确率 | 必需证据覆盖率 |
|---|---:|---:|---:|---:|
| CONSTRAINED_HYBRID | 30 | 87.89% | 86.67% | 70.00% |
| DECISION_TREE | 30 | 83.67% | 80.00% | 70.00% |
| EXPLORATORY | 30 | 85.78% | 83.33% | 70.00% |

6 次失败全部发生在内存故障确认阶段。故障进程 RSS 未超过基线，系统按证据门禁终止流程并执行无条件清理；这些失败保留在正式结果中，用于反映故障注入稳定性，而不是被重跑覆盖。

完整证据与复现方式见 [`official-90/README.md`](official-90/README.md)。

## 历史 official-90 复现命令

```powershell
python scripts/run_official_campaign.py `
  --base-url http://localhost `
  --output-dir reports/benchmark/official-90 `
  --timeout 120
```

该运行器支持断点续跑；已有执行 ID 会跳过，基础设施连接失败会直接退出，不会被记录成策略失败。

## OTel orchestrator-backed live subset

状态：**READY TO RUN / NOT YET CLOUD-VERIFIED**。

新的独立子集只覆盖 `T1-CPU-001` 与 `T1-MEM-001`，每个案例执行 3 种策略、每种策略重复 3 次，共 18 个唯一 execution ID，来自 6 个有界 campaign window。执行器通过正式 LIVE Diagnosis API 获取终态结论，不使用历史 Campaign 的静态 `CASE_RUNTIME` 或 `SUPPORTED_TAGS` 构造答案。

```powershell
python scripts/run_live_subset.py `
  --otel-root external/opentelemetry-demo `
  --base-url http://localhost `
  --agent-id <online-agent-id> `
  --approve-r2 `
  --output-dir reports/benchmark/live-subset
```

只有 18 个 submission 完整唯一、6 个 raw manifest 均已发布且清理无错误、Oracle 隔离检查通过时，才会生成最终 JSON/HTML 报告。`INSUFFICIENT_EVIDENCE` 是允许保留的真实诊断终态，不得改写为成功定位。该目录尚无正式云端结果，运行时不得覆盖 [`official-90/`](official-90/)。
