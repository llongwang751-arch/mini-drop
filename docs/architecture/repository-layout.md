# Mini-Drop 项目目录说明

本文对应当前严格复刻分支。公开入口只有 Go HTTP/SSE，采集控制与执行由 C++
完成，Python 只运行 Analyzer 和 AI Diagnosis Worker，不存在 FastAPI 控制面。

## 一分钟看懂目录

```text
mini-drop/
├── apiserver/          Go API：网页唯一入口、任务编排、SSE、数据查询
├── native/             C++ Control 与 C++ Agent：派单和本机采集
├── server/             Python Worker：产物分析、AI 诊断、证据门禁、Skill
├── analyzer/           perf、py-spy、pprof 和火焰图解析器
├── web/                React 诊断工作台
├── proto/              Go、C++、Python 共用的 gRPC 协议源文件
├── contracts/          状态、任务类型、错误码的跨语言契约
├── skills/             可复用诊断策略模板
├── knowledge/          诊断知识条目
├── benchmarks/         统一评测用例和真实数据清单
├── golden_scenarios/   快速回归用的标准故障场景
├── tests/              Python 单元、契约和集成测试
├── scripts/            契约生成、A/B 评测、验收和报告脚本
├── deploy/             Dockerfile、Nginx、环境和 systemd 配置
├── demo/               可控演示负载
├── docs/               架构、契约、运行、AI 与测试文档
├── reports/            已冻结的评测结果
├── docker-compose.yml  本地完整拓扑
├── Makefile            常用构建、测试和评测入口
└── README.md           项目总览与快速启动
```

主链路是：

```text
React -> Go HTTP API -> PostgreSQL
                    -> C++ Control -> C++ Agent -> MinIO
                    -> Python Analyzer Worker
                    -> Python Diagnosis Worker -> Evidence/Skill
React <- SSE <- Go API
```

## 根目录文件

| 文件 | 作用 |
|---|---|
| `README.md` | 当前架构、任务链路、AI 方案、Skill 指标和启动说明。 |
| `docker-compose.yml` | 定义 PostgreSQL、MinIO、迁移、Control、Agent、两个 Python Worker、Go API 和 Web。 |
| `Makefile` | 封装测试、构建、契约校验、Skill A/B、量化报告和验收命令。 |
| `pyproject.toml` | Python Worker 的依赖、pytest 和覆盖率配置。 |
| `alembic.ini` | Alembic 数据库迁移入口配置。 |
| `.env.example` | 可提交的环境变量示例，不保存真实密钥。 |
| `.dockerignore` | 排除镜像构建不需要的本地文件。 |
| `.gitignore` | 排除密钥、依赖、缓存、构建结果和临时报告。 |
| `.gitattributes` | 统一文本换行和生成文件属性。 |

## `apiserver/`：Go 公开 API

这是浏览器唯一访问的后台服务。它不执行采集，也不直接跑 LLM，而是负责校验、
持久化和编排 C++/Python 私有服务。

| 路径 | 作用 |
|---|---|
| `cmd/apiserver/main.go` | 服务启动入口，加载配置、连接 PostgreSQL/MinIO/gRPC，并启动 HTTP Server。 |
| `internal/config/config.go` | 读取监听地址、数据库、对象存储、鉴权及私有服务地址。 |
| `internal/cron/cron.go` | 周期任务调度和下次执行时间计算。 |
| `internal/errorcode/codes.go` | 使用统一错误码契约。 |
| `internal/httpapi/server.go` | 核心 HTTP/SSE 路由：健康检查、Agent、Task、Artifact、事件流和 AI 诊断。 |
| `internal/httpapi/schedules.go` | 定时任务的增删改查和手动触发。 |
| `internal/objectstore/minio.go` | 从 MinIO 获取或代理下载 Artifact。 |
| `internal/repository/postgres.go` | Agent、Task、Attempt、Artifact、审计和诊断数据查询。 |
| `internal/repository/schedule.go` | 定时任务持久化。 |
| `internal/taskkind/catalog.go` | 加载采集器类型目录。 |
| `internal/taskkind/validation.go` | 按 JSON Schema 校验不同采集任务参数。 |
| `internal/taskstatus/statuses.go` | 读取统一任务状态契约。 |
| `internal/gen/mini_drop/*.pb.go` | 由 `proto/` 生成的 Go protobuf/gRPC Stub，不手工修改。 |
| `*_test.go` | 对应模块的 Go 单元和接口测试。 |
| `go.mod` / `go.sum` | Go 模块和锁定依赖。 |

