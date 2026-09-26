# Mini-Drop 性能 SRE Agent：开源调研与接入方案

日期：2026-09-19。状态：研究提案，未改变生产架构，未部署外部服务。

本报告基于 Mini-Drop 当前工作树、官方文档、论文和部分上游源码。没有对开源项目进行生产效果排名，也没有运行同机比较。用户提供的凭据不进入本文件；本轮没有调用收费模型 API，因此不宣称账号或模型已经实测可用。

## 1. 建议定位与选型

定位为性能 SRE Agent：从业务影响出发，查询已有观测，必要时主动采集，维护可证伪假设，输出可追溯结论，并独立验证修复效果。

建议保留 LangGraph Runtime、PostgreSQL 权威状态、MinIO 原始产物和 C++ 按需采集器。新增能力围绕这些基础渐进接入，不同时运行两套领域状态机。

| 能力 | 建议 | 暂不做 |
|---|---|---|
| 在线调查 | 有预算的多轮 ReAct 风格工具循环 | 将单轮问答作为主要基线 |
| 分支选择 | 可选的假设调度策略，保留现有 LATS 作为实验臂 | 把实时跨时间观察称为可复位完整 MCTS |
| RAG | BM25 + 向量检索 + RRF + reranker | 用纯向量检索替换函数名、错误码精确匹配 |
| Embedding | 先评估硅基流动 Qwen3-Embedding-4B，1024 维 | 未评测就选最大模型 |
| Reranker | 先评估 Qwen3-Reranker-4B | 把相关性分数当根因概率 |
| 向量索引 | Chroma 单节点服务；定义可替换接口 | 首批同时部署 Chroma、Milvus |
| 权威记忆 | PostgreSQL + 可重建向量索引 | 将全部聊天自动写成永久事实 |
| 外部观测 | Grafana MCP 的允许工具子集、受限查询 | 开放任意第三方 MCP、任意生产 shell |
| 开源 Agent | HolmesGPT 独立评测或隔离咨询模式 | 将它的最终自然语言直接当本项目 Evidence |

这些是候选方案。向量模型应按账号可见模型、领域检索质量、成本与延迟实测后固定版本。当前线上仍是原有架构。

## 2. 开源项目观察

### HolmesGPT：工具循环和运行治理

源码核对快照：`3bd44edf04f9587c778ee8e9b244965190c40fdf`。

`holmes/core/tool_calling_llm.py` 包含工具调用循环、重复调用抑制、上下文压缩、大结果转存和并发工具执行。它展示了工具 Agent 的工程重点，不需要先用树搜索才能形成多轮调查。

Mini-Drop 借鉴点：结构化 ToolResult、结果引用和按需读取、取消和截止时间、工具身份与审计。并发度必须按目标负载重新设计，不能照搬上游线程池数量。

