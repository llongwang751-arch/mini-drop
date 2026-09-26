# 性能 SRE Agent 改造实施记录

日期：2026-09-19。状态：本地代码、索引及测试已完成本轮验证；未发布云端，未完成真实事故修复对照。

## 做了什么

保留 Mini-Drop 已有的 LangGraph 编排、PostgreSQL 业务状态、MinIO 产物、Native Agent 和证据门禁。在这些基础上增加可按需查资料、查历史、查服务观测的调查能力。参考资料不会变成本次事故事实。

| 模块 | 本轮实现 | 仍需验证的边界 |
|---|---|---|
| 证据判定 | 恢复完整条件覆盖，缺失反证判据不能用 80% 覆盖率平均掉 | VERIFIED 仍只是当前门禁通过，不等于真实根因或修复成功 |
| 处置建议 | 按 SUPPORT claim 类型生成前提、验证与回滚要求；不按自由文本关键词分类；TOP_FUNCTION_PERCENT 单独不能推断 CPU | 没有自动执行修复；历史命令方案不再作为当前可执行建议展示 |
| Agent 工具循环 | search_knowledge、read_knowledge_chunk、search_incident_memory；查询预算、去重、错误降级及持久审计 | 不是通用自主 shell；6 次主模型调用上限不包含摘要调用，45 秒是查询准入期限 |
| RAG | BM25 + Qwen3-Embedding-4B/1024 + Chroma + RRF + Qwen3-Reranker-4B；展示真实后端及降级 | 尚未标定召回率、拒答阈值、延迟分位数及成本 |
| 索引 | 白名单公共文档、分块 hash、模型/维度/内容版本、构建完成再发布；旧索引保留 | 未支持私有租户知识导入、自动垃圾回收或高可用 |
| 记忆 | 在现有状态、Checkpoint、显式偏好、Skill 之外，读取同用户/服务/环境近期严格报告；归档撤销召回 | 历史事实未重验；无自由写回、跨租户共享或自动经验晋升 |
| 外部观测 | Grafana Prometheus HTTP datasource proxy 适配器；两种有界服务指标模板，固定目标和时间窗，保留 provenance | 尚无真实 Grafana 连接验收；不是 MCP transport，也未冒充原生 Evidence |
| 调查策略 | 页面/API 可选 ReAct 顺序基线和 LATS；同工具、模型、资源与门禁，持久化真实选择策略 | 复用候选生成及审计事件，不是论文原样复现；未做真实公平 A/B |
| 部署准备 | 检索配置示例、可选 compose 层、Python 镜像可安装检索依赖 | Docker daemon 未启动，容器部署与原线上服务未改变 |

主要代码在 `server/app/agent_runtime/semantic_retrieval.py`、`incident_memory.py`、`grafana_observations.py`、`investigation_strategy.py`，接入点在 `server/app/drop_insight/diagnosis_agent.py` 与 `service.py`。当前 Runtime 标识为 `diagnosis-agent-v5-retrieval`，Theme 为 `evidence-first-diagnosis-v2-retrieval`。

## 真实验证记录

| 检查 | 结果与记录 |
|---|---|
| Python 全量回归 | 583 passed / 6 skipped，见 `sre-agent-python-20260919-r2.xml`。其中 5 项需要独立 PostgreSQL，1 项需要可选 Chroma 包。后续改动再做下述定向验证。 |
| 最终 Agent/RAG/门禁回归 | 带 Chroma 环境下 67 passed，见 `sre-agent-final-validation-20260919.xml`。包含真实 Chroma 持久化/查询、SDK 启动及管理请求超时检查、实际 LangGraph 工具循环、记忆隔离和撤销、Grafana 绑定范围、ReAct 持久选择与幂等。外部 API 和 LLM 在这些测试中用替身。 |
| 策略与原有搜索回归 | 94 passed / 2 skipped，见 `sre-agent-targeted-final-20260919.xml`；两项 Chroma 测试已在可选依赖环境另行通过。 |
| Web 全量及修复复验 | 全量最初 173 passed / 1 failed，失败是新增 budget 字段使旧请求断言失效；修正后相关 17 项通过。最终新增策略切换、旧命令隐藏并复验驾驶舱等共 40 项通过，见 `sre-agent-web-final-20260919.json`。没有把分批测试重复相加成全量成绩。 |
| Web 构建 | `npm run build:check` 通过，入口约 689.5 KiB；图表大包提示仍存在。 |
| API 合同 | 83 组 method/path 路由检查通过；手写 OpenAPI 源合同补充 investigation_strategy。 |
| Compose | 用合成占位环境执行 `docker compose ... config --quiet` 返回 0；不等于容器启动成功。 |
| 硅基流动真实连通性 | `semantic-retrieval-smoke-20260919-r4.json`：两篇合成文档，中文 Java 阻塞查询，首位命中 lock，实际后端 BM25_CHROMA_RRF_RERANK，无降级，单次 3.294 秒。不是延迟 Benchmark，也不是事故准确率。 |
| 本仓库真实索引 | `knowledge-index-20260919.json`：公共知识 9 块已建立，集合 `md-knowledge-b68ffe398cb98d60640e6b9da8dee8a3`，状态 READY。持久文件在本地 `artifacts/knowledge-chroma`，被 Git 忽略。 |