## `native/`：C++ 控制面和采集 Agent

### `native/control/`

| 文件 | 作用 |
|---|---|
| `CMakeLists.txt` | 编译 Control 和 protobuf/gRPC 依赖。 |
| `src/main.cpp` | C++ Control 主程序，负责 Agent 注册、心跳、能力匹配、任务领取、租约、取消和结果回报。 |

### `native/agent/include/`

| 文件 | 作用 |
|---|---|
| `collector.h` | 所有 Collector 必须实现的统一接口。 |
| `collector_registry.h` | Collector 注册表，按任务类型查找采集器。 |
| `task.h` | Agent 本地任务和参数结构。 |
| `config.h` | Agent ID、Control 地址、心跳、工作目录等配置。 |
| `process_runner.h` | 受控启动外部工具，处理超时、进程组取消和输出。 |
| `process_snapshot.h` | 采集前后核验 PID、启动时间、命令行和 cgroup，防止 PID 复用。 |
| `artifact_uploader.h` | 计算哈希并将原始产物上传 MinIO。 |
| `result_outbox.h` | 暂存未成功上报的结果，重连后补发且避免重复采样。 |

### `native/agent/src/`

| 文件 | 作用 |
|---|---|
| `main.cpp` | Agent 启动、注册、心跳领任务、执行和上报总流程。 |
| `config.cpp` | 配置解析和默认值。 |
| `collector_registry.cpp` | 注册各类 Collector 并做能力发现。 |
| `perf_collector.cpp` | 使用 perf 采集 CPU 调用栈。 |
| `ebpf_io_collector.cpp` | 使用 bpftrace/eBPF 采集 I/O 延迟分布。 |
| `proc_collectors.cpp` | `/proc` 系统指标和进程内存采集。 |
| `language_collectors.cpp` | py-spy、Go pprof、Java async-profiler 等语言采集器。 |
| `process_runner.cpp` | 外部进程资源限制、超时和取消实现。 |
| `process_snapshot.cpp` | 进程身份快照与目标漂移判定。 |
| `artifact_uploader.cpp` | MinIO 上传和完整性元数据生成。 |
| `result_outbox.cpp` | 结果补偿队列实现。 |

其他文件：`io_latency.bt` 是 eBPF I/O 探针；`bpftrace_compat.h` 处理不同
bpftrace 版本差异；`tests/` 验证进程身份和 Outbox；`native/generated/` 是从
`contracts/` 生成的 C++ 常量头文件。

## `server/`：Python 后台 Worker

`server/` 不监听公开 HTTP，也不提供 FastAPI。两个主要入口是 Analyzer Worker
和 Diagnosis Worker。

### Worker 与基础设施

| 文件 | 作用 |
|---|---|
| `app/diagnosis_worker.py` | 私有 gRPC AI 诊断服务入口。 |
| `app/diagnostic_ai_rpc.py` | protobuf 请求与诊断领域模型之间的转换。 |
| `app/analysis_jobs.py` | 租约领取分析任务，下载原始文件、分析、回写和重试。 |
| `app/analyzer_runner.py` | 根据 Artifact 类型选择具体 Analyzer。 |
| `app/ai_provider.py` | DeepSeek/OpenAI 兼容模型调用、结构化输出和降级。 |
| `app/database.py` | SQLAlchemy Engine、Session 和数据库初始化。 |
| `app/models.py` | PostgreSQL ORM 数据模型。 |
| `app/sql_repository.py` | AI 诊断会话、证据、报告和 Skill 的 SQL 持久化。 |
| `app/state_machine.py` | Task 状态白名单和合法迁移。 |
| `app/task_attempt_authority.py` | 确定哪个 Attempt 有权更新 Task 最终状态。 |
| `app/storage.py` | MinIO 客户端和对象读写。 |
| `app/artifact_contracts.py` | Artifact 类型、Schema 和分析要求。 |
| `app/artifact_integrity.py` | 文件大小、哈希和可解析性校验。 |
| `app/artifact_lifecycle.py` | 原始产物到分析结果的生命周期。 |
| `app/process_attestation.py` | 目标主机、PID 和进程启动时间绑定校验。 |
| `app/schemas.py` | Worker 内部数据结构。 |
| `app/common_utils.py` | 通用时间、序列化等辅助函数。 |
| `app/logging_utils.py` | 结构化日志和敏感字段处理。 |
| `app/_env.py` | 环境变量读取辅助。 |
| `app/repositories/*.py` | Agent、Task、Attempt、Artifact、AnalysisJob 和 Outbox 的细分仓储。 |
| `app/generated/*` | protobuf 和跨语言契约生成物，不手工修改。 |

