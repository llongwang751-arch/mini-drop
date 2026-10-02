# 阶梯负载、恢复与持续运行实测（2026-09-27）

本轮代码 `be34ff5` 在[远程 CI](https://github.com/llongwang751-arch/mini-drop/actions/runs/36298996094) 的 12 个作业全部通过。Python 723 passed / 7 skipped；PostgreSQL 和 Chroma 依赖相关跳过由专项实际执行覆盖。新增 27 项负向/边界回归，本机定向合计 59 passed。

## 方法与边界

现有 SQLite FTS5 + 本地摘录样例在独立子进程中运行，固定模拟依赖耗时 10ms；压测端与服务共享本机。一次预热后同一 PID 经过阶梯、恢复、持续三个阶段，无远程 LLM、生产请求、云端注入或本机 Docker。

默认业务判据为 P95 ≤200ms、成功率与固定引用检查均 ≥99%；持续阶段每 30 秒独立判定。延迟从计划到达时刻算起，超时/失败进入分母；未发出请求不补造延迟，发压端饱和或迟到会判 INVALID。P99 仅在至少 100 个实际请求时给出。

环境：Windows-11-10.0.26200-SP0，Python 3.14.0，CPU 逻辑数量 24。固定源码/语料/问题集指纹和完整计划在机器报告内。没有目标 RSS/CPU/句柄测量，不能证明无内存泄漏或小时级稳定性。

## 本机实测

| 阶段 | 到达速率 | 时长 | 计划/实发 | P95 ms | 成功/引用检查 | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| step-1 | 5 /秒 | 15秒 | 75/75 | 40.38 | 100%/100% | PASSED |
| step-2 | 20 /秒 | 15秒 | 300/300 | 39.61 | 100%/100% | PASSED |
| step-3 | 40 /秒 | 15秒 | 600/600 | 39.70 | 100%/100% | PASSED |
| step-4 | 80 /秒 | 15秒 | 1200/1200 | 480.44 | 100%/100% | SLO_FAILED |
| recovery | 5 /秒 | 15秒 | 75/75 | 40.82 | 100%/100% | PASSED |
| soak | 5 /秒 | 600秒 | 3000/3000 | 39.85 | 100%/100% | PASSED |

持续阶段 20 个窗口，P95 范围 38.93–41.10ms；整体判定 `PASSED`。高负载有效发压时出现 SLO_FAILED 是容量探索结果，完整成功仍要求低档通过、恢复成功与持续阶段每个窗口达标。

连续通过的最高测试档位为 40 请求/秒，首个未通过档位为 80 请求/秒。这不是精确极限或生产容量；40 与 80 之间的速率尚未测试。

原始分阶段计时显示，40/80 请求每秒的队列等待 P95 分别约 8.27/448.18ms；降载后约 0.12ms。80 档 HTTP 与固定引用仍全部成功，延迟却不达标，说明只断言 HTTP 200 会漏掉此类回归。此结论来自样例阶段计时，不冒充 Mini-Drop AI VERIFIED 根因。

## 复现与证据

```powershell
python scripts/run_quality_gate.py --profile endurance
python scripts/run_load_endurance.py --output output/quality/endurance-new
```

第一条是 Linux CI 同款短回归（12 秒持续、P95 门槛 500ms），第二条才是本次默认 600 秒实测配置。输出目录必须全新；运行时不要修改源码或同时做其他重负载工作。

[完整测量报告](../business-acceptance/endurance-20260927/report.json) · [逐请求原始记录 ZIP](../business-acceptance/endurance-20260927/requests.zip) · [CI 作业索引](../business-acceptance/endurance-20260927/ci.json) · [CI 短版原始报告](../business-acceptance/endurance-20260927/ci-short-report.json)。

原始报告内容哈希 `1f79dcc072d1d33a3e8aa278c2c9fb0573bfac4cdbd2cc77c7c61af197241484`。逐请求 ZIP 含全部阶段 JSONL，各成员 SHA-256 在测量报告里；归档前逐项检查哈希、槽位完整性、计数、成功/质量分母和 P95。原始本机目录 `output/quality/endurance-600s-20260927/` 保留，没有覆盖短版或历史失败。

## 仍待完成

下一步是更长运行及目标进程 RSS/CPU/句柄采样、在独立压测机上缩小容量区间，以及从 Python 源码热点/Go CPU 热点补独立对照。21 个历史云端场景仍只有 1 项严格通过，本轮仅只读整理为 9 项链路缺口和 11 项根因缺口，未重跑云端；详见故障广场规范。
