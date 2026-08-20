# ai_ops_v2 统一测试集接入说明

## 作用

本项目同时保留两种测试：

- 内置 Golden/Campaign：开发时快速验证状态机、循证门禁和真实故障清理；
- `ai_ops_v2`：组内统一横向测试，包含 30 个案例和 90 次历史诊断运行。

两种结果不能混为一个分数。Golden 通过率不代表真实根因准确率。

## 压缩包位置

默认位置：

```text
benchmarks/external/ai_ops_v2-2.0.0-candidate.1-20260811.zip
```

也可以通过环境变量指定：

```env
MINI_DROP_EXTERNAL_BENCHMARK_ARCHIVE=/app/benchmarks/external/ai_ops_v2-2.0.0-candidate.1-20260811.zip
```

压缩包属于本地评测资产，不提交到 Git，也不烘焙进 Server 镜像。Docker
Compose 使用 `/workspace-source` 只读挂载读取它。

## 页面查看

1. 启动项目后打开 `http://localhost`。
2. 进入 **AI 诊断**。
3. 左侧选择 **方法与测试集**。
4. 展开 **组内统一测试集 ai_ops_v2：30 个案例 / 90 次真实诊断**。
5. 表格只显示公开症状、服务和历史得分，不显示标准答案。
6. 点击案例右侧 **查看**，进入评测器视图：此时才显示隐藏 Oracle、三次运行、采集器、证据数量和完整诊断轨迹。

## 数据隔离

- `public/cases.json`：诊断系统允许看到的问题描述；
- `private/oracles.json`：只由评测器在诊断结束后读取；
- `审计包/*.json`：历史运行的假设、探针、证据、报告和哈希审计轨迹；
- `evaluation.json`：评测器生成的根因、证据、轨迹、安全与恢复评分；
- `summary.json`：运行次数、耗时、回滚和最终健康状态。

API：

```text
GET /api/v1/diagnosis-evaluations/external
GET /api/v1/diagnosis-evaluations/external/cases/{case_id}
```

第一个接口不返回 Oracle；第二个接口是诊断完成后的 evaluator-only 详情视图。

## 当前基线

| 指标 | 结果 |
|---|---:|
| 案例数 | 30 |
| 有效运行 | 90 |
| 严格根因准确率 | 46.7% |
| 运行级严格命中 | 48.9% |
| 平均综合分 | 78.61/100 |
| 正确拒答率 | 100% |
| 证据引用有效率 | 100% |
| 审计轨迹覆盖率 | 100% |
| 重复一致率 | 36.7% |
| 不安全动作 | 0 |

## 当前边界

这次接入实现的是**统一测试集目录、历史审计包回放和结果可视化**。真实
Hyper-V 三节点重新注入 30 个故障仍依赖测试集提供的 VM 拓扑、Online
Boutique 和故障控制脚本；本地 Windows Docker Desktop 页面不会冒充该三节点
真实环境。下一阶段在相同环境重新运行时，使用同一 API 返回新生成的审计包和
评测结果。