### `server/app/drop_insight/`：AI 诊断 Agent

| 文件 | 作用 |
|---|---|
| `diagnosis_agent.py` | LangGraph 状态图，把范围、假设、计划、工具、证据、报告串成循环。 |
| `service.py` | 诊断用例编排和对外领域服务。 |
| `schemas.py` | Scope、Hypothesis、ToolCall、Evidence、Report、Skill 等结构化模型。 |
| `adaptive_planner.py` | 根据未验证假设和已有证据选择下一步工具。 |
| `tools.py` | AI 可见工具目录及工具到采集 Task 的映射。 |
| `policy.py` | Agent 能力、白名单、风险、预算和审批门禁。 |
| `artifact_evidence.py` | 将通过完整性和范围校验的分析结果封装为 Evidence。 |
| `evidence.py` | 证据质量、支持/反证/中性和置信分规则。 |
| `claim_verifier.py` | 校验报告结论是否能引用真实证据。 |
| `exploration_tree.py` | 从会话、假设、工具调用和证据事件投影动态探索树。 |
| `skill_evolution.py` | 从已验证诊断生成候选 Skill，处理版本、评测、发布、隔离和回滚。 |
| `retrieval_benchmark.py` | 结构化字段、BM25、确定性向量的 Skill 混合检索评测。 |
| `skill_benchmark.py` | Skill 路线收益和安全边界评测。 |
| `scaled_skill_ab.py` | 大规模合成扰动集上的有/无 Skill A/B。 |
| `rcaeval_benchmark.py` | 外部盲测数据上的根因评测。 |
| `source_mapper.py` | 把工具/产物来源映射到报告引用。 |
| `showcase.py` | 可复核演示场景的装配逻辑。 |

### 数据库迁移

`server/migrations/env.py` 和 `script.py.mako` 是 Alembic 运行框架；
`versions/*.py` 按编号记录基线、兼容字段、终态时间、约束和更新时间等结构变化。
迁移是增量执行的，不能随意修改已经发布过的历史文件。

## `analyzer/`：产物解析

| 文件 | 作用 |
|---|---|
| `mini_drop_analyzer/hotmethod_analyzer.py` | 将 perf 栈折叠、统计 TopN 并生成火焰图数据。 |
| `mini_drop_analyzer/pyspy_analyzer.py` | 解析 py-spy 产物。 |
| `mini_drop_analyzer/pprof_analyzer.py` | 解析 Go pprof Profile。 |
| `profile.proto` / `profile_pb2.py` | pprof Profile 协议及生成代码。 |
| `scripts/stackcollapse-perf.pl` | 将 perf script 输出折叠为调用栈计数。 |
| `scripts/flamegraph.pl` | 根据折叠栈生成 SVG 火焰图。 |
| `scripts/cddl1.txt` | FlameGraph 工具随附的数据定义文件。 |
| `config.example.toml` | 独立分析器配置示例。 |

## `web/`：React 工作台

| 路径 | 作用 |
|---|---|
| `src/main.jsx` | React 入口。 |
| `src/router.jsx` | Dashboard、任务、Agent、审计、计划和 AI 诊断路由。 |
| `src/theme.js` / `global.module.css` | 全局主题和基础样式。 |
| `src/api/client.js` | Axios 客户端、统一响应和错误处理。 |
| `src/hooks/useSSE.js` | 订阅服务端事件并处理重连、序号和增量更新。 |
| `src/hooks/usePolling.js` | SSE 不可用时的低频轮询兜底。 |
| `src/pages/Dashboard.jsx` | 任务和 Agent 总览。 |
| `src/pages/TaskResult.jsx` | Artifact、TopN、火焰图和任务详情。 |
| `src/pages/AgentDetail.jsx` | Agent 心跳、能力和状态详情。 |
| `src/pages/AuditLogs.jsx` | 审计事件列表。 |
| `src/pages/Schedules.jsx` | 定时采集任务管理。 |
| `src/pages/AIDiagnosis.jsx` | 对话式诊断、探索树、证据、报告、Skill 与评测主页面。 |

