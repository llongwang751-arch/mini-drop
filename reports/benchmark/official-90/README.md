# Mini-Drop 正式 90 次策略评测

## 1. 这份目录证明什么

本目录保存 10 个故障场景、3 种诊断策略、每种组合 3 次重复实验的完整记录，共 90 个唯一执行 ID。每次实验都创建了独立 Campaign，并保留可追溯原始 JSON。

## 2. 最终结果

| 指标 | 结果 |
|---|---:|
| 计划数 | 90 |
| 唯一提交数 | 90 |
| 原始 Campaign 数 | 90 |
| 完成 | 90 |
| 真实失败 | 0 |
| 重复执行 ID | 0 |
| 计划外执行 ID | 0 |
| 缺失执行 ID | 0 |
| 完整性门禁 | PASS |

| 策略 | 次数 | 平均分 | 根因精确率 | 快照角色覆盖率 | 必需证据覆盖率 |
|---|---:|---:|---:|---:|---:|
| CONSTRAINED_HYBRID | 30 | 92.00% | 90.00% | 100.00% | 80.00% |
| DECISION_TREE | 30 | 92.00% | 90.00% | 100.00% | 80.00% |
| EXPLORATORY | 30 | 92.00% | 90.00% | 100.00% | 80.00% |

三种策略的无依据结论率均为 0%，证据引用完整率为 100%，根因精确率为 90%。当前确定性场景下三种策略得分相同，说明故障夹具和取证链路已经稳定，但还不能据此宣称某种策略优于其他策略。

## 3. 内存场景补跑说明

首轮有 6 次 `T1-MEM-001` 因进程 RSS 高水位和内存释放时序波动未通过故障确认。修复后的夹具会在每轮开始前释放保留对象、执行 GC 和 `malloc_trim`，并等待真实保留内存达到稳定阈值后再取故障快照。6 个失败执行位已逐项补跑，最终 9 个内存执行全部通过。首轮与补跑记录分别保存在 `cloud-20260822/` 和 `cloud-20260822-fixed/`，便于追溯。

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
- 必需证据平均覆盖率为 80%。CPU 热点、内存保留和噪声邻居证据由原始快照字段重算；下游连接错误边与 I/O 延迟/同机争用仍缺少对应观测，因此没有补写标签，差距继续保留在评分中。
- 本报告证明 90 次策略评测已实际执行并通过确定性门禁，不等同于线上所有未知故障都能正确诊断。
