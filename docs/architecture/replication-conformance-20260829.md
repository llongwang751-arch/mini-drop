# Mini-Drop 复刻架构一致性验收

基准：`docs/architecture/replication-guide.md`，与桌面附件 `drop系统复刻指南.md` 一致。更新时间：2026-08-30。

本文件严格区分三件事：代码是否实现、自动测试是否通过、真实 Linux/云环境是否验收。没有运行证据的项目不会写成“已上线”。

## 当前结论

阶段一“契约与基础设施”、阶段二“perf CPU 闭环”的代码主链，以及阶段三的大部分可靠性能力已经对齐。此前列为 P0 的 TaskKind、双状态、ErrorCode、TaskAttempt、mTLS 身份绑定、任务级对象授权和 MANIFEST 差距已经补齐。

```text
React Web
  → Go API（唯一浏览器 REST/SSE 入口、鉴权与任务编排）
  → C++ Control（gRPC 注册、心跳、队列、结果入库）
  ↔ C++ Native Agent（进程快照、Runner、上传、结果重放）
  → PostgreSQL + MinIO
  → Python Analyzer / AI Worker
  → Go API → React Web
```

核心复刻链和 AI 诊断链已经在本机 Docker 完成真实 E2E。仍不能宣称“生产化 100% 完成”，因为完整 OIDC/用户组、Collector 故障矩阵、Java Heap、容量压测和发布工程仍属于后续阶段。

## 已完成的核心对齐

| 领域 | 状态 | 当前证据 |
|---|---|---|
| 四模块默认拓扑 | 已实现 | Web 只访问 Go；Go 调 C++ Control；C++ Agent 采集；Python 领取 AnalysisJob |
| TaskKind 单一真源 | 已实现 | `contracts/taskkinds.json` 生成 Go、C++、Python、Web、Proto；C++ Collector 注册时校验名称和数字 |
| Task 参数契约 | 已实现 | 8 份 JSON Schema 从 TaskKind 自动生成；Go API 执行同源的类型、范围、枚举、URI 和长度校验 |
| 双状态契约 | 已实现 | `contracts/task-statuses.json` 生成 Python、Go、C++、Web；数据库保存 collection/analysis 两个维度 |
| ErrorCode 单一真源 | 已实现 | `contracts/error-codes.json` 生成 Proto、Go、C++、Python、Web，包含阶段、retryable、gRPC 映射和中文提示 |
| TaskAttempt | 已实现 | 每次下发创建独立 attempt；Artifact 与 AnalysisJob 强制绑定同一 task/attempt；回调使用 SHA-256 authority 防重放 |
| 对象存储最小权限 | 已实现 | Agent 不持有长期 MinIO 凭据；Go 为本次 attempt 的每个精确 object key 预签 PUT；C++ 再校验 TaskKind 文件名 |
| RAW + MANIFEST | 已实现 | 原始文件在 `attempts/<id>/raw/`；独立 `manifest.json` 位于 attempt 根；含文件大小、SHA-256 和原始产物清单 |
| Agent 本地目录 | 已实现 | `/tmp/mini-drop-native/<task>/<attempt>`，逐级拒绝符号链接并收紧为 owner-only；确认回调后只清理该 attempt |
| Agent 结果恢复 | 已实现 | durable outbox 保留 attempt authority、attempt id、ErrorCode；重启后只重放 NotifyResult，不重复采样 |
| mTLS 与 Agent 身份 | 已实现 | Agent 使用专属 `agent.crt/agent.key`；Server 将证书身份与 `agent_id` 绑定；不降级明文 |
| 默认 Agent 权限 | 已实现 | 不再默认 privileged/SYS_ADMIN/seccomp-unconfined；保留 SYS_PTRACE/PERFMON/BPF/SYS_RESOURCE 和只读 tracefs |
| PID 复用防护 | 已实现 | Agent 上报 boot/start/namespace/executable 快照；C++ 下发前对最新可信快照复核 |
| 任务事务与恢复 | 已实现 | Task、初始 Event、Outbox 同事务；Go 幂等重放会重新预签并重新触发待分发任务 |
| TaskEvent 可重放字段 | 已实现 | API 返回递增 `sequence`、`source`、`task_attempt_id`、时间和脱敏 metadata；SSE 支持 Last-Event-ID |
| Analyzer 租约 | 已实现 | 数据库 lease、迟到提交保护、有限重试、dead-letter、输入/输出 attempt 约束 |
| Artifact 完整性 | 已实现 | Agent 声明 SHA-256；Server 保存 canonical manifest；Analyzer 下载后复核 hash、大小与格式 |
| 动态探索树 | 已实现 | 假设、工具、证据、反证、转向、报告和复测会生成新 revision；SSE 推动前端增量更新 |
| Skill 检索与演进 | 已实现 | 硬门禁 + BM25/特征向量混排 + 置信/歧义阈值 + 反馈 Beta 后验 + 负迁移隔离/回滚 |
| Diagnosis Agent | 已实现并实跑 | LangChain create_agent + LangGraph + ChatDeepSeek；PostgreSQL checkpoint；受控工具请求接入 Go/C++/Evidence 主链 |

## 本轮补齐的原复刻差距

