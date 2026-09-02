# Mini-Drop

Mini-Drop 是一个面向开发与 SRE 的分布式性能诊断平台。本仓库按《Drop 性能诊断系统架构与复刻指南》收敛为一条运行主线：React Web、Go API、C++ Control/Agent、PostgreSQL、MinIO、Python Analyzer，以及通过私有 gRPC 接入的 Drop Insight V2 AI 诊断 Worker。

## 当前架构

```text
Browser
  | HTTP / SSE
  v
React Web ---> Go API ---------------------> PostgreSQL
                 |  \                         ^
                 |   \ private gRPC          |
                 |    -> Python Diagnosis ----|  Drop Insight V2 / Evidence Gate / Skill
                 |
                 | Control gRPC
                 v
             C++ Control <---- heartbeat ---- C++ Agent
                                                | collect
                                                v
                                         target process
                                                |
                                                v
                                              MinIO
                                                ^
                                                |
                                        Python Analyzer
```

边界是固定的：

- Go API 是唯一公开 HTTP/SSE 入口，负责认证、任务建模、状态查询和页面事件。
- C++ Control/Agent 负责注册、心跳、能力匹配、任务领取和受控采集。
- PostgreSQL 保存状态、审计事件和文件元数据；MinIO 保存原始文件及分析产物。
- Python Analyzer 异步解析产物；Python Diagnosis Worker 只通过内部 gRPC 接收诊断请求。
- 浏览器不直接访问 Python Worker，AI 也不能绕过 Go 和 C++ 的权限、能力及预算门禁。

## 项目目录

各顶层目录、关键文件、生成文件与测试文件的职责，见
[`docs/architecture/repository-layout.md`](docs/architecture/repository-layout.md)。

## 任务数据流

1. Web 通过 HTTP 创建 Task；Go 校验参数、权限和幂等键，将 `PENDING` Task 写入 PostgreSQL 后立即返回任务 ID。
2. C++ Agent 周期性向 Control 上报身份、能力和心跳，并领取与自身能力匹配的任务。
3. Agent 创建独立 TaskAttempt，复核 PID、进程启动时间、权限、磁盘和时长后调用 Collector。
4. 原始 Artifact 上传 MinIO；数据库只记录 object key、大小、SHA-256、Schema 和来源信息。
5. Python Analyzer 通过租约领取 AnalysisJob，下载原始文件，生成火焰图、TopN 和结构化结果，再写回 MinIO 与 PostgreSQL。
6. Go 从数据库读取状态事件，通过 SSE 推送给 Web；SSE 断线不影响后台执行。

Task 成功只说明采集动作完成。对象、时间窗、文件完整性、Schema、Analyzer 结果和假设谓词全部通过后，Artifact 才能进入 Evidence 层。

## AI 诊断方案

Drop Insight V2 的循环是：

```text
自然语言范围解析
  -> 真实主机/进程绑定
  -> 规则基线生成可验证假设
  -> 计划器选择一个受控探针
  -> C++ Agent 采集
  -> Analyzer 结构化
  -> 证据门禁
  -> 支持 / 反证 / 中性
  -> 更新探索树或生成可引用报告
```

关键约束：

- 模型循环使用 `LangChain create_agent + LangGraph Runtime`，并通过 PostgreSQL Checkpointer 按诊断线程保存短期状态。
- 范围不唯一时要求澄清，不让模型猜目标。
- 没拿到证据只会标记未验证；拿到有效反证才允许剪枝。
- 报告中的关键结论必须引用当前诊断内校验通过的 Evidence。
- 模型只拥有一个受控探针请求工具；结构化 Schema、工具白名单、策略门禁和规则回退兜底。
- 探索树由会话、假设、工具调用、Evidence 和递增事件投影重建，不以页面 JSON 作为唯一事实来源。

## Skill 演进亮点

Skill 不是历史报告或旧根因缓存，而是有版本、有适用边界的诊断策略。它保存：

- 故障类别、服务、环境和 Agent 能力约束；
- 已验证的工具顺序和停止条件；
- 必需证据、有效反证及分支转向原因；
- 评测、发布、隔离和回滚状态。

新诊断先由普通计划器给出基线步骤，再对 `ACTIVE` Skill 做环境与能力硬过滤、混合检索和歧义拒绝。命中后 Skill 只能建议本次的下一步探针，仍需重新采集并经过安全门禁和证据门禁；环境漂移、工具不可用或出现反证时立即退出 Skill，回到普通计划器。

仓库内的冻结 15 条离线策略契约集结果为基线 `6/15 = 40%`、启用 Skill `15/15 = 100%`，即提升 `60` 个百分点。该数字验证的是路由与 Skill 生命周期契约，不是生产根因准确率，也不代表真实诊断耗时下降。

规模化 Skill A/B、门槛校准和稳定性验证可直接运行：

```bash
make skill-ab-large
# 6 小时检索决策 soak
make skill-stability
```

结果写入 `artifacts/skill-ab-large/`。该评测衡量路线先验选择和安全拒绝，真实遥测根因准确率仍需 Linux Campaign 或外部 RCA 数据集验证。

外部真实遥测可用 RCAEval RE1 做隔离 Oracle 的有无 Skill 配对回放：

```powershell
python -m pip install -e ".[benchmark]"
make rcaeval-skill-ab
```

当前冻结结果使用 RE1 的三套系统和 375 个公开故障 Case，其中 2 个因没有有效故障前窗口被如实排除。重复 1 到 3 只用于训练和 Skill 准入，重复 4 到 5 的 150 个 Case 盲测中，根因服务 Top-1 从 `64%` 到 `66%`，3 例改善、0 例退化；配对精确检验 `p=0.25`，因此只能表述为本次观察到 `+2` 个百分点，尚不能声称统计显著或生产稳定收益。报告写入 `artifacts/rcaeval-skill-ab/`。

统一量化报告会现场重跑 Python、Go、Web 测试和前端生产构建，并汇总上述不同证据层，避免把契约回放、生成压力集和外部真实遥测混成一个“准确率”：

```powershell
make quantitative-report
```

报告写入 [`docs/benchmarks/final-quantitative-test-report-20260902.md`](docs/benchmarks/final-quantitative-test-report-20260902.md)，结构化结果写入 `artifacts/final-test-metrics/`。

```powershell
python scripts/run_skill_evolution_benchmark.py
```

## 本地启动

需要 Docker Desktop 或 Linux Docker Engine。完整采集依赖 Linux `perf`、eBPF 和进程命名空间能力。

```powershell
Copy-Item .env.example .env
docker compose up -d --build
docker compose ps
```

Web 默认访问 `http://localhost/`。可选的受控 Python CPU 目标：

```powershell
docker compose --profile demo-target up -d --build python-hotspot
```

停止服务：

```powershell
docker compose down
```

## 验证

```powershell
python -m pytest tests -q
go -C apiserver test ./...
npm --prefix web test -- --run
npm --prefix web run build
docker compose config
```

常用入口：

- `apiserver/`：Go HTTP/SSE 控制面
- `native/control/`：C++ Control
- `native/agent/`：C++ Agent 与 Collector
- `analyzer/`：Python 产物分析
- `server/app/drop_insight/`：AI 诊断、证据门禁与 Skill
- `proto/diagnostic_ai.proto`：Go 到 Python AI Worker 的内部 gRPC 契约
- `skills/`：可审计的诊断 Skill 示例