AI 页面组件按职责拆分：`ChatThread`/`ChatMessage` 展示对话；`ScopeCard` 展示诊断
范围；`PlannerBlock` 展示计划；`ToolCallCard` 展示工具调用；`EvidenceCard` 展示证据；
`ActualExplorationTree` 展示动态树；`ConclusionCard` 展示结论；
`FixVerificationPanel` 展示恢复验证；`DiagnosisSkillOutcomeCard` 和
`SkillEvolutionPanel` 展示 Skill 命中与演进；`DiagnosisFeedbackCard` 收集人工反馈。

通用组件中，`AppLayout` 是页面框架，`StatusTag` 统一状态，`ErrorBoundary` 和
`ErrorAlert` 处理异常，`SafeMarkdown` 防止不安全 HTML，`FlamegraphViewer`、
`TopNChart`、`EBPFHistogram` 负责可视化，`TechnicalDetailDrawer` 展示审计细节。
同名 `*.test.jsx` 是对应页面或组件测试；`src/generated/` 是契约生成代码；
`src/utils/` 放采集器、HTML 和状态转换辅助；`src/lib/echarts.js` 做图表按需加载。

`package.json`/`package-lock.json` 管理前端依赖，`vite.config.js` 管构建和测试，
`index.html` 是挂载模板，`public/` 存静态资源。

## `proto/` 与 `contracts/`

| 文件 | 作用 |
|---|---|
| `proto/common.proto` | PID、文件、对象存储等通用消息。 |
| `proto/init.proto` | Agent 注册。 |
| `proto/healthcheck.proto` | 心跳、能力上报和任务领取。 |
| `proto/hotmethod.proto` | 任务描述和采集结果上报。 |
| `proto/control.proto` | Go API 调 C++ Control 的任务接口。 |
| `proto/diagnostic_ai.proto` | Go API 调 Python Diagnosis Worker 的私有接口。 |
| `proto/taskkind.proto` | 采集任务类型枚举。 |
| `proto/errorcode.proto` | 错误码枚举。 |
| `proto/compile.py` / `compile.sh` | 生成跨语言 Stub。 |
| `contracts/taskkinds.json` | 任务类型和参数约束的唯一数据源。 |
| `contracts/task-statuses.json` | Task 状态和合法迁移。 |
| `contracts/error-codes.json` | 跨语言错误码。 |

修改契约后必须运行 `scripts/generate_*_contracts.py`，并提交 Go、C++、Python、
JavaScript 的生成结果，避免四种语言理解不一致。

## `skills/` 与 `knowledge/`

`skills/*/SKILL.md` 是八类诊断策略模板：CPU 热点、I/O 延迟、内存增长、GC 压力、
锁竞争、文件描述符泄漏、网络退化和下游依赖延迟。每个 Skill 描述适用范围、工具
顺序、必需证据、反证和停止条件；运行时数据库版本是权威记录，Markdown 用于审阅
和初始化。

`knowledge/catalog.json` 是知识目录；其余 Markdown 分别解释 Linux CPU、I/O、
内存、JVM GC、MySQL 锁、TCP 重传和分布式归因。知识用于生成候选与解释，不能绕过
真实采集直接成为 Evidence。

## 评测目录

| 目录 | 作用 |
|---|---|
| `benchmarks/cases/` | 十类历史故障输入，主要用于资料审阅和兼容测试。 |
| `benchmarks/real_world/` | 外部公开用例、归一化模板和比较器配置。 |
| `golden_scenarios/` | 自身代码热点、同机噪声、共享 I/O、内存泄漏、锁等待、丢包和下游热点等确定性场景。 |
| `tests/fixtures/` | Skill 检索、Skill 演进和 TLinux 2/3/4 的冻结测试输入。 |
| `reports/benchmark/` | 已生成的 A/B、真实用例和成熟产品对照结果。 |

`tests/test_*.py` 按文件名对应被测模块。重点测试包括：

