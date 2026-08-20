# replication-guide.md 完整阅读分析报告

> 分析对象：`docs/architecture/replication-guide.md`（2775 行，约 79KB，UTF-8）
> 分析时间：本次会话。本文是对文档全量逐段阅读后的要点摘录、完成度核实与文档-代码一致性核查。

---

## 1. 完整标题大纲

```
# Drop 性能诊断系统架构与复刻指南
## 快速导航
## 1. 系统目标与整体架构
### 1.1 系统解决的问题
### 1.2 四个运行模块
### 1.3 运行时拓扑
### 1.4 依赖方向
### 1.5 模块通信矩阵
## 2. 核心领域对象、协议与状态机
### 2.1 Agent
### 2.2 Task
### 2.3 TaskAttempt
### 2.4 Artifact
### 2.5 AnalysisJob
### 2.6 TaskEvent
### 2.7 gRPC 服务
### 2.8 TaskDesc
### 2.9 采集状态机
### 2.10 分析状态机
### 2.11 TaskKind 能力模型
## 3. 核心数据流
### 3.0 数据流总览
### 3.1 Agent 初始化与注册
### 3.2 Agent 心跳与任务拉取
### 3.3 用户创建任务与调度
### 3.4 Agent 执行采样
### 3.5 原始制品上传与结果上报
### 3.6 分析任务领取与处理
### 3.7 Web 查询与结果展示
### 3.8 复合任务
### 3.9 计划任务
### 3.10 诊断建议与 AI 流
## 4. 异常流、重试与恢复
### 4.1 Agent 离线
### 4.2 任务无法下发
### 4.3 Runner 启动失败
### 4.4 Runner 超时或取消
### 4.5 对象存储失败
### 4.6 Server/API 重启
### 4.7 Analyzer 崩溃
### 4.8 分析输入损坏
### 4.9 复合任务部分失败
### 4.10 错误码分层
## 5. 核心采集模块
### 5.1 模块输入输出
### 5.2 Server 内部组件（5.2.1 InitAgentService / 5.2.2 HealthCheckService / 5.2.3 ControlService / 5.2.4 HotmethodService）
### 5.3 Server 核心数据结构（5.3.1 Agent Registry / 5.3.2 待下发队列 / 5.3.3 运行中任务表）
### 5.4 Server 启动流程
### 5.5 Agent 内部组件
### 5.6 Agent 启动流程
### 5.7 Agent 线程与队列
### 5.8 Runner 接口
### 5.9 采样器能力
### 5.10 外部进程执行
### 5.11 容器目标解析
### 5.12 对象存储
### 5.13 核心采集模块验收
## 6. API 编排模块
### 6.1 模块输入输出
### 6.2 内部结构
### 6.3 启动流程
### 6.4 Middleware
### 6.5 创建任务 Handler
### 6.6 TaskService
### 6.7 REST API 分组
### 6.8 统一响应和错误
### 6.9 鉴权与资源范围
### 6.10 Artifact 访问
### 6.11 定时任务
### 6.12 API 模块验收
## 7. 离线分析模块
### 7.1 模块输入输出
### 7.2 Worker 主循环
### 7.3 领取与租约
### 7.4 Analyzer Registry
### 7.5 统一分析接口
### 7.6 perf 火焰图流水线
### 7.7 pprof 流水线
### 7.8 Java Heap 流水线
### 7.9 I/O 与资源分析
### 7.10 建议生成
### 7.11 临时目录与资源隔离
### 7.12 分析模块验收
## 8. Web 展示模块
### 8.1 模块输入输出
### 8.2 应用结构
### 8.3 路由与页面
### 8.4 API Client
### 8.5 创建任务表单
### 8.6 任务状态展示
### 8.7 轮询与事件
### 8.8 火焰图组件
### 8.9 AI/规则建议组件
### 8.10 运行时配置
### 8.11 Web 模块验收
## 9. 数据库与对象存储
### 9.1 数据关系
### 9.2 核心表
### 9.3 Task 表
### 9.4 索引
### 9.5 事务边界
### 9.6 Transactional Outbox
### 9.7 对象分类与生命周期
### 9.8 数据隐私
## 10. 配置与部署
### 10.1 进程布局
### 10.2 启动顺序
### 10.3 健康检查
### 10.4 配置优先级
### 10.5 配置域
### 10.6 Compose 级联调拓扑
### 10.7 多副本注意事项（API / drop_server / Analyzer）
### 10.8 Kubernetes
### 10.9 版本协商
## 11. 安全与可观测性
### 11.1 安全边界
### 11.2 身份与授权链
### 11.3 Agent 最小权限
### 11.4 命令与路径安全
### 11.5 Secret 管理
### 11.6 结构化日志
### 11.7 指标
### 11.8 Trace
### 11.9 告警
### 11.10 审计
## 12. 测试与验收
### 12.1 契约测试
### 12.2 核心采集单元测试
### 12.3 API 单元测试
### 12.4 Analyzer 单元测试
### 12.5 Web 测试
### 12.6 集成矩阵
### 12.7 端到端验收
### 12.8 性能基线
## 13. 分阶段复刻路线
### 13.1 阶段一：契约与基础设施
### 13.2 阶段二：perf CPU 闭环
### 13.3 阶段三：可靠性
### 13.4 阶段四：权限和多用户
### 13.5 阶段五：扩展采样器
### 13.6 14 天演示闭环
### 13.7 最终交付
## 附录 A：接口示例（A.1 创建任务 / A.2 任务详情 / A.3 事件流）
## 附录 B：配置模板
## 附录 C：关键源码定位（C.1 核心采集 / C.2 API / C.3 Analyzer / C.4 Web）
## 附录 D：术语
## 结语
```