来源：[源码](https://github.com/HolmesGPT/holmesgpt/blob/3bd44edf04f9587c778ee8e9b244965190c40fdf/holmes/core/tool_calling_llm.py)、[评测](https://holmesgpt.dev/latest/development/evaluations/)。

### Redis SRE Agent：与当前技术栈接近

源码核对快照：`5a833b8f284fce7aaad534f0678d5fc659f3ca77`。

`redis_sre_agent/agent/langgraph_agent.py` 显式组织 agent、tools、reasoning 节点，工具节点回到 agent。`docs/how-to/agent-memory.md` 将用户偏好与资产历史分开；原始线程、知识库和实时观测不由长期记忆替代。

Mini-Drop 借鉴点：知识查询与现场调查区分，用户/资产记忆隔离；记忆服务失败时继续当前调查，并显示降级状态。

来源：[循环源码](https://github.com/redis-applied-ai/redis-sre-agent/blob/5a833b8f284fce7aaad534f0678d5fc659f3ca77/redis_sre_agent/agent/langgraph_agent.py)、[记忆设计](https://github.com/redis-applied-ai/redis-sre-agent/blob/5a833b8f284fce7aaad534f0678d5fc659f3ca77/docs/how-to/agent-memory.md)。

该快照 LICENSE.txt 提供 RSALv2、SSPLv1、AGPLv3 选择，不应笼统当作 Apache/MIT 项目直接复制；本提案采用机制参考。[许可证原文](https://github.com/redis-applied-ai/redis-sre-agent/blob/5a833b8f284fce7aaad534f0678d5fc659f3ca77/LICENSE.txt)

### RunbookAI：假设树与混合检索

源码核对快照：`75f8ba52012f79e5c40b8e98ed4480416c2a8c65`。

`src/knowledge/retriever/hybrid-search.ts` 将全文与向量结果通过 RRF 合并。`src/agent/hypothesis.ts` 实现假设的生成、证据、分支和剪枝。后者本身不能证明采用 LATS/UCT。

借鉴：将调查对象与工具选择分开，检索保留精确和语义两个通道。谨慎对待其置信度和确认条件，不替换 Mini-Drop 证据门禁。GitHub API 未识别该快照许可证；不在本次复制实现。

来源：[检索源码](https://github.com/Runbook-Agent/RunbookAI/blob/75f8ba52012f79e5c40b8e98ed4480416c2a8c65/src/knowledge/retriever/hybrid-search.ts)、[假设源码](https://github.com/Runbook-Agent/RunbookAI/blob/75f8ba52012f79e5c40b8e98ed4480416c2a8c65/src/agent/hypothesis.ts)。

### ITBench：外部评测参照

旧 ITBench-SRE-Agent 仓库已归档并指向合并仓库。本次读取合并仓库快照 `30673b23a7166fc53162b2e6c23a364e7c5f0197`，README 展示 runner、SRE 工具与 evaluator 的分离，并有 ReAct 调查提示入口。不能沿用旧文章将其当前实现描述为固定 CrewAI 架构。

借鉴：独立事故输入、Agent 输出和真值裁判，版本化运行记录。先导入适合性能诊断的少量场景；Kubernetes 基础设施事故成绩与四语言进程采样成绩分开报告。

来源：[现仓库](https://github.com/itbench-hub/ITBench-CISO-SRE-FinOps-Agent/tree/30673b23a7166fc53162b2e6c23a364e7c5f0197)、[ITBench](https://github.com/itbench-hub/ITBench)。

### Deep Agents：Harness 机制参考

适合研究大工具结果外置、文件后端、摘要、Skills 与子任务上下文隔离。文件读写或执行能力依赖后端与权限，并不天然适合直接接生产机器。

本项目保留 LangGraph，选择性借鉴 middleware 机制；如引入包，先验证版本兼容与工具表，不默认继承 shell 或通用文件写入。

来源：[项目](https://github.com/langchain-ai/deepagents)、[文件系统 middleware](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/filesystem.py)。

### 其他候选

- [Grafana MCP](https://github.com/grafana/mcp-grafana)：直接对接候选，有禁止写工具的运行模式；仍需查询预算和后端凭据权限。
- [Coroot](https://github.com/coroot/coroot)：参考服务拓扑、业务影响与观测关联，不先复制整个平台。
- [Pyroscope](https://grafana.com/docs/pyroscope/latest/configure-client/trace-span-profiles/)：Profile 历史与 Span 关联参照，Trace 字段透传不等于样本关联。
- [srtux/sre-agent 记忆设计](https://github.com/srtux/sre-agent/blob/main/docs/concepts/memory.md)：参考工具失败经验与调查模式，属于社区实现，不因使用 GCP 就称为 Google 官方 Agent。
- [Mem0](https://github.com/mem0ai/mem0)：可评估记忆抽取/更新机制，不直接授予权威证据写入权。
- [Graphiti](https://github.com/getzep/graphiti)：当服务关系与历史变化成为实际瓶颈，再评估时间关系图；当前不为完整性新增图数据库。

## 3. ReAct、LATS 与组合

ReAct 是观察驱动的推理与行动循环，不等于一次工具调用。LATS 在轨迹候选上增加树搜索、评价与回传。二者不是互斥产品框架。

原论文明确将可在迭代间回退的任务作为研究设定之一；线上性能故障随时间变化，Profile 也会消耗时间并产生扰动。搜索历史分支时不能恢复事故现场。

来源：[ReAct 原实现](https://github.com/ysymyth/ReAct)、[LATS 论文](https://arxiv.org/abs/2310.04406)、[论文全文](https://openreview.net/pdf?id=njwv9BsGHF)。

推荐将下一步选择封装为统一策略接口，先建设强 ReAct 基线：

```text
读取调查状态和未解决证据缺口
  -> 按需查文档/历史事故/现有观测
  -> 提出下一动作和简短依据
  -> Harness 检查身份、预算、并发和参数
  -> 执行查询或采集，保存不可变观察
  -> 证据校验、更新假设、检查停止条件
  -> 继续 / 等待审批 / 证据不足 / 输出报告
```

候选 HYBRID 策略：外层只决定当前最值得调查的假设；内层用有界 ReAct 完成该假设的取证。每次真实工具结果都回到全局状态并扣同一总预算，不允许内外层各自获得一份预算。Hypothesis ID、ToolCall ID 和 Evidence 引用跨层一致。

在线称为预算化假设搜索，不称 FULL_LATS。冻结只读快照、可重置实验环境才允许完整 rollout。任何模拟观察必须与生产 Evidence 分开。

升级搜索的候选触发条件是多个原因仍难区分、同分支连续无信息增益、已有反证、采集成本差异较大；条件与阈值需要回放校准。

奖励优先考虑减少了什么不确定性、是否获得归属清楚的有效观察，同时扣除时间、成本和扰动。不能单纯奖励 VERIFIED 数量，也不能把模型自评分当正确率。检索命中和重复引用同一产物不增加独立证据数量。

## 4. RAG：真正可按需调用的调查工具

当前 retrieval.py 是本地确定性检索；diagnosis_agent.py 的主要模型工具是 request_diagnostic_probe。新增向量索引并不会自动将其变成能主动改变检索问题的 Agentic RAG。

建议提供 search_knowledge、read_knowledge_chunk、search_incident_memory，允许模型在观察到新信息后继续检索；输入范围由 Harness 绑定。

候选流水线：

```text
批准的 runbook / 官方技术资料 / 架构记录 / 已复核事故
  -> 结构化切块，保留标题、代码块与父章节
  -> 文档 hash、版本、适用运行时、服务和权限标签
  -> BM25 与 Embedding 两路索引
  -> 检索前按权限过滤
  -> 两路候选 RRF 融合
  -> reranker
  -> 少量片段及来源引用；必要时读取父章节
```

起步实验参数：每路召回 20 条，融合后重排不超过 30 条，正文入上下文 5～8 条，另设总 token 上限。这些是待调参数，不是实测最佳配置。

向量库只负责候选相似度检索。故障日志、时序指标、Trace 和 Profile 应主要通过对应查询引擎获取；不要把整机采样和全部日志无差别向量化。

需保存 chunk_id、source_uri、source_hash、source_version、runtime、service、environment、tenant/ACL、valid_from/to、embedding_model、dimension 和 index_version。索引以可见版本原子切换；原文删除或撤销后，词法和向量两侧同步失效。

报告中含无证据指标的材料先隔离，不可作为可靠事故知识入库。Benchmark 私有答案、账号配置和旧错误结论不进入运行时语料。

### 硅基流动与向量库

官方文档列出 Qwen3-Embedding 4B 及可选 1024 维；rerank 是独立接口。建议分别配置生成模型、Embedding、Reranker，增加超时、限流退避、批处理与内容 hash 缓存。当前 ChatDeepSeek 的构造不能被当作任意模型通用适配器，需新增模型工厂并验证工具调用兼容。

来源：[Embedding API](https://docs.siliconflow.cn/docs/api/embeddings-post)、[Rerank API](https://docs.siliconflow.cn/docs/api/rerank-post)。

Chroma 适合当前单节点接入试验。服务放内网，应用负责身份过滤，不依赖集合名实现完整授权。BM25/RRF 在应用层实现，不假定本地版具备云服务的所有搜索功能。[Chroma 架构](https://docs.trychroma.com/reference/architecture/overview)、[服务配置](https://docs.trychroma.com/reference/server-env-vars)

Milvus 作为后续规模化后端候选，迁移由容量、并发、延迟和运维实测触发。Milvus Lite 当前官方列出的本地平台是 Linux/macOS；本项目 Windows 开发环境不默认按原生 Lite 路线部署。[Milvus Lite](https://milvus.io/docs/milvus_lite.md)

向量或重排服务失败时退回词法检索，并记录实际后端与降级原因；不可显示为语义检索成功。Embedding 模型或维度变化必须创建新索引，不能混写旧集合。

## 5. 记忆：五类内容与生命周期

| 类别 | 存储与作用 | 必要约束 |
|---|---|---|
| 当前调查状态 | PostgreSQL 与 LangGraph checkpoint | 已尝试工具、反证、证据缺口、预算不能在摘要中丢失 |
| 用户偏好 | PostgreSQL，按 principal | 语言/详略等；不含资产权限 |
| 资产知识 | PostgreSQL，可向量索引 | 服务/环境/版本/有效期；变化后复核 |
| 事故经历 | PostgreSQL + Chroma 派生索引 | 区分观察、推测、人工确认、修复验证；保留来源 |
| 程序性经验 | 版本化 Skill/runbook | 失败路径也可记录，发布有评测与回滚 |

可跨会话保存“该版本工具在某内核缺能力”，但应有失效时间，升级后重新探测。不可永久记住旧 PID，也不可把上次根因复制成本次结论。

写入流程：提取候选 -> 去除敏感信息 -> 绑定用户或资产范围 -> 校验来源/验证级别 -> 去重或标记冲突 -> 审核或规则晋级 -> 建索引。删除、撤销和权限变化必须同时影响事实存储与索引。

Mem0 可用作候选记忆抽取实验臂；先用自有结构化模型更容易保持审计。Graphiti 留待时间关系查询需求明确之后，不一开始同时引入多套记忆后端。

## 6. Harness：模型外部的执行约束

当前 harness.py 主要保护 scope 候选；实际 Harness 职责还分散在 service.py、policy.py、Evidence 和 Worker 中。应统一合同，不必把所有逻辑搬入一个文件。

1. 目标：绑定租约、进程启动身份、服务范围与观测窗口；恢复时重新确认。
2. 工具：严格参数 schema、风险级别、耗时、可观察能力、只读/采样/变更分类。
3. 预算：总截止时间、模型 token、检索次数、工具数量、单目标采样并发与字节上限。
4. 故障：超时、取消、幂等重试、Worker 崩溃恢复；不明执行结果先对账，不能盲目重放变更。
5. 上下文：大输出转存 MinIO，通过有界分页或字段读取；摘要保留 Evidence ID、反证和未解决问题。
6. 信任：文档、日志、MCP 返回文字都按数据处理，不能改变授权；摘要或 reranker 也不能授予权限。
7. 证据：保留实际时间、单位、资源归属、查询语句、质量限制和来源；工具成功不等于结论正确。
8. 变更：生成结构化 ChangePlan，包括前置条件、范围、回滚、观察指标和停止条件；变更与只读调查独立授权。
9. 观测：记录每步延迟、token、工具失败、实际模型、降级、重试、重复调用和最终结果类型。

子 Agent 是按需能力，不作为第一阶段必选。可在同一冻结窗口分别分析 CPU、内存、依赖；它们共享证据索引与总预算，并返回结构化发现。多个模型同意不计作多份独立证据。同一 PID 的高开销采样由统一调度串行或限流。

## 7. 外部接入合同

建议定义 ConnectorObservation，至少包含：connector_id、query、resource_identity、requested/effective_window、retrieved_at、payload_uri、sha256、schema_version、units、quality、limitations、auth_scope。

当前 Evidence 要求原生 TaskAttempt/Artifact/AnalysisJob 引用。外部 Prometheus/Trace 查询不能伪造这些身份来通过门禁。应新增显式来源类型及专属验证器，保留同等强度的来源、时间、身份和不可变产物校验，再统一投影为可验证声明。

第一条接入建议为 Grafana MCP，只启用必要只读工具和受限凭据，限制时间范围、series 数量、返回字节与服务标签。读取成本和查询范围同样需要预算。版本固定后核对所需 Profile/Trace 工具；缺少时使用对应产品官方 API adapter，不假定 MCP 包含全部能力。

HolmesGPT 两种用法：

- 独立实验臂：同样的数据、工具能力和预算，保存全部调用与结果，比较最终业务结论。
- 隔离咨询：返回候选假设和下一探针建议，由 Mini-Drop 验证执行。它的总结属于建议，不属于可直接引用的现场证据。

不要把“嵌入另一个 Agent”当成数据接入捷径，否则预算、审批、重试与证据来源会出现两个权威。

## 8. 实施顺序与验收

### 阶段 A：建立可信基线

纠正无原始支撑的评测表述、80% 条目覆盖放宽和关键词处置命令。固定当前版本，保存既有严格失败记录。强 ReAct 基线与 LATS 共用工具、证据门禁和总预算。

验收：无反证覆盖不能被其他条目抵消；证据不足不得自动生成无条件变更建议；旧报告不可回写。

### 阶段 B：Hybrid RAG 与按需检索

引入独立 provider 接口和 Chroma adapter，版本化增量索引，注册知识查询工具，保留词法降级。只用公开或批准语料进行最小 API smoke test，记录模型、维度、耗时和请求状态，不记录凭据。

验收：中英混合、函数名、错误码、同义症状和无相关文档；删除/版本切换/跨用户过滤；Embedding 超时与模型维度变化。度量 Recall@k、nDCG、引用正确率、延迟和成本。

### 阶段 C：运行约束与资产记忆

将预算和 ToolResult 合同集中，完善结果分页、重复抑制、checkpoint 恢复与记忆候选晋级。

验收：崩溃后不重复高风险动作，取消后停止采样；假结论不会晋级，用户偏好不进入共享资产记忆，旧版本经验可撤销。

### 阶段 D：接入业务观测

先接一个服务的指标和 Trace，再按证据缺口触发 C++ Agent。建立 CPU、锁等待、下游延迟三条同负载修复案例。

验收：服务实例/时间/来源一致；Trace 关联区分窗口级与 Span 采样级；记录 P95/P99、吞吐、错误率和采集开销。

### 阶段 E：搜索策略对照

对比规则路线、多轮 ReAct、ReAct+RAG/记忆、Hybrid LATS+ReAct。采用分阶段消融，避免一次改变模型、RAG 和算法后把收益都归给 LATS。

同模型、同目标、同工具权限、同总预算；可控实验随机顺序或重置，多个重复试验。未知事故家族、无故障和工具不可用样本必须包含在保留集。

报告分别给出定位正确率、错误确认率、正确弃权率、证据覆盖、首次有用发现耗时、总成本与修复验证率。按事故家族聚类统计，模板变体不能全部当独立事故计算显著性。模型裁判可辅助，但不能是唯一真值。

## 9. 建议代码落点

| 当前位置 | 候选改造 |
|---|---|
| agent_runtime/retrieval.py | 保留词法实现，增加 Retriever 接口和版本化 RetrievalTrace |
| agent_runtime 新 providers/ | 分离 Chat、Embedding、Rerank provider；配置不携带密钥明文 |
| agent_runtime 新 knowledge_index/ | Chroma adapter、入库/删除/重建、索引版本与 ACL |
| drop_insight/diagnosis_agent.py | 按需知识和记忆工具、统一模型工厂、有界工具循环 |
| drop_insight/lats.py | 继续作为策略模块，独立配置实验臂 |
| drop_insight/service.py | 逐步分离 scope、investigation、report、effects，保留事务边界 |
| 新 connectors/ | 外部观测合同、Grafana MCP 与产品 API adapters |
| 现 Evidence/Claim verifier | 显式外部来源验证器；不放宽原生证据完整性 |
| tests 与 benchmarks/ | 检索保留集、恢复故障注入、策略消融、外部事故集 |

实施架构或部署变更时，必须同步 PROJECT_CONTEXT.md、AGENT_RUNTIME.md 及对应专题文档；本文件是研究记录，不能替代当前架构真相。