1. TaskUploadAuthorization 从 `task_id + object_key` 扩展为 `task_id + task_attempt_id + object_key`，重试不会覆盖旧证据。
2. Go 预签地址可配置为 Agent 实际可达的 MinIO endpoint，生产环境缺失时 fail closed。
3. C++ Agent 只使用 Server 下发的精确 object key，不再自行拼接对象前缀。
4. Agent 在上传原始产物后生成并上传独立 TaskAttempt manifest；Analyzer 接受但不把它当分析输入。
5. TaskKind、双状态、ErrorCode 全部改为多语言生成契约，并加入 CI 漂移检查。
6. 补齐此前引用但不存在的 8 份 TaskKind 参数 JSON Schema，并让 Go API 实际执行校验。
7. C++ Control 和 Python 服务将稳定 ErrorCode、task_attempt_id 写入任务、事件和分析链路。
8. 默认 C++ Agent 移除广泛特权与长期对象存储 Secret，工作目录改为 attempt 级私有目录。
9. TaskEvent 对外补齐 sequence/source/attempt 语义，便于审计与 SSE 恢复。

## 仍未完成：必须诚实保留的边界

> 2026-09-02 增量：旧 Pi Sidecar 路线已退出默认代码，Drop Insight V2 直接使用 LangChain/LangGraph Agent。Go API 到 Python 诊断 Worker 采用私有 gRPC，真实 py-spy 诊断、审批、Task、Artifact、Analyzer、Evidence 和报告链已完成本机验收。详见 [`closure-audit-20260901.md`](./closure-audit-20260901.md)。

### R0：真实运行仍需扩大覆盖

1. **单条真实 E2E 已完成。** `insight_8cbd699439f84dfd9d21124932ecba18` 已跑通 LangGraph Planner、审批、C++ Agent py-spy、Analyzer、Evidence 和报告。
2. **完整故障矩阵未跑完。** Control/Agent/Analyzer 重启、断网、Storage 5xx、重复 Notify、证书过期和越权下载仍需逐项保存验收记录。
3. **Skill 真实开关 A/B 未完成。** 离线 15 Case 结果只能证明路由与生命周期契约，不能当作生产根因准确率。
4. **当前没有可迁移的在线云实例。** 本机 Compose 验收不等于云端生产部署。

### R1：严格架构与生产多用户

1. 浏览器入口只有 Go，AI 诊断由 Go 通过私有 gRPC 调用 Python Worker；若要求完全消息化解耦，可进一步改成数据库命令/outbox。
2. TaskDesc 已有 TaskKind、attempt_id 和短时上传目标，但尚未完全改成指南示例的 `oneof payload + deadline + ResourceBudget + protocol_version` 新模型。
3. Agent capability 当前可驱动 TaskKind 筛选，但还缺结构化的工具版本、不可用原因、资源预算和协议最低版本协商。
4. TaskEvent 的 sequence/source/attempt 已通过稳定 id 与 metadata 对外提供；若要求数据库物理列完全同名，仍需一次专门 migration。
5. 现有 API Key/RBAC/principal scope 能限制 Agent 范围，但完整 `users/groups/group_members/group_agents`、OIDC、证书轮换/吊销与权限矩阵尚未完成。
6. 可观测性已有结构化日志、审计和部分指标；Prometheus 全量指标、OTel 异步 span link、告警和 Runbook 仍不完整。

### R2：指南后续阶段与发布工程

1. Java Heap 的 Go 子进程、HPROF 支配树/Retained Size/泄漏路径尚未实现；它属于指南阶段五扩展采样器，不是 perf 演示闭环的阻塞项。
2. Java Heap、Python Memory、BOLT、gperftools 等扩展还没有完整的 capability、Schema、Runner、Analyzer、黄金样本和 Web 展示套件。
3. 10k/50k/100k 火焰图、大 Artifact、百万 Task 查询、心跳规模和 Analyzer 吞吐尚未形成正式性能报告。
4. SBOM、镜像签名、多架构构建、备份恢复、数据保留删除、灰度和回滚仍属于发布工程待办。

## Skill 亮点及真实边界

当前链路为：

```text
真实诊断轨迹
→ 提炼适用条件/成功信号/反证/转向/停止条件
→ 确定性契约门禁
→ 人工发布
→ 硬过滤
→ BM25 + 特征向量混排
→ 置信与歧义门槛
→ 实际诊断复用
→ 用户反馈和结果反馈
→ Beta 后验调权
→ 连续负迁移自动隔离/回滚
```

检索分数只回答“像不像”，反馈后验回答“过去复用是否可靠”，两者都不能绕过 TaskKind、环境、风险和 capability 硬门禁。

仍需真实 Linux Campaign 补三项证据：Skill 命中前后工具调用数和诊断耗时；时间切分测试集的 Recall@1/3、MRR、拒绝率和负迁移率；独立 Campaign 发布门禁，避免同源回放被误认为泛化证明。

## 可复跑验收命令

```bash
python scripts/generate_taskkind_contracts.py --check
python scripts/generate_status_contracts.py --check
python scripts/generate_error_code_contracts.py --check
python -m pytest -q
cd apiserver && go test ./...
cd web && npm test && npm run build
docker compose -f docker-compose.yml config --quiet
docker compose --env-file deploy/env/control.env.example -f docker-compose.control.yml config --quiet
docker compose --env-file deploy/env/worker.env.example -f docker-compose.worker.yml config --quiet
docker compose --env-file deploy/env/control.env.example -f docker-compose.cloud-control.yml config --quiet
```

Docker daemon 可用后必须补跑：

```bash
docker build -f deploy/dockerfiles/native-control.Dockerfile -t mini-drop-native-control:test .
docker build -f deploy/dockerfiles/native-agent.Dockerfile -t mini-drop-native-agent:test .
```