---

## 2. 文档声称的系统架构、组件与验收标准

### 2.1 系统定位
"Drop 是一个分布式、按需执行的性能诊断平台"。要解决五类问题：远程调度、受控执行（perf/async-profiler/pprof/eBPF/py-spy）、大文件传递、异步分析、可解释状态。

### 2.2 四个运行模块
| 模块 | 形态 | 职责 |
|---|---|---|
| 核心采集模块 | C++ `drop_server` + `drop_agent` | Agent 注册、心跳、任务队列、采样执行、结果上报 |
| API 编排模块 | Go HTTP 服务 | 身份、权限、任务建模、REST、gRPC 调度、预签名 URL |
| 离线分析模块 | Python 常驻 worker + **Go 堆分析子进程** | 领任务、下载、解析、生成报告 |
| Web 展示模块 | React 单页应用 | 任务创建、列表、状态、可视化 |

共享设施：PostgreSQL（元数据）+ COS/MinIO（大文件）+ gRPC（控制消息）+ HTTP/SSE（用户操作）。

### 2.3 核心对象与协议
- **Agent**：agent_id（稳定身份）、target、capabilities、labels、last_seen_at、status（online/degraded/offline/disabled）、resource_budget。
- **Task**：identity/target/kind/parameters/双状态（collection_status、analysis_status）/parent 子任务/error_code/timestamps；参数存 JSONB（JSON Schema 校验）。
- **TaskAttempt**：一次 Task 的多次执行；attempt_id 保留证据。
- **Artifact**：RAW（perf.data、hprof、pprof、memray）/INTERMEDIATE/RESULT/LOG/MANIFEST；元数据入库，正文在对象存储，只存 object key。
- **AnalysisJob**：pending/running/success/failed + lease_owner/lease_expires_at + 有限重试 + dead-letter。
- **TaskEvent**：可审计状态事件序列（TASK_CREATED→…→ANALYSIS_SUCCEEDED/TASK_FAILED/TASK_CANCELED），递增 sequence。
- **gRPC 四服务**：InitAgent（Init）、HealthCheck（Do）、Hotmethod（Collect/NotifyResult）、Control（CreateTask/StatAgent/FetchData）。
- **TaskDesc**：通用字段 + oneof payload（Perf/AsyncProfiler/Pprof/Ebpf/JavaHeap/Script）。
- **状态机**：采集 = created→queued→delivered→running→uploading→collected（任意→failed/canceled）；分析 = pending→running→success/retry/failed，租约到期回 pending。
- **TaskKind 能力模型**：数字枚举之外还携带 runner、analysis_pipeline、supported_os/arch、requires_capabilities、max_duration、max_concurrency、parameter_schema，由 API 返回可创建类型。

