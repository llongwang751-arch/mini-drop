# Mini-Drop 云端生产链路验收记录（2026-08-25）

## 1. 验收范围

本次验收针对 `47.112.10.137` 上的云端部署，重点验证近期修复后的采集回调、TaskAttempt、Artifact、Analyzer Job、AI 诊断、隐藏 Oracle 对照和故障恢复是否处于同一条可追溯链路。

本记录只描述实际执行结果。它不把短时云端验收等同于长期生产压测，也不把工具已安装等同于所有语言采集器均已完成现场矩阵验收。

## 2. 版本与部署

| 项目 | 结果 |
|---|---|
| GitHub `master` | `6080727` |
| GitHub 发布分支 | `6080727` |
| 云端等价补丁提交 | `3509f93` |
| 数据库迁移头 | `20260825_0033` |
| 公网页面 | `https://47.112.10.137` 返回 HTTP 200 |

云端提交与 GitHub 提交号不同，是因为云端保留了部署环境的独立提交历史；本次功能补丁内容等价。

## 3. 自动化回归

执行范围：

```text
tests/test_hotmethod_analysis_lineage.py
tests/test_grpc_services.py
tests/test_analysis_jobs.py
```

结果：`56 passed`。Python 编译检查通过。当前开发环境未安装 Ruff，因此本轮没有执行 Ruff 检查。

覆盖的关键行为：

- 采集结果回调必须携带有效的 `task_attempt_authority`；
- 状态迁移、Artifact 和 AnalysisJob 绑定到同一个 TaskAttempt；
- 伪造或过期的回调被拒绝；
- Analyzer 入队失败时任务进入明确的 FAILED，而不是永久停在 ANALYZING；
- SQL 持久化链路和内存仓库保持相同行为。

## 4. 云端工具实测

| 工具 | 实测结果 |
|---|---|
| perf | `perf version 6.12.101`，`perf stat` 返回 0 |
| bpftrace | `v0.23.2`，BEGIN 真探针成功 attach 并返回 0 |
| py-spy | `0.4.2` |
| async-profiler | `4.4` |

这里证明工具在当前 Linux 容器环境中可执行。跨语言完整采集矩阵仍应单独保留任务、产物和可视化证据。

## 5. LIVE-CPU-001 真实故障 Campaign

Campaign：`campaign_20260824_165822_9a4c56`

| 检查项 | 结果 |
|---|---|
| Campaign 状态 | COMPLETED |
| 基线 CPU | 0.32% |
| 故障 CPU | 96.82% |
| 恢复 CPU | 0.51% |
| 关联任务 | `task_20260824_165825_a0aae4` / DONE |
| TaskAttempt | SUCCEEDED |
| AnalysisJob | SUCCEEDED |
| Artifact 完整性 | SHA-256 与大小校验通过 |
| 证据链 | verified |
| 根因 | `SELF_CODE_CPU_HOTSPOT` |
| 置信度 | 0.91 |
| 隐藏 Oracle 对比 | 通过 |
| finally 清理与恢复验证 | 通过 |

这次运行证明此前“采集完成但任务停在 ANALYZING”的缺陷已经关闭：AnalysisJob 与产生采集物的 TaskAttempt 已正确绑定。

## 6. LIVE-MEM-001 真实故障 Campaign

Campaign：`campaign_20260824_170203_440c65`

| 检查项 | 结果 |
|---|---|
| Campaign 状态 | COMPLETED |
| 基线 RSS | 28.90 MB |
| 故障 RSS | 108.99 MB |
| 恢复 RSS | 32.91 MB |
| 关联任务 | `task_20260824_170205_42a8b6` / DONE |
| TaskAttempt / AnalysisJob | SUCCEEDED / SUCCEEDED |
| 证据链 | verified |
| 根因 | `SELF_CODE_RETAINED_MEMORY` |
| 置信度 | 0.88 |
| 隐藏 Oracle 对比 | 通过 |
| finally 清理与恢复验证 | 通过 |

## 7. 重启恢复与短稳态观察

重启 `server`、`analyzer`、`diagnosis-worker` 后：

- Server 和 Analyzer 恢复为 healthy；
- 公网页面继续返回 HTTP 200；
- 一分钟观察期内核心容器持续运行；
- 关键服务日志未发现新的 `ERROR`、`Traceback`、`Exception` 或 `UNAUTHENTICATED`。

## 8. 结论与剩余边界

本次结论：**云端核心 AI 循证链路和 CPU/内存真实故障 Campaign 通过，可以用于汇报演示。**

仍需如实保留的边界：

1. 正式 90 次历史评测的必需证据覆盖率仍为 70%，不能把策略平均分当成证据完整度；
2. 跨语言采集器虽已安装并有既往端到端记录，本轮没有逐个重跑 Python、Java、Go 和 eBPF 全矩阵；
3. 一分钟观察只能证明重启恢复和短时稳定，不能替代多副本、长时间 Continuous Profiling 和容量压测；
4. 生产化的 OIDC/RBAC、多租户、密钥轮换、MinIO 生命周期、SBOM 和镜像签名仍属于后续工程工作。

