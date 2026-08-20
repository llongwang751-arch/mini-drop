# Mini-Drop 正式 90 次策略评测

## 1. 这份目录证明什么

本目录保存 10 个故障场景、3 种诊断策略、每种组合 3 次重复实验的完整记录，共 90 个唯一执行 ID。每次实验都创建了独立 Campaign，并保留可追溯原始 JSON。

## 2. 最终结果

| 指标 | 结果 |
|---|---:|
| 计划数 | 90 |
| 唯一提交数 | 90 |
| 原始 Campaign 数 | 90 |
| 完成 | 84 |
| 真实失败 | 6 |
| 重复执行 ID | 0 |
| 计划外执行 ID | 0 |
| 缺失执行 ID | 0 |
| 完整性门禁 | PASS |

| 策略 | 次数 | 平均分 | 根因精确率 | 快照角色覆盖率 | 必需证据覆盖率 |
|---|---:|---:|---:|---:|---:|
| CONSTRAINED_HYBRID | 30 | 87.89% | 86.67% | 98.89% | 70.00% |
| DECISION_TREE | 30 | 83.67% | 80.00% | 96.67% | 70.00% |
| EXPLORATORY | 30 | 85.78% | 83.33% | 97.78% | 70.00% |

三种策略的无依据结论率均为 0%，证据引用完整率均为 100%。受约束混合策略在本轮平均分和根因精确率上最高。

## 3. 六次真实失败

失败均属于 `T1-MEM-001`：内存故障注入后 RSS 没有高于基线，Campaign 在“故障确认”门禁停止，并执行无条件清理。失败样本仍占正式 90 次中的一个执行位，用于暴露注入器稳定性。

- CONSTRAINED_HYBRID：第 3 次
- DECISION_TREE：第 1、2、3 次
- EXPLORATORY：第 1、3 次

失败记录没有快照就不计证据，也不会被评分器填入预期根因。

## 4. 文件说明

- `run-plan.json`：90 次执行计划和计划指纹。
- `submissions.json`：评分器的正式输入。
- `raw-campaigns/`：90 个原始 Campaign，逐次审计故障、快照、比较和清理过程。
- `evaluation-report.json`：正式结构化评分报告。
- `evaluation-report.md`：便于阅读的策略摘要。
- `evaluation-confirmed.json`：使用 CLI 再次执行完整性门禁后的独立结果。
- `SHA256SUMS.txt`：上述文件及 90 个原始记录的 SHA-256 校验值。

## 5. 复现与校验

在项目根目录 `D:\tx\mini-drop` 的 PowerShell 执行：

```powershell
python scripts/run_official_campaign.py `
  --base-url http://localhost `
  --output-dir reports/benchmark/official-90 `
  --timeout 120

python scripts/diagnosis_benchmark.py status `
  reports/benchmark/official-90/submissions.json

python scripts/diagnosis_benchmark.py evaluate `
  reports/benchmark/official-90/submissions.json `
  --require-complete `
  --output reports/benchmark/official-90/evaluation-confirmed.json
```

预期 `complete=true`、`unique_recorded=90`、`remaining=0`。运行器为断点续跑设计：已完成的执行 ID 会显示 `SKIP`。

## 6. 结果边界

- I/O 场景当前是目标进程自身同步写入，与测试 Oracle 的“共享宿主机资源争用”语义并不完全一致，因此该场景分数较低；报告没有掩盖该差距。
- 6 次内存注入失败说明内存故障夹具仍需增强稳定性。
- 本报告证明 90 次策略评测已实际执行，并不表示所有场景均通过。