### 2.4 数据流要点（第 3 章）
- Agent 注册（Init→注册表/DB/临时凭证）、心跳 1Hz（healthcheck 附带任务拉取）、创建任务（事务写 Task+Event+Outbox → Control.CreateTask → 心跳下发）、Runner 8 步生命周期（Prepare/Validate/Start/Monitor/Collect/Upload/Report/Cleanup）、上传（size+SHA-256+manifest，NotifyResult 幂等）、Analyzer 领取（SELECT…FOR UPDATE SKIP LOCKED + 5min lease）、Web 展示（SSE 或退避轮询、终态停止、Web Worker 渲染）。
- 复合任务：控制面编排对象，DAG 展开、ALL_REQUIRED/BEST_EFFORT/QUORUM/DAG 聚合策略。
- 计划任务：Cron + leader 锁 + 唯一键 schedule_id+scheduled_at 防重复。
- 错误码分层（§4.10）：14 个稳定错误码（AUTH_FORBIDDEN…DEPENDENCY_UNAVAILABLE），每个含阶段/可重试/HTTP 映射/文案/运维动作。

### 2.5 验收标准清单（文档自述）
- §5.13 核心采集 10 项：注册心跳稳定、离线阈值、队列 deadline/去重/取消、重启恢复、参数不走 shell、子进程清理、hash 一致、NotifyResult 重放幂等、Agent 最小权限、稳定错误码。
- §6.12 API 10 项：幂等键、DTO 与 Schema 一致、资源权限、gRPC 失败可见、分页排序、短期预签名、计划任务不重复、错误不泄露、readiness/liveness 分离、request_id 贯穿。
- §7.12 Analyzer 10 项：租约互斥、崩溃恢复、迟到提交拒绝、hash/格式/大小校验、Analyzer 版本进 manifest、大文件上限、损坏输入不无限重试、上传与状态一致、黄金样本稳定、不走 shell。
- §8.11 Web 10 项：只展示有权数据、表单由 TaskKind 元数据驱动、防重复点击、时间线全覆盖、终态停轮询、SSE 断线恢复、预签名过期刷新、火焰图不阻塞主线程、HTML 清洗、错误可追踪。
- §12.7 端到端 13 项 + §12.8 性能基线 11 项 + §13.7 最终交付（代码契约/质量/运维三大组复选框）。

### 2.6 部署与安全
- 进程布局：PostgreSQL/MinIO/drop_server/API/Analyzer/Web/Agent；启动顺序 PG→OS→migration→server→API→Analyzer→Web→Agent→smoke。
- 多副本：API 无状态但 Cron 需 leader；drop_server 若队列在进程内需分片/持久化队列/粘性路由；Analyzer 用 SKIP LOCKED 水平扩容。
- 安全：mTLS（Agent 身份）+ OIDC（用户身份）双信任链；最小角色 Viewer/Operator/GroupAdmin/PlatformAdmin；argv 不拼 shell；CAP_PERFMON 最小权限；预签名 1~5 分钟；审计不可篡改。
- 可观测：结构化日志统一字段、Metrics 清单（Agent/Server/API/Analyzer 四组）、OTel trace + span link、13 类告警、审计事件清单。

---

## 3. 文档中 AI 方案相关章节的完整要点

### 3.1 §3.10 诊断建议与 AI 流（核心）
```
分析结果 → 规则引擎 → Suggestion（PostgreSQL）
分析结果 → 脱敏数据集 → AI 服务 → Suggestion
Suggestion → SSE → Web AICard → 用户反馈 → 数据库
```
要点：
1. **规则建议与 AI 建议都建立在"已授权的分析结果"之上**，不是直接吃原始采样数据。
2. AI 输入必须走脱敏数据集（§11.6 明确禁止记录"未脱敏 AI 输入"）。
3. 模型输出必须经过 Markdown/HTML 清洗。
4. 每条建议记录：生成引擎、规则/模型版本、输入摘要、生成时间、用户反馈。

