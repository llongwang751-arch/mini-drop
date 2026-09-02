# Mini-Drop 闭环审计（更新于 2026-09-02）

这份文档只记录当前工作区、自动测试和本机容器能够证明的事实。

## 已完成

### 1. 默认复刻主链收敛

默认拓扑为：

`React -> Go HTTP/SSE -> C++ Control/Agent -> MinIO/PostgreSQL -> Python Analyzer/Diagnosis Worker`

Python 进程没有公开 FastAPI 入口。浏览器只访问 Go API，Go API 通过私有 gRPC 调用诊断 Worker。

### 2. 真实 Agent Runtime

- 诊断 Agent 已迁移到 `LangChain create_agent + LangGraph Runtime`。
- 模型通过 `ChatDeepSeek` 使用 `deepseek-chat` 的工具调用能力。
- PostgreSQL Checkpointer 按 `diagnosis_id` 保存线程消息和工具调用。
- 长消息使用 Summarization Middleware 压缩。
- 模型只拥有 `request_diagnostic_probe`，不能运行 Shell 或直接调用 Collector。
- ToolCall 仍经过目标、能力、Schema、风险、预算和人工审批门禁。
- 模型失败或输出不合规时回退确定性规则。

### 3. 本机真实 E2E

本机 Docker 已恢复并完成一条真实链路：

- 诊断：`insight_8cbd699439f84dfd9d21124932ecba18`
- Planner：`LANGGRAPH_AGENT / diagnosis-agent-v1`
- ToolCall：`start_pyspy_profile`，先进入人工审批；
- Task：`task_20260901_181801_1a6fef`
- C++ Agent 与 py-spy：1476 个样本；
- Analyzer：`_run` 占 90.4%；
- Evidence：两份 `HIGH / ACCEPT_SUPPORT`；
- Report：置信分 0.73，`PARTIAL_WITHOUT_COUNTER`。

该链路证明 Agent 规划、人工门禁、Task、C++ 采集、Artifact、Analyzer、Evidence 和报告引用已串通。系统保留“缺少独立反证或对照”的限制，没有把单路支持证据包装成完全验证。

### 4. Skill 指标复跑

`python scripts/run_skill_evolution_benchmark.py` 已重新执行：

- 冻结 Case：15；
- 通用初筛基线：`6/15=40%`；
- Skill 路由与生命周期契约：`15/15=100%`；
- 提升：`60` 个百分点；
- 误导反例拒绝：`4/4`；
- 环境漂移正确降级：`2/2`；
- 负迁移：`0/15`；
- 隔离和回滚：各 `1/1`。

该结果证明冻结离线路由与生命周期契约。它不测根因准确率、不测真实诊断耗时，工具调用数也是投影值。

## 当前边界

### P0：真实 Skill 开关 A/B

仍需在同一 Linux 故障 Campaign 上随机交错运行 baseline 与 Skill，至少报告：

- 端到端根因准确率及置信区间；
- Skill Recall@1/3、MRR、拒绝率和误激活率；
- 负迁移率；
- 真实工具调用数、P50/P95 耗时和模型成本；
- 环境漂移、工具不可用、相似症状不同根因等困难负例。

完成前只能说“冻结离线路由与生命周期契约提升 60 个百分点”。

### P1：生产化

- 原生 Ubuntu 上复跑完整 Collector 矩阵和权限模型；
- OIDC、证书轮换和完整租户权限矩阵；
- Prometheus/OTel、容量报告和告警 Runbook；
- SBOM、镜像签名、备份恢复和数据保留策略；
- 为 PostgreSQL checkpoint 增加保留与清理策略。

## 面试最短口径

> 我把 AI 诊断迁移到 LangChain create_agent 和 LangGraph Runtime，按 diagnosis_id 用 PostgreSQL 保存线程状态；模型只请求下一探针，真正执行仍由 Go 策略门禁和 C++ Agent 完成。Skill 是通过硬过滤和混合检索注入的版本化路线记忆，命中后也必须重新取证。当前本机真实跑通了 py-spy 的完整证据链；Skill 的 60 个百分点来自 15 条冻结离线路由与生命周期 Case，不代表生产根因准确率。
