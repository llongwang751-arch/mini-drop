# Mini-Drop 统一诊断测试集 v1.1.0

这是 Mini-Drop AI 诊断的统一评测材料。它用于比较不同诊断策略，不是完整外部数据集的镜像。

## 先看当前完成度

- 已定义 10 类统一故障用例；
- 历史 `official-90` 已保存 90 个唯一执行记录：84 个 Campaign 完成、6 个真实 fixture failure，完整性门禁通过；
- 3 个本地 Golden 回放和 7 个 Golden 质量门禁场景已通过；
- 7 个 OpenTelemetry Demo 官方故障开关已完成映射，但“存在映射”不等于 fixture 或诊断链路已验收；
- `T1-CPU-001` 与 `T1-MEM-001` 的 orchestrator-backed live subset 已完成实现和聚焦回归，协议为 2 案例 × 3 策略 × 3 重复，共 18 次执行、6 个有界故障窗口；
- 该 live subset 尚未在云端生成正式运行产物，不能据此宣称 18 次真实诊断已经完成。

历史 90 次结果见 [`reports/benchmark/official-90/README.md`](../reports/benchmark/official-90/README.md)。新的 live subset 使用独立输出目录，不修改历史 `official-90`。当前版本已经具备用例契约、评分器、真实窗口执行器和严格发布门禁，但不代表十个场景都完成了现场实验。

## 目录

```text
benchmarks/
  unified_manifest.json        统一清单、来源和运行策略
  cases/                       10 个严格 JSON 用例
golden_scenarios/              7 个快速回归场景
docs/
  ../docs/benchmarks/sources.md
  ../reports/benchmark/README.md
reports/                       预检、计划、回放和实测结果
runner/                        用例加载、计划生成和评分相关源码
scripts/                       完整仓库中的命令行入口
SHA256SUMS.txt                 包内文件 SHA-256
```

## 评测原则

1. AI 看不到 Oracle；
2. 每个正式案例预热 1 次、重复至少 3 次；
3. baseline、incident 和 verification 使用相同负载；
4. 诊断结论必须带 `evidence_refs`；
5. 信息不足时允许返回 unknown；
6. 高风险操作必须等待人工确认。

## 在完整仓库中运行

在 Mini-Drop 仓库根目录打开 PowerShell：

```powershell
python scripts/diagnosis_benchmark.py plan --output reports/benchmark/run-plan.json
python scripts/diagnosis_benchmark.py preflight --otel-root external/opentelemetry-demo --output reports/benchmark/preflight.json
python scripts/diagnosis_benchmark.py replay T1-CODE-001
python scripts/diagnosis_benchmark.py replay T1-IO-001
python scripts/diagnosis_benchmark.py replay T1-NOISY-001
pytest -q tests/test_benchmark_cases.py tests/test_benchmark_runner.py tests/test_benchmark_adapters.py
```

压缩包中的源码用于审阅实现，运行完整命令仍以 Mini-Drop 仓库为准。OpenTelemetry Demo、RCAEval 等大型外部数据需要按资料索引中的固定版本另行下载。