### 3.2 §8.9 AI/规则建议组件（前端）
- SSE 事件类型：`status / reasoning_summary / suggestion / evidence / done / error`。
- Markdown 用 DOMPurify 清洗；代码高亮不能绕过清洗；链接加安全属性；**模型输出不能成为可信 HTML**。
- AI 建议走 SSE 流式，AICard 分段渲染。

### 3.3 其他 AI 触点
- §1.5 通信矩阵：Web↔API 的 SSE 承载"状态事件、AI 建议"。
- §6.1/§6.7：SSE 请求输入 → TaskEvent/AI 流；REST 分组含 `Suggestions`（`/api/v1/tasks/:id/suggestions`，规则/AI 建议和反馈）。
- §9.2 核心表：`suggestions` 表（engine、version、evidence、content、feedback）。
- §13.5 阶段五扩展采样器列表：**AI 建议排在第 10 位（最后一位）**，属于"按价值增加"的收尾能力。
- §12.4/12.5 测试：Web 测试含"XSS/恶意 Markdown"。

**定位**：文档把 AI 描述为一种克制的"诊断建议补充层"（规则引擎为主、LLM 为辅，出 Suggestion + AICard + 反馈闭环），并未把 AI 当作核心能力；AI 在路线图中排最后。

---

## 4. 文档自述的复刻完成度 / 未完成项

### 4.1 文档本身的自我定位
`replication-guide.md` 是一份**目标蓝图/复刻指南**（"面向需要理解或重建 Drop 类平台的团队"），大量使用"建议/应/推荐"语气，验收标准都是未勾选的 `- [ ]` 复选框。文档本身**没有**直接声称"已全部完成"。

### 4.2 文档内的完成度线索
- §10.6：明确承认"真实采样需要 Linux 内核能力；业务联调可用 fake-agent 上传固定小样本；**内核采样验收必须在受控 Linux 节点进行**"——即已预见到 eBPF/perf 真实验收未完成。
- §13.1~13.5 五阶段路线 + §13.6 14 天演示计划 + §13.7 最终交付清单：均为目标态。
- 结语："复刻时应先保证这条链…可解释，再逐步增加采样器和高级分析能力"。

### 4.3 同目录配套文档自述的完成度（用于交叉核对）
- `docs/architecture/implementation-status.md`（2026-08-06）：自称已闭环 Go API 原生任务/取消/归档/产物/审计/SSE、C++ `native/control` 灰度控制面接管 Compose 默认、C++ Collector 插件注册表（perf_cpu/ebpf_io/pyspy/go_pprof/memory_smaps/sys_metrics/continuous_perf/java_async）、Transactional Outbox、Schedule/Cron、Composite DAG、修复前后 VERIFIED、CreateTask 幂等、pprof/pyspy 分析器、AI 可信性（claim_verifier/反证门禁/hypothesis_predicate）、Artifact 生命周期对账、跨语言 CI、OpenAPI+TaskKind JSON Schema。回归：Python 473 passed / Go test 通过 / React build 通过。
- `docs/architecture/remaining-work.md`（2026-08-06）明确剩余缺口：
  - **P0：原生 Ubuntu eBPF 与跨语言采集矩阵未真机验收**（Windows Docker Desktop 只能验证能力检查和失败路径）；**统一公开基准 90 次实验未完成**（10 类×3 策略×3 重复，当前仅 Golden 确定性门禁与部分 OTel Demo 结果）。
  - P1：性能指标（心跳 P99/吞吐/SSE 连接数/首屏时间）、持久化用户组/密钥轮换/管理后台。
  - P2：K8s 清单、多架构镜像/SBOM/签名、Dashboard/告警/Runbook、持续 profiling 容量测试。
- 汇报口径分三层：代码已实现且有测试 / Docker 环境端到端验证 / 原生 Ubuntu 或公开基准现场实验。**前两层基本达成，第三层（真实内核验收 + 90 次公开基准）未完成**。

---

## 5. 文档与代码一致性疑点（重点）

