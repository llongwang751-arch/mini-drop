# Historical Fault Fixtures

`benchmarks/cases/` 保存早期故障分类样本，仅用于资料审阅，不进入当前
Docker 运行镜像，也不作为 Skill 指标来源。

当前可复核的 Skill 演进评测位于
`tests/fixtures/diagnostic_skill_evolution/`：

```powershell
python scripts/run_skill_evolution_benchmark.py
```

该评测衡量冻结离线路由与生命周期契约，不衡量线上根因准确率。
