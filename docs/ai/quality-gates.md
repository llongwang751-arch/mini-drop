# AI 诊断 Golden 评测与质量门禁

更新时间：2026-07-29

## 1. 为什么要做

AI 能生成一段“看起来合理”的分析，并不代表它真的定位正确。Mini-Drop 因此把典型故障整理成可重复执行的 Golden 数据集，并在每次修改诊断规则、知识库或模型接入方式后执行回归。

这套门禁重点检查：

1. 根因分类是否正确；
2. 结论引用的证据是否真实存在；
3. 是否错误地自动执行高风险动作；
4. 是否提供了可以推翻当前假设的反证采集计划；
5. 必需场景是否被误删。

## 2. 数据集

数据集目录：`golden_scenarios/`

清单文件：`golden_scenarios/manifest.json`

当前版本：`mini-drop-diagnosis-golden@2.0.0`

覆盖 7 类场景：

- 进程自身代码热点；
- 同宿主机噪声邻居；
- 共享 I/O 争抢；
- 下游节点 CPU 热点；
- 内存泄漏；
- 网络丢包；
- MySQL 锁等待。

每个场景都经过严格 Schema 校验。`manifest.json` 声明必需场景和质量阈值，场景缺失、字段拼错或类型错误会直接使评测失败。

## 3. 反证式诊断

采集动作增加 `evidence_purpose`：

- `VERIFY`：确认已有事实或数据链路；
- `SUPPORT`：补充支持当前假设的证据；
- `FALSIFY`：主动寻找能够推翻当前假设的证据。

例如系统初步判断“服务自身 CPU 热点”后，不会直接把猜测写成根因，而是建议执行一次受审批保护的 `perf_cpu` 采集。若采不到与假设相符的热点，当前结论必须降级。

跨节点场景会对对照目标补采 `sys_metrics`。如果对照节点没有同步异常，“下游或噪声邻居导致问题”的结论也要降级。

## 4. 质量门禁

当前阈值：

| 指标 | 阈值 |
|---|---:|
| 场景通过率 | 100% |
| 根因分类准确率 | 100% |
| 证据引用完整率 | 100% |
| 不安全自动执行数 | 0 |
| 反证计划覆盖率 | 100% |

运行命令：

```powershell
cd D:\tx\mini-drop
.\.venv\Scripts\python.exe -m server.app.diagnosis.eval_harness `
  --format markdown `
  --output reports/golden-evaluation-v2.md
```

退出码为 `0` 表示门禁通过，非 `0` 表示至少一项指标没有达到阈值，可直接接入 CI。

## 5. HTTP 接口

启动系统后执行：

```powershell
Invoke-RestMethod http://localhost/api/v1/diagnosis-evaluations/golden
```

核心返回字段：

- `dataset_version`：数据集版本；
- `dataset_fingerprint`：场景和阈值的 SHA-256 指纹；
- `gate_status`：`PASSED` 或 `FAILED`；
- `metrics`：准确率、证据完整率、反证覆盖率等；
- `results`：每个场景的检查结果。

## 6. Prometheus 指标

接口每执行一次，会更新：

- `mini_drop_ai_golden_evaluations_total`
- `mini_drop_ai_golden_gate_passed`
- `mini_drop_ai_golden_scenario_pass_rate`
- `mini_drop_ai_golden_classification_accuracy`
- `mini_drop_ai_golden_evidence_reference_integrity`
- `mini_drop_ai_golden_falsification_plan_rate`

查看：

```powershell
(Invoke-WebRequest http://localhost/api/metrics -UseBasicParsing).Content
```

## 7. 当前实测

- 数据集版本：`2.0.0`
- 数据集指纹：`bef12ba05ba1d9edbb3080ee671b201e512a356f93014581bc1674687e91d2b4`
- 场景：`7/7`
- 根因分类准确率：`100%`
- 证据引用完整率：`100%`
- 反证计划覆盖率：`100%`
- 不安全自动执行：`0`
- 门禁：`PASSED`

详细报告：`reports/golden-evaluation-v2.md`

## 8. Docker 可复现性修复

Server 镜像现在同时打包：

- `golden_scenarios/`：离线回归数据；
- `knowledge/`：在线诊断和回归共用的领域知识库。

该修复不仅保证评测接口在容器内可运行，也修复了在线诊断容器缺少知识库文件的隐患。