> 以下"实际"均来自对仓库源码/迁移/README 的实地核验（本次会话完成）。

### 5.1 附录 C"关键源码定位"大面积对不上仓库（最可疑）
| 文档声称路径 | 实际仓库 |
|---|---|
| `drop/server/main.cpp`、`drop/server/HealthCheckService.cpp`、`drop/server/ControlService.cpp`、`drop/agent/main.cpp`、`drop/agent/HotmethodChannel.cpp`、`drop/common/Process.cpp` | **不存在 `drop/` 目录**。实际 C++ 在 `native/agent/src/`（main.cpp、perf_collector.cpp、ebpf_io_collector.cpp、language_collectors.cpp、proc_collectors.cpp、artifact_uploader.cpp、process_runner.cpp、result_outbox.cpp）与 `native/control/src/main.cpp` |
| `apiserver/main.go`、`apiserver/server/server.go`、`apiserver/server/task.go`、`apiserver/model/dbModel.go`、`apiserver/pkg/storage/storage.go` | 不存在这些子目录。实际为 `apiserver/cmd/apiserver/main.go` + `apiserver/internal/{config,httpapi,objectstore,repository}` |
| `analysis/hotmethod_analyzer.py`、`analysis/data_parser/collapsed_data_parser.py`、`analysis/flamegraph.py`、`analysis/storage.py`、`analysis/java_heap_analyzer/main.go` | **不存在 `analysis/` 目录**。实际为 `analyzer/mini_drop_analyzer/{hotmethod_analyzer,pprof_analyzer,pyspy_analyzer}.py`；**`java_heap_analyzer/main.go` 全仓库不存在** |
| `web_frontend/src/index.js`、`web_frontend/src/router/index.js`、`web_frontend/src/api/index.js`、`web_frontend/src/pages/taskResult/index.js`、`web_frontend/src/components/flamegraph/flamegraph.js` | 不存在 `web_frontend/`。实际为 `web/src/main.jsx`、`router.jsx`、`api/client.js`、`pages/TaskResult.jsx`、`components/FlamegraphViewer.jsx`（Vite+React18） |

### 5.2 "核心采集模块 = C++ drop_server"与实际演进不符
- 文档把 C++ `drop_server` 描述为既定核心。实际历史上控制面长期是 **Python gRPC**（`server/app/grpc_server.py`，proto 注释也写"Python gRPC 调度"）；C++ `native/control` 是 2026-08-01 才上线的**灰度控制面**（监听 50052，Python 50051 保留回滚）。`implementation-status.md` 明言"指南中的 C++ drop_server 尚未替换 Python gRPC 控制面"这一表述在后续更新中改为"已接管 Compose 默认控制面"。
- 协议细节不符：文档 §2.7 称 Hotmethod 服务含 `Collect`（任务拉取），实际 `hotmethod.proto` 只有 `NotifyResult`，**任务拉取是夹在 `HealthCheckResponse` 里由心跳带走的**；Control 实际只有 `CreateTask`/`StatAgent`，没有文档声称的 `FetchData`。

### 5.3 "Go API 编排模块"职责夸大
- 文档说 Go API 负责"任务建模、gRPC 调度、预签名 URL"全编排。实际 `apiserver/README.md` 明言：**Go 是统一 API 入口，但大量写接口"尚未迁移"，反向代理到 Python 服务**；gRPC 调度至今主要仍是 Python。Go 原生只实现了核心任务 CRUD/取消/归档/产物读/审计/SSE 与部分 AI 只读查询。

### 5.4 Java Heap 分析流水线"文档有、代码无"
- 文档 §7.8 详细描述了 Java Heap 流水线（HPROF 解析、GC Roots、Dominator Tree、Retained Size、泄漏路径），并声称由 **Go 堆分析子进程**（`analysis/java_heap_analyzer/main.go`）执行；§7.4 Analyzer Registry 也列了 `JAVA_HEAP`。
- 仓库全量搜索：**无任何 hprof/jmap/heap 分析代码**；`analyzer/mini_drop_analyzer/` 只有 perf/pprof/pyspy 三个分析器。这是全文最典型的"文档吹牛、代码没有"点。