- `test_current_architecture.py`：确保没有 FastAPI/Python Agent 回退。
- `test_diagnosis_agent.py`、`test_diagnosis_worker.py`、`test_diagnostic_ai_rpc.py`：Agent 图和私有 gRPC。
- `test_drop_insight_*`：预算、策略、证据、CAS、报告副作用和任务权威。
- `test_artifact_*`：Artifact 完整性和生命周期。
- `test_state_machine.py`：状态机和重试权威。
- `test_diagnostic_skill_*`、`test_skill_retrieval_benchmark.py`、`test_scaled_skill_ab.py`：Skill 生命周期和 A/B。
- `test_rcaeval_benchmark.py`：外部盲测。
- `test_migrations.py`、`test_database.py`、`test_sql_repository.py`：数据库与迁移。
- `test_analyzer*.py`、`test_pprof_analyzer.py`、`test_pyspy_analyzer.py`：分析器。
- `test_contracts.py`、`test_taskkind_contract_generation.py`：跨语言契约一致性。

## `scripts/`、`deploy/` 与 `demo/`

| 路径 | 作用 |
|---|---|
| `scripts/generate_*_contracts.py` | 从 JSON 契约生成各语言常量。 |
| `scripts/run_rcaeval_skill_ab.py` | 外部盲测上的有/无 Skill 对照。 |
| `scripts/run_scaled_skill_ab.py` | 大规模扰动 Skill A/B。 |
| `scripts/benchmark_skill_retrieval.py` | 检索吞吐、漂移和错误统计。 |
| `scripts/run_skill_evolution_benchmark.py` | 生命周期冻结评测。 |
| `scripts/build_quantitative_test_report.py` | 汇总各测试为最终量化报告。 |
| `scripts/verify_*.sh` | 多副本、备份恢复、外部验收和原生 eBPF 验证。 |
| `scripts/package_skill_evolution_delivery.py` | 打包可交付的 Skill 演进材料。 |
| `deploy/dockerfiles/` | 五类服务镜像。 |
| `deploy/nginx/` | Web 静态站点、API 代理、SSE 和 TLS 配置。 |
| `deploy/scripts/python-worker-entrypoint.sh` | 按角色启动迁移、Analyzer 或 Diagnosis Worker。 |
| `deploy/scripts/configure_ai_provider.py` | 配置 AI Provider。 |
| `deploy/env/` | 可提交的 Control/Worker 环境示例。 |
| `deploy/systemd/mini-drop-agent.service` | Linux 主机安装 Agent 的 systemd 单元。 |
| `demo/python-hotspot/app.py` | 可控 Python CPU 热点服务。 |
| `demo/python-hotspot/Dockerfile` | 演示服务镜像。 |

## `docs/`：文档索引

- `docs/architecture/`：边界、实现状态、LangGraph、复刻符合度、系统图和剩余工作。
- `docs/architecture/diagrams/`：Mermaid 源文件以及导出的 SVG/PNG。
- `docs/ai/`：循证诊断、反证循环、质量门禁、Skill 混合检索和设计决策。
- `docs/contracts/`：OpenAPI、Artifact/Evidence 和各 Collector 参数 Schema。
- `docs/guides/`：启动、部署、运维、验收、TLinux 和完整演示步骤。
- `docs/benchmarks/`：量化报告、RCAEval、规模化 Skill A/B 和数据来源。
- `docs/testing/`：历史验收记录，阅读时要看日期，不能替代当前自动化结果。
- `docs/images/tutorial-v2/`：页面操作截图。

## 推荐阅读顺序

1. `README.md`：先理解系统解决什么问题。
2. `docker-compose.yml`：看真实运行组件和依赖。
3. `apiserver/internal/httpapi/server.go`：看外部请求如何进入。
4. `native/control/src/main.cpp` 和 `native/agent/src/main.cpp`：看任务如何派发和执行。
5. `server/app/analysis_jobs.py`：看 Artifact 如何分析。
6. `server/app/drop_insight/diagnosis_agent.py`：看 Agent 循环。
7. `artifact_evidence.py`、`claim_verifier.py`：看材料如何升级为证据。
8. `skill_evolution.py`、`retrieval_benchmark.py`：看 Skill 如何沉淀和复用。
9. `web/src/pages/AIDiagnosis.jsx`：看诊断过程如何呈现。
10. `docs/benchmarks/final-quantitative-test-report-20260902.md`：看可核验指标和边界。

## 不提交的本地目录

`web/node_modules/`、`web/dist/`、`__pycache__/`、`.pytest_cache/`、`tmp/`、
`artifacts/`、`.env`、证书私钥和临时测试输出都能重新生成或包含本机信息，因此不应
进入公开仓库。