凭据通过隐藏输入进入当前进程，不写入代码、配置样例、报告或索引。全量测试首次并发执行曾发生前端内存耗尽和一次 SQLite disk I/O error；降低前端并发、后端重新运行后通过，原失败报告保留。Chroma 联调中发现 Windows 短路径归一化及修改 ready 时不能重复传距离函数两处问题，均已修复并新增真实 SDK 回归。

## 开源项目如何落到本项目

源码版本、许可证注意事项、论文与官方文档链接见 [开源调研](performance-sre-agent-research-20260919.md)。该调研的“未调用 API”描述属于当时的研究阶段；本实施记录包含后续实际 API 调用。

- HolmesGPT：借鉴观察后继续调用工具、控制上下文与重复查询的方式；未引入其整套运行时。
- Redis SRE Agent：借鉴按用户和资产范围区分记忆；此处先用现有 PostgreSQL 报告读取，避免多套权威状态。
- RunbookAI：借鉴词法/语义融合及假设组织，代码独立实现；未复制许可不明代码。
- Chroma 与 LangGraph：实际使用其库；Grafana 连接器对接官方 HTTP 数据源接口，没有声称已部署 Grafana MCP。
- ITBench：作为真实事故评测方向，本轮未下载并运行官方完整事故套件。Mem0、Graphiti、Milvus 尚未接入，不把工具数量当作效果。

## 当前怎么使用

配置和容器命令见 [Agent Runtime](../../docs/AGENT_RUNTIME.md)。本地在安装检索可选依赖的 Python 环境中设置 `MINI_DROP_CHROMA_PATH` 到仓库 `artifacts/knowledge-chroma`、`MINI_DROP_RETRIEVAL_MODE=hybrid`，并从私有环境提供 SiliconFlow key。默认配置继续走词法检索，避免缺密钥时假装向量服务在线。

资料或模型变更后运行 `scripts/build_knowledge_index.py` 重建；可用 `--prompt-key --output <新的报告路径>` 隐藏输入。云端启用需完整重建 Python 镜像、部署 Chroma、构建对应知识版本索引，再配置 Worker；不能只覆盖源码。

新建诊断选择“ReAct 顺序对照”或“LATS 假设分支”。已有会话策略不被悄悄替换。历史、知识与外部观察不能覆盖目标授权和取证事实。

## 没有完成、不能宣称完成的内容

没有云端发布、真实 Grafana 接入验收、硅基流动聊天模型完整诊断验收、跨副本压力测试、函数 Span 与采样精确关联、自动修复执行，也没有真实缺陷修复前后同负载验证。还不能据此判断 LATS 优于 ReAct。

下一批效果验收必须使用固定模型/预算/工具/知识版本，对 CPU、锁等待、依赖延迟至少三类可控事故做重置后的重复配对实验，分别报告根因、证据充分性、错误处置、耗时、模型/工具次数及真实修复收益。两篇合成文档连通、9 块索引建好和测试通过都不能替代该实验。

旧 `FAULT_PLAZA_21_BENCHMARK_REPORT.md` 的 95.2%、20/21 与 ReAct 42.8% 已标为无原始实验支持的撤回草稿。原始严格 21 场景结果仍为 1 项通过、20 项未通过；既不改写历史，也不把它直接当根因准确率。