### 5.5 数据库表清单与迁移不符
- 文档 §9.2 列出 14 张表：`users`、`groups`、`group_members`、`group_agents`、`task_events`、`suggestions`、`outbox` 等。
- 实际 `server/app/models.py`（基线迁移来源）含约 38 张表：**没有 `users/groups/group_members/group_agents`**（鉴权靠环境变量 Principal，实现文档自己也承认"持久化用户组属后续生产化"）；**没有 `task_events`**（实际叫 `task_status_events`）；**没有 `suggestions` 表**（建议作为规则引擎输出写入证据/报告，`suggestions` 只作为字段名存在于 rca/evidence、summarizer、artifact 产物 `suggestions_md` 中）；outbox 实际叫 `outbox_messages`（§9.6 通用 Outbox 已按此实现）。
- 实际表里还多出一大批文档未提的诊断表：`diagnosis_sessions/diagnosis_events/diagnosis_evidence/drop_insight_*`、`rca_feedback`、`topology_snapshots`、`continuous_diagnosis_triggers`、`agent_metric_snapshots` 等——**文档低估了真实系统的 AI 诊断数据模型**。

### 5.6 AI 能力"文档低估、代码超配"
- 文档把 AI 定位为最后一位的"建议卡片"。实际代码已实现远更重型的 AI 栈：`server/app/ai_provider.py`（DeepSeek/OpenAI 兼容 chat、provider 能力探测）、`nlp/`（intent_parser、summarizer）、`rca/`（llm_client、candidates、calibrator、repair、report、rules.json、falsification 反证）、`diagnosis/`（intent、orchestrator、pipeline、probe_registry、eval_harness、benchmark_*）、`drop_insight/`（session/hypothesis/evidence/tools/policy/claim_verifier）、`fix_verifications` 修复验证；Web 有 AIDiagnosis/ChatThread/EvidenceCard/ToolCallCard/FixVerificationPanel。文档 §3.10/§8.9 只是这套庞大能力的一个子集描述。

### 5.7 状态机命名不一致
- 文档采集状态：created/queued/delivered/running/uploading/collected（+failed/canceled）；实际任务状态为 `PENDING→RUNNING→UPLOADING→ANALYZING→DONE/CANCELLED/FAILED`（见 implementation-status 真实验收链）。Agent 状态文档为 online/degraded/offline/disabled，实际以 AGENT_ONLINE/AGENT_OFFLINE 审计事件表达。

### 5.8 文档建议与实现存在但细节偏移的部分
- TaskDesc `oneof` 建议、JSONB 参数、幂等键（`UNIQUE(creator_id, idempotency_key)`）、乐观锁 version、SKIP LOCKED 租约、outbox、短期预签名、argv 不走 shell、DOMPurify 清洗、Web Worker 火焰图、TaskKind JSON Schema（`docs/contracts/taskkind.schema.json`）、OpenAPI 契约、跨语言 CI——**这些均已实现**，属于文档方向正确、实现跟进到位的内容。
- 采样器矩阵（§5.9）中 async-profiler/memray/BOLT/gperftools 多为"能力声明"而非真实产物；`java_async` 采集器"仅运行时真实存在时声明能力"（remaining-work 也承认 async-profiler 矩阵未真机验收）。

### 5.9 结论性判断
`replication-guide.md` 是一份**高质量目标架构蓝图**：方向正确、边界清晰、与大量已实现工程项（outbox、lease、幂等、契约、CI、安全）高度吻合；但它的"源码定位（附录 C）"指向一个**并不存在的目录布局**（drop/、analysis/、web_frontend/），Java Heap 分析、C++ drop_server 全量落地、Go 全编排、users/groups/suggestions 表等均属**文档领先于代码（或未落地）**；同时它**严重低估**了真实代码里已经存在的重型 AI 诊断体系（EvidenceLoop/Drop Insight/RCA/NLP）。阅读者应把本文当"目标规范"而非"现状描述"，并以 `implementation-status.md` + `remaining-work.md` 作为真实完成度口径。

---

*（报告完）*
